# Monitoring

Health checks, service status, and system metrics for the Mailyte email server. The
`/api/v1/monitoring/*` routes proxy to the internal monitoring service.

!!! info "Platform-only surface"
    Every `/api/v1/monitoring/*` endpoint is **platform-scoped** -- tenant
    credentials cannot reach any of them. The read endpoints require operator role
    `support` or higher; the three destructive POSTs require role `operator` plus
    the admin permission level, and additionally forward an `X-Admin-Token` header
    to the internal monitoring service. In practice these routes are called from an
    operator console session.

## Health Check (public)

Quick health check for the API server and its database connection.

```
GET /health
```

!!! note "No auth required"
    The `/health` endpoint is at the root level, not under `/api/v1/`. It does not
    require any credential, which makes it suitable for load balancers and uptime
    monitors. It is a different route from `GET /api/v1/monitoring/health` below,
    which returns internal component status and is guarded like every other
    monitoring route.

**Example Request**

```bash
curl http://your-server:8083/health
```

**Example Response**

```json
{
  "status": "healthy",
  "database": "connected",
  "version": "1.0.0"
}
```

When the database is down, `database` reads `"failed"` (the endpoint still returns
`200` with `status: "healthy"` in that case; `503` is returned only if the handler
itself fails).

## Prometheus Metrics (public)

```
GET /metrics
```

Root-level Prometheus text-format metrics for the API process (request counts,
latencies, status codes).

## Monitoring Service Health

Overall health of all email server components as reported by the monitoring
service.

```
GET /api/v1/monitoring/health
```

**Auth:** platform scope, role `support`+.

**Example Response**

```json
{
  "status": "healthy",
  "monitoring_service": { ... },
  "timestamp": "2026-08-30T10:30:00Z"
}
```

Returns `503` with `status: "unhealthy"` when the monitoring service answers with a
non-200, and `500` with `status: "error"` when it is unreachable.

## Service Status

### All Services

Get the status of all monitored services (Postfix, Dovecot, Rspamd, MySQL, Redis,
and the worker services).

```
GET /api/v1/monitoring/services
```

**Auth:** platform scope, role `support`+.

The response is the monitoring service's heartbeat payload, passed through
verbatim.

### Single Service

```
GET /api/v1/monitoring/services/{service_name}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `service_name` | string | Service name (e.g., `postfix`, `dovecot`, `redis`) |

## System Metrics

System and mail-server metrics (CPU, memory, disk, throughput, queue depths),
passed through from the monitoring service.

```
GET /api/v1/monitoring/metrics
```

**Auth:** platform scope, role `support`+.

## Dashboard Stats

Pre-aggregated statistics for monitoring dashboards.

```
GET /api/v1/monitoring/stats
```

**Auth:** platform scope, role `support`+.

## Admin Operations

The three POST endpoints below require:

1. A platform-scoped credential with role `operator` or higher (an operator
   session) and admin permission, **and**
2. An `X-Admin-Token` header, which the gateway forwards to the internal monitoring
   service for its own check. A missing or invalid token returns `401`
   (`"Admin token required..."` / `"Invalid admin token"`).

### Restart a Service

```
POST /api/v1/monitoring/services/{service_name}/restart
```

**Request Body** (optional)

| Field | Type | Default | Description |
|---|---|---|---|
| `force` | boolean | `false` | Force-kill before restarting instead of a graceful restart |
| `reason` | string | -- | Recorded in the audit log |

**Example Request**

```bash
curl -X POST -b operator-cookies.txt \
  -H "X-CSRF-Token: $CSRF" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "config change"}' \
  http://your-server:8083/api/v1/monitoring/services/postfix/restart
```

The monitoring service's restart result is passed through verbatim.

### Auto-Heal

Run the auto-healing procedure across all (or selected) services: detect unhealthy
components and attempt automatic recovery.

```
POST /api/v1/monitoring/auto-heal
```

**Request Body** (optional)

| Field | Type | Default | Description |
|---|---|---|---|
| `services` | array | -- | Limit auto-heal to these service names; omit to heal all unhealthy services |
| `dry_run` | boolean | `false` | Report what would be healed without acting |

### Test Webhooks

Send a test payload to the configured webhook endpoints via the monitoring service.

```
POST /api/v1/monitoring/webhooks/test
```

**Request Body** (optional)

| Field | Type | Default | Description |
|---|---|---|---|
| `webhook_urls` | array | -- | Specific URLs to test; omit to test all configured webhooks |
| `payload_type` | string | `ping` | `ping`, `alert`, or `recovery` |
