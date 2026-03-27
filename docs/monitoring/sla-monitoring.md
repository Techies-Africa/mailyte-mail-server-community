# SLA Monitoring

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Track your uptime promises and delivery targets — know before your customers do if you're falling short.

## What SLAs to Track

For an email server, three SLAs matter most:

| SLA | Target | Measurement |
|-----|--------|-------------|
| **Uptime** | 99.9% (8.7h downtime/year) | Health monitor availability |
| **Delivery Success** | > 95% within 5 minutes | Postfix delivery metrics |
| **API Response Time** | p95 < 500ms | FastAPI metrics |

## Uptime Tracking

Uptime is the percentage of time all critical services are available.

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

```promql
# Uptime percentage over 30 days
avg_over_time(up{job="api"}[30d]) * 100

# Per-service uptime
avg_over_time(mailyte_service_up[30d]) * 100

# Combined system uptime (all critical services)
(
  avg_over_time(mailyte_service_up{service="postfix"}[30d])
  * avg_over_time(mailyte_service_up{service="dovecot"}[30d])
  * avg_over_time(mailyte_service_up{service="api"}[30d])
  * avg_over_time(mailyte_service_up{service="mysql"}[30d])
) * 100

# Minutes of downtime in the last 30 days
(1 - avg_over_time(up{job="api"}[30d])) * 30 * 24 * 60
```

### Uptime Table

| Target | Max Downtime/Month | Max Downtime/Year |
|--------|-------------------|-------------------|
| 99.0% | 7.3 hours | 3.65 days |
| 99.5% | 3.65 hours | 1.83 days |
| 99.9% | 43.8 minutes | 8.77 hours |
| 99.95% | 21.9 minutes | 4.38 hours |
| 99.99% | 4.38 minutes | 52.6 minutes |

## Delivery SLA

Track what percentage of emails are delivered successfully within your target window.

```promql
# Delivery success rate (last 24h)
(
  increase(postfix_delivery_total[24h])
  - increase(postfix_bounce_total[24h])
)
/ increase(postfix_delivery_total[24h]) * 100

# Emails delivered within 5 minutes (percentage)
histogram_quantile(1,
  rate(postfix_delivery_delay_seconds_bucket{le="300"}[1h])
)
/ rate(postfix_delivery_delay_seconds_count[1h]) * 100

# Average delivery time
rate(postfix_delivery_delay_seconds_sum[1h])
/ rate(postfix_delivery_delay_seconds_count[1h])
```

**Delivery SLA targets:**

| Metric | Target | Alert Threshold |
|--------|--------|----------------|
| Delivery rate | > 95% | < 93% |
| Delivered within 1 min | > 80% | < 70% |
| Delivered within 5 min | > 95% | < 90% |
| Bounce rate | < 3% | > 5% |

## API Response Time SLA

```promql
# Percentage of requests under 500ms
sum(rate(http_request_duration_seconds_bucket{le="0.5"}[1h]))
/ sum(rate(http_request_duration_seconds_count[1h])) * 100

# p95 response time trend
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Requests meeting SLA by endpoint
sum by (endpoint) (
  rate(http_request_duration_seconds_bucket{le="0.5"}[1h])
)
/ sum by (endpoint) (
  rate(http_request_duration_seconds_count[1h])
) * 100
```

## SLA Dashboard

Build a dedicated SLA dashboard in Grafana:

```
+----------------------------+----------------------------+
|    System Uptime (30d)     |    Delivery Rate (30d)     |
|    99.97%  [gauge]         |    96.2%  [gauge]          |
+----------------------------+----------------------------+
|   API SLA Compliance       |   Delivery Time SLA        |
|   98.5% < 500ms [gauge]   |   94.1% < 5min [gauge]     |
+----------------------------+----------------------------+
|   Uptime Over Time (daily, time series)                 |
|   ------------------------------------------------      |
|   Shows daily uptime % with 99.9% target line           |
+---------------------------------------------------------+
|   SLA Breach Log (table)                                |
|   Date | Service | Duration | Impact                    |
+---------------------------------------------------------+
```

### Gauge Thresholds

Configure Grafana gauges with color-coded thresholds:

| Range | Color | Meaning |
|-------|-------|---------|
| > target | Green | Meeting SLA |
| target - 1% to target | Yellow | Close to breaching |
| < target - 1% | Red | SLA breached |

## SLA Alerting Rules

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
          summary: "24h uptime dropped below 99.9%"
          description: "Current 24h uptime: {{ $value | humanizePercentage }}"

      - alert: DeliverySLABreach
        expr: >
          (increase(postfix_delivery_total[24h])
           - increase(postfix_bounce_total[24h]))
          / increase(postfix_delivery_total[24h]) < 0.95
        for: 30m
        labels:
          severity: critical
        annotations:
          summary: "Delivery rate below 95% SLA target"

      - alert: APILatencySLABreach
        expr: >
          histogram_quantile(0.95,
            rate(http_request_duration_seconds_bucket[1h])
          ) > 0.5
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "API p95 latency exceeds 500ms SLA target"
```

## Monthly SLA Reports

Generate a monthly report with this script:

```bash
#!/bin/bash
# monthly-sla-report.sh

PROM="http://localhost:9090"
PERIOD="30d"

echo "=== Mailyte SLA Report ==="
echo "Period: Last 30 days"
echo "Generated: $(date)"
echo ""

echo "--- Uptime ---"
curl -s "$PROM/api/v1/query" \
  --data-urlencode "query=avg_over_time(up{job=\"api\"}[$PERIOD]) * 100" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data['data']['result']:
    print(f\"  API Uptime: {float(r['value'][1]):.3f}%\")
"

echo ""
echo "--- Delivery ---"
curl -s "$PROM/api/v1/query" \
  --data-urlencode "query=(increase(postfix_delivery_total[$PERIOD]) - increase(postfix_bounce_total[$PERIOD])) / increase(postfix_delivery_total[$PERIOD]) * 100" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data['data']['result']:
    print(f\"  Delivery Rate: {float(r['value'][1]):.2f}%\")
"

echo ""
echo "--- API Latency ---"
curl -s "$PROM/api/v1/query" \
  --data-urlencode "query=histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[$PERIOD]))" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data['data']['result']:
    ms = float(r['value'][1]) * 1000
    print(f\"  p95 Response Time: {ms:.1f}ms\")
"
```

## Error Budgets

An error budget is the inverse of your SLA — it's how much downtime you're "allowed."

For a 99.9% uptime SLA over 30 days:

- **Total minutes:** 43,200
- **Error budget:** 43.2 minutes
- **Burn rate:** How fast you're consuming the budget

```promql
# Error budget remaining (percentage)
1 - (
  (1 - avg_over_time(up{job="api"}[30d]))
  / (1 - 0.999)
)

# Error budget burn rate (>1 means you'll breach the SLA)
(1 - avg_over_time(up{job="api"}[1h]))
/ (1 - 0.999) * 720
```

> **Tip:** When your error budget is nearly exhausted, freeze deployments and focus on stability. It's a clear, data-driven signal that reliability work needs to happen now.
