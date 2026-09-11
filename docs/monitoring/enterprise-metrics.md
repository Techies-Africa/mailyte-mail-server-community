# Enterprise Metrics

Business-level numbers that matter to your organization — not just whether servers are up, but how they're being used.

## Where They Come From

Enterprise metrics are collected by the monitoring service (`worker/monitoring/enterprise_metrics.py`), not by Prometheus. The collector queries MySQL and Redis directly and returns a JSON payload — available on demand and refreshed automatically during the 5-minute monitoring sweep.

```bash
# Full enterprise metrics payload
curl -s http://localhost:8085/api/metrics | python3 -m json.tool

# Lighter system + mail stats
curl -s http://localhost:8085/api/stats | python3 -m json.tool
```

Through the API gateway, the same data backs `GET /api/v1/monitoring/metrics` and the dashboard-stats endpoint (platform-scope credential, `support` role).

## What Is Collected

### Mail metrics (from MySQL)

| Field | Source | Meaning |
|-------|--------|---------|
| `hourly_email_stats` | `tracking_events` (last hour) | total / delivered / bounced / failed counts |
| `active_domains` | `domains WHERE active = 1` | Domain count |
| `rate_limit_stats` | `rate_limit_usage` (last hour) | Average and peak request counts |
| `storage_stats` | `storage_usage_current` | Total bytes used, organizations using storage |

### Performance metrics

| Field | Source |
|-------|--------|
| `redis.connected_clients`, `used_memory`, `keyspace_hits/misses` | Redis `INFO` |
| `database.connections`, `total_queries` | MySQL `SHOW STATUS` |

### System metrics

CPU, memory, disk, load average, and network/disk I/O counters via psutil — the same numbers the [system monitor](system-monitoring.md) alerts on.

### SLA metrics

| Field | How it's computed |
|-------|-------------------|
| `uptime_percentage` | Share of non-`down` rows in `health_checks` over the last 24 h |
| `delivery_success_rate` | Share of `delivered` rows in `tracking_events` over the last 24 h |
| `sla_compliance` | `min(uptime, delivery_rate)` |

See [SLA Monitoring](sla-monitoring.md) for how to use these.

## Per-Organization Numbers

Per-organization usage does **not** come from Prometheus — worker metrics are not labelled by `org_id`. Use the database-backed APIs instead:

| Question | Where to look |
|----------|--------------|
| Emails sent/delivered/bounced per org | `/api/v1/analytics/*` (backed by `mail_logs` / `delivery_events`, produced by `log_ingestor` since 2026-08-22) |
| Storage per org | `/api/v1/storage/*` (backed by `storage_usage_current`) |
| Rate-limit consumption per org | `/api/v1/rate-limiter/*` (backed by `rate_limit_usage`) |
| SMTP credential usage per key | `/api/v1/smtp-credentials/{id}/usage` |

**Healthy delivery rates** to hold organizations to:

| Metric | Healthy | Concerning | Critical |
|--------|---------|-----------|----------|
| Delivery rate | > 95% | 90-95% | < 90% |
| Bounce rate | < 3% | 3-5% | > 5% |
| Spam complaint rate | < 0.1% | 0.1-0.3% | > 0.3% |

> **Warning:** If a single org's bounce rate spikes, they might be sending to a bad list. That can hurt your entire server's reputation. Catch it early — and note that `AUTO_SUSPEND_ENABLED` (automatic suspension of abusive SMTP credentials by `log_ingestor`) is **off by default** until thresholds have been observed against real traffic.

## Metrics Reports via Webhook

During each 5-minute sweep, `send_metrics_report()` pushes the full metrics payload to the configured webhook endpoints (the `webhook_urls` table, falling back to the `WEBHOOK_URLS` environment variable). If no webhook is configured, the report is skipped silently — the `/api/metrics` endpoint always works regardless.

## Exporting for External Reporting

Pull the JSON and reshape it however you need:

```bash
# Snapshot the current metrics to a dated file
curl -s http://localhost:8085/api/metrics \
  > "metrics-$(date +%Y%m%d-%H%M).json"

# Extract the 24h SLA numbers
curl -s http://localhost:8085/api/metrics | python3 -c "
import json, sys
m = json.load(sys.stdin)
sla = m.get('sla', {})
print(f\"uptime={sla.get('uptime_percentage')}% delivery={sla.get('delivery_success_rate')}%\")
"
```

For historical trends beyond what a snapshot gives you, query the underlying tables (`tracking_events`, `mail_logs`, `usage_history`, `storage_usage_current`) directly — they carry timestamps and organization IDs.
