# Troubleshooting Monitoring

When the monitoring stack itself breaks, here's how to fix it.

## Prometheus Not Scraping

**Symptom:** Grafana shows "No data" or gaps in graphs.

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
| `connection refused` | Exporter not running | Start the exporter container |
| `context deadline exceeded` | Scrape timeout | Increase `scrape_timeout` or fix slow exporter |
| `no such host` | DNS resolution failed | Check container name in `prometheus.yml` matches Docker Compose service name |
| `server returned HTTP status 404` | Wrong `metrics_path` | Verify the correct path in scrape config |

### Check 3: Config Syntax

```bash
# Validate prometheus.yml
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml

# If you changed the config, reload it
curl -X POST http://localhost:9090/-/reload
```

### Check 4: Storage Issues

```bash
# Check Prometheus storage usage
docker compose exec prometheus df -h /prometheus

# If storage is full, Prometheus stops ingesting
# Clean up or increase the volume size
docker volume inspect mailyte_prometheus-data
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

If this fails, the containers might be on different Docker networks.

```bash
# Check networks
docker network ls
docker inspect grafana --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
docker inspect prometheus --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
```

### Check 3: Datasource Configuration

```bash
# List configured datasources
curl -s -u admin:$GRAFANA_PASSWORD \
  http://localhost:3000/api/datasources | python3 -m json.tool

# Test the Prometheus datasource
curl -s -u admin:$GRAFANA_PASSWORD \
  http://localhost:3000/api/datasources/proxy/1/api/v1/query?query=up \
  | python3 -m json.tool
```

### Check 4: Dashboard Provisioning

```bash
# Check Grafana logs for provisioning errors
docker compose logs grafana | grep -i "provision"

# Verify dashboard files exist
docker compose exec grafana ls /var/lib/grafana/dashboards/
```

### Check 5: Reset Admin Password

Locked out? Reset it:

```bash
docker compose exec grafana grafana-cli admin reset-admin-password newpassword
```

## Metrics Missing for a Service

**Symptom:** Prometheus is scraping, but specific metrics are missing.

### Check 1: Is the Exporter Running?

```bash
# For the postfix exporter, for example
docker compose ps postfix-exporter
docker compose logs postfix-exporter | tail -20
```

### Check 2: Does the Exporter Have Metrics?

```bash
# Hit the exporter directly
curl http://localhost:9154/metrics | head -30

# If empty or erroring, the exporter can't reach its service
# Check the exporter's connection settings
```

### Check 3: Metric Name Changed

Sometimes after an upgrade, metric names change. Check:

```bash
# Search for a metric by partial name
curl -s 'http://localhost:9090/api/v1/label/__name__/values' \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for name in data['data']:
    if 'postfix' in name.lower():
        print(name)
"
```

## Alertmanager Not Sending Alerts

**Symptom:** Alerts fire in Prometheus but notifications never arrive.

### Check 1: Is Alertmanager Running?

```bash
docker compose ps alertmanager
curl http://localhost:9093/-/healthy
```

### Check 2: Are Alerts Actually Firing?

```bash
# Check active alerts in Prometheus
curl -s http://localhost:9090/api/v1/alerts | python3 -m json.tool

# Check alerts in Alertmanager
curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool
```

### Check 3: Config Validation

```bash
# Validate alertmanager config
docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
```

### Check 4: Active Silences

Maybe someone silenced the alerts:

```bash
curl -s http://localhost:9093/api/v2/silences | python3 -m json.tool
```

### Check 5: Webhook Delivery

```bash
# Check Alertmanager logs for send errors
docker compose logs alertmanager | grep -i "error\|fail\|webhook"

# Test webhook manually
curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {"alertname": "TestAlert", "severity": "warning"},
    "annotations": {"summary": "Testing alertmanager delivery"}
  }]'
```

## Health Monitor Issues

**Symptom:** Health monitor shows incorrect status or isn't responding.

### Check 1: Basic Connectivity

```bash
curl http://localhost:8080/health
docker compose logs health-monitor | tail -30
```

### Check 2: Docker Socket Access

The health monitor needs the Docker socket for auto-healing:

```bash
# Check if the socket is mounted
docker compose exec health-monitor ls -la /var/run/docker.sock

# If permission denied, check the socket permissions on the host
ls -la /var/run/docker.sock
```

### Check 3: False Positives

If the health monitor reports a service as down when it's actually up:

```bash
# Test the same check manually
# For example, if it says Postfix is down:
docker compose exec health-monitor nc -z postfix 25

# The health monitor might be using the wrong hostname
# Check its environment variables
docker compose exec health-monitor env | grep -i host
```

## Common Fixes

### Restart the Entire Monitoring Stack

When in doubt:

```bash
docker compose restart prometheus grafana alertmanager health-monitor
```

### Rebuild After Config Changes

```bash
# Recreate containers to pick up new config
docker compose up -d --force-recreate prometheus grafana alertmanager
```

### Clear Prometheus Data

If Prometheus data is corrupted (rare, but it happens):

```bash
# Stop Prometheus
docker compose stop prometheus

# Remove the data volume
docker volume rm mailyte_prometheus-data

# Restart — it'll start fresh
docker compose up -d prometheus
```

> **Warning:** This deletes all historical metrics. Only do this as a last resort.

### Check Docker Compose Logs for Everything

```bash
# All monitoring services at once
docker compose logs --tail=50 prometheus grafana alertmanager health-monitor

# Follow live
docker compose logs -f prometheus grafana alertmanager health-monitor
```

## Quick Diagnostic Checklist

Run through this when monitoring breaks:

- [ ] Are the containers running? (`docker compose ps`)
- [ ] Can services reach each other? (check Docker networks)
- [ ] Is the config valid? (use built-in validation tools)
- [ ] Is there enough disk space? (`df -h`)
- [ ] Did someone change a config file recently? (`git log --oneline -5`)
- [ ] Are ports being blocked by a firewall? (`ss -tlnp | grep 9090`)
- [ ] Were there recent Docker or OS updates?
