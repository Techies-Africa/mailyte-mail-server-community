# Service Monitoring

How to tell if each Mailyte service is healthy — quick checks you can run right now.

## At a Glance

```bash
# One-liner: check all services
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
```

If everything says "Up" and "healthy," you're good. If not, read on.

```bash
# Or ask the monitoring service, which runs real protocol probes
curl -s http://localhost:8085/heartbeat | python3 -m json.tool
```

## Postfix (SMTP)

Postfix handles all email delivery. If it's down, no email goes in or out.

**Quick check:**

```bash
# Test SMTP connection
echo "EHLO test" | nc -w 3 localhost 25

# Check the mail queue
docker compose exec postfix postqueue -p

# Count queued messages
docker compose exec postfix postqueue -p | tail -1
```

**What healthy looks like:**

```
220 mail.yourdomain.com ESMTP
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Queue size | < 100 | > 500 |
| Delivery delay | < 30s | > 5min |
| Bounce rate | < 3% | > 5% |

**Common problems:**

- Queue growing: check DNS resolution, recipient server availability
- High bounces: check sender reputation, SPF/DKIM records
- Connection refused: Postfix crashed — check logs
- Queue empty but nothing arriving: check `transport_maps` — a stale transport cutover file loop-bounced all inbound for 13 domains in production (fixed 2026-08-27); "healthy" containers do not prove mail is routing

```bash
docker compose logs --tail=50 postfix
```

Per-message history (accepted, delivered, bounced, deferred) lives in the `mail_logs` and `delivery_events` tables, produced by the `log_ingestor` service since 2026-08-22, and is queryable via `/api/v1/analytics` and the message-trace endpoints.

## Dovecot (IMAP/POP3)

Dovecot handles mailbox access. If it's down, users can't read email.

**Quick check:**

```bash
# Test IMAP greeting
nc -w 3 localhost 143 < /dev/null

# Check active connections
docker compose exec dovecot doveadm who

# Verify user mailbox
docker compose exec dovecot doveadm mailbox list -u user@domain.com
```

**What healthy looks like:**

```
* OK [CAPABILITY IMAP4rev1 ...] Dovecot ready.
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Active connections | Stable | Sudden drop or spike |
| Auth failures (`failed_auth_attempts` table) | < 10/min | > 50/min (possible brute force) |
| Mailbox size | Within quota | Near quota limit |

!!! note "Auth cache"
    Dovecot caches successful auth for up to 1 hour (`auth_cache_ttl`). A disabled/suspended mailbox or a changed password keeps authenticating from cache until the TTL expires or you flush it: `docker compose exec dovecot doveadm auth cache flush <user@domain>`. SMTP-credential mutations flush this automatically via the doveadm HTTP API; mailbox-level changes currently do not.

## Rspamd (Spam Filter)

**Quick check:**

```bash
# Ping Rspamd (no credentials needed)
curl http://localhost:11334/ping
```

**What healthy looks like:** `pong`

!!! warning "/stat requires the controller password"
    `GET /stat` answers `403` without the controller password — a healthy Rspamd looks broken if you probe `/stat` unauthenticated. This exact mistake once made the monitoring service report Rspamd as permanently degraded and repeatedly auto-restart a healthy container; its probe now uses `/ping`. This build's controller serves no `/metrics` either (verified 2026-08-31) — Prometheus's rspamd scrape job is commented out, and there are no `rspamd_*` series.

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Scan time | < 500ms | > 2s |
| Reject rate | 10-40% of scanned | > 60% or < 5% |
| Memory usage | < 500MB | > 1GB |

## MySQL

MySQL stores organizations, domains, mailboxes, credentials, logs, and configuration. If it's down, the API can't function.

**Quick check:**

```bash
docker compose exec mysql mysqladmin -u root -p ping
docker compose exec mysql mysqladmin -u root -p status
docker compose exec mysql mysql -u root -p -e "SHOW PROCESSLIST;"
```

**Key things to watch (via `mysql-exporter` on :9104):**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| `mysql_global_status_threads_connected / mysql_global_variables_max_connections` | < 80% | > 80% (the `DatabaseConnectionPoolExhausted` alert) |
| `rate(mysql_global_status_slow_queries[5m])` | ~0 | > 10/s (the `SlowQueries` alert) |

## Redis

Redis handles caching, rate-limit counters, auth-policy state, and geo/DLP policy caches.

**Quick check:**

```bash
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli info memory | grep used_memory_human
docker compose exec redis redis-cli info clients | grep connected_clients
```

**Key things to watch (via `redis-exporter` on :9121):**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| `redis_memory_used_bytes / redis_memory_max_bytes` | < 80% | > 80% (the `RedisMemoryHigh` alert) |
| Connected clients | Stable | Climbing fast |
| Hit rate | > 90% | < 80% |

## API Gateway

The FastAPI gateway listens on **8080 inside the container** (host-mapped to **8083** in dev; in production it has no host port and is reached through Traefik on 443).

**Quick check:**

```bash
# From the host (dev)
curl http://localhost:8083/health

# Check response time
curl -w "Total time: %{time_total}s\n" -o /dev/null -s http://localhost:8083/health
```

**Key things to watch (Prometheus, `api_*` series):**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| `api_http_request_duration_seconds{quantile="0.95"}` | < 0.5s | > 2s |
| Error ratio (`api_http_errors_total` / `api_http_requests_total`) | < 1% | > 5% |

!!! warning "Blocking endpoints"
    Many API routes are declared `async def` but perform blocking database I/O — one slow query can stall the whole event loop and make *every* endpoint slow simultaneously. If the API goes globally slow, check the currently running MySQL queries before blaming load.

## Worker Services

Each worker (tracking, webhooks, rate_limiter, archiver, queue_manager, rag, …) exposes `/health` and `/metrics` on its own container port — the compose file maps them to host ports 8081-8104 in dev, all loopback-only in production.

**Quick check:**

```bash
# Any worker, by container name and internal port
docker compose exec tracking curl -s http://localhost:8086/health

# Postfix queue via the queue_manager (through the API gateway)
curl -s -H "X-API-Key: $KEY" https://<api-host>/api/v1/queue/queue/status
```

## Health Check Script

Save this as `check-all.sh` for a quick full-system check (dev host ports):

```bash
#!/bin/bash

echo "=== Mailyte Service Status ==="
echo ""

services=("postfix:25" "dovecot:143" "rspamd:11334" "mysql:3306" "redis:6379" "api:8083" "monitoring:8085")

for svc in "${services[@]}"; do
    name="${svc%%:*}"
    port="${svc##*:}"
    if nc -z -w 2 localhost "$port" 2>/dev/null; then
        echo "  [OK]   $name (:$port)"
    else
        echo "  [FAIL] $name (:$port)"
    fi
done

echo ""
echo "=== Docker Containers ==="
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo ""
echo "=== Protocol-level verdicts ==="
curl -s http://localhost:8085/heartbeat | python3 -c \
  "import json,sys; d=json.load(sys.stdin); [print(f\"  {k}: {v['status']}\") for k,v in d.get('services',{}).items()]"
```

In production, note that mysql/redis have no host ports at all — rely on `docker compose ps` and the monitoring service instead of raw `nc`.
