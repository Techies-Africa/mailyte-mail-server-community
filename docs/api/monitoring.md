# Monitoring

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Health checks, service status, and system metrics for the Mailyte email server.

## Health Check

Quick health check for the API server and its database connection. This endpoint does not require authentication.

```
GET /health
```

!!! note "No auth required"
    The `/health` endpoint is at the root level, not under `/api/v1/`. It does not require an API key, which makes it suitable for load balancers and uptime monitors.

**Example Request**

```bash
curl http://your-server:5000/health
```

**Example Response**

```json
{
  "status": "healthy",
  "database": "connected",
  "version": "1.0.0"
}
```

When the database is down:

```json
{
  "status": "unhealthy",
  "database": "failed",
  "version": "1.0.0"
}
```

**HTTP status:** `200` when healthy, `503` when unhealthy.

## Monitoring Service Health

Check the health of the internal monitoring service.

```
GET /api/v1/monitoring/health
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/monitoring/health
```

**Example Response**

```json
{
  "status": "healthy",
  "monitoring_service": {
    "uptime_seconds": 86400,
    "services_monitored": 8
  },
  "timestamp": "2025-03-25T10:30:00Z"
}
```

## Service Status

### All Services

Get the status of all monitored services (Postfix, Dovecot, Redis, etc.).

```
GET /api/v1/monitoring/services
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/monitoring/services
```

**Example Response**

```json
{
  "services": {
    "postfix": {"status": "running", "uptime": "5d 12h", "pid": 1234},
    "dovecot": {"status": "running", "uptime": "5d 12h", "pid": 1235},
    "redis": {"status": "running", "uptime": "5d 12h", "pid": 1236},
    "mysql": {"status": "running", "uptime": "5d 12h", "pid": 1237},
    "tracking": {"status": "running", "uptime": "5d 12h", "pid": 1238},
    "analytics": {"status": "running", "uptime": "5d 12h", "pid": 1239},
    "rate_limiter": {"status": "running", "uptime": "5d 12h", "pid": 1240},
    "rag": {"status": "running", "uptime": "5d 12h", "pid": 1241}
  }
}
```

### Single Service

Get the status of a specific service.

```
GET /api/v1/monitoring/services/{service_name}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `service_name` | string | Service name (e.g., `postfix`, `dovecot`, `redis`) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/monitoring/services/postfix
```

## System Metrics

Get system-level metrics (CPU, memory, disk, mail queue size).

```
GET /api/v1/monitoring/metrics
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/monitoring/metrics
```

**Example Response**

```json
{
  "system": {
    "cpu_percent": 23.5,
    "memory_percent": 45.2,
    "disk_percent": 62.1,
    "load_average": [1.2, 0.8, 0.6]
  },
  "mail": {
    "queue_size": 12,
    "deferred_count": 3,
    "active_connections": 45,
    "messages_today": 2500
  }
}
```

## Dashboard Stats

Get statistics formatted for dashboard display.

```
GET /api/v1/monitoring/stats
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/monitoring/stats
```

## Admin Operations

These endpoints require the `X-Admin-Password` (or `X-Admin-Token`) header.

### Restart a Service

Restart a specific service.

```
POST /api/v1/monitoring/services/{service_name}/restart
```

!!! warning "Admin only"
    This endpoint requires the `X-Admin-Password` header. API keys are not sufficient.

**Example Request**

```bash
curl -X POST \
  -H "X-Admin-Password: your-admin-password" \
  http://your-server:5000/api/v1/monitoring/services/postfix/restart
```

**Example Response**

```json
{
  "service": "postfix",
  "action": "restart",
  "status": "success",
  "message": "Service postfix restarted successfully"
}
```

### Auto-Heal

Trigger auto-healing for all services. The system checks each service and restarts any that are not running correctly.

```
POST /api/v1/monitoring/auto-heal
```

**Example Request**

```bash
curl -X POST \
  -H "X-Admin-Password: your-admin-password" \
  http://your-server:5000/api/v1/monitoring/auto-heal
```

**Example Response**

```json
{
  "status": "completed",
  "actions_taken": [
    {"service": "postfix", "action": "none", "reason": "already healthy"},
    {"service": "dovecot", "action": "restarted", "reason": "not responding"}
  ]
}
```

### Test Webhooks

Send a test event to all configured webhook endpoints to verify they are working.

```
POST /api/v1/monitoring/webhooks/test
```

**Example Request**

```bash
curl -X POST \
  -H "X-Admin-Password: your-admin-password" \
  http://your-server:5000/api/v1/monitoring/webhooks/test
```
