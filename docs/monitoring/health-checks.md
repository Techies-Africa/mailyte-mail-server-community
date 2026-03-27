# Health Checks

The health monitor on `:8080` is your single source of truth for whether Mailyte is alive and well.

## What It Does

The health monitor is a lightweight service that continuously probes every component. It exposes a simple HTTP API you can hit from load balancers, uptime checkers, or your own scripts.

It checks seven services every 30 seconds:

| Service | Check Method | What Passes |
|---------|-------------|-------------|
| Postfix | SMTP connection on port 25 | Responds with 220 banner |
| Dovecot | IMAP connection on port 143 | Responds with OK greeting |
| Rspamd | HTTP GET to `:11334/ping` | Returns `pong` |
| MySQL | TCP connection + `SELECT 1` | Query returns successfully |
| Redis | `PING` command | Returns `PONG` |
| FastAPI | HTTP GET to `:5000/health` | Returns 200 with `{"status": "ok"}` |
| Workers | Heartbeat timestamp check | Last heartbeat within 60 seconds |

## Endpoints

### `GET /health`

The main endpoint. Returns the status of everything.

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
```

**Response when healthy:**

```json
{
  "status": "healthy",
  "timestamp": "2026-03-25T10:30:00Z",
  "uptime_seconds": 86400,
  "services": {
    "postfix": {
      "status": "healthy",
      "response_time_ms": 12,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "SMTP banner received"
    },
    "dovecot": {
      "status": "healthy",
      "response_time_ms": 8,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "IMAP greeting received"
    },
    "rspamd": {
      "status": "healthy",
      "response_time_ms": 5,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "pong"
    },
    "mysql": {
      "status": "healthy",
      "response_time_ms": 3,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "Query OK"
    },
    "redis": {
      "status": "healthy",
      "response_time_ms": 1,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "PONG"
    },
    "api": {
      "status": "healthy",
      "response_time_ms": 25,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "HTTP 200"
    },
    "workers": {
      "status": "healthy",
      "response_time_ms": 2,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "Last heartbeat 15s ago"
    }
  }
}
```

**Response when something is broken:**

```json
{
  "status": "degraded",
  "timestamp": "2026-03-25T10:30:00Z",
  "uptime_seconds": 86400,
  "services": {
    "postfix": {
      "status": "unhealthy",
      "response_time_ms": 5000,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "Connection refused on port 25",
      "consecutive_failures": 3
    },
    "dovecot": {
      "status": "healthy",
      "response_time_ms": 8,
      "last_check": "2026-03-25T10:29:55Z",
      "details": "IMAP greeting received"
    }
  }
}
```

**HTTP status codes:**

| Code | Meaning |
|------|---------|
| `200` | All services healthy |
| `207` | Some services degraded |
| `503` | Critical services down |

### `GET /health/{service}`

Check a single service:

```bash
curl -s http://localhost:8080/health/postfix | python3 -m json.tool
```

```json
{
  "service": "postfix",
  "status": "healthy",
  "response_time_ms": 12,
  "last_check": "2026-03-25T10:29:55Z",
  "details": "SMTP banner received",
  "uptime_percentage": 99.97
}
```

### `GET /health/summary`

A stripped-down version for dashboards and status pages:

```bash
curl -s http://localhost:8080/health/summary
```

```json
{
  "status": "healthy",
  "healthy_count": 7,
  "unhealthy_count": 0,
  "services": {
    "postfix": "healthy",
    "dovecot": "healthy",
    "rspamd": "healthy",
    "mysql": "healthy",
    "redis": "healthy",
    "api": "healthy",
    "workers": "healthy"
  }
}
```

### `GET /metrics`

Prometheus-formatted metrics from the health monitor itself:

```
mailyte_service_up{service="postfix"} 1
mailyte_service_up{service="dovecot"} 1
mailyte_service_up{service="rspamd"} 1
mailyte_service_up{service="mysql"} 1
mailyte_service_up{service="redis"} 1
mailyte_service_up{service="api"} 1
mailyte_service_up{service="workers"} 1
mailyte_service_response_time_ms{service="postfix"} 12
mailyte_service_response_time_ms{service="dovecot"} 8
mailyte_health_check_duration_seconds 0.045
mailyte_health_checks_total 2880
```

## Using Health Checks

### Load Balancer Integration

Point your load balancer at `/health`:

```nginx
# nginx upstream health check
upstream mailyte_api {
    server api:5000;
    health_check uri=/health interval=10s fails=3 passes=2;
}
```

### Uptime Monitoring

Use with external services like UptimeRobot or Pingdom:

- **URL:** `https://mail.yourdomain.com:8080/health/summary`
- **Expected status:** `200`
- **Expected body contains:** `"status": "healthy"`
- **Check interval:** 60 seconds

### Scripted Checks

```bash
#!/bin/bash
# Quick health check script

STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health)

if [ "$STATUS" -eq 200 ]; then
    echo "All services healthy"
    exit 0
elif [ "$STATUS" -eq 207 ]; then
    echo "Some services degraded — check dashboard"
    exit 1
else
    echo "CRITICAL: Services are down!"
    curl -s http://localhost:8080/health | python3 -m json.tool
    exit 2
fi
```

## Configuration

The health monitor is configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `HEALTH_CHECK_INTERVAL` | `30` | Seconds between checks |
| `HEALTH_CHECK_TIMEOUT` | `5` | Seconds before a check times out |
| `UNHEALTHY_THRESHOLD` | `3` | Consecutive failures before marking unhealthy |
| `RECOVERY_THRESHOLD` | `2` | Consecutive passes before marking healthy again |
| `ENABLE_AUTO_HEALING` | `true` | Restart failed containers automatically |

> **Tip:** The `UNHEALTHY_THRESHOLD` prevents a single network blip from triggering alerts. A service has to fail three times in a row (90 seconds at default interval) before it's marked as unhealthy.
