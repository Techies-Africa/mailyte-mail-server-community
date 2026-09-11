---
edition: enterprise
---

# Reputation

Sending-reputation reads for the operations console: per-domain reputation with trends, per-IP reputation, ISP feedback-loop complaints, and a summary aggregate. All endpoints are read-only.

**Base path:** `/api/v1/reputation`
**Auth:** platform scope, operator session at role `support` or above (every endpoint uses `require_scope("platform", "read", role="support")`). Responses use the standard `{"type", "msg", "data"}` envelope.

## Domain Reputation List

Current reputation per sending domain — the newest `domain_reputation` period row for each domain — with `trend` computed against that domain's previous period.

```
GET /api/v1/reputation/domains
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Narrow to one organization |
| `period_type` | string | daily | Period granularity |
| `q` | string | -- | Free text over the domain name |
| `max_score` | integer | -- | Only domains scoring at or below this (0–100) |
| `sort_by` | string | `overall_score` | Sort column |
| `sort_dir` | string | `asc` | `asc` \| `desc` (ascending = worst first) |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reputation/domains?max_score=70&sort_dir=asc"
```

!!! info "`trend` is null, never 0, for a first period"
    A domain with no earlier period gets `trend: null` — the console must be able to distinguish "unchanged" from "nothing to compare".

## One Domain's Reputation

Current reputation, the score history behind the trend, and the ISP feedback-loop complaints filed against mail this domain **sent**.

```
GET /api/v1/reputation/domains/{domain}
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period_type` | string | daily | Period granularity |
| `history_limit` | integer | `30` | Periods of score history (1–365) |
| `complaint_limit` | integer | `25` | Recent complaints to include (1–200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reputation/domains/acme.com?history_limit=60"
```

!!! info "Complaints are keyed by sender"
    The feedback-loop table has no domain column: a domain's complaints are those whose **sender** is in the domain — an FBL report is a complaint about mail we sent. Message content (`raw_feedback`, `headers`) is never returned (ADR-002 §5).

## IP Reputation List

Per-IP reputation with the counters behind the score.

```
GET /api/v1/reputation/ips
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | -- | Free text over the IP address |
| `is_blocked` | boolean | -- | Filter on block state |
| `max_score` / `min_score` | float | -- | Score bounds |
| `sort_by` | string | `reputation_score` | Sort column |
| `sort_dir` | string | `asc` | `asc` \| `desc` (ascending = worst first) |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reputation/ips?is_blocked=true"
```

!!! info "Platform-wide by construction"
    `ip_reputation` carries no `organization_id` — an IP's behaviour belongs to the host, not to a tenant — so this list is platform-wide and organization filtering does not apply. The default sort is ascending score (worst first), because that is the row an operator opened this screen to find.

## ISP Feedback-Loop Complaints

Paginated complaint feed from ISP feedback loops.

```
GET /api/v1/reputation/feedback-loops
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Narrow to one organization |
| `domain` | string | -- | Sending domain (matches the sender's domain) |
| `provider` | string | -- | ISP name, e.g. `gmail`, `outlook`, `yahoo` |
| `feedback_type` | string | -- | e.g. `abuse` |
| `processed` | boolean | -- | Filter on processed flag |
| `date_from` / `date_to` | string | -- | ISO date or datetime range |
| `q` | string | -- | Free text over sender |
| `sort_by` | string | `created_at` | Sort column |
| `sort_dir` | string | `desc` | `asc` \| `desc` |
| `page` / `per_page` | integer | `1` / `50` | Pagination |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reputation/feedback-loops?provider=gmail&processed=false"
```

Message content (`raw_feedback`, `headers`) is never selected.

## Reputation Summary

The abuse screen's landing aggregate: worst-scoring sending domains and IPs, a complaint-rate trend, and the suppression-list totals.

```
GET /api/v1/reputation/summary
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Narrow to one organization |
| `days` | integer | `30` | Length of the trend window (1–365) |
| `period_type` | string | daily | Period granularity |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reputation/summary?days=30"
```

!!! info "The trend carries both count and volume"
    The complaint trend includes BOTH the raw ISP complaint count (`feedback_loops`) and the delivered/complained volume the rate is computed from (`domain_reputation`) — a complaint count rising while volume rises faster is not the same incident as a complaint count rising on flat volume.
