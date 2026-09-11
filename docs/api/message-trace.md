---
edition: enterprise
---

# Message Trace

Search mail delivery logs, follow a message's full delivery lifecycle, manage the quarantine, and search audit logs — the Exchange-style "message trace" surface used by the operations console.

**Base path:** `/api/v1/message-trace`
**Auth:** platform scope with an operator role — every endpoint here is console-facing. Reads require an operator session at role `support` or above; releasing a quarantined message requires `admin`, blocking a sender requires `operator`. A tenant API key cannot call these endpoints.

## Search Mail Delivery Logs

Search mail delivery logs with filters, newest first.

```
GET /api/v1/message-trace/trace
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sender` | string | -- | Filter by sender address |
| `recipient` | string | -- | Filter by recipient address |
| `subject` | string | -- | Filter by subject (partial match) |
| `status` | string | -- | `delivered`, `bounced`, `deferred`, `rejected` |
| `message_id` | string | -- | Filter by Message-ID |
| `organization_id` | string | -- | Filter by organization |
| `start_date` / `end_date` | string | -- | ISO 8601 date range |
| `limit` | integer | `50` | 1–500 |
| `offset` | integer | `0` | Pagination offset |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/message-trace/trace?recipient=john@acme.com&status=bounced&limit=20"
```

**Example Response**

```json
[
  {
    "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
    "timestamp": "2026-08-29T15:42:10",
    "sender": "news@sender.io",
    "recipient": "john@acme.com",
    "subject": "Weekly digest",
    "status": "bounced",
    "message_id": "<20260829154210.ABC123@sender.io>",
    "size": 48211,
    "relay": "mx.acme.com",
    "dsn": "5.1.1",
    "spam_score": 0.4,
    "bounce_reason": "user unknown"
  }
]
```

## Get Message Delivery Lifecycle

Full delivery lifecycle for one message by its Message-ID: all delivery log entries plus related audit events, in chronological order.

```
GET /api/v1/message-trace/trace/{msg_id}
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/message-trace/trace/%3C20260829154210.ABC123%40sender.io%3E"
```

The path parameter is the message's Message-ID (URL-encode the angle brackets and `@`).

## List Quarantined Messages

Messages held pending review, with a threat level computed from the spam score. **Headers and metadata only** — the console is never shown a message body (ADR-002 §5), so `storage_key` (the pointer to the stored message) is never returned.

```
GET /api/v1/message-trace/quarantine
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Narrow to one organization |
| `status` | string | `quarantined` | `quarantined` \| `released` \| `deleted` |
| `reason` | string | -- | `spam` \| `virus` \| `policy` ... |
| `q` | string | -- | Substring match on sender/recipient/subject |
| `limit` | integer | `50` | 1–200 |
| `offset` | integer | `0` | Pagination offset |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/message-trace/quarantine?status=quarantined&reason=spam"
```

**Example Response**

```json
{
  "messages": [
    { "id": "01J9AB...", "status": "quarantined", "threat_level": "high", "...": "headers and metadata" }
  ],
  "total": 3,
  "limit": 50,
  "offset": 0
}
```

## Release Quarantined Message

Deliver a message a filter judged malicious. Requires a reason, and records which operator released it. Also the undo for a block.

```
POST /api/v1/message-trace/quarantine/{quarantine_id}/release
```

**Auth:** operator session, role `admin` or above.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | Yes | Min 10 characters. Why this decision was made — recorded in the operator audit trail |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Verified with the recipient; legitimate invoice"}' \
  http://your-server:5000/api/v1/message-trace/quarantine/01J9AB.../release
```

## Block Quarantined Sender

Marks the held message as deleted and adds its sender to the organization's suppression list, so future mail from them is refused.

```
POST /api/v1/message-trace/quarantine/{quarantine_id}/block
```

**Auth:** operator session, role `operator` or above.

**Request Body:** same `{ "reason": "..." }` shape as release (min 10 characters).

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Repeated phishing attempts from this sender"}' \
  http://your-server:5000/api/v1/message-trace/quarantine/01J9AB.../block
```

!!! info "Where the block lives"
    The quarantine status enum has no `blocked` member — `deleted` is its terminal state. The durable part of the block is the **suppression entry**, not the message status.

## Search Audit Logs

Search audit logs with comprehensive filters, newest first.

```
GET /api/v1/message-trace/audit
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `event_type` | string | -- | Filter by event type |
| `event_source` | string | -- | Filter by source service |
| `organization_id` | string | -- | Filter by organization |
| `user_email` | string | -- | Filter by user |
| `client_ip` | string | -- | Filter by client IP |
| `severity` | string | -- | `info`, `warning`, `error`, ... |
| `start_date` / `end_date` | string | -- | ISO 8601 date range |
| `limit` | integer | `100` | 1–1000 |
| `offset` | integer | `0` | Pagination offset |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/message-trace/audit?severity=warning&limit=50"
```

**Example Response**

```json
[
  {
    "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
    "event_type": "auth_failure",
    "event_source": "dovecot",
    "organization_id": "01J8ZJ...",
    "user_email": "john@acme.com",
    "client_ip": "198.51.100.7",
    "severity": "warning",
    "details": { "attempts": 3 },
    "created_at": "2026-08-29T15:42:10"
  }
]
```
