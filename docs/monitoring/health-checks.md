# Health Checks

The monitoring service on `:8085` is your single source of truth for whether Mailyte is alive and well.

## What It Does

The monitoring service (`worker/monitoring/app.py`, container name `monitoring`) probes the mail stack with real protocol checks — an SMTP banner, an IMAP greeting, an HTTP `/health` call — not just "is the port open". It exposes an HTTP API you can hit from uptime checkers or your own scripts.

It runs three background jobs:

| Job | Cadence | What it does |
|-----|---------|-------------|
| Comprehensive monitoring | every 5 minutes | Full service sweep, enterprise metrics collection, auto-heal of down critical services, webhook summary |
| Resource monitoring | every 30 seconds | CPU / memory / disk via psutil |
| Backup freshness | every minute | Publishes `monitoring_backup_age_seconds` from the `backup_history` table |

## What It Checks

Configured in `worker/monitoring/services/service_monitor.py`:

| Service | Check Method | Critical (auto-healed)? |
|---------|-------------|------------------------|
| postfix | SMTP connection on port 25 | yes |
| dovecot | IMAP connection on port 143 | yes |
| rspamd | HTTP GET `rspamd:11334/ping` | yes |
| api | HTTP GET `api:8080/health` | yes |
| tracking | HTTP GET `tracking:8086/health` | no |
| rate_limiter | HTTP GET `rate_limiter:8082/health` | no |
| webhooks | HTTP GET `webhooks:8081/webhook/status` | no |
| cert_manager | Container status via Docker API | no (deliberately — its own supervisord + `restart: always` cover crashes) |
| rag | HTTP GET `rag:8090/health` | no |

MySQL and Redis are not on this list — their liveness is covered by compose healthchecks and the `mysql-exporter` / `redis-exporter` `up` series in Prometheus.

## Endpoints

### `GET /health`

Liveness of the monitoring service itself (this is also what the container healthcheck uses):

```bash
curl -s http://localhost:8085/health
```

```json
{
  "status": "healthy",
  "service": "unified-monitor",
  "timestamp": "2026-08-30T10:30:00Z"
}
```

### `GET /heartbeat`

The main endpoint — probes every configured service and returns the full map:

```bash
curl -s http://localhost:8085/heartbeat | python3 -m json.tool
```

```json
{
  "overall_status": "healthy",
  "services": {
    "postfix": {
      "status": "up",
      "protocol": "smtp",
      "critical": true,
      "response_time": 0.012,
      "check_duration": 0.014,
      "timestamp": "2026-08-30T10:30:00"
    },
    "...": "..."
  },
  "critical_services_down": [],
  "services_with_warnings": [],
  "total_services": 9,
  "healthy_services": 9,
  "timestamp": "2026-08-30T10:30:00"
}
```

`overall_status` is `healthy`, `degraded` (warnings), or `critical` (a critical service is down). A webhook health summary is dispatched as a background task after the response is sent, so a slow webhook endpoint cannot stall the check.

Check a single service with a query parameter:

```bash
curl -s 'http://localhost:8085/heartbeat?service=postfix' | python3 -m json.tool
```

An unknown service name returns `404`.

### Other endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /` | none | HTML monitoring dashboard |
| `GET /api/stats` | none | System + mail stats JSON |
| `GET /api/metrics` | none | Full [enterprise metrics](enterprise-metrics.md) payload |
| `GET /metrics` | none | This service's own Prometheus metrics |
| `GET /metrics/prometheus` | none | Aggregated metrics pulled from all services |
| `POST /restart/{service}` | `X-Admin-Token` | Manual container restart |
| `POST /auto-heal` | `X-Admin-Token` | Heal every unhealthy service |
| `POST /test/webhooks` | `X-Admin-Token` | Fire a test event at every configured webhook |
| `GET /admin/services/status` | `X-Admin-Token` | Detailed status + system stats |
| `GET /admin/token` | `X-Admin-Password` header (= `ADMIN_PASSWORD` env) | Fetch the current admin token |
| `POST /admin/regenerate-token` | `X-Admin-Token` | Rotate the admin token |

The admin token is an HMAC-signed value generated at process start (signed with `WEBHOOK_SECRET`, valid 24 hours) — fetch it with the admin password, then use it in the `X-Admin-Token` header for the destructive endpoints.

The same operations are also exposed through the API gateway at `/api/v1/monitoring/*` — there they additionally require a **platform-scope** API credential (`support` role for reads, `operator` for restart/auto-heal/test-webhooks) on top of the forwarded `X-Admin-Token`.

## Using Health Checks

### Uptime Monitoring

Point an external checker at the mail-stack heartbeat **through an authenticated path** — in production `:8085` is bound to `127.0.0.1` and is not directly reachable:

- **Internal:** `http://localhost:8085/heartbeat` (from the host, via SSH)
- **External:** `https://<your-api-host>/api/v1/monitoring/health` with a platform API key

### Scripted Checks

```bash
#!/bin/bash
# Quick health check script (run on the host)

STATUS=$(curl -s http://localhost:8085/heartbeat | python3 -c \
  "import json,sys; print(json.load(sys.stdin).get('overall_status','error'))")

case "$STATUS" in
  healthy)  echo "All services healthy"; exit 0 ;;
  degraded) echo "Some services degraded — check the dashboard"; exit 1 ;;
  *)        echo "CRITICAL: services are down"; exit 2 ;;
esac
```

### Docker healthchecks

Independently of the monitoring service, nearly every container in `docker-compose.yml` has its own `healthcheck:` (TCP connect or HTTP probe). `docker compose ps` shows those verdicts, and Traefik only routes to containers Docker considers healthy.

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```

## Configuration

| Variable | Used for |
|----------|----------|
| `ADMIN_PASSWORD` | Gate on `GET /admin/token` |
| `WEBHOOK_SECRET` | Signs the admin token and webhook payloads |
| `WEBHOOK_URLS` / `HEALTH_WEBHOOK_URLS` | Fallback webhook targets when the `webhook_urls` table is unavailable |
| `DOCKER_HOST=tcp://docker-proxy:2375` | Routes restart operations through the scoped Docker socket proxy — see [Auto-Healing](auto-healing.md) |

Check cadence and restart thresholds (3 attempts, 300 s cooldown) are constants in `service_monitor.py` / `app.py`, not environment variables.
