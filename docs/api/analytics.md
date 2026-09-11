# Analytics

Domain-level analytics routes, proxied through the API gateway to the internal analytics service (`http://analytics:8085`).

**Base path:** `/api/v1/analytics`
**Auth:** `X-API-Key` (read; `write` for creating scheduled reports) or a dashboard session. Domain-scoped routes verify the caller owns the domain before proxying — a domain belonging to another organization answers `404`.

!!! warning "Most of this surface has no backend implementation"
    The real analytics worker is a small, non-org-aware service exposing `/health`, `/stats`, `/user-activity/{email}`, and an HTML `/dashboard` over the global `mail_logs` table. It has **no** domain-scoped dashboard/email-volume/engagement/deliverability endpoints and **no** report generation or scheduling system. Of the ten gateway routes below, only **Health** reaches a real upstream endpoint today; the other nine proxy to paths that do not exist upstream and come back as `404` from the (reachable) service. They are documented here because they are part of the gateway's registered surface; treat them as reserved until the analytics service grows the matching endpoints.

All routes forward the caller's query string to the upstream service and return its response verbatim (`503` with `{"error": "Analytics service unavailable"}` when the service is unreachable).

## Domain Analytics Dashboard

Aggregate email metrics for a domain: total sent, delivered, bounced, opened, clicked over the selected period.

```
GET /api/v1/analytics/dashboard/{domain}
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/analytics/dashboard/acme.com
```

## Email Volume

Email volume trends broken down by time interval — inbound and outbound counts.

```
GET /api/v1/analytics/email-volume/{domain}
```

## Engagement Metrics

Open rates, click-through rates, reply rates, and read times.

```
GET /api/v1/analytics/engagement/{domain}
```

## Deliverability Metrics

Bounce rates, spam-complaint rates, SPF/DKIM/DMARC pass rates, inbox placement estimates.

```
GET /api/v1/analytics/deliverability/{domain}
```

## Generate Report

Generate an analytics report asynchronously; retrieve it later by ID.

```
POST /api/v1/analytics/reports/generate
```

The JSON request body is forwarded to the upstream service unchanged.

## List Scheduled Reports

```
GET /api/v1/analytics/reports/scheduled
```

Declared before `GET /reports/{report_id}` on purpose — FastAPI matches in declaration order, and the generic `{report_id}` route would otherwise capture the literal path segment `scheduled`.

## Create Scheduled Report

```
POST /api/v1/analytics/reports/scheduled
```

**Auth:** `X-API-Key` with **write** permission. Body forwarded unchanged.

## Get Report by ID

```
GET /api/v1/analytics/reports/{report_id}
```

## Domain Metrics

Message counts, storage usage, active users, and performance indicators for a domain over the requested window.

```
GET /api/v1/analytics/metrics/{domain}
```

## Analytics Service Health

Health of the analytics service — the one route with a live upstream implementation.

```
GET /api/v1/analytics/health
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/analytics/health
```
