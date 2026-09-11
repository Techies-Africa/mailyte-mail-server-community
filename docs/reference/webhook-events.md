---
title: Webhook Events Reference
description: Complete reference for webhook event types, payload structure, signature verification, and delivery behavior.
---

# Webhook Events Reference

Mailyte sends webhooks for significant events across the mail server.
Each delivery is an HTTP POST with a JSON payload, signed with HMAC-SHA256.
The canonical dispatcher is `shared/webhook_dispatcher.py`; the full event-name
catalogue is its `Events` class.

---

## Envelope Structure

Every webhook, regardless of event type, follows this envelope:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "email.delivered",
  "timestamp": "2026-03-28T10:00:00.000000+00:00",
  "source": "log_ingestor",
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
| `source` | string | Service that generated the event (`api`, `log_ingestor`, `postfix`, `dovecot`, `tracking`, …) |
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

Every request includes these headers:

```
Content-Type:        application/json
X-Webhook-Id:        550e8400-e29b-41d4-a716-446655440000
X-Webhook-Signature: sha256=<hex>
X-Webhook-Event:     email.delivered
X-Webhook-Source:    log_ingestor
X-Webhook-Timestamp: 2026-03-28T10:00:00.000000+00:00
User-Agent:          Mailyte-Webhook/2.0
```

`X-Webhook-Signature` is `sha256=` + HMAC-SHA256 (hex) of the raw request body, keyed with `WEBHOOK_SECRET`. If no secret is configured the header value is `sha256=` with an empty digest — always configure a secret in production.

Verify in Python:

```python
import hmac, hashlib


def verify_signature(raw_body: bytes, header: str, secret: str) -> bool:
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
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

The `signature` object in the payload is HMAC-SHA256 of `str(timestamp) + token`
keyed with the same secret. Check it and reject stale events:

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
| Destination | The global `WEBHOOK_URL` / `WEBHOOK_URLS` endpoint (the canonical dispatcher delivers to the configured global endpoint; per-organization endpoints in the `webhook_urls` table are served by the webhooks service's notification sender, a separate path) |
| Attempts | 7 total (`WEBHOOK_MAX_RETRIES`) |
| Retry schedule | 10m → 10m → 15m → 30m → 1h → 2h → 4h (fixed table, ~8 hours) |
| Timeout per attempt | 15 seconds (`WEBHOOK_TIMEOUT`) |
| Success | Any HTTP status < 300 |
| Permanent no-retry | HTTP **406** — delivery stops immediately and the event is **not** dead-lettered |
| Final failure | After all 7 attempts, the envelope is written to the `webhook_dead_letters` table (`status='pending'`) for later requeue |
| Delivery log | Each attempt outcome is recorded in `webhook_delivery_logs` (`delivered` / `retrying` / `failed`) |

Return `406` to permanently decline an event type. All other non-2xx codes trigger a retry.

---

## Event Catalogue

The `Events` class defines 163 event names. The tables below list them by
category. **Events marked ✗ are defined in the catalogue but not yet emitted by
any code path** — they are reserved names; do not build integrations that wait
for them.

### Email — Delivery Lifecycle

Emitted by the log ingestor as it parses Postfix logs (one event per final
delivery attempt), and by the webhooks service for raw inbound/outbound
notifications.

| Event | Emitted | When fired |
|-------|---------|-----------|
| `email.delivered` | log_ingestor | Remote MTA accepted the message |
| `email.bounced` | log_ingestor, tracking | Permanent failure |
| `email.deferred` | log_ingestor | Temporary failure, Postfix will retry |
| `email.rejected` | log_ingestor | Rejected at SMTP time (`NOQUEUE`) |
| `email.inbound` | webhooks | Inbound message received |
| `email.outbound` | webhooks | Outbound message sent |
| `email.accepted` ✗ · `email.dropped` ✗ · `email.stored` ✗ · `email.queued` ✗ | — | reserved |

#### Delivery lifecycle payload (`email.delivered` / `bounced` / `deferred` / `rejected`)

The log ingestor sends the same `data` shape for all four:

```json
{
  "message_id": "<abc123@acme.com>",
  "queue_id": "8A3F2B1C",
  "sender": "noreply@acme.com",
  "from": "noreply@acme.com",
  "subject": "Your order is confirmed",
  "html": "<html>...captured body if available...</html>",
  "text": "...captured body if available...",
  "recipient": "user@example.com",
  "to": "user@example.com",
  "domain": "acme.com",
  "organization_id": "01J5X8...",
  "status": "delivered",
  "relay": "gmail-smtp-in.l.google.com[142.250.x.x]:25",
  "dsn": "2.0.0",
  "size": 14580,
  "detail": "250 2.0.0 OK  1743158400 x9-2002si",
  "timestamp": "2026-03-28T10:00:00"
}
```

`html`/`text` come from the `email_bodies` capture table when body capture is enabled; `detail` carries the remote server's full response (bounce reason for failures).

#### `email.inbound` payload

```json
{
  "message_id": "<ext456@external.com>",
  "from": "sender@external.com",
  "to": "inbox@acme.com",
  "subject": "Re: Project update",
  "size": 9840,
  "has_attachments": false,
  "spam_score": 1.2,
  "tls_used": true,
  "spf_result": "pass",
  "dkim_result": "pass",
  "dmarc_result": "pass"
}
```

---

### Tracking

| Event | Emitted | When fired |
|-------|---------|-----------|
| `tracking.open` | tracking | Tracking pixel loaded |
| `tracking.click` | tracking | Link clicked |
| `tracking.unsubscribe` | tracking | Recipient unsubscribed |

Payload keys: `email_id`, `recipient`, `ip_address`, `user_agent` (+ `url` for clicks; `email`, `reason` for unsubscribes).

---

### IMAP / POP3 User Actions

Emitted by the webhooks service from Dovecot activity (source `dovecot`).

| Event | Emitted | Notes |
|-------|---------|-------|
| `email.read`, `email.deleted`, `email.moved`, `email.copied`, `email.flagged`, `email.unflagged` | webhooks | payload: `user`, `mailbox`, `message_uid`, `message_id`, `action`, `flags` |
| `pop3.session.start`, `pop3.session.end`, `pop3.messages.downloaded` | webhooks | payload: `user`, `action`, `messages_downloaded`, `bytes_downloaded`, `session_duration` |
| `email.unread` ✗ · `email.replied` ✗ · `email.forwarded` ✗ · `email.drafted` ✗ · `email.draft.deleted` ✗ · `email.attachment.downloaded` ✗ | — | reserved |
| `folder.created` ✗ · `folder.deleted` ✗ · `folder.renamed` ✗ · `folder.subscribed` ✗ · `folder.unsubscribed` ✗ | — | reserved (entire group) |

---

### Delivery Complaints & Spam

| Event | Emitted | When fired |
|-------|---------|-----------|
| `delivery.bounce.hard` / `delivery.bounce.soft` | postfix (bounce_handler) | payload: `recipient`, `bounce_type`, `bounce_reason`, `message_id`, `tracking_id` |
| `delivery.complaint` | tracking | Spam complaint recorded |
| `delivery.delayed` ✗ · `delivery.success` ✗ | — | reserved |
| `spam.reported` ✗ · `spam.quarantined` ✗ · `spam.released` ✗ · `spam.false_positive` ✗ | — | reserved (entire group) |

---

### Storage

| Event | Emitted | When fired |
|-------|---------|-----------|
| `storage.quota.warning` | storage_usage | Approaching quota (`alert_level` in payload) |
| `storage.quota.exceeded` | storage_usage | Quota exceeded |
| `storage.usage.report` | storage_usage | Periodic usage report |
| `storage.cleanup` | storage_usage | Cleanup run completed |

#### `storage.quota.*` payload

```json
{
  "entity_type": "mailbox",
  "identifier": "user@acme.com",
  "storage_type": "total",
  "current_usage": 4563402752,
  "quota_limit": 5368709120,
  "usage_percentage": 85.0
}
```

---

### Authentication & Security

| Event | Emitted | When fired |
|-------|---------|-----------|
| `auth.login.success` / `auth.login.failure` | dovecot auth policy | IMAP/POP3/SMTP login result (`user`, `ip_address`, `protocol`) |
| `security.brute_force` | dovecot auth policy | Failure threshold crossed (`failure_count`, `block_duration_secs`, `block_type`) |
| `auth.totp.enabled` / `auth.totp.disabled` / `auth.totp.verified` | totp | TOTP lifecycle (`user`, `method`) |
| `security.ip.blocked` | postfix (ip_access_policy) | Connection blocked by IP rule (`ip_address`, `domain`, `rule_type`, `cidr`) |
| `security.dlp.violation` | dlp | DLP policy violation (`sender`, `recipient`, `violation_type`, `policy_name`, `action_taken`, `severity`, `message_id`) |
| `security.geo.blocked` | geo_blocking | Geo-policy block (`ip_address`, `country`, `user`, `reason`, `action`) |
| `auth.password.changed` ✗ · `auth.api_key.created` ✗ · `auth.api_key.revoked` ✗ · `security.auth.failure` ✗ · `security.ip.whitelisted` ✗ · `security.spam.detected` ✗ · `security.phishing.detected` ✗ · `security.virus.detected` ✗ · `security.policy.violation` ✗ | — | reserved |

---

### Rate Limiting

| Event | Emitted | When fired |
|-------|---------|-----------|
| `rate_limit.exceeded` | rate_limiter | A send was denied (`entity_type`, `identifier`, `direction`, `window`, `current_count`, `limit`) |
| `rate_limit.threshold_breach` | rate_limiter | Warning/critical threshold crossed (+ `usage_percentage`, `threshold_level`) |
| `rate_limit.reset` | rate_limiter | Limits updated or counters reset |

---

### Queue Management

| Event | Emitted | When fired |
|-------|---------|-----------|
| `queue.flushed` | queue_manager | Queue manually flushed |
| `queue.message.failed` | queue_manager | Message operation failed |
| `queue.held` / `queue.released` | queue_manager | Message held / released |
| `queue.message.queued` ✗ · `queue.message.processed` ✗ | — | reserved |

---

### Health & Monitoring

| Event | Emitted | When fired |
|-------|---------|-----------|
| `health.service.down` / `health.service.up` | monitoring | Service state transition (`service`, `old_status`, `new_status`) |
| `health.system.alert` | monitoring | CPU/memory/bounce-rate/SLA alert (`alert_type`, `severity`, `message`, …) |
| `health.recovery` | monitoring | Auto-heal action (`action`, `service`, `success`) |
| `health.check.failed` | monitoring | A health check itself errored |

---

### Mailbox / Domain / Alias Admin

All emitted by the API on the corresponding admin operation.

| Event | Payload keys (typical) |
|-------|------------------------|
| `org.created` | `organization_id`, `name`, `admin_email` |
| `org.updated` | `organization_id`, `updated_fields` (or quota/rate-limit fields) |
| `org.deleted` | `organization_id`, `name` |
| `domain.added` | `domain_id`, `domain`, `organization_id` |
| `domain.updated` | `domain_id`, `domain`, `updated_fields` / `update_type` |
| `domain.deleted` | `domain_id`, `domain`, `organization_id` (or `mailboxes_deleted`, `aliases_deleted`) |
| `domain.verified` | `organization_id`, `custom_domain`, `resolved_cname` (white-label verification) |
| `mailbox.created` | `account_id`, `email`, `domain_id`, `organization_id` |
| `mailbox.updated` | `account_id`, `email`, `updated_fields` |
| `mailbox.deleted` | `account_id`, `email`, `organization_id` |
| `alias.created` | `alias_id`, `source`, `destination`, `domain` |
| `alias.updated` | `alias_id`, `updated_fields` |
| `alias.deleted` | `alias_id`, `source` |
| `org.suspended` ✗ · `org.activated` ✗ · `domain.verification.failed` ✗ · `domain.dns.configured` ✗ · `mailbox.suspended` ✗ · `mailbox.activated` ✗ · `mailbox.quota.changed` ✗ · `mailbox.password.changed` ✗ | reserved |

---

### Filters, Transport Rules, Shared Mailboxes

| Event | Emitted | Payload keys |
|-------|---------|--------------|
| `filter.created` / `filter.updated` / `filter.deleted` | api | `script_name`, `email` (+ `active` / `action`) |
| `transport_rule.created` / `updated` / `deleted` | api | `rule_id`, `name`, `organization_id`, `direction`, `priority` |
| `shared_mailbox.created` / `deleted` | api | `shared_mailbox_id`, `email`, `name`, `organization_id` |
| `shared_mailbox.member.added` / `removed` | api | `shared_mailbox_id`, `member_id`, `member_email`, `permission` |
| `filter.triggered` ✗ · `transport_rule.triggered` ✗ · `shared_mailbox.permission.changed` ✗ | — | reserved |

---

### Migration

| Event | Emitted | Payload keys |
|-------|---------|--------------|
| `migration.started` | migration | `job_id`, `direction`, `source_host`, `target_email`, `is_delta` |
| `migration.progress` | migration | `job_id`, `progress_percentage`, `messages_migrated`, `messages_failed`, `total_messages`, `current_folder`, `speed_msgs_per_sec` |
| `migration.completed` | migration | `job_id`, `final_status`, `total_migrated`, `total_failed`, `duration_seconds` |
| `migration.failed` | migration | `job_id`, `direction`, `error` |
| `migration.cancelled` | migration | `job_id`, `cancelled_at`, `migrated_messages`, `failed_messages` |
| `migration.message.error` ✗ | — | reserved |

---

### AI / RAG, Encryption, Archive, Delivery Optimizer

| Event | Emitted | Payload keys |
|-------|---------|--------------|
| `rag.indexing.started` / `rag.indexing.complete` | rag | `mailbox`, `document_count` / `documents_indexed`, `chunks_created`, `duration_seconds` |
| `rag.search.complete` | rag | `query`, `results_count`, `user`, `processing_time_ms` |
| `rag.ai.transaction` | rag | `operation`, `model`, `tokens_used`, `estimated_cost_usd`, `status`, `transaction_id` |
| `encryption.key.generated` | encryption, api (DKIM) | `user`/`domain`, `key_type`/`kind`, `fingerprint`, `key_length`/`selector` |
| `encryption.key.imported` | encryption | `user`, `key_type`, `fingerprint`, `has_private` |
| `archive.stored` | archiver | `message_id`, `archive_location`, `storage_type`, `compressed_size`, `legal_hold` |
| `archive.restored` | archiver | `message_id`, `storage_key` |
| `optimizer.ip.warmed` | delivery_optimizer | `ip`, `stage`, `today_limit`, `target_volume` |
| `optimizer.domain.throttled` | delivery_optimizer | `domain`, `reason`, `organization_id` |
| `optimizer.reputation.changed` | delivery_optimizer | `domain`, `old_score`, `new_score`, `rating`, `bounce_rate`, `complaint_rate` |
| `optimizer.fbl.received` | delivery_optimizer | `reporter`, `feedback_type`, `original_recipient`, `suppressed` |
| `rag.classification` ✗ · `rag.summarization` ✗ · `encryption.key.revoked` ✗ · `encryption.message.encrypted` ✗ · `encryption.message.decrypted` ✗ · `archive.deleted` ✗ · `archive.hold.created` ✗ · `archive.hold.released` ✗ | — | reserved |

---

### Compliance, White-Label & Reseller

| Event | Emitted | Payload keys |
|-------|---------|--------------|
| `compliance.data_export` | api | `export_id`, `user_email`, `export_type`, `requested_by` |
| `compliance.data_erasure` | api | `erasure_id`, `user_email`, `requested_by`, `hard_delete_scheduled_at` |
| `compliance.consent_changed` | api | `user_email`, `consent_type`, `granted`, `action` |
| `whitelabel.configured` / `whitelabel.updated` | api | `organization_id`, `updated_fields` |
| `reseller.account.created` | api | `organization_id`, `parent_org_id`, `name`, `admin_email`, `plan_name` |
| `compliance.hold.placed` ✗ · `compliance.hold.released` ✗ · `reseller.account.suspended` ✗ | — | reserved |

---

### Reserved Groups (defined, nothing emits them)

These whole categories exist in the catalogue only:

- **Sieve & vacation:** `sieve.script.updated`, `sieve.script.activated`, `vacation.enabled`, `vacation.disabled`, `vacation.response.sent`
- **Backup & restore:** `backup.started`, `backup.completed`, `backup.failed`, `restore.started`, `restore.completed`, `restore.failed` — backup state is surfaced through Prometheus gauges instead (see Prometheus Metrics (Enterprise Edition))
- **URL protection:** `url.rewritten`, `url.blocked`, `url.sandbox.result`
- **Webhook meta:** `webhook.delivery.failed` (final failures land in the `webhook_dead_letters` table instead)

### Webhook System (Meta)

| Event | Emitted | When fired |
|-------|---------|-----------|
| `webhook.test` | api, webhooks | Test ping to verify an endpoint |

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
    },
    org_id=42,
    domain="acme.com",
    tags=["transactional", "campaign:welcome"],
    user_variables={"user_id": "usr_9901"},
)
```

Services in the mailer tier (`log_ingestor`, Postfix/Dovecot scripts) pass `use_redis=True`, publishing to the `mailyte:webhooks` Redis channel; the webhooks service subscribes and performs the actual delivery.

---

## Non-Canonical Webhook Paths

Two additional senders exist with **different envelopes and event names** — don't confuse them with the catalogue above:

- The webhooks service's **notification sender** delivers to per-organization endpoints from the `webhook_urls` table with events `rate_limit.quota_alert`, `rate_limit.limit_exceeded`, `email.smtp.inbound`, `email.smtp.outbound`, envelope `{"event", "timestamp", "payload"}` (optionally Fernet-encrypted), and its own retry policy (defaults: 3 retries, linear backoff).
- The monitoring service's **webhook notifier** sends `health.service.status_change`, `health.system.<type>`, `health.recovery.<action>`, `health.summary.periodic` to `HEALTH_WEBHOOK_URLS`.
