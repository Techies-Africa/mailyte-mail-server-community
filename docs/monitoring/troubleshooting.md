# Troubleshooting Monitoring

When the monitoring stack itself breaks, here's how to fix it.

## Prometheus Not Scraping

**Symptom:** Grafana shows "No data" or gaps in graphs.

### Check 0: Is it a target that exists?

Before debugging, remember there is no Postfix, Dovecot, or node exporter in the stack (the `postfix`/`dovecot` scrape jobs that pointed at the nonexistent `postfix-exporter:9154` / `dovecot-exporter:9166` are commented out in `prometheus.yml` as of 2026-08-30). Panels on `postfix_*` / `dovecot_*` / `node_*` series are empty by design until those exporters are added — see Prometheus (Enterprise Edition).

### Check 1: Is Prometheus Running?

```bash
docker compose ps prometheus
curl http://localhost:9090/-/healthy
```

If it's not running:

```bash
docker compose logs prometheus | tail -30
docker compose up -d prometheus
```

### Check 2: Are Targets Up?

Open `http://localhost:9090/targets` in your browser, or:

```bash
curl -s http://localhost:9090/api/v1/targets \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for target in data['data']['activeTargets']:
    state = target['health']
    job = target['labels']['job']
    error = target.get('lastError', '')
    print(f'  [{state}] {job} {error}')
"
```

**Common target errors:**

| Error | Cause | Fix |
|-------|-------|-----|
| `connection refused` | Service not running, or scraping the host port instead of the container port | Targets must use container ports (`api:8080`, `archiver:8083`), not host mappings (8083, 8089) |
| `context deadline exceeded` | Scrape timeout | Increase `scrape_timeout` or fix the slow endpoint |
| `no such host` | DNS resolution failed | Container name in `prometheus.yml` must match the compose service name |
| `server returned HTTP status 404` | Wrong `metrics_path` | Every worker serves `/metrics` |

### Check 3: Config Syntax

```bash
# Validate prometheus.yml
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml

# Lifecycle reload is enabled, so config changes apply without a restart
curl -X POST http://localhost:9090/-/reload
```

### Check 4: Storage Issues

```bash
# Check Prometheus storage usage
docker compose exec prometheus df -h /prometheus

# If storage is full, Prometheus stops ingesting.
# Retention is pinned to 30d in the compose command; the volume is prometheus_data.
docker volume inspect $(docker volume ls -q | grep prometheus_data)
```

## Grafana Not Loading

**Symptom:** Grafana shows a blank page, login fails, or dashboards are empty.

### Check 1: Is Grafana Running?

```bash
docker compose ps grafana
curl http://localhost:3000/api/health
```

### Check 2: Can Grafana Reach Prometheus?

```bash
# From inside the Grafana container
docker compose exec grafana wget -qO- http://prometheus:9090/-/healthy
```

If this fails, check both containers are on `mailserver_network`:

```bash
docker inspect grafana --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
docker inspect prometheus --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
```

### Check 3: Datasource Configuration

```bash
# List configured datasources
curl -s -u "$GRAFANA_ADMIN_USER:$GRAFANA_ADMIN_PASSWORD" \
  http://localhost:3000/api/datasources | python3 -m json.tool

# Test the Prometheus datasource
curl -s -u "$GRAFANA_ADMIN_USER:$GRAFANA_ADMIN_PASSWORD" \
  "http://localhost:3000/api/datasources/proxy/1/api/v1/query?query=up" \
  | python3 -m json.tool
```

### Check 4: Dashboard Provisioning

```bash
# Check Grafana logs for provisioning errors
docker compose logs grafana | grep -i "provision"

# Verify dashboard files exist (mounted read-only from monitoring/grafana/dashboards)
docker compose exec grafana ls /var/lib/grafana/dashboards/
```

### Check 5: Reset Admin Password

Locked out? Reset it:

```bash
docker compose exec grafana grafana-cli admin reset-admin-password newpassword
```

Then update `GRAFANA_ADMIN_PASSWORD` in `.env` to match, or the next recreate reverts it.

## Metrics Missing for a Service

**Symptom:** Prometheus is scraping, but specific metrics are missing.

### Check 1: Does the service actually export that name?

Worker metrics are **service-prefixed** (`api_http_requests_total`, never bare `http_requests_total`), and durations are summaries (`{quantile="0.95"}`), not `_bucket` histograms. Search for the real name:

```bash
curl -s 'http://localhost:9090/api/v1/label/__name__/values' \
  | python3 -c "
import json, sys
for name in json.load(sys.stdin)['data']:
    if 'archiver' in name:
        print(name)
"
```

### Check 2: Hit the endpoint directly

```bash
# Deployed exporters
curl -s http://localhost:9104/metrics | head -20   # mysql-exporter
curl -s http://localhost:9121/metrics | head -20   # redis-exporter

# A worker, from inside the network
docker compose exec api curl -s http://localhost:8080/metrics | head -30
```

### Check 3: A counter with no events emits nothing

The shared collector only emits a series after its first increment — a webhook counter on a system that has never delivered a webhook simply doesn't exist yet. `absent()`-style alerts (as used in `backup_alerts.yml`) are the right tool when "no series" must itself be an alarm.

## Alertmanager Not Sending Alerts

**Symptom:** Alerts fire in Prometheus but notifications never arrive.

### Check 0: Is the forwarding chain configured?

`alertmanager.yml` routes every alert to the webhooks service's `POST /alertmanager` route (added 2026-08-30 — before that the route didn't exist and deliveries 404'd). The route forwards each alert through the global webhook dispatcher as `system.alert.firing` / `system.alert.resolved` events, so the notification only reaches you if `WEBHOOK_URL` is set in the webhooks container — with it unset, dispatch silently no-ops and firing alerts are visible only in the Prometheus (`:9090/alerts`) and Alertmanager (`:9093`) UIs. See [Alerting](alerting.md).

### Check 1: Is Alertmanager Running?

```bash
docker compose ps alertmanager
curl http://localhost:9093/-/healthy
```

### Check 2: Are Alerts Actually Firing?

```bash
# Active alerts in Prometheus
curl -s http://localhost:9090/api/v1/alerts | python3 -m json.tool

# Alerts in Alertmanager
curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool
```

If an alert you expect never fires, check its expression matches a real series — several rules whose series have no producer are commented out in `mail_alerts.yml` with dated notes rather than left silently dead (see [Alerting](alerting.md#mail_alertsyml)).

### Check 3: Config Validation

```bash
docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
```

### Check 4: Active Silences

```bash
curl -s http://localhost:9093/api/v2/silences | python3 -m json.tool
```

### Check 5: Delivery Errors

```bash
docker compose logs alertmanager | grep -i "error\|fail\|webhook"
```

## Monitoring Service Issues

**Symptom:** the monitoring service (`monitoring`, port 8085) shows incorrect status or isn't responding.

### Check 1: Basic Connectivity

```bash
curl http://localhost:8085/health
docker compose logs monitoring | tail -30
```

### Check 2: Docker Access (via docker-proxy)

The monitoring container has **no** Docker socket mount — it reaches Docker through `DOCKER_HOST=tcp://docker-proxy:2375`. If restarts/auto-heal fail:

```bash
docker compose ps docker-proxy
docker compose exec monitoring env | grep DOCKER_HOST
docker compose logs docker-proxy | tail -20
```

### Check 3: False Positives

If the monitoring service reports a service as down when it's actually up:

```bash
# Test the same probe manually, using the CONTAINER name and port
docker compose exec monitoring python3 -c \
  "import socket; socket.create_connection(('postfix', 25), timeout=5); print('ok')"
```

Historical footguns already fixed in `service_monitor.py` — worth knowing if you edit it: probes must target the container name (never `0.0.0.0`, which is the prober's own loopback), Rspamd must be probed at `/ping` (not `/stat`, which 403s without credentials), and the API listens on 8080 (not 5000).

## Common Fixes

### Restart the Monitoring Stack

When in doubt:

```bash
docker compose restart prometheus grafana alertmanager monitoring
```

### Rebuild After Config Changes

```bash
docker compose up -d --force-recreate prometheus grafana alertmanager
```

### Clear Prometheus Data

If Prometheus data is corrupted (rare, but it happens):

```bash
docker compose stop prometheus
docker volume rm $(docker volume ls -q | grep prometheus_data)
docker compose up -d prometheus
```

> **Warning:** This deletes all historical metrics. Only do this as a last resort.

## Quick Diagnostic Checklist

Run through this when monitoring breaks:

- [ ] Are the containers running? (`docker compose ps`)
- [ ] Is the target one of the not-yet-deployed exporters (postfix/dovecot/node)? That's expected, not a fault
- [ ] Can services reach each other? (check `mailserver_network`)
- [ ] Is the config valid? (`promtool` / `amtool` checks)
- [ ] Is there enough disk space? (`df -h`)
- [ ] Did someone change a config file recently? (`git log --oneline -5`)
- [ ] In production: are you trying to reach a loopback-bound port remotely? Since 2026-08-22 all monitoring ports bind `127.0.0.1` — use SSH forwarding
