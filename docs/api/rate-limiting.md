# Rate Limiting

View and manage email **sending** rate limits per organization, domain, mailbox, and
SMTP credential. These limits control how many messages can be sent (or received)
within a time window; they are unrelated to any HTTP request throttling.

All routes in this module live under the `/api/v1/rate-limiter` prefix and proxy to
the internal rate limiter service. Every route verifies that the domain or mailbox
in the path belongs to the caller's organization (`404` otherwise); platform-scoped
credentials can address any organization's entities.

## The rate limit rule

A rule is keyed by entity (`organization` | `domain` | `mailbox`), identifier, and
`direction` (`outbound`, the default, or `inbound`). Its fields:

| Field | Type | Description |
|---|---|---|
| `second_limit` | integer | Max messages per second (`0` = no limit) |
| `minute_limit` | integer | Max messages per minute |
| `hourly_limit` | integer | Max messages per hour |
| `daily_limit` | integer | Max messages per day |
| `monthly_limit` | integer | Max messages per month |
| `burst_limit` | integer | Burst allowance |
| `active` | boolean | Whether the rule is enforced (default `true`) |
| `priority` | integer | Rule priority (default `1`) |
| `warning_threshold` | integer | Percent of a limit at which a warning fires (default `80`) |
| `critical_threshold` | integer | Percent at which a critical alert fires (default `95`) |
| `description` | string | Free-text label |

Set endpoints accept a **partial** body: the gateway reads the entity's current rule
first and merges your fields on top, so setting `daily_limit` alone does not zero
out the other limits. `max_outbound_per_day` is accepted as an alias for
`daily_limit` (it wins if both are present).

## Get Domain Rate Limits

```
GET /api/v1/rate-limiter/rate-limits/domain/{domain}
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `direction` | string | `outbound` | `outbound` or `inbound` |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:8083/api/v1/rate-limiter/rate-limits/domain/acme.com
```

**Example Response**

```json
{
  "success": true,
  "data": {
    "entity_type": "domain",
    "identifier": "acme.com",
    "direction": "outbound",
    "config": {
      "hourly_limit": 500,
      "daily_limit": 5000,
      "monthly_limit": 100000,
      "burst_limit": 0,
      "active": true,
      "warning_threshold": 80,
      "critical_threshold": 95
    },
    "usage": {
      "hourly_count": 42,
      "daily_count": 350,
      "monthly_count": 12500
    },
    "percentages": {"hourly": 8.4, "daily": 7.0, "monthly": 12.5},
    "remaining": {"hourly": 458, "daily": 4650, "monthly": 87500},
    "timestamp": "2026-08-30T10:30:00"
  }
}
```

A limit of `0` means "no limit" and is omitted from `percentages`/`remaining`.

## Set Domain Rate Limits

```
POST /api/v1/rate-limiter/rate-limits/domain/{domain}
```

**Request Body** -- any subset of the [rule fields](#the-rate-limit-rule), plus an
optional `direction` (default `outbound`).

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "hourly_limit": 1000,
    "daily_limit": 10000,
    "monthly_limit": 200000
  }' \
  http://your-server:8083/api/v1/rate-limiter/rate-limits/domain/acme.com
```

On success the rate limiter answers
`{"success": true, "message": "Rate limits updated successfully"}`.

## Get Mailbox Rate Limits

```
GET /api/v1/rate-limiter/rate-limits/mailbox/{email}
```

Same query parameters and response shape as the domain read, with
`entity_type: "mailbox"`.

## Set Mailbox Rate Limits

```
POST /api/v1/rate-limiter/rate-limits/mailbox/{email}
```

Same body and merge semantics as the domain write.

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"hourly_limit": 100, "daily_limit": 1000}' \
  http://your-server:8083/api/v1/rate-limiter/rate-limits/mailbox/john@acme.com
```

## Get Domain Usage

```
GET /api/v1/rate-limiter/rate-limits/usage/{domain}
```

Returns the same config-plus-usage payload as
[Get Domain Rate Limits](#get-domain-rate-limits) (limits and usage are always
reported together). Accepts the same `direction` query parameter.

## Reset Rate Limit Counters

Reset the usage counters for an organization, domain, or mailbox -- for example
after resolving a deliverability incident.

```
POST /api/v1/rate-limiter/rate-limits/reset
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | `organization`, `domain`, or `mailbox` |
| `identifier` | string | Yes | Organization ID, domain name, or email address |
| `direction` | string | No | `outbound` (default) or `inbound` |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"type": "domain", "identifier": "acme.com", "direction": "outbound"}' \
  http://your-server:8083/api/v1/rate-limiter/rate-limits/reset
```

**Example Response**

```json
{
  "success": true,
  "message": "Counters reset successfully",
  "entity_type": "domain",
  "identifier": "acme.com",
  "direction": "outbound"
}
```

## Domain Quotas

The rate limiter has no quota concept separate from rate limits -- these endpoints
are alternate views over the **same rule** (with `monthly_limit`/`daily_limit` being
the closest equivalents of a sending quota).

```
GET  /api/v1/rate-limiter/quotas/domain/{domain}
POST /api/v1/rate-limiter/quotas/domain/{domain}
```

The GET returns the same payload as
[Get Domain Rate Limits](#get-domain-rate-limits); the POST accepts the same body
and merge semantics as [Set Domain Rate Limits](#set-domain-rate-limits).

## Per-SMTP-credential limits

Individual SMTP credentials carry their own optional `hourly_limit` and
`daily_limit`, set when creating or updating a credential via the
`/api/v1/smtp-credentials` API. The rate limiter enforces them per credential
username during sending, in addition to the mailbox/domain/organization rules
above. A `null` limit means the credential inherits no per-key bound for that
window.

## Errors

| Status | Meaning |
|---|---|
| `400` | Invalid entity type or direction |
| `404` | Domain/mailbox not found or not owned by your organization |
| `503` | Rate limiter service unavailable (`{"error": "Rate limiter service unavailable"}`) |
