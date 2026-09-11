---
title: Performance Metrics
description: Key metrics to monitor and their healthy ranges for each Mailyte service.
---

# Performance Metrics

These are the metrics that matter. Each one has a healthy range and guidance on what to do when it's not healthy.

## Email Flow

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Mail queue depth | < 100 | > 500 | > 5,000 | Check Postfix, increase workers |
| Bounce rate | < 2% | > 3% | > 5% | Clean lists, check blacklists |
| Spam score (avg outbound) | < 3 | > 5 | > 8 | Check content, verify DKIM/SPF |
| Delivery latency (p95) | < 5s | > 15s | > 60s | Check DNS, queue congestion |
| Deferred messages | < 50 | > 200 | > 1,000 | Check remote server status, rate limits |
| Rejection rate (inbound) | < 20% | > 40% | > 60% | Review Rspamd thresholds |

## API

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Response time (p95) | < 500ms | > 1s | > 5s | Check DB queries, add caching |
| Error rate (5xx) | < 0.1% | > 1% | > 5% | Check logs, DB connection |
| Requests per second | < 80% capacity | > 80% | > 95% | Scale API instances |
| Active connections | < 80% of limit | > 90% | > 95% | Increase limits, check for leaks |

## Database (MySQL)

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Connections used | < 70% of max | > 80% | > 95% | Increase max_connections, pool |
| Slow queries/min | 0-2 | > 10 | > 50 | Add indexes, optimize queries |
| InnoDB buffer hit rate | > 99% | < 95% | < 90% | Increase buffer_pool_size |
| Replication lag (if replica) | < 1s | > 5s | > 30s | Check replica load |
| Table lock waits | 0 | > 5/min | > 20/min | Optimize queries, batch deletes |
| Disk usage | < 70% | > 80% | > 90% | Clean old data, expand disk |

## Redis

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Memory usage | < 70% of max | > 80% | > 95% | Increase maxmemory, tune eviction |
| Hit rate | > 90% | < 80% | < 60% | Check eviction policy, increase memory |
| Connected clients | < 500 | > 800 | > 950 | Check for connection leaks |
| Evictions/sec | 0 | > 10 | > 100 | Increase memory |
| Command latency (p99) | < 1ms | > 5ms | > 50ms | Check server load, slow commands |

## System Resources

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| CPU usage | < 70% | > 80% | > 95% | Scale up, optimize services |
| Memory usage | < 80% | > 85% | > 95% | Increase RAM, tune services |
| Disk usage | < 70% | > 80% | > 90% | Clean up, expand storage |
| Disk I/O wait | < 5% | > 10% | > 20% | Faster disk (SSD/NVMe), reduce writes |
| Network throughput | < 70% of link | > 80% | > 95% | Upgrade bandwidth |
| Load average (per core) | < 1.0 | > 2.0 | > 5.0 | Find bottleneck, scale up |

## SSL Certificates

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Days until expiry | > 30 | < 14 | < 3 | Check cert_manager, manual renewal |

## Workers

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Health check status | UP | — | DOWN | Restart, check logs |
| Task processing rate | Stable | Declining | Zero | Check dependencies |
| Error rate | < 1% | > 5% | > 20% | Check logs, fix root cause |
| Queue backlog | < 100 | > 500 | > 5,000 | Increase worker concurrency |

## Deliverability

| Metric | Healthy Range | Warning | Critical | Action |
|--------|--------------|---------|----------|--------|
| Google domain reputation | High | Medium | Low/Bad | Reduce volume, clean lists |
| Blacklist listings | 0 | 1 | > 2 | Investigate, request delisting |
| SPF pass rate | > 99% | < 95% | < 90% | Fix SPF record |
| DKIM pass rate | > 99% | < 95% | < 90% | Check DKIM keys |
| DMARC pass rate | > 95% | < 90% | < 80% | Fix alignment issues |

## How to Check These Metrics

### Quick command-line checks:

```bash
# Queue depth
docker exec -it postfix postqueue -p | tail -1

# MySQL connections
docker exec -it mysql mysql -u root -p"$DB_ROOT_PASSWORD" -e "SHOW STATUS LIKE 'Threads_connected';"

# Redis memory
docker exec -it redis redis-cli info memory | grep used_memory_human

# Disk usage
df -h /

# System load
uptime
```

### Via Prometheus (if monitoring is set up):

Metric names are prefixed with the emitting service (`api_`, `monitoring_`, `archiver_`, …) — see [Prometheus Metrics](prometheus-metrics.md) for the full list. There is no Postfix exporter deployed, so queue depth and bounce rate come from `postqueue` and the `mail_logs` table rather than Prometheus.

```promql
# API latency p95 (summary quantile — durations are summaries, not histograms)
api_http_request_duration_seconds{quantile="0.95"}

# API error ratio
rate(api_http_errors_total[5m]) / rate(api_http_requests_total[5m])

# Backup freshness (hours since last full backup)
monitoring_backup_age_seconds{backup_type="full"} / 3600

# MySQL connections used %
mysql_global_status_threads_connected / mysql_global_variables_max_connections
```

Bounce rate from the database instead:

```sql
SELECT SUM(status='bounced') / COUNT(*) AS bounce_rate
FROM mail_logs
WHERE timestamp > NOW() - INTERVAL 1 DAY;
```
