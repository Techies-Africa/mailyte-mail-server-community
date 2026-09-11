---
title: "Troubleshooting: Monitoring Issues"
description: Fix Prometheus scrape failures, Grafana connection errors, missing metrics, and alerting problems.
---

# Monitoring Issues

Your dashboards are empty, Prometheus isn't scraping, or alerts aren't firing. Here's how to get monitoring back on track.

!!! note "Reaching the monitoring stack in production"
    Production binds Prometheus (9090), Alertmanager (9093), and the exporters to `127.0.0.1` on the server — only Grafana is published, via Traefik at `grafana.<your-domain>`. Run the `localhost` commands below on the server itself, or through an SSH tunnel: `ssh -L 9090:127.0.0.1:9090 devops@your-server`.

## Problem: Prometheus Not Scraping Targets

### Check Target Status

Open the Prometheus UI at `http://localhost:9090/targets`. Each target shows its state, or check via the API:

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json, sys
data = json.load(sys.stdin)
for target in data['data']['activeTargets']:
    print(f\"{target['labels'].get('job', 'unknown'):25s} {target['health']:6s} {target.get('lastError', '')}\")
"
```

!!! bug "Four targets are DOWN by design flaw — don't chase them"
    As of 2026-08-30 the shipped `monitoring/prometheus/prometheus.yml` contains four jobs that can never come up:

    - `postfix` (`postfix-exporter:9154`) and `dovecot` (`dovecot-exporter:9166`) — these exporter containers don't exist in any compose file
    - `analytics` — targets `analytics:8087`, but the container listens on 8085
    - `rag` — targets `rag:8091`, but the container listens on 8090

    Everything else showing DOWN is a real problem.

### Common Causes

**Service not running:**

```bash
docker compose ps | grep -E "Exit|Restarting"
```

**Wrong port in the scrape config:**

Prometheus scrapes **container** ports on the compose network, which often differ from published host ports (e.g. `api:8080`, published as 8083). Verify what a service actually listens on from its compose block or Dockerfile `EXPOSE`, and compare against `monitoring/prometheus/prometheus.yml`.

**Network issue:**

Prometheus must be on the same Docker network:

```bash
docker network inspect $(docker network ls -q --filter name=mailserver_network) | grep prometheus
```

**Metrics endpoint not responding:**

```bash
# Test from inside the network (the prometheus image ships wget)
docker exec prometheus wget -qO- http://api:8080/metrics | head -5
```

If you get a 404, the service might not expose `/metrics` — `oauth`, `url_protection`, and `templates` expose it but have no scrape job; conversely, every configured job sets `metrics_path: /metrics` explicitly.

### Reload after config changes

The lifecycle API is enabled, so edits to `monitoring/prometheus/prometheus.yml` or the rules directory don't need a container restart:

```bash
curl -X POST http://localhost:9090/-/reload
```

## Problem: Grafana Can't Connect to Prometheus

### Check the Data Source

The data source is **provisioned** (from `monitoring/grafana/provisioning/datasources/prometheus.yml`) and non-editable in the UI: name `Prometheus`, URL `http://prometheus:9090`, default. In Grafana, go to **Connections > Data Sources > Prometheus** and click **Save & Test**.

### Common Fixes

**Wrong URL in a hand-added source:** it must be `http://prometheus:9090` (Docker service name), not `http://localhost:9090`.

**Network isolation:** Grafana and Prometheus must be on the same Docker network.

```bash
docker exec grafana wget -qO- http://prometheus:9090/api/v1/status/config | head -5
```

**Prometheus not running:**

```bash
docker compose ps prometheus
```

**Panels say "datasource not found":** the bundled dashboards reference datasource UID `prometheus`, but the provisioning file doesn't declare a `uid`. Add `uid: prometheus` to `monitoring/grafana/provisioning/datasources/prometheus.yml` and restart Grafana, or re-point the panels at the default data source.

## Problem: Missing Metrics

### Metrics exist in Prometheus but not in Grafana

1. Check the time range — Grafana might be looking at a time window before the metric existed
2. Check the data source — make sure the panel is using the right Prometheus instance
3. Check the query — try the same PromQL query directly in Prometheus UI

### Metrics don't exist in Prometheus

```bash
# List every metric name Prometheus has seen
curl -s "http://localhost:9090/api/v1/label/__name__/values" | python3 -m json.tool | less
```

If the metric doesn't appear:

- The service exposing it might have restarted and the metric hasn't been emitted yet
- The metric might have a different name than expected — worker metrics are prefixed with the service name (`api_...`, `webhooks_...`); check [Prometheus Metrics](../../reference/prometheus-metrics.md)
- Counters start at 0 and only show up after the first increment

### Metrics have wrong values

Check the metric type:

- **Counters** always go up. Use `rate()` or `increase()` to get per-second or per-interval values
- **Gauges** can go up and down. Use them directly
- **Histograms** have `_bucket`, `_sum`, and `_count` suffixes (most worker services render histograms as summaries via the shared collector)

## Problem: Alerts Not Firing

### Check Alert Rules

```bash
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import json, sys
data = json.load(sys.stdin)
for group in data['data']['groups']:
    for rule in group['rules']:
        print(f\"{rule['name']:35s} state={rule.get('state', '-'):10s} health={rule.get('health', '-')}\")
"
```

### Common Issues

**Rule file not loaded:**

Rules mount at `/etc/prometheus/rules/` (from `monitoring/prometheus/rules/` in the repo):

```bash
docker exec prometheus cat /etc/prometheus/prometheus.yml | grep -A3 rule_files
docker exec prometheus ls /etc/prometheus/rules/
```

**Expression never evaluates to true:**

Test the expression at `http://localhost:9090/graph`. If it returns empty, the condition hasn't been met — or the metric it references doesn't exist. Several shipped alerts depend on the missing postfix/dovecot exporters or the commented-out Kafka job and can **never** fire; verify the metric exists before trusting the alert.

**Alertmanager not connected:**

```bash
curl -s http://localhost:9093/api/v2/status | python3 -m json.tool
curl -s http://localhost:9090/api/v1/alertmanagers | python3 -m json.tool
```

**Alert is firing but no notification arrives:**

Both shipped receivers deliver to the **webhooks service** at `http://webhooks:8081/alertmanager` — there is no Slack/email/PagerDuty receiver out of the box. Check both ends:

```bash
docker logs alertmanager --tail 30
docker logs webhooks 2>&1 | grep -i alertmanager | tail -20
```

To notify an external channel, add your own receiver to `monitoring/alertmanager/alertmanager.yml` and restart the `alertmanager` container.

## Problem: Grafana Dashboard Errors

### "No data" on Panels

1. Check the time range (top-right corner)
2. Check variables (dropdowns at the top) — they might be filtering too aggressively
3. Click the panel title > Edit > check the query
4. Panels built on Postfix/Dovecot exporter metrics have no feed (see the known-broken targets above) — empty is expected there

### Dashboard won't load

```bash
docker logs grafana --tail 50
```

Common causes:

- Corrupt dashboard JSON in `monitoring/grafana/dashboards/`
- Data source UID mismatch (see above)

## Problem: High Prometheus Storage Usage

```bash
docker exec prometheus du -sh /prometheus/
curl -s http://localhost:9090/api/v1/status/tsdb | python3 -m json.tool
```

### Fix: Reduce Retention

Retention is a container flag (default `--storage.tsdb.retention.time=30d` in `docker-compose.yml`) — change it via a compose override and recreate the container; optionally add `--storage.tsdb.retention.size=2GB` as a hard cap.

### Fix: Reduce Scrape Frequency

The shipped config uses the 15s global interval for every job. For slow-moving targets, add a per-job override in `monitoring/prometheus/prometheus.yml`, then reload:

```yaml
  - job_name: mysql
    scrape_interval: 60s
    static_configs:
      - targets: ["mysql-exporter:9104"]
```

## Monitoring the Monitor

Prometheus and Alertmanager have their own health endpoints — wire an external check against them (via a tunnel or from the host's crontab):

```bash
curl -sf http://localhost:9090/-/healthy || echo "Prometheus is down"
curl -sf http://localhost:9093/-/healthy || echo "Alertmanager is down"
```

The `monitoring` worker service (port 8085) independently watches container health and can auto-heal — see [Monitoring Setup](../monitoring-setup.md).
