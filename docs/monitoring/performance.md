# Performance Monitoring

Track how fast things run — response times, throughput, queue depths, and database performance.

## Why Performance Monitoring

A service can be "healthy" and still be slow. Performance monitoring catches the gray area between "working fine" and "completely broken" — the zone where users start complaining.

## API Response Times

Every FastAPI worker records request duration through the shared collector (`shared/metrics.py`). Durations are exposed as **summaries with pre-computed quantiles**, not Prometheus histograms — so query the `quantile` label directly; `histogram_quantile(...)` over `_bucket` series will return nothing.

```promql
# Median response time (p50)
api_http_request_duration_seconds{quantile="0.5"}

# 95th percentile — most users' experience
api_http_request_duration_seconds{quantile="0.95"}

# 99th percentile — worst case
api_http_request_duration_seconds{quantile="0.99"}

# Average response time
rate(api_http_request_duration_seconds_sum[5m])
/ rate(api_http_request_duration_seconds_count[5m])
```

The same pattern works for any worker: replace the `api_` prefix (`tracking_`, `webhooks_`, `archiver_`, …).

**Target response times:**

| Endpoint Type | p50 Target | p95 Target | p99 Target |
|--------------|-----------|-----------|-----------|
| Health checks | < 10ms | < 50ms | < 100ms |
| Read operations | < 50ms | < 200ms | < 500ms |
| Write operations | < 100ms | < 500ms | < 1s |

!!! warning "One slow endpoint can slow all of them"
    Many API routes are `async def` but do blocking database I/O. When one request blocks the event loop, *every* concurrent request on that worker stalls — a uniform latency spike across unrelated endpoints usually means one blocking call, not general load.

## Throughput

```promql
# API requests per second
sum(rate(api_http_requests_total[5m]))

# Requests per second by path
sum by (path) (rate(api_http_requests_total[5m]))

# Outbound webhook deliveries per minute
sum(rate(webhooks_webhook_deliveries_total[5m])) * 60
```

Email throughput (messages accepted/delivered per hour) is not in Prometheus — no Postfix exporter is deployed. It lives in the `mail_logs` / `delivery_events` tables (produced by `log_ingestor` since 2026-08-22) and is served by `/api/v1/analytics`.

## Queue Depths

Queues are the early warning system. Growing queues mean something downstream is slower than upstream.

**Postfix queue** — via `postqueue` (directly or through the queue_manager service):

```bash
# Direct
docker compose exec postfix postqueue -p | tail -1

# Through the API gateway (proxies to queue_manager)
curl -s -H "X-API-Key: $KEY" https://<api-host>/api/v1/queue/queue/status
curl -s -H "X-API-Key: $KEY" https://<api-host>/api/v1/queue/queue/deferred
```

**Archive spool** — the one queue that *is* in Prometheus:

```promql
# Archived messages waiting for S3 (alerts at >0 for 30m, critical >5000)
archiver_archive_spool_depth
```

**Queue health indicators:**

| Queue | Healthy | Warning | Critical |
|-------|---------|---------|----------|
| Postfix active | < 50 | 50-200 | > 200 |
| Postfix deferred | < 100 | 100-500 | > 500 |
| Archive spool | 0 | > 0 for 30 min | > 5000 |

## Database Query Performance

Slow database queries ripple through everything. The `mysql-exporter` (:9104) feeds these:

```promql
# Slow queries per second (SlowQueries alert fires above 10/s)
rate(mysql_global_status_slow_queries[5m])

# Query rate
rate(mysql_global_status_questions[5m])

# Connections as a fraction of max (alert above 0.8)
mysql_global_status_threads_connected / mysql_global_variables_max_connections
```

Application-side timings are in each worker's `<service>_database_operation_duration_seconds{quantile}` summaries and `<service>_database_operations_total{operation,status}` counters.

### Finding Slow Queries

```bash
# Enable slow query log in MySQL
docker compose exec mysql mysql -u root -p -e "
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;
SET GLOBAL log_queries_not_using_indexes = 'ON';
"

# View currently running queries
docker compose exec mysql mysql -u root -p -e "SHOW FULL PROCESSLIST;"
```

## Redis Performance

Via `redis-exporter` (:9121):

```promql
# Operations per second
rate(redis_commands_processed_total[5m])

# Cache hit rate
rate(redis_keyspace_hits_total[5m])
/ (rate(redis_keyspace_hits_total[5m])
   + rate(redis_keyspace_misses_total[5m]))

# Memory pressure (RedisMemoryHigh alert fires above 0.8)
redis_memory_used_bytes / redis_memory_max_bytes
```

**Redis health targets:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Hit rate | > 90% | < 80% |
| Ops/sec | Stable | Erratic |
| Connected clients | Stable | Climbing |

## Email Delivery Performance

Delivery latency and outcome distribution come from the database, not Prometheus:

```sql
-- Delivery outcomes in the last hour
SELECT event_type, COUNT(*)
FROM delivery_events
WHERE created_at >= NOW() - INTERVAL 1 HOUR
GROUP BY event_type;
```

Or through `/api/v1/analytics` and the message-trace endpoints.

## Performance Alerts

The shipped alert rules that actually watch performance (see [Alerting](alerting.md) for the full honest list):

| Alert | Watches |
|-------|---------|
| `SlowQueries` | MySQL slow-query rate |
| `DatabaseConnectionPoolExhausted` | MySQL connection headroom |
| `RedisMemoryHigh` | Redis memory |
| `ArchiveSpoolNotDraining` / `ArchiveSpoolBacklogCritical` | Archive backlog |

To alert on API latency, add a rule against the summary quantile:

```yaml
- alert: APISlowResponse
  expr: api_http_request_duration_seconds{quantile="0.95"} > 2
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "API p95 response time above 2 seconds"
```

## Quick Performance Check

Run this when things feel slow (dev host ports):

```bash
#!/bin/bash
echo "=== Mailyte Performance Check ==="

echo -e "\n--- API Response Time ---"
curl -s -w "DNS: %{time_namelookup}s | Connect: %{time_connect}s | Total: %{time_total}s\n" \
  -o /dev/null http://localhost:8083/health

echo -e "\n--- Mail Queue ---"
docker compose exec -T postfix postqueue -p | tail -1

echo -e "\n--- MySQL ---"
docker compose exec -T mysql mysqladmin -u root -p"$DB_ROOT_PASSWORD" status 2>/dev/null

echo -e "\n--- Redis ---"
docker compose exec -T redis redis-cli info stats | grep instantaneous_ops_per_sec
```
