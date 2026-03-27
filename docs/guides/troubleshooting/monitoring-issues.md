---
title: "Troubleshooting: Monitoring Issues"
description: Fix Prometheus scrape failures, Grafana connection errors, missing metrics, and alerting problems.
---

# Monitoring Issues

Your dashboards are empty, Prometheus isn't scraping, or alerts aren't firing. Here's how to get monitoring back on track.

## Problem: Prometheus Not Scraping Targets

### Check Target Status

Open the Prometheus UI at `http://your-server:9090/targets`. Each target shows its state:

- **UP** — scraping successfully
- **DOWN** — can't reach the target
- **UNKNOWN** — never attempted

Or check via the API:

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json, sys
data = json.load(sys.stdin)
for target in data['data']['activeTargets']:
    print(f\"{target['labels'].get('job', 'unknown'):25s} {target['health']:6s} {target.get('lastError', '')}\")
"
```

### Common Causes

**Service not running:**

```bash
docker compose ps | grep -E "Exit|Restarting"
```

**Wrong port in Prometheus config:**

Double-check the ports in `prometheus.yml` match what the services actually listen on:

```bash
# Check what port a service uses
docker exec -it api ss -tlnp | grep LISTEN
docker exec -it tracking ss -tlnp | grep LISTEN
```

**Network issue:**

Prometheus must be on the same Docker network:

```bash
docker network inspect mailserver_network | grep prometheus
```

If missing:

```bash
docker network connect mailserver_network prometheus
```

**Metrics endpoint not enabled:**

```bash
# Test if the metrics endpoint responds
docker exec -it prometheus wget -qO- http://api:8080/metrics | head -5
```

If you get a 404, the service might not expose `/metrics`. Check the service's configuration.

## Problem: Grafana Can't Connect to Prometheus

### Check the Data Source

In Grafana, go to **Connections > Data Sources > Prometheus** and click **Save & Test**.

### Common Fixes

**Wrong URL:** The URL should be `http://prometheus:9090` (Docker service name), not `http://localhost:9090` (unless Grafana runs on the host).

**Network isolation:** Grafana and Prometheus must be on the same Docker network.

```bash
# Verify connectivity
docker exec -it grafana wget -qO- http://prometheus:9090/api/v1/status/config | head -5
```

**Prometheus not running:**

```bash
docker compose ps prometheus
```

## Problem: Missing Metrics

### Metrics exist in Prometheus but not in Grafana

1. Check the time range — Grafana might be looking at a time window before the metric existed
2. Check the data source — make sure the panel is using the right Prometheus instance
3. Check the query — try the same PromQL query directly in Prometheus UI

### Metrics don't exist in Prometheus

```bash
# Search for a metric by name
curl -s "http://localhost:9090/api/v1/label/__name__/values" | python3 -m json.tool | grep mailyte
```

If the metric doesn't appear:

- The service exposing it might have restarted and the metric hasn't been emitted yet
- The metric might have a different name than expected — check [Prometheus Metrics](../../reference/prometheus-metrics.md)
- Counters start at 0 and only show up after the first increment

### Metrics have wrong values

Check the metric type:

- **Counters** always go up. Use `rate()` or `increase()` to get per-second or per-interval values
- **Gauges** can go up and down. Use them directly
- **Histograms** have `_bucket`, `_sum`, and `_count` suffixes

## Problem: Alerts Not Firing

### Check Alert Rules

```bash
# List loaded rules
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import json, sys
data = json.load(sys.stdin)
for group in data['data']['groups']:
    for rule in group['rules']:
        print(f\"{rule['name']:30s} state={rule['state']:10s} health={rule['health']}\")
"
```

### Common Issues

**Rule file not loaded:**

```bash
# Check Prometheus config for rule files
docker exec -it prometheus cat /etc/prometheus/prometheus.yml | grep rule_files -A5

# Check if rule files exist
docker exec -it prometheus ls /etc/prometheus/alerts/
```

**Expression never evaluates to true:**

Test the expression in the Prometheus UI:

1. Go to `http://localhost:9090/graph`
2. Paste the alert expression
3. Click Execute

If it returns empty, the condition hasn't been met.

**Alertmanager not connected:**

```bash
# Check Alertmanager status
curl -s http://localhost:9093/api/v2/status | python3 -m json.tool

# Check Prometheus->Alertmanager connection
curl -s http://localhost:9090/api/v1/alertmanagers | python3 -m json.tool
```

**Alert is firing but notification not sending:**

Check Alertmanager logs:

```bash
docker logs alertmanager --tail 30
```

Look for errors related to Slack webhooks, email SMTP, or PagerDuty API keys.

## Problem: Grafana Dashboard Errors

### "No data" on Panels

1. Check the time range (top-right corner)
2. Check variables (dropdowns at the top) — they might be filtering too aggressively
3. Click the panel title > Edit > check the query

### "Template variables could not be fetched"

The variable query is failing. Check:

- Data source is reachable
- The label or metric name in the variable query exists

### Dashboard won't load

```bash
# Check Grafana logs
docker logs grafana --tail 50
```

Common causes:

- Corrupt dashboard JSON
- Plugin not installed
- Data source deleted

## Problem: High Prometheus Storage Usage

```bash
# Check Prometheus disk usage
docker exec -it prometheus du -sh /prometheus/

# Check TSDB stats
curl -s http://localhost:9090/api/v1/status/tsdb | python3 -m json.tool
```

### Fix: Reduce Retention

```yaml
# Prometheus command
command:
  - '--storage.tsdb.retention.time=15d'   # Down from 30d
  - '--storage.tsdb.retention.size=2GB'   # Hard cap
```

### Fix: Reduce Scrape Frequency

For metrics that don't change fast, scrape less often:

```yaml
# In prometheus.yml
- job_name: "mysql"
  scrape_interval: 60s   # Instead of default 15s
```

## Monitoring the Monitor

Set up a basic external check that Prometheus itself is healthy:

```bash
# Add to crontab or external monitoring
*/5 * * * * curl -sf http://localhost:9090/-/healthy || echo "Prometheus is down" | mail -s "Alert" admin@yourdomain.com
```
