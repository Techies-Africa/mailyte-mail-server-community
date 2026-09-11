# Statistics & Analytics

Retrieve delivery statistics, engagement metrics, reports, and manage the
suppression list.

Analytics endpoints (`/api/v1/analytics/...`) proxy to the internal analytics
service, which aggregates the `mail_logs` delivery record and `email_tracking`
events. Tracking statistics and suppressions live under `/api/v1/tracking/...`. All
domain-scoped endpoints verify the domain belongs to your organization (`404`
otherwise).

Time windows are selected with a single `days` query parameter (default `30`) --
counting back from now.

## Dashboard Data

Aggregate send/delivery/engagement metrics for a domain.

```
GET /api/v1/analytics/dashboard/{domain}
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | integer | `30` | Look-back window in days |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:8083/api/v1/analytics/dashboard/acme.com?days=30"
```

**Example Response**

```json
{
  "domain": "acme.com",
  "period_days": 30,
  "total_sent": 5000,
  "delivered": 4925,
  "bounced": 60,
  "failed": 15,
  "opened": 3200,
  "clicked": 800,
  "complained": 2,
  "unsubscribed": 12,
  "generated_at": "2026-08-30T10:30:00Z"
}
```

## Email Volume

Daily send volume for a domain, split into outbound (sender on the domain) and
inbound (recipient on the domain).

```
GET /api/v1/analytics/email-volume/{domain}
```

**Example Response**

```json
{
  "domain": "acme.com",
  "period_days": 30,
  "volume": [
    {"date": "2026-08-29", "outbound": 245, "inbound": 1032}
  ],
  "generated_at": "2026-08-30T10:30:00Z"
}
```

## Engagement Metrics

Open and click rates for a domain (rates are computed against delivered messages).

```
GET /api/v1/analytics/engagement/{domain}
```

**Example Response**

```json
{
  "domain": "acme.com",
  "period_days": 30,
  "delivered": 4925,
  "opens": 3200,
  "clicks": 800,
  "unique_opens": 2100,
  "unique_clicks": 650,
  "open_rate": 42.64,
  "click_rate": 13.2,
  "click_to_open_rate": 25.0,
  "reply_rate": null,
  "average_read_time_seconds": null,
  "generated_at": "2026-08-30T10:30:00Z"
}
```

!!! note "Honest nulls"
    `reply_rate` and `average_read_time_seconds` are always `null` -- no
    reply-detection or read-time instrumentation exists in the stack, and the API
    reports the absence rather than a fabricated number.

## Deliverability Metrics

Bounce/complaint rates and DKIM configuration status for a domain.

```
GET /api/v1/analytics/deliverability/{domain}
```

**Example Response**

```json
{
  "domain": "acme.com",
  "period_days": 30,
  "total_sent": 5000,
  "bounced": 60,
  "bounce_rate": 1.2,
  "complaints": 2,
  "complaint_rate": 0.04,
  "average_spam_score": 1.35,
  "dkim_configured": true,
  "dkim_selector": "default",
  "spf_pass_rate": null,
  "dmarc_pass_rate": null,
  "inbox_placement_estimate": null,
  "generated_at": "2026-08-30T10:30:00Z"
}
```

`dkim_configured` reflects local configuration, not a live DNS check (use
[Verify DNS](domains.md#verify-dns-records) for that). SPF/DMARC pass rates and
inbox-placement estimates are not tracked anywhere in the stack and are reported as
`null`.

## Domain Metrics

Everything the dashboard returns, plus real storage and account usage from the
domain record.

```
GET /api/v1/analytics/metrics/{domain}
```

Adds: `storage_used_bytes`, `storage_quota_bytes`, `active_email_accounts`,
`max_email_accounts`, `lifetime_emails`, `lifetime_attachments`.

## Reports

### Generate Report

Generate an analytics report for a domain. Generation is **synchronous** -- the
completed report is returned immediately and also stored (Redis, 90-day retention)
for later retrieval by `report_id`.

```
POST /api/v1/analytics/reports/generate
```

**Request Body**

| Field | Type | Default | Description |
|---|---|---|---|
| `domain` | string | required | Domain to report on |
| `report_type` | string | `dashboard` | `dashboard`, `email-volume`, `engagement`, `deliverability`, or `metrics` |
| `days` | integer | `30` | Look-back window |

**Example Response**

```json
{
  "report_id": "0c8f9f8e-3f2a-4a57-9a3e-1b2c3d4e5f60",
  "report_type": "deliverability",
  "domain": "acme.com",
  "period_days": 30,
  "status": "completed",
  "data": { ... },
  "generated_at": "2026-08-30T10:30:00Z"
}
```

An unknown `report_type` returns `400`; an unknown domain returns `404`.

### Get Report

```
GET /api/v1/analytics/reports/{report_id}
```

Returns the stored report, or `404` (`"Report not found or expired"`) after the
90-day retention window.

### Scheduled Reports

```
GET  /api/v1/analytics/reports/scheduled?organization_id=...
POST /api/v1/analytics/reports/scheduled
```

The list requires an `organization_id` query parameter (`400` without it) and
returns `{"schedules": [...], "count": n}`.

**Create body**

| Field | Type | Required | Description |
|---|---|---|---|
| `organization_id` | string | Yes | Owning organization |
| `domain` | string | Yes | Domain to report on |
| `report_type` | string | No | Same values as on-demand reports (default `dashboard`) |
| `interval` | string | No | `daily`, `weekly` (default), or `monthly` |
| `recipients` | array | No | Email addresses to deliver to |

Returns `201` with the stored schedule entry (including its `schedule_id`).

!!! note "Delivery format"
    A scheduler tick runs roughly every 15 minutes; due schedules are generated
    over a 7-day window and emailed to the recipients as **plain text with the
    report JSON inline** -- there is no PDF/HTML rendering. Schedules with no
    recipients record `"no recipients configured"` in `last_error`.

## Tracking Statistics

### Domain Tracking Stats

Aggregated tracking statistics for a domain, proxied to the tracking service.

```
GET /api/v1/tracking/stats/domain/{domain}
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | integer | `30` | Look-back window, 1--365 |

The response is the tracking service payload passed through verbatim -- e.g.
`event_statistics`, `total_sent`, `total_opens`, `total_clicks`, `period_days`,
`generated_at`.

### Single Email Tracking

Tracking data for one sent email by its tracking/email ID.

```
GET /api/v1/tracking/stats/email/{email_id}
```

## Suppression List

Suppressed addresses are never sent to again. Suppression rows are per-organization
(`email` + `organization_id` + `suppression_type` is unique) with types `BOUNCE`,
`COMPLAINT`, `UNSUBSCRIBE`, `MANUAL`.

### Add to Suppression List

```
POST /api/v1/tracking/suppress
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Address to suppress |
| `organization_id` | string | Platform only | Owning organization. A tenant credential always writes to its own org (a supplied value is overwritten, never honoured); a platform credential must name one and gets `422` otherwise |
| `reason` | string | No | Default `manual` |

### Remove from Suppression List

```
DELETE /api/v1/tracking/suppress/{email}
```

Scoped the same way as adding: a tenant credential removes from its own
organization only; a platform credential must name the org with an
`organization_id` query parameter (`422` otherwise).

### List Suppressions

Paginated, filterable view over the suppression list.

```
GET /api/v1/tracking/suppressions
```

**Auth:** requires operator role `support` or higher (operator console session),
which sees every organization and may narrow with `organization_id`.

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Platform scope only -- narrow to one org |
| `suppression_type` | string | -- | `BOUNCE`, `COMPLAINT`, `UNSUBSCRIBE`, `MANUAL` |
| `active` | boolean | -- | Filter on the active flag |
| `q` | string | -- | Substring match on the email address |
| `date_from` / `date_to` | string | -- | ISO date/datetime bounds on `created_at` (a bare `date_to` date means through end of that day) |
| `sort_by` | string | `created_at` | `created_at` or `email` |
| `sort_dir` | string | `desc` | `asc` or `desc` |
| `page` / `per_page` | integer | `1` / `50` | Pagination (max 200) |

Items include `id`, `email`, `organization_id`, `suppression_type`, `reason`,
`source`, `bounce_type`, `bounce_count`, `active`, `expires_at`, `created_at`,
`updated_at`.

### Bulk Suppress

Add up to 1,000 addresses in one call, with a per-address outcome report.

```
POST /api/v1/tracking/suppressions/bulk
```

**Auth:** requires operator role `operator` or higher.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `emails` | array | Yes | Addresses to suppress (max 1000; de-duplicated case-insensitively) |
| `suppression_type` | string | No | Default `MANUAL` |
| `reason` | string | Yes | Stored on every row and shown in the console |
| `organization_id` | string | Yes (in practice) | Owning organization. Operator sessions have no organization of their own, so it must be named -- suppressions belong to exactly one tenant |

Re-suppressing an address already on the list reactivates it and refreshes the
reason rather than erroring. The response reports `suppressed` and `failed` arrays
-- individual failures are never swallowed.

## Analytics Health

```
GET /api/v1/analytics/health
```

Returns `{"status": "healthy", "service": "analytics"}` when the analytics service
is reachable, `503` otherwise.
