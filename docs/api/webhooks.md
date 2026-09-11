# Webhooks

Real-time HTTP notifications for mail-system events, with HMAC-signed payloads,
automatic retries, a delivery log, and a dead-letter queue.

## How delivery actually works

Events are produced by `dispatch_event()` calls across the services and delivered
by a shared dispatcher (`shared/webhook_dispatcher.py`):

- Delivery goes to **one global endpoint**: the `WEBHOOK_URL` environment variable,
  signed with the global `WEBHOOK_SECRET`. If `WEBHOOK_URL` is unset, events are
  silently skipped -- nothing is queued, delivered, or logged.
- Events for **all organizations** go to that single URL; the envelope's `org_id`
  and `domain` fields tell the receiver whose event it is.

!!! warning "Per-organization endpoints are stored, not yet delivered to"
    The `/api/v1/webhooks/endpoints` CRUD below manages per-organization endpoint
    rows (URL, per-endpoint secret, `event_types` filter). As of 2026-08-30 the
    dispatcher does **not** read those rows: it delivers every event to the global
    `WEBHOOK_URL` only, and the per-endpoint `event_types` filter and secret are not
    consulted during delivery. Registering an endpoint therefore does not by itself
    cause events to arrive at it. The CRUD is the stable contract for
    per-organization fan-out; treat it as configuration ahead of the delivery
    feature.

### Event envelope

Every delivery is a JSON POST shaped like this:

```json
{
  "id": "5f3a2e0c-9a1b-4c7d-8e2f-0a1b2c3d4e5f",
  "event": "domain.added",
  "timestamp": "2026-08-30T10:30:00+00:00",
  "source": "api",
  "org_id": "01J1ABCDEF2345GHJKMNPQRSTV",
  "domain": "acme.com",
  "tags": [],
  "user_variables": {},
  "data": { ... },
  "metadata": {},
  "signature": {
    "timestamp": 1788088200,
    "token": "3f9c...64 hex chars...",
    "signature": "hmac-sha256 hex"
  }
}
```

**Request headers**

| Header | Value |
|---|---|
| `X-Webhook-Id` | The envelope `id` (use for idempotency) |
| `X-Webhook-Signature` | `sha256=<hex>` -- HMAC-SHA256 of the raw body with `WEBHOOK_SECRET` |
| `X-Webhook-Event` | The event name |
| `X-Webhook-Source` | The producing service |
| `X-Webhook-Timestamp` | The envelope timestamp |
| `User-Agent` | `Mailyte-Webhook/2.0` |

**Verifying a delivery**

1. Compute `HMAC-SHA256(WEBHOOK_SECRET, raw_body)` and compare with the
   `X-Webhook-Signature` value (after the `sha256=` prefix).
2. Independently verify the inline block:
   `HMAC-SHA256(WEBHOOK_SECRET, str(signature.timestamp) + signature.token)`
   must equal `signature.signature`, and reject payloads whose `timestamp` is more
   than 15 minutes old (replay protection).

### Retries and the dead-letter queue

- Up to **7 attempts over roughly 8 hours**: immediate, then after 10m, 10m, 15m,
  30m, 1h, 2h, 4h.
- Respond with any `2xx` to acknowledge.
- Respond with **`406`** to permanently stop delivery of that event (no retry, no
  dead letter).
- Every attempt is logged to the delivery log; an event that exhausts all attempts
  is written to the dead-letter queue with status `pending`.

## Event Types

Event names follow `{category}.{action}` (or `{category}.{subcategory}.{action}`).
The registry lives in `shared/webhook_dispatcher.py` (`Events`); the main
categories:

| Category | Examples |
|---|---|
| Email lifecycle | `email.accepted`, `email.inbound`, `email.outbound`, `email.delivered`, `email.bounced`, `email.deferred`, `email.rejected`, `email.queued`, `email.stored` |
| IMAP/POP3 activity | `email.read`, `email.deleted`, `email.moved`, `email.flagged`, `pop3.session.start`, `folder.created` |
| Tracking | `tracking.open`, `tracking.click`, `tracking.unsubscribe` |
| Delivery status | `delivery.success`, `delivery.bounce.hard`, `delivery.bounce.soft`, `delivery.complaint`, `delivery.delayed` |
| Authentication | `auth.login.success`, `auth.login.failure`, `auth.password.changed`, `auth.totp.enabled` |
| Rate limiting | `rate_limit.threshold_breach`, `rate_limit.exceeded`, `rate_limit.reset` |
| Storage | `storage.quota.warning`, `storage.quota.exceeded`, `storage.cleanup` |
| Queue | `queue.message.queued`, `queue.flushed`, `queue.held` |
| Health | `health.service.down`, `health.service.up`, `health.system.alert` |
| Security | `security.brute_force`, `security.spam.detected`, `security.virus.detected` |
| Admin CRUD | `org.created`, `org.updated`, `org.deleted`, `domain.added`, `domain.updated`, `domain.deleted`, `mailbox.created`, `mailbox.deleted`, `alias.created`, `alias.updated`, `alias.deleted` |
| Migration | `migration.started`, `migration.progress`, `migration.completed`, `migration.failed` |
| Meta | `webhook.test`, `webhook.delivery.failed` |

!!! note "An event name existing does not guarantee it fires"
    An event is only emitted where a code path actually calls `dispatch_event()`
    with it. The admin CRUD events, rate-limit events, storage events, and
    `webhook.test` are instrumented; some names in the registry (particularly the
    fine-grained IMAP/POP3 and encryption events) have no producer yet.

## List Webhook Endpoints

Retrieve all webhook endpoints for your organization.

```
GET /api/v1/webhooks/endpoints
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoints retrieved",
  "data": {
    "items": [
      {
        "id": "01J1WHK0000000000000000000",
        "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
        "url": "https://hooks.acme.com/mailyte",
        "description": "Production webhook",
        "active": 1,
        "event_types": ["email.delivered", "email.bounced"],
        "created_at": "2026-01-20T09:00:00",
        "updated_at": "2026-03-20T14:22:00"
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

!!! note "Secrets are not returned"
    The signing secret is never included in list or get responses. You only see it
    once, in the create response.

## Create Webhook Endpoint

```
POST /api/v1/webhooks/endpoints
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | Yes | Must start with `http://` or `https://` |
| `name` | string | No | Label (default `"webhook"`) |
| `description` | string | No | Description (HTML is stripped) |
| `active` | boolean | No | Default `true` |
| `event_types` | array or null | No | Event names to subscribe to; `null` is stored as `[]` |
| `secret` | string | No | Signing secret (a 64-hex-char secret is generated if omitted) |

**Example Response** (`201 Created`)

```json
{
  "type": "success",
  "msg": "Webhook endpoint created",
  "data": {
    "id": "01J1WHK0000000000000000000",
    "url": "https://hooks.acme.com/mailyte",
    "secret": "a1b2c3d4e5f6...",
    "active": true
  }
}
```

!!! warning "Save the secret"
    The `secret` is only returned once, in the create response.

## Get Webhook Endpoint

```
GET /api/v1/webhooks/endpoints/{endpoint_id}
```

Returns the stored endpoint record (without the secret). `404` if the endpoint does
not exist or belongs to another organization.

## Update Webhook Endpoint

```
PUT /api/v1/webhooks/endpoints/{endpoint_id}
```

**Request Body** -- any of: `url`, `description`, `active`, `event_types`
(`null` = all), `secret`. At least one accepted field is required (`400` otherwise).

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoint updated",
  "data": {"id": "01J1WHK0000000000000000000"}
}
```

## Delete Webhook Endpoint

```
DELETE /api/v1/webhooks/endpoints/{endpoint_id}
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoint deleted"
}
```

## Test Webhook Endpoint

Dispatch a `webhook.test` event. The endpoint must exist, belong to your
organization, and be active.

```
POST /api/v1/webhooks/endpoints/{endpoint_id}/test
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Test event dispatched",
  "data": {"endpoint_id": "01J1WHK0000000000000000000"}
}
```

The test event is dispatched through the normal dispatcher -- i.e. it is delivered
to the global `WEBHOOK_URL` (see the delivery-model note at the top).

## Delivery History

View the delivery log for webhook events belonging to your organization.

```
GET /api/v1/webhooks/deliveries
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `status` | string | -- | Filter by delivery status: `delivered`, `failed`, `retrying`, `pending`, `abandoned` (case-insensitive) |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Response**

```json
{
  "type": "success",
  "msg": "Delivery log retrieved",
  "data": {
    "items": [
      {
        "id": "01J1DLV0000000000000000000",
        "event_type": "email.bounced",
        "webhook_url": "https://hooks.example.com/mailyte",
        "delivery_status": "failed",
        "http_status_code": 500,
        "attempts": 3,
        "error_message": "HTTP 500: ...",
        "created_at": "2026-03-25T10:30:00",
        "delivered_at": null
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

## Dead Letter Queue

Events that fail after all retry attempts land here with status `pending`.
Dead-letter status values: `pending`, `retrying`, `resolved`, `abandoned`.

### List dead letters

```
GET /api/v1/webhooks/dead-letters
```

Standard `page`/`per_page` pagination. Items:

```json
{
  "id": 5,
  "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
  "event_type": "email.bounced",
  "endpoint_url": "https://hooks.example.com/mailyte",
  "last_error": "Failed after 7 delivery attempts",
  "attempt_count": 7,
  "status": "pending",
  "created_at": "2026-03-24T08:15:00",
  "last_attempted_at": "2026-03-24T16:15:00",
  "resolved_at": null
}
```

The stored event `payload` is deliberately omitted from the list -- fetch a single
row for it.

### Get one dead letter

```
GET /api/v1/webhooks/dead-letters/{dead_letter_id}
```

**Auth:** platform scope, operator role `support` or higher.

Returns the full row including the decoded `payload` (the exact envelope the
dispatcher tried to deliver) and a `replayable` flag. A payload that was truncated
at write time cannot be replayed; the row then carries `payload_error` instead.

### Replay one dead letter

```
POST /api/v1/webhooks/dead-letters/{dead_letter_id}/replay
```

**Auth:** platform scope, operator role `operator` or higher.

**Request Body:** `{"reason": "<min 10 characters -- recorded in the audit log>"}`

Re-queues the original envelope through the normal dispatcher path -- same signing,
retry schedule, and logging; the receiver sees the original event `id`, so its
idempotency check can recognise the duplicate. The row moves to `retrying`.

- `409` if the row is already `resolved`/`abandoned`, its payload is unreplayable,
  or `WEBHOOK_URL` is unset (there is nothing to replay to).
- Nothing automatically promotes `retrying` to `resolved` on a later successful
  delivery -- mark the row resolved manually once receipt is confirmed. The
  response carries this note verbatim.

### Replay several dead letters

```
POST /api/v1/webhooks/dead-letters/replay-bulk
```

**Auth:** platform scope, operator role `operator` or higher.

**Request Body**

| Field | Type | Description |
|---|---|---|
| `dead_letter_ids` | array | Up to 100 ids per call (`422` above that); duplicates are de-duplicated |
| `reason` | string | Min 10 characters |

Returns `200` with an honest per-id breakdown: `replayed` and `failed` arrays plus
the `requested` count -- a partial failure is not an all-or-nothing error.
