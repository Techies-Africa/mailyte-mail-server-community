---
title: Webhook Events Reference
description: Complete reference for all webhook event types, payload structure, signature verification, and delivery behavior.
---

# Webhook Events Reference

Mailyte sends webhooks for every significant event across the mail server.
Each delivery is an HTTP POST with a JSON payload, signed with HMAC-SHA256.

> **Edition note** — Events marked **[EE]** are only dispatched by the
> Enterprise Edition. All other events are available in both CE and EE.

---

## Envelope Structure

Every webhook, regardless of event type, follows this envelope:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "email.delivered",
  "timestamp": "2026-03-28T10:00:00.000000+00:00",
  "source": "smtp-service",
  "org_id": 42,
  "domain": "acme.com",
  "tags": ["campaign:q1-newsletter"],
  "user_variables": {
    "order_id": "ORD-9901"
  },
  "data": {
    "...event-specific fields..."
  },
  "metadata": {},
  "signature": {
    "timestamp": 1743158400,
    "token": "a3f8c1d2e9b047...",
    "signature": "e3b0c44298fc1c..."
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string (UUID4) | Unique event ID — use for idempotency |
| `event` | string | Dotted event name (e.g. `email.delivered`) |
| `timestamp` | ISO 8601 | UTC time the event occurred |
| `source` | string | Service that generated the event |
| `org_id` | int \| null | Organization context |
| `domain` | string \| null | Domain context |
| `tags` | string[] | Caller-supplied tags, passed through unchanged |
| `user_variables` | object | Caller-supplied key-value pairs, passed through unchanged |
| `data` | object | Event-specific payload (see per-event docs below) |
| `metadata` | object | Optional extra context |
| `signature` | object | Inline replay-protection block (see Verification) |

---

## Signature Verification

### Method 1 — Full body HMAC (recommended)

Every request includes the header:

```
X-Webhook-Signature: sha256=<hex>
X-Webhook-Id: 550e8400-e29b-41d4-a716-446655440000
X-Webhook-Event: email.delivered
X-Webhook-Timestamp: 2026-03-28T10:00:00.000000+00:00
```

Verify in Python:

```python
import hmac, hashlib

def verify_signature(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, header)
```

Verify in PHP:

```php
function verifySignature(string $rawBody, string $header, string $secret): bool {
    $expected = 'sha256=' . hash_hmac('sha256', $rawBody, $secret);
    return hash_equals($expected, $header);
}
```

### Method 2 — Inline signature block (replay-attack protection)

The `signature` object in the payload lets you verify without parsing the full body,
and lets you reject replays by checking the timestamp age.

```python
import hmac, hashlib, time

def verify_inline(payload: dict, secret: str, max_age_seconds: int = 900) -> bool:
    sig_block = payload.get("signature", {})
    ts = sig_block.get("timestamp", 0)
    token = sig_block.get("token", "")
    claimed_sig = sig_block.get("signature", "")

    # Reject stale events (default: older than 15 minutes)
    if abs(time.time() - ts) > max_age_seconds:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        (str(ts) + token).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, claimed_sig)
```

---

## Delivery Behavior

| Property | Value |
|----------|-------|
| Retries | 7 attempts over ~8 hours |
| Retry schedule | 10m → 10m → 15m → 30m → 1h → 2h → 4h |
| Timeout per attempt | 15 seconds (configurable via `WEBHOOK_TIMEOUT`) |
| Success codes | Any HTTP 2xx |
| Permanent no-retry | HTTP **406** — endpoint declines further delivery |
| Final failure | Written to `webhook_dead_letters` table |

Return `406` to permanently stop delivery for a specific event (e.g. the event
type is not relevant to your endpoint). All other non-2xx codes trigger a retry.

---

## Events by Category

### Email — Delivery Lifecycle

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `email.accepted` | ✓ | ✓ | MTA accepted the message for delivery |
| `email.inbound` | ✓ | ✓ | Inbound message received |
| `email.outbound` | ✓ | ✓ | Outbound message sent |
| `email.delivered` | ✓ | ✓ | Successfully delivered to recipient MTA |
| `email.bounced` | ✓ | ✓ | Bounced (hard or soft) |
| `email.deferred` | ✓ | ✓ | Temporarily deferred, will retry |
| `email.rejected` | ✓ | ✓ | Rejected by policy or filter |
| `email.dropped` | ✓ | ✓ | Dropped (suppression list, duplicate, etc.) |
| `email.stored` | ✓ | ✓ | Stored in mailbox via LMTP |
| `email.queued` | ✓ | ✓ | Placed in outbound queue |

#### `email.delivered` payload

```json
{
  "message_id": "<abc123@acme.com>",
  "recipient": "user@example.com",
  "sender": "noreply@acme.com",
  "subject": "Your order is confirmed",
  "domain": "acme.com",
  "delivery_status": {
    "code": 250,
    "message": "OK: queued as 8A3F"
  },
  "queue_id": "8A3F2B1C",
  "size": 14580
}
```

#### `email.bounced` payload

```json
{
  "message_id": "<abc123@acme.com>",
  "recipient": "noone@invalid.com",
  "sender": "noreply@acme.com",
  "domain": "acme.com",
  "bounce_type": "hard",
  "delivery_status": {
    "code": 550,
    "message": "5.1.1 The email account does not exist"
  },
  "error": "User unknown",
  "attempt": 1
}
```

#### `email.deferred` payload

```json
{
  "message_id": "<abc123@acme.com>",
  "recipient": "user@slowmx.com",
  "sender": "noreply@acme.com",
  "domain": "acme.com",
  "delivery_status": {
    "code": 421,
    "message": "4.7.0 Try again later"
  },
  "next_retry_at": "2026-03-28T10:10:00+00:00",
  "attempt": 2
}
```

#### `email.inbound` payload

```json
{
  "message_id": "<ext456@external.com>",
  "from": "sender@external.com",
  "to": "inbox@acme.com",
  "subject": "Re: Project update",
  "size": 9840,
  "has_attachments": false,
  "security": {
    "tls": true,
    "spf": "pass",
    "dkim": "pass",
    "dmarc": "pass",
    "spam_score": 1.2
  },
  "client_ip": "203.0.113.10",
  "client_hostname": "mail.external.com"
}
```

---

### Tracking

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `tracking.open` | ✓ | ✓ | Email opened (tracking pixel loaded) |
| `tracking.click` | ✓ | ✓ | Link clicked in email |
| `tracking.unsubscribe` | ✓ | ✓ | Recipient unsubscribed |

#### `tracking.open` payload

```json
{
  "message_id": "<abc123@acme.com>",
  "recipient": "user@example.com",
  "domain": "acme.com",
  "ip": "198.51.100.7",
  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)",
  "timestamp": "2026-03-28T10:05:22+00:00"
}
```

#### `tracking.click` payload

```json
{
  "message_id": "<abc123@acme.com>",
  "recipient": "user@example.com",
  "domain": "acme.com",
  "url": "https://acme.com/offers/spring",
  "ip": "198.51.100.7",
  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)",
  "timestamp": "2026-03-28T10:06:01+00:00"
}
```

#### `tracking.unsubscribe` payload

```json
{
  "message_id": "<abc123@acme.com>",
  "recipient": "user@example.com",
  "domain": "acme.com",
  "ip": "198.51.100.7",
  "timestamp": "2026-03-28T10:06:45+00:00"
}
```

---

### IMAP / POP3 User Actions

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `email.read` | ✓ | ✓ | Message marked as read |
| `email.unread` | ✓ | ✓ | Message marked as unread |
| `email.deleted` | ✓ | ✓ | Message deleted |
| `email.moved` | ✓ | ✓ | Message moved between folders |
| `email.copied` | ✓ | ✓ | Message copied to folder |
| `email.flagged` | ✓ | ✓ | Message flagged / starred |
| `email.unflagged` | ✓ | ✓ | Message unflagged |
| `email.replied` | ✓ | ✓ | Reply sent |
| `email.forwarded` | ✓ | ✓ | Message forwarded |
| `email.drafted` | ✓ | ✓ | Draft saved |
| `email.draft.deleted` | ✓ | ✓ | Draft deleted |
| `email.attachment.downloaded` | ✓ | ✓ | Attachment downloaded |
| `pop3.session.start` | ✓ | ✓ | POP3 session started |
| `pop3.session.end` | ✓ | ✓ | POP3 session ended |
| `pop3.messages.downloaded` | ✓ | ✓ | Messages downloaded via POP3 |

---

### Folder Operations

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `folder.created` | ✓ | ✓ | Folder created |
| `folder.deleted` | ✓ | ✓ | Folder deleted |
| `folder.renamed` | ✓ | ✓ | Folder renamed |
| `folder.subscribed` | ✓ | ✓ | Folder subscribed (IMAP) |
| `folder.unsubscribed` | ✓ | ✓ | Folder unsubscribed (IMAP) |

---

### Delivery Complaints & Spam

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `delivery.complaint` | ✓ | ✓ | Spam complaint received (FBL) |
| `delivery.bounce.hard` | ✓ | ✓ | Permanent bounce |
| `delivery.bounce.soft` | ✓ | ✓ | Temporary bounce |
| `delivery.delayed` | ✓ | ✓ | Delivery delay notification |
| `spam.reported` | ✓ | ✓ | Message reported as spam by user |
| `spam.quarantined` | ✓ | ✓ | Message quarantined by filter |
| `spam.released` | ✓ | ✓ | Message released from quarantine |
| `spam.false_positive` | ✓ | ✓ | Message marked as not spam |

---

### Storage

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `storage.quota.warning` | ✓ | ✓ | Mailbox approaching quota limit |
| `storage.quota.exceeded` | ✓ | ✓ | Mailbox quota exceeded |
| `storage.usage.report` | ✓ | ✓ | Periodic storage usage report |
| `storage.cleanup` | ✓ | ✓ | Storage cleanup completed |

#### `storage.quota.warning` payload

```json
{
  "mailbox": "user@acme.com",
  "domain": "acme.com",
  "used_bytes": 4563402752,
  "quota_bytes": 5368709120,
  "used_percent": 85.0,
  "threshold_percent": 80
}
```

---

### Authentication

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `auth.login.success` | ✓ | ✓ | Successful IMAP/POP3/SMTP login |
| `auth.login.failure` | ✓ | ✓ | Failed login attempt |
| `auth.password.changed` | ✓ | ✓ | Password changed |
| `auth.totp.enabled` | ✓ | ✓ | TOTP 2FA enabled |
| `auth.totp.disabled` | ✓ | ✓ | TOTP 2FA disabled |
| `auth.totp.verified` | ✓ | ✓ | TOTP code verified |
| `auth.api_key.created` | ✓ | ✓ | API key created |
| `auth.api_key.revoked` | ✓ | ✓ | API key revoked |

---

### Rate Limiting

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `rate_limit.threshold_breach` | ✓ | ✓ | Rate limit threshold breached |
| `rate_limit.exceeded` | ✓ | ✓ | Rate limit exceeded |
| `rate_limit.reset` | ✓ | ✓ | Rate limit counter reset |

---

### Queue Management

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `queue.message.queued` | ✓ | ✓ | Message placed in queue |
| `queue.message.processed` | ✓ | ✓ | Message processed from queue |
| `queue.message.failed` | ✓ | ✓ | Message permanently failed in queue |
| `queue.flushed` | ✓ | ✓ | Queue manually flushed |
| `queue.held` | ✓ | ✓ | Queue held (paused) |
| `queue.released` | ✓ | ✓ | Queue released (resumed) |

---

### Health & Monitoring

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `health.service.down` | ✓ | ✓ | A service went down |
| `health.service.up` | ✓ | ✓ | A service came back up |
| `health.system.alert` | ✓ | ✓ | System-level alert triggered |
| `health.recovery` | ✓ | ✓ | System recovered from alert |
| `health.check.failed` | ✓ | ✓ | Health check failed |

---

### Security

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `security.auth.failure` | ✓ | ✓ | Authentication failure |
| `security.brute_force` | ✓ | ✓ | Brute-force attack detected |
| `security.ip.blocked` | ✓ | ✓ | IP address blocked |
| `security.ip.whitelisted` | ✓ | ✓ | IP address whitelisted |
| `security.spam.detected` | ✓ | ✓ | Spam detected by filter |
| `security.phishing.detected` | ✓ | ✓ | Phishing attempt detected |
| `security.virus.detected` | ✓ | ✓ | Virus detected in message |
| `security.policy.violation` | ✓ | ✓ | Policy rule violated |
| `security.dlp.violation` | **[EE]** | ✓ | DLP policy violation |
| `security.geo.blocked` | **[EE]** | ✓ | Connection blocked by geo-policy |

---

### Mailbox / Domain / Alias Admin

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `mailbox.created` | ✓ | ✓ | Mailbox created |
| `mailbox.updated` | ✓ | ✓ | Mailbox settings updated |
| `mailbox.deleted` | ✓ | ✓ | Mailbox deleted |
| `mailbox.suspended` | ✓ | ✓ | Mailbox suspended |
| `mailbox.activated` | ✓ | ✓ | Mailbox activated |
| `mailbox.quota.changed` | ✓ | ✓ | Mailbox quota changed |
| `mailbox.password.changed` | ✓ | ✓ | Mailbox password changed |
| `domain.added` | ✓ | ✓ | Domain added |
| `domain.updated` | ✓ | ✓ | Domain settings updated |
| `domain.deleted` | ✓ | ✓ | Domain deleted |
| `domain.verified` | ✓ | ✓ | Domain DNS verified |
| `domain.verification.failed` | ✓ | ✓ | Domain verification failed |
| `domain.dns.configured` | ✓ | ✓ | DNS records configured |
| `alias.created` | ✓ | ✓ | Alias created |
| `alias.updated` | ✓ | ✓ | Alias updated |
| `alias.deleted` | ✓ | ✓ | Alias deleted |

---

### Filters & Transport Rules

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `filter.created` | ✓ | ✓ | Email filter created |
| `filter.updated` | ✓ | ✓ | Filter updated |
| `filter.deleted` | ✓ | ✓ | Filter deleted |
| `filter.triggered` | ✓ | ✓ | Filter matched and applied |
| `transport_rule.created` | ✓ | ✓ | Transport rule created |
| `transport_rule.updated` | ✓ | ✓ | Transport rule updated |
| `transport_rule.deleted` | ✓ | ✓ | Transport rule deleted |
| `transport_rule.triggered` | ✓ | ✓ | Transport rule applied to message |

---

### Sieve & Vacation

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `sieve.script.updated` | ✓ | ✓ | Sieve script saved |
| `sieve.script.activated` | ✓ | ✓ | Sieve script activated |
| `vacation.enabled` | ✓ | ✓ | Vacation/out-of-office enabled |
| `vacation.disabled` | ✓ | ✓ | Vacation disabled |
| `vacation.response.sent` | ✓ | ✓ | Auto-reply sent |

---

### Backup & Restore

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `backup.started` | ✓ | ✓ | Backup job started |
| `backup.completed` | ✓ | ✓ | Backup completed successfully |
| `backup.failed` | ✓ | ✓ | Backup failed |
| `restore.started` | ✓ | ✓ | Restore job started |
| `restore.completed` | ✓ | ✓ | Restore completed |
| `restore.failed` | ✓ | ✓ | Restore failed |

---

### Organization Admin (CRUD)

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `org.created` | ✓ | ✓ | Organization created |
| `org.updated` | ✓ | ✓ | Organization updated |
| `org.deleted` | ✓ | ✓ | Organization deleted |
| `org.suspended` | ✓ | ✓ | Organization suspended |
| `org.activated` | ✓ | ✓ | Organization activated |

---

### Shared Mailboxes — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `shared_mailbox.created` | ✓ | Shared mailbox created |
| `shared_mailbox.deleted` | ✓ | Shared mailbox deleted |
| `shared_mailbox.member.added` | ✓ | Member added to shared mailbox |
| `shared_mailbox.member.removed` | ✓ | Member removed |
| `shared_mailbox.permission.changed` | ✓ | Permissions updated |

---

### Migration — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `migration.started` | ✓ | Migration job started |
| `migration.progress` | ✓ | Progress update |
| `migration.completed` | ✓ | Migration completed |
| `migration.failed` | ✓ | Migration failed |
| `migration.cancelled` | ✓ | Migration cancelled |
| `migration.message.error` | ✓ | Individual message migration error |

---

### AI / RAG — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `rag.indexing.started` | ✓ | AI indexing job started |
| `rag.indexing.complete` | ✓ | AI indexing complete |
| `rag.search.complete` | ✓ | AI search request completed |
| `rag.ai.transaction` | ✓ | AI model invocation logged |
| `rag.classification` | ✓ | Message classified by AI |
| `rag.summarization` | ✓ | Message summarized by AI |

---

### Encryption — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `encryption.key.generated` | ✓ | Encryption key generated |
| `encryption.key.imported` | ✓ | Key imported |
| `encryption.key.revoked` | ✓ | Key revoked |
| `encryption.message.encrypted` | ✓ | Message encrypted |
| `encryption.message.decrypted` | ✓ | Message decrypted |

---

### Archive — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `archive.stored` | ✓ | Message archived |
| `archive.restored` | ✓ | Message restored from archive |
| `archive.deleted` | ✓ | Archive record deleted |
| `archive.hold.created` | ✓ | Legal hold created |
| `archive.hold.released` | ✓ | Legal hold released |

---

### Compliance — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `compliance.data_export` | ✓ | Data export request fulfilled |
| `compliance.data_erasure` | ✓ | Data erasure completed |
| `compliance.consent_changed` | ✓ | Consent preference updated |
| `compliance.hold.placed` | ✓ | Compliance hold placed |
| `compliance.hold.released` | ✓ | Compliance hold released |

---

### Delivery Optimizer — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `optimizer.ip.warmed` | ✓ | IP warm-up completed |
| `optimizer.domain.throttled` | ✓ | Sending throttled to a domain |
| `optimizer.reputation.changed` | ✓ | Sending reputation changed |
| `optimizer.fbl.received` | ✓ | Feedback loop (FBL) report received |

---

### URL Protection — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `url.rewritten` | ✓ | URL rewritten for tracking/protection |
| `url.blocked` | ✓ | Malicious URL blocked |
| `url.sandbox.result` | ✓ | URL sandbox analysis complete |

---

### White-Label & Reseller — **[EE]**

| Event | EE | When fired |
|-------|----|-----------|
| `whitelabel.configured` | ✓ | White-label settings configured |
| `whitelabel.updated` | ✓ | White-label settings updated |
| `reseller.account.created` | ✓ | Reseller account created |
| `reseller.account.suspended` | ✓ | Reseller account suspended |

---

### Webhook System (Meta)

| Event | CE | EE | When fired |
|-------|----|----|-----------|
| `webhook.test` | ✓ | ✓ | Test ping to verify endpoint |
| `webhook.delivery.failed` | ✓ | ✓ | Webhook delivery permanently failed |

---

## Idempotency

Each event has a unique `id` (UUID4). Store processed IDs to safely handle
duplicate deliveries — network retries can deliver the same event more than once.

```python
processed_ids = set()

def handle_webhook(payload: dict):
    event_id = payload["id"]
    if event_id in processed_ids:
        return  # Already handled
    processed_ids.add(event_id)
    process(payload)
```

---

## Dispatching Events (Server-Side)

From any service, fire an event with optional tags and user_variables:

```python
from shared.webhook_dispatcher import dispatch_event, Events

dispatch_event(
    event_type=Events.EMAIL_DELIVERED,
    data={
        "message_id": "<abc@acme.com>",
        "recipient": "user@example.com",
        "delivery_status": {"code": 250, "message": "OK"},
    },
    org_id=42,
    domain="acme.com",
    tags=["transactional", "campaign:welcome"],
    user_variables={"user_id": "usr_9901"},
)
```
