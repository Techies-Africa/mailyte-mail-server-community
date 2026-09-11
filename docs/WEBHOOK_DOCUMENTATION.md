# Mailyte Webhook Documentation

This document describes the webhook system as it actually works in the current codebase (verified against `shared/webhook_dispatcher.py`, `worker/api/routes/webhooks.py`, and the event producers on 2026-08-30).

## Architecture

All events funnel through a single shared dispatcher, `shared/webhook_dispatcher.py`. Every service imports `dispatch_event()`; the dispatcher builds a signed envelope, queues it in memory, and a background worker pool delivers it.

```
service code ──dispatch_event()──▶ in-memory queue (10,000)
                                        │  4 worker threads
                                        ▼
                              POST to WEBHOOK_URL (global)
                                        │
                 2xx ──▶ logged to webhook_delivery_logs
                 406 ──▶ permanent stop (no retry, no DLQ)
                 else ─▶ retry: 10m, 10m, 15m, 30m, 1h, 2h, 4h
                          └─ after 7 attempts ─▶ webhook_dead_letters
```

Services without their own dispatcher process (e.g. Postfix pipe scripts) can publish envelopes to Redis (`mailyte:webhooks` channel); the webhooks container subscribes and delivers them.

### The single global URL

**Delivery goes to exactly one URL**: the `WEBHOOK_URL` environment variable, which docker-compose maps from the `.env` key `WEBHOOK_URLS`. If it is unset, `dispatch_event()` returns without doing anything — no error, no delivery.

The API's endpoint-management routes (`/api/v1/webhooks/endpoints`, backed by the `webhook_urls` table) store per-organization endpoint registrations, but the dispatcher does **not** read that table — registered endpoints receive no events, and `POST /endpoints/{id}/test` dispatches its `webhook.test` event to the global URL. Per-endpoint/per-org delivery is a planned capability, not a current one.

## Envelope format

Every delivered payload has this shape:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "email.delivered",
  "timestamp": "2026-08-30T14:30:00.123456+00:00",
  "source": "log_ingestor",
  "org_id": "01J5XQ8ZK3...",
  "domain": "example.com",
  "tags": ["campaign:newsletter"],
  "user_variables": {"order_id": "12345"},
  "data": { "...event-specific payload..." },
  "metadata": {},
  "signature": {
    "timestamp": 1756563000,
    "token": "3f9a...64 hex chars...",
    "signature": "hmac-sha256 hex"
  }
}
```

HTTP headers on every request:

| Header | Content |
|--------|---------|
| `X-Webhook-Id` | The envelope `id` (use for idempotency) |
| `X-Webhook-Signature` | `sha256=<hmac-sha256 hex of the raw body>` |
| `X-Webhook-Event` | Event type |
| `X-Webhook-Source` | Emitting service |
| `X-Webhook-Timestamp` | Envelope timestamp |
| `User-Agent` | `Mailyte-Webhook/2.0` |

## Verifying deliveries

Two mechanisms, both keyed with `WEBHOOK_SECRET`:

**1. Body signature** (authenticity):

```python
import hmac, hashlib


def verify_body(raw_body: bytes, header_value: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", header_value)
```

**2. Inline signature block** (replay protection):

```python
import time


def verify_replay(sig_block: dict, secret: str, max_age=900) -> bool:
    expected = hmac.new(
        secret.encode(),
        (str(sig_block["timestamp"]) + sig_block["token"]).encode(),
        hashlib.sha256,
    ).hexdigest()
    fresh = abs(time.time() - sig_block["timestamp"]) <= max_age
    return fresh and hmac.compare_digest(expected, sig_block["signature"])
```

If `WEBHOOK_SECRET` is unset, both signatures are empty strings — always configure it.

## Delivery semantics

- **Retry schedule** (Mailgun-inspired): immediate attempt, then retries after 10m, 10m, 15m, 30m, 1h, 2h, 4h — 7 retries over ~8 hours (`WEBHOOK_MAX_RETRIES`, default 7).
- **Success** is any HTTP status < 300.
- **HTTP 406** from your endpoint permanently stops delivery of that event — no further retries, no dead letter. Use it to decline event types you never want.
- **Timeout** per attempt: `WEBHOOK_TIMEOUT` (default 15 seconds).
- **Exhausted retries** write the full original envelope to the `webhook_dead_letters` table (`status='pending'`) for operator triage.
- **Every attempt** is logged to `webhook_delivery_logs` (status `delivered` / `retrying` / `failed`, HTTP code, attempt count, error).
- **Queue overflow**: the in-memory queue holds 10,000 envelopes per process; when full, new events are dropped and counted in dispatcher stats.

## Operator API

On the platform API (`X-API-Key` auth):

| Route | Purpose |
|-------|---------|
| `GET /api/v1/webhooks/deliveries` | Paginated delivery log |
| `GET /api/v1/webhooks/dead-letters` | Dead-lettered events |
| `GET /api/v1/webhooks/dead-letters/{id}` | One dead letter with its original envelope |
| `POST /api/v1/webhooks/dead-letters/{id}/replay` | Re-queue the original envelope (same `id`, so receivers can dedupe) |
| `POST /api/v1/webhooks/dead-letters/replay` | Bulk replay |
| `GET/POST/PUT/DELETE /api/v1/webhooks/endpoints...` | Endpoint registration CRUD (stored only — see above) |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_URLS` (.env) → `WEBHOOK_URL` (container) | *(unset)* | The global delivery URL — **required for any delivery** |
| `WEBHOOK_SECRET` | *(empty)* | HMAC key for both signatures |
| `WEBHOOK_TIMEOUT` | `15` | Seconds per attempt |
| `WEBHOOK_MAX_RETRIES` | `7` | Attempts before dead-lettering |
| `WEBHOOK_WORKERS` | `4` | Worker threads per process |
| `WEBHOOK_QUEUE_SIZE` | `10000` | In-memory queue capacity |

## Event catalog

The authoritative constant list is the `Events` class in `shared/webhook_dispatcher.py`. Events with live producers today include:

**Mail lifecycle** (from the log ingestor, which tails the Postfix log): `email.delivered`, `email.bounced`, `email.deferred`, `email.rejected` — with sender, recipient, subject, relay, DSN, size, and (when captured) the message body in `data`.

**Tracking**: `tracking.open`, `tracking.click`, `tracking.unsubscribe`, `email.bounced`, `delivery.complaint`, `delivery.bounce.hard`, `delivery.bounce.soft`.

**Rate limiting**: `rate_limit.exceeded`, `rate_limit.threshold_breach`, `rate_limit.reset`.

**Storage**: `storage.quota.warning`, `storage.quota.exceeded`, `storage.usage.report`, `storage.cleanup`.

**Admin CRUD** (from the platform API): `org.*`, `domain.*` (including `domain.verified`), `mailbox.*`, `alias.*`, `filter.*`, `transport_rule.*`, `shared_mailbox.*`, `whitelabel.*`, `reseller.*`, `compliance.*`.

**Delivery optimizer**: `optimizer.domain.throttled`, `optimizer.reputation.changed`, `optimizer.ip.warmed`, `optimizer.fbl.received`.

**Security/auth**: `auth.login.success`, `auth.login.failure`, `security.brute_force`, `security.ip.blocked`.

**Operations**: `health.service.down/up`, `health.recovery`, `health.system.alert`, `health.check.failed`, `queue.flushed/held/released`, `queue.message.failed`, `archive.stored`, `archive.restored`, `migration.*`, `rag.*`, `encryption.key.generated/imported`, `webhook.test`.

### Defined but not currently emitted

The catalog also defines per-message IMAP/POP3 user-action events (`email.read`, `email.deleted`, `email.moved`, `email.flagged`, `pop3.*`, `folder.*`), `email.inbound`/`email.outbound` full-content events, and others whose producers are not wired: Postfix's `master.cf` defines a `webhook-filter` pipe service and the webhooks container exposes `POST /webhook/email/{inbound,outbound,imap,pop3}` ingestion endpoints, but **no transport or content filter currently routes mail through them, and Dovecot's config does not invoke the IMAP/POP3 notify scripts**. Do not depend on these event types.

Historical note: earlier revisions of this document described full-EML inbound/outbound webhooks with SPF/DKIM results as a working feature. That pipeline was never wired end-to-end; the current, real producers are the ones listed above.
