# SLA Monitoring

Track your uptime promises and delivery targets — know before your customers do if you're falling short.

## What SLAs to Track

For an email server, three SLAs matter most:

| SLA | Target | Measured from |
|-----|--------|---------------|
| **Uptime** | 99.9% (8.7h downtime/year) | `health_checks` table + Prometheus `up` |
| **Delivery Success** | > 95% | `tracking_events` / `delivery_events` tables |
| **API Response Time** | p95 < 500ms | `api_http_request_duration_seconds{quantile="0.95"}` |

## The Built-In SLA Numbers

The monitoring service computes a 24-hour SLA summary on every sweep (`worker/monitoring/enterprise_metrics.py`):

```bash
curl -s http://localhost:8085/api/metrics | python3 -c "
import json, sys
print(json.load(sys.stdin).get('sla'))
"
```

| Field | How it's computed |
|-------|-------------------|
| `uptime_percentage` | Share of non-`down` rows in `health_checks` over the last 24 h |
| `delivery_success_rate` | Share of `delivered` rows in `tracking_events` over the last 24 h |
| `sla_compliance` | `min(uptime, delivery_rate)` |

For per-message delivery evidence (which messages, when, to whom), use `mail_logs` / `delivery_events` — produced by the `log_ingestor` service since 2026-08-22 — via `/api/v1/analytics` and the message-trace endpoints.

## Uptime Tracking

### What Counts as Downtime

| Scenario | Counts as Downtime? |
|----------|-------------------|
| Postfix down | Yes |
| API returning 500s | Yes |
| Dovecot unreachable | Yes |
| MySQL down | Yes |
| Grafana down | No (monitoring, not core) |
| Scheduled maintenance | Depends on your SLA definition |
| Single failed health check | No (needs sustained failure) |

### Prometheus Queries

`up` exists for every deployed scrape job (api, monitoring, rspamd, mysql, redis, and the other workers — but **not** postfix/dovecot, whose exporters aren't deployed; their uptime is in the `health_checks` table via the monitoring service's probes):

```promql
# Uptime percentage over 30 days for the API
avg_over_time(up{job="api"}[30d]) * 100

# Per-job uptime
avg_over_time(up[30d]) * 100

# Minutes of downtime in the last 30 days
(1 - avg_over_time(up{job="api"}[30d])) * 30 * 24 * 60
```

Prometheus retention is 30 days, so windows beyond `[30d]` are not answerable from Prometheus — use the `health_checks` table for longer history.

### Uptime Table

| Target | Max Downtime/Month | Max Downtime/Year |
|--------|-------------------|-------------------|
| 99.0% | 7.3 hours | 3.65 days |
| 99.5% | 3.65 hours | 1.83 days |
| 99.9% | 43.8 minutes | 8.77 hours |
| 99.95% | 21.9 minutes | 4.38 hours |
| 99.99% | 4.38 minutes | 52.6 minutes |

## Delivery SLA

```sql
-- Delivery success rate, last 24 hours
SELECT
  SUM(status = 'delivered') / COUNT(*) * 100 AS delivery_rate
FROM tracking_events
WHERE created_at >= NOW() - INTERVAL 24 HOUR;

-- Outcome breakdown from the delivery log
SELECT event_type, COUNT(*)
FROM delivery_events
WHERE created_at >= NOW() - INTERVAL 24 HOUR
GROUP BY event_type;
```

**Delivery SLA targets:**

| Metric | Target | Alert Threshold |
|--------|--------|----------------|
| Delivery rate | > 95% | < 93% |
| Bounce rate | < 3% | > 5% |

## API Response Time SLA

```promql
# p95 response time (summary quantile)
api_http_request_duration_seconds{quantile="0.95"}

# Average over the evaluation window
rate(api_http_request_duration_seconds_sum[1h])
/ rate(api_http_request_duration_seconds_count[1h])
```

## SLA Dashboard

Build a dedicated SLA dashboard in Grafana:

```
+----------------------------+----------------------------+
|    API Uptime (30d)        |    Delivery Rate (24h)     |
|    avg_over_time(up...)    |    from /api/metrics       |
+----------------------------+----------------------------+
|   API p95 Latency          |   Backup Freshness         |
|   {quantile="0.95"}        |   monitoring_backup_age_s  |
+----------------------------+----------------------------+
|   Uptime Over Time (daily, time series)                 |
+---------------------------------------------------------+
```

### Gauge Thresholds

| Range | Color | Meaning |
|-------|-------|---------|
| > target | Green | Meeting SLA |
| target - 1% to target | Yellow | Close to breaching |
| < target - 1% | Red | SLA breached |

## SLA Alerting Rules

Rules you can add to `monitoring/prometheus/rules/` — written against series that exist:

```yaml
groups:
  - name: sla
    rules:
      - alert: UptimeSLAAtRisk
        expr: avg_over_time(up{job="api"}[24h]) < 0.999
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "24h API uptime dropped below 99.9%"

      - alert: APILatencySLABreach
        expr: api_http_request_duration_seconds{quantile="0.95"} > 0.5
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "API p95 latency exceeds 500ms SLA target"
```

Delivery-rate breaches can't be alerted from Prometheus today (no per-message delivery series); watch the `/api/metrics` SLA payload or add a periodic check against the database.

## Monthly SLA Reports

```bash
#!/bin/bash
# monthly-sla-report.sh

PROM="http://localhost:9090"

echo "=== Mailyte SLA Report ==="
echo "Generated: $(date)"
echo ""

echo "--- API Uptime (30d) ---"
curl -s "$PROM/api/v1/query" \
  --data-urlencode "query=avg_over_time(up{job=\"api\"}[30d]) * 100" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data['data']['result']:
    print(f\"  API Uptime: {float(r['value'][1]):.3f}%\")
"

echo ""
echo "--- Delivery (24h, from monitoring service) ---"
curl -s http://localhost:8085/api/metrics | python3 -c "
import json, sys
sla = json.load(sys.stdin).get('sla', {})
print(f\"  Delivery Rate: {sla.get('delivery_success_rate')}%\")
print(f\"  Uptime (health checks): {sla.get('uptime_percentage')}%\")
"
```

## Error Budgets

An error budget is the inverse of your SLA — it's how much downtime you're "allowed."

For a 99.9% uptime SLA over 30 days:

- **Total minutes:** 43,200
- **Error budget:** 43.2 minutes
- **Burn rate:** How fast you're consuming the budget

```promql
# Error budget remaining (fraction)
1 - (
  (1 - avg_over_time(up{job="api"}[30d]))
  / (1 - 0.999)
)

# Error budget burn rate (>1 means you'll breach the SLA)
(1 - avg_over_time(up{job="api"}[1h]))
/ (1 - 0.999) * 720
```

> **Tip:** When your error budget is nearly exhausted, freeze deployments and focus on stability. It's a clear, data-driven signal that reliability work needs to happen now.
