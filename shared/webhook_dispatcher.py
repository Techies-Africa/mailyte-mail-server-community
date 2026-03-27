#!/usr/bin/env python3
"""
Unified Global Webhook Dispatcher

ALL events in the Mailyte system are funneled through this single dispatcher.
Every service imports `dispatch_event()` and fires events here — they all get
pushed to one global webhook URL.

Architecture:
  - One global WEBHOOK_URL (env var) receives ALL events
  - Events are queued in-memory and dispatched by a background worker pool
  - Every payload has a consistent envelope: event type, timestamp, source
    service, org/domain context, and the event-specific data
  - HMAC-SHA256 signature on every request (X-Webhook-Signature header)
  - Automatic retries with exponential backoff on failure
  - Failed deliveries are logged to the `webhook_delivery_log` table
  - Redis pub/sub for cross-container fan-out (services in different
    containers publish to Redis; a single dispatcher process picks them up)

Usage from any service:

    from shared.webhook_dispatcher import dispatch_event

    dispatch_event(
        event_type="email.inbound",
        data={"message_id": "abc123", "from": "a@b.com", "to": "c@d.com"},
        org_id=1,
        domain="example.com",
    )

That's it. The dispatcher handles queuing, signing, delivery, retries, and logging.
"""

import os
import json
import time
import hmac
import hashlib
import logging
import threading
from queue import Queue, Empty
from datetime import datetime, timezone
from typing import Optional, Any, Dict

logger = logging.getLogger("webhook_dispatcher")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_TIMEOUT = int(os.getenv("WEBHOOK_TIMEOUT", "15"))
WEBHOOK_MAX_RETRIES = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))
WEBHOOK_WORKERS = int(os.getenv("WEBHOOK_WORKERS", "4"))
WEBHOOK_QUEUE_SIZE = int(os.getenv("WEBHOOK_QUEUE_SIZE", "10000"))

# Redis for cross-container pub/sub (optional — falls back to in-process queue)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_CHANNEL = "mailyte:webhooks"

# Database for delivery log (optional — gracefully degrades if unavailable)
DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "mailserver")
DB_USER = os.getenv("DB_USER", "mailuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mailpassword")

# Service identity (set by each service on import)
SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")


# ---------------------------------------------------------------------------
# Event Envelope
# ---------------------------------------------------------------------------

def _build_envelope(
    event_type: str,
    data: Dict[str, Any],
    org_id: Optional[int] = None,
    domain: Optional[str] = None,
    source_service: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """Build a standardized webhook event envelope."""
    return {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source_service or SERVICE_NAME,
        "org_id": org_id,
        "domain": domain,
        "data": data,
        "metadata": metadata or {},
    }


# ---------------------------------------------------------------------------
# HMAC Signature
# ---------------------------------------------------------------------------

def _sign_payload(payload_bytes: bytes) -> str:
    """Generate HMAC-SHA256 signature for the webhook payload."""
    if not WEBHOOK_SECRET:
        return ""
    return hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _deliver(envelope: dict, attempt: int = 1) -> bool:
    """
    Deliver a webhook event to the global URL.
    Returns True on success, False on failure.
    """
    if not WEBHOOK_URL:
        return False

    import requests

    payload_bytes = json.dumps(envelope, default=str).encode("utf-8")
    signature = _sign_payload(payload_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
        "X-Webhook-Event": envelope.get("event", ""),
        "X-Webhook-Source": envelope.get("source", ""),
        "X-Webhook-Timestamp": envelope.get("timestamp", ""),
        "User-Agent": "Mailyte-Webhook/1.0",
    }

    try:
        resp = requests.post(
            WEBHOOK_URL,
            data=payload_bytes,
            headers=headers,
            timeout=WEBHOOK_TIMEOUT,
        )

        if resp.status_code < 300:
            _log_delivery(envelope, resp.status_code, attempt, success=True)
            return True

        logger.warning(
            f"Webhook delivery failed: {resp.status_code} for {envelope.get('event')} "
            f"(attempt {attempt}/{WEBHOOK_MAX_RETRIES})"
        )
        _log_delivery(envelope, resp.status_code, attempt, success=False,
                       error=f"HTTP {resp.status_code}: {resp.text[:500]}")
        return False

    except requests.Timeout:
        logger.warning(f"Webhook timeout for {envelope.get('event')} (attempt {attempt})")
        _log_delivery(envelope, 0, attempt, success=False, error="Timeout")
        return False
    except requests.ConnectionError as e:
        logger.warning(f"Webhook connection error for {envelope.get('event')}: {e}")
        _log_delivery(envelope, 0, attempt, success=False, error=str(e)[:500])
        return False
    except Exception as e:
        logger.error(f"Webhook delivery exception: {e}")
        _log_delivery(envelope, 0, attempt, success=False, error=str(e)[:500])
        return False


def _write_to_dlq(envelope: dict):
    """Write a permanently failed webhook event to the dead letter queue table."""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
            connect_timeout=3,
        )
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO webhook_dead_letters
                (event_type, payload, webhook_url, error_message,
                 organization_id, retry_count, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (
            envelope.get("event", ""),
            json.dumps(envelope, default=str)[:50000],
            WEBHOOK_URL,
            f"Failed after {WEBHOOK_MAX_RETRIES} delivery attempts",
            envelope.get("org_id"),
            WEBHOOK_MAX_RETRIES,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Written to DLQ: {envelope.get('event')}")
    except Exception as e:
        logger.warning(f"Failed to write to webhook DLQ: {e}")


def _deliver_with_retries(envelope: dict):
    """Deliver with exponential backoff retries. Writes to DLQ on final failure."""
    for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
        if _deliver(envelope, attempt):
            return
        if attempt < WEBHOOK_MAX_RETRIES:
            delay = min(2 ** attempt, 60)  # 2s, 4s, 8s... max 60s
            time.sleep(delay)

    logger.error(
        f"Webhook delivery abandoned after {WEBHOOK_MAX_RETRIES} attempts: "
        f"{envelope.get('event')}"
    )
    _stats["failed"] += 1
    _write_to_dlq(envelope)


# ---------------------------------------------------------------------------
# Delivery Log (MySQL — best-effort, never blocks event dispatch)
# ---------------------------------------------------------------------------

def _log_delivery(envelope: dict, status_code: int, attempt: int,
                  success: bool, error: str = ""):
    """Log webhook delivery to database. Non-blocking, best-effort."""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
            connect_timeout=3,
        )
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO webhook_delivery_log
                (event_type, event_data, webhook_url, delivery_status,
                 http_status_code, attempts, error_message,
                 organization_id, created_at, delivered_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        """, (
            envelope.get("event", ""),
            json.dumps(envelope.get("data", {}), default=str)[:10000],
            WEBHOOK_URL,
            "DELIVERED" if success else ("FAILED" if attempt >= WEBHOOK_MAX_RETRIES else "RETRYING"),
            status_code,
            attempt,
            error[:2000] if error else None,
            envelope.get("org_id"),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if success else None,
        ))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        pass  # Never block dispatch over a logging failure


# ---------------------------------------------------------------------------
# Background Worker Pool
# ---------------------------------------------------------------------------

_event_queue: Queue = Queue(maxsize=WEBHOOK_QUEUE_SIZE)
_workers_started = False
_lock = threading.Lock()

# Stats
_stats = {
    "dispatched": 0,
    "delivered": 0,
    "failed": 0,
    "dropped": 0,
}


def _worker():
    """Background worker that processes events from the queue."""
    while True:
        try:
            envelope = _event_queue.get(timeout=5)
            _deliver_with_retries(envelope)
            _stats["delivered"] += 1
        except Empty:
            continue
        except Exception as e:
            logger.error(f"Webhook worker error: {e}")
            _stats["failed"] += 1


def _ensure_workers():
    """Start background worker threads if not already running."""
    global _workers_started
    if _workers_started:
        return
    with _lock:
        if _workers_started:
            return
        for i in range(WEBHOOK_WORKERS):
            t = threading.Thread(target=_worker, name=f"webhook-worker-{i}", daemon=True)
            t.start()
        _workers_started = True
        logger.info(f"Started {WEBHOOK_WORKERS} webhook dispatcher workers")


# ---------------------------------------------------------------------------
# Redis Pub/Sub (cross-container fan-out)
# ---------------------------------------------------------------------------

_redis_subscriber_started = False


def _start_redis_subscriber():
    """
    Listen on Redis pub/sub for events from other containers.
    This allows services running in different Docker containers to fire
    events via Redis, and a single dispatcher instance picks them up.
    """
    global _redis_subscriber_started
    if _redis_subscriber_started:
        return
    _redis_subscriber_started = True

    def _redis_listener():
        try:
            import redis
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(REDIS_CHANNEL)
            logger.info(f"Redis webhook subscriber listening on {REDIS_CHANNEL}")
            for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        envelope = json.loads(message["data"])
                        _enqueue(envelope)
                    except Exception as e:
                        logger.warning(f"Invalid Redis webhook message: {e}")
        except Exception as e:
            logger.warning(f"Redis subscriber not available: {e}")

    t = threading.Thread(target=_redis_listener, name="webhook-redis-sub", daemon=True)
    t.start()


def _publish_to_redis(envelope: dict):
    """Publish event to Redis for cross-container dispatch."""
    try:
        import redis
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.publish(REDIS_CHANNEL, json.dumps(envelope, default=str))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

def _enqueue(envelope: dict):
    """Put an event on the dispatch queue."""
    try:
        _event_queue.put_nowait(envelope)
    except Exception:
        _stats["dropped"] += 1
        logger.warning(f"Webhook queue full, event dropped: {envelope.get('event')}")


# ---------------------------------------------------------------------------
# Public API — this is what all services call
# ---------------------------------------------------------------------------

def dispatch_event(
    event_type: str,
    data: Dict[str, Any],
    org_id: Optional[int] = None,
    domain: Optional[str] = None,
    source_service: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    use_redis: bool = False,
):
    """
    Dispatch a webhook event to the global webhook URL.

    This is the ONLY function services need to call. Everything else
    (queuing, signing, delivery, retries, logging) is handled internally.

    Args:
        event_type: Dotted event name, e.g. "email.inbound", "tracking.open",
                    "storage.quota.exceeded", "migration.completed"
        data: Event-specific payload (dict)
        org_id: Organization ID (optional, for context)
        domain: Domain (optional, for context)
        source_service: Override the source service name (defaults to SERVICE_NAME env var)
        metadata: Additional metadata (optional)
        use_redis: If True, publish to Redis for cross-container dispatch.
                   Use this when the calling service doesn't have a direct
                   dispatcher worker pool (e.g., Postfix scripts).
    """
    if not WEBHOOK_URL:
        return  # No webhook configured, silently skip

    envelope = _build_envelope(
        event_type=event_type,
        data=data,
        org_id=org_id,
        domain=domain,
        source_service=source_service,
        metadata=metadata,
    )

    _stats["dispatched"] += 1

    if use_redis:
        if _publish_to_redis(envelope):
            return
        # Fall through to in-process queue if Redis is down

    _ensure_workers()
    _enqueue(envelope)


def dispatch_event_sync(
    event_type: str,
    data: Dict[str, Any],
    org_id: Optional[int] = None,
    domain: Optional[str] = None,
    source_service: Optional[str] = None,
) -> bool:
    """
    Synchronous version — blocks until delivery completes (or all retries fail).
    Use sparingly; prefer dispatch_event() for non-blocking dispatch.
    """
    if not WEBHOOK_URL:
        return False

    envelope = _build_envelope(event_type, data, org_id, domain, source_service)
    _stats["dispatched"] += 1
    _deliver_with_retries(envelope)
    return True


def get_dispatcher_stats() -> dict:
    """Get dispatcher statistics."""
    return {
        **_stats,
        "queue_size": _event_queue.qsize(),
        "queue_capacity": WEBHOOK_QUEUE_SIZE,
        "workers": WEBHOOK_WORKERS,
        "webhook_url_configured": bool(WEBHOOK_URL),
    }


# ---------------------------------------------------------------------------
# Event Type Constants
# ---------------------------------------------------------------------------

class Events:
    """
    All event types in the Mailyte system.

    Naming convention: {category}.{action} or {category}.{subcategory}.{action}

    Every activity dispatches a webhook — like Mailgun, Postmark, and SendGrid.
    """

    # ── Email — SMTP Lifecycle (Mailgun-style) ──────────────────────────
    EMAIL_ACCEPTED = "email.accepted"          # Accepted by MTA for delivery
    EMAIL_INBOUND = "email.inbound"            # Inbound message received
    EMAIL_OUTBOUND = "email.outbound"          # Outbound message sent
    EMAIL_DELIVERED = "email.delivered"         # Successfully delivered to recipient MTA
    EMAIL_BOUNCED = "email.bounced"            # Bounced (hard or soft)
    EMAIL_DEFERRED = "email.deferred"          # Temporarily deferred, will retry
    EMAIL_REJECTED = "email.rejected"          # Rejected by policy/filter
    EMAIL_DROPPED = "email.dropped"            # Dropped (suppression list, duplicate, etc.)
    EMAIL_STORED = "email.stored"              # Stored in mailbox (LMTP delivery)
    EMAIL_QUEUED = "email.queued"              # Placed in outbound queue

    # ── Email — IMAP/POP3 User Actions ──────────────────────────────────
    EMAIL_READ = "email.read"                  # Message marked as read
    EMAIL_UNREAD = "email.unread"              # Message marked as unread
    EMAIL_DELETED = "email.deleted"            # Message deleted
    EMAIL_MOVED = "email.moved"                # Message moved between folders
    EMAIL_COPIED = "email.copied"              # Message copied to folder
    EMAIL_FLAGGED = "email.flagged"            # Message flagged/starred
    EMAIL_UNFLAGGED = "email.unflagged"        # Message unflagged
    EMAIL_REPLIED = "email.replied"            # Reply sent
    EMAIL_FORWARDED = "email.forwarded"        # Message forwarded
    EMAIL_DRAFTED = "email.drafted"            # Draft saved
    EMAIL_DRAFT_DELETED = "email.draft.deleted"  # Draft deleted
    EMAIL_ATTACHMENT_DOWNLOADED = "email.attachment.downloaded"

    # ── Email — POP3 Specific ───────────────────────────────────────────
    POP3_SESSION_START = "pop3.session.start"
    POP3_SESSION_END = "pop3.session.end"
    POP3_MESSAGES_DOWNLOADED = "pop3.messages.downloaded"

    # ── Email — Folder Operations ───────────────────────────────────────
    FOLDER_CREATED = "folder.created"
    FOLDER_DELETED = "folder.deleted"
    FOLDER_RENAMED = "folder.renamed"
    FOLDER_SUBSCRIBED = "folder.subscribed"
    FOLDER_UNSUBSCRIBED = "folder.unsubscribed"

    # ── Tracking (Mailgun/SendGrid-style) ───────────────────────────────
    TRACKING_OPEN = "tracking.open"            # Email opened (pixel loaded)
    TRACKING_CLICK = "tracking.click"          # Link clicked
    TRACKING_UNSUBSCRIBE = "tracking.unsubscribe"  # Unsubscribe action

    # ── Delivery Status ─────────────────────────────────────────────────
    DELIVERY_SUCCESS = "delivery.success"
    DELIVERY_BOUNCE_HARD = "delivery.bounce.hard"    # Permanent failure
    DELIVERY_BOUNCE_SOFT = "delivery.bounce.soft"    # Temporary failure
    DELIVERY_COMPLAINT = "delivery.complaint"        # Spam complaint (FBL)
    DELIVERY_DELAYED = "delivery.delayed"            # Delayed delivery notification

    # ── Authentication Events ───────────────────────────────────────────
    AUTH_LOGIN_SUCCESS = "auth.login.success"     # Successful IMAP/POP3/SMTP login
    AUTH_LOGIN_FAILURE = "auth.login.failure"     # Failed login attempt
    AUTH_PASSWORD_CHANGED = "auth.password.changed"
    AUTH_TOTP_ENABLED = "auth.totp.enabled"
    AUTH_TOTP_DISABLED = "auth.totp.disabled"
    AUTH_TOTP_VERIFIED = "auth.totp.verified"
    AUTH_API_KEY_CREATED = "auth.api_key.created"
    AUTH_API_KEY_REVOKED = "auth.api_key.revoked"

    # ── Rate Limiting ───────────────────────────────────────────────────
    RATE_LIMIT_THRESHOLD = "rate_limit.threshold_breach"
    RATE_LIMIT_EXCEEDED = "rate_limit.exceeded"
    RATE_LIMIT_RESET = "rate_limit.reset"

    # ── Storage ─────────────────────────────────────────────────────────
    STORAGE_QUOTA_WARNING = "storage.quota.warning"
    STORAGE_QUOTA_EXCEEDED = "storage.quota.exceeded"
    STORAGE_USAGE_REPORT = "storage.usage.report"
    STORAGE_CLEANUP = "storage.cleanup"

    # ── Queue Management ────────────────────────────────────────────────
    QUEUE_MESSAGE_QUEUED = "queue.message.queued"
    QUEUE_MESSAGE_PROCESSED = "queue.message.processed"
    QUEUE_MESSAGE_FAILED = "queue.message.failed"
    QUEUE_FLUSHED = "queue.flushed"
    QUEUE_HELD = "queue.held"
    QUEUE_RELEASED = "queue.released"

    # ── Health / Monitoring ─────────────────────────────────────────────
    HEALTH_SERVICE_DOWN = "health.service.down"
    HEALTH_SERVICE_UP = "health.service.up"
    HEALTH_SYSTEM_ALERT = "health.system.alert"
    HEALTH_RECOVERY = "health.recovery"
    HEALTH_CHECK_FAILED = "health.check.failed"

    # ── Security ────────────────────────────────────────────────────────
    SECURITY_AUTH_FAILURE = "security.auth.failure"
    SECURITY_BRUTE_FORCE = "security.brute_force"
    SECURITY_DLP_VIOLATION = "security.dlp.violation"
    SECURITY_GEO_BLOCKED = "security.geo.blocked"
    SECURITY_IP_BLOCKED = "security.ip.blocked"
    SECURITY_IP_WHITELISTED = "security.ip.whitelisted"
    SECURITY_SPAM_DETECTED = "security.spam.detected"
    SECURITY_PHISHING_DETECTED = "security.phishing.detected"
    SECURITY_VIRUS_DETECTED = "security.virus.detected"
    SECURITY_POLICY_VIOLATION = "security.policy.violation"

    # ── Spam / Filtering ────────────────────────────────────────────────
    SPAM_QUARANTINED = "spam.quarantined"
    SPAM_RELEASED = "spam.released"
    SPAM_REPORTED = "spam.reported"
    SPAM_FALSE_POSITIVE = "spam.false_positive"

    # ── Migration ───────────────────────────────────────────────────────
    MIGRATION_STARTED = "migration.started"
    MIGRATION_PROGRESS = "migration.progress"
    MIGRATION_COMPLETED = "migration.completed"
    MIGRATION_FAILED = "migration.failed"
    MIGRATION_CANCELLED = "migration.cancelled"
    MIGRATION_MESSAGE_ERROR = "migration.message.error"

    # ── RAG / AI ────────────────────────────────────────────────────────
    RAG_INDEXING_STARTED = "rag.indexing.started"
    RAG_INDEXING_COMPLETE = "rag.indexing.complete"
    RAG_SEARCH_COMPLETE = "rag.search.complete"
    RAG_AI_TRANSACTION = "rag.ai.transaction"
    RAG_CLASSIFICATION = "rag.classification"
    RAG_SUMMARIZATION = "rag.summarization"

    # ── Organization / Admin CRUD ───────────────────────────────────────
    ORG_CREATED = "org.created"
    ORG_UPDATED = "org.updated"
    ORG_DELETED = "org.deleted"
    ORG_SUSPENDED = "org.suspended"
    ORG_ACTIVATED = "org.activated"

    DOMAIN_ADDED = "domain.added"
    DOMAIN_UPDATED = "domain.updated"
    DOMAIN_DELETED = "domain.deleted"
    DOMAIN_VERIFIED = "domain.verified"
    DOMAIN_VERIFICATION_FAILED = "domain.verification.failed"
    DOMAIN_DNS_CONFIGURED = "domain.dns.configured"

    MAILBOX_CREATED = "mailbox.created"
    MAILBOX_UPDATED = "mailbox.updated"
    MAILBOX_DELETED = "mailbox.deleted"
    MAILBOX_SUSPENDED = "mailbox.suspended"
    MAILBOX_ACTIVATED = "mailbox.activated"
    MAILBOX_QUOTA_CHANGED = "mailbox.quota.changed"
    MAILBOX_PASSWORD_CHANGED = "mailbox.password.changed"

    ALIAS_CREATED = "alias.created"
    ALIAS_UPDATED = "alias.updated"
    ALIAS_DELETED = "alias.deleted"

    # ── Filters / Rules ─────────────────────────────────────────────────
    FILTER_CREATED = "filter.created"
    FILTER_UPDATED = "filter.updated"
    FILTER_DELETED = "filter.deleted"
    FILTER_TRIGGERED = "filter.triggered"

    TRANSPORT_RULE_CREATED = "transport_rule.created"
    TRANSPORT_RULE_UPDATED = "transport_rule.updated"
    TRANSPORT_RULE_DELETED = "transport_rule.deleted"
    TRANSPORT_RULE_TRIGGERED = "transport_rule.triggered"

    # ── Shared Mailboxes ────────────────────────────────────────────────
    SHARED_MAILBOX_CREATED = "shared_mailbox.created"
    SHARED_MAILBOX_DELETED = "shared_mailbox.deleted"
    SHARED_MAILBOX_MEMBER_ADDED = "shared_mailbox.member.added"
    SHARED_MAILBOX_MEMBER_REMOVED = "shared_mailbox.member.removed"
    SHARED_MAILBOX_PERMISSION_CHANGED = "shared_mailbox.permission.changed"

    # ── Encryption ──────────────────────────────────────────────────────
    ENCRYPTION_KEY_GENERATED = "encryption.key.generated"
    ENCRYPTION_KEY_IMPORTED = "encryption.key.imported"
    ENCRYPTION_KEY_REVOKED = "encryption.key.revoked"
    ENCRYPTION_MESSAGE_ENCRYPTED = "encryption.message.encrypted"
    ENCRYPTION_MESSAGE_DECRYPTED = "encryption.message.decrypted"

    # ── Archiver ────────────────────────────────────────────────────────
    ARCHIVE_STORED = "archive.stored"
    ARCHIVE_RESTORED = "archive.restored"
    ARCHIVE_DELETED = "archive.deleted"
    ARCHIVE_HOLD_CREATED = "archive.hold.created"
    ARCHIVE_HOLD_RELEASED = "archive.hold.released"

    # ── Delivery Optimizer ──────────────────────────────────────────────
    OPTIMIZER_IP_WARMED = "optimizer.ip.warmed"
    OPTIMIZER_DOMAIN_THROTTLED = "optimizer.domain.throttled"
    OPTIMIZER_REPUTATION_CHANGED = "optimizer.reputation.changed"
    OPTIMIZER_FBL_RECEIVED = "optimizer.fbl.received"

    # ── URL Protection ──────────────────────────────────────────────────
    URL_REWRITTEN = "url.rewritten"
    URL_BLOCKED = "url.blocked"
    URL_SANDBOX_RESULT = "url.sandbox.result"

    # ── White-Label / Reseller ──────────────────────────────────────────
    WHITELABEL_CONFIGURED = "whitelabel.configured"
    WHITELABEL_UPDATED = "whitelabel.updated"
    RESELLER_ACCOUNT_CREATED = "reseller.account.created"
    RESELLER_ACCOUNT_SUSPENDED = "reseller.account.suspended"

    # ── Compliance ──────────────────────────────────────────────────────
    COMPLIANCE_DATA_EXPORT = "compliance.data_export"
    COMPLIANCE_DATA_ERASURE = "compliance.data_erasure"
    COMPLIANCE_CONSENT_CHANGED = "compliance.consent_changed"
    COMPLIANCE_HOLD_PLACED = "compliance.hold.placed"
    COMPLIANCE_HOLD_RELEASED = "compliance.hold.released"

    # ── Sieve / Vacation ────────────────────────────────────────────────
    SIEVE_SCRIPT_UPDATED = "sieve.script.updated"
    SIEVE_SCRIPT_ACTIVATED = "sieve.script.activated"
    VACATION_ENABLED = "vacation.enabled"
    VACATION_DISABLED = "vacation.disabled"
    VACATION_RESPONSE_SENT = "vacation.response.sent"

    # ── Backup / Restore ────────────────────────────────────────────────
    BACKUP_STARTED = "backup.started"
    BACKUP_COMPLETED = "backup.completed"
    BACKUP_FAILED = "backup.failed"
    RESTORE_STARTED = "restore.started"
    RESTORE_COMPLETED = "restore.completed"
    RESTORE_FAILED = "restore.failed"

    # ── Webhook System (Meta) ───────────────────────────────────────────
    WEBHOOK_TEST = "webhook.test"
    WEBHOOK_DELIVERY_FAILED = "webhook.delivery.failed"


# ---------------------------------------------------------------------------
# Initialize Redis subscriber if this module is loaded in the webhooks service
# ---------------------------------------------------------------------------

if SERVICE_NAME == "webhooks":
    _start_redis_subscriber()
