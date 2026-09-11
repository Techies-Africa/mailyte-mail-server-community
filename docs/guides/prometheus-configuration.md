---
title: Prometheus Configuration
description: The Prometheus configuration Mailyte ships — scrape jobs, alert rules, retention, reloading, and how to extend it.
edition: enterprise
---

# Prometheus Configuration

Prometheus is part of the base compose stack. Its configuration is bind-mounted read-only from the repo:

| What | Repo path | Mounted at |
|------|-----------|------------|
| Main config | `monitoring/prometheus/prometheus.yml` | `/etc/prometheus/prometheus.yml` |
| Alert rules | `monitoring/prometheus/rules/*.yml` | `/etc/prometheus/rules/` |
| TSDB storage | `prometheus_data` volume | `/prometheus` |

The container runs `prom/prometheus:v2.51.0` with `--storage.tsdb.retention.time=30d` and `--web.enable-lifecycle` (so config reloads work without a restart).

## The Shipped Configuration

Global settings — every job inherits them (no per-job overrides are set):

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

rule_files:
  - /etc/prometheus/rules/*.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]
```

### Scrape Jobs

All worker jobs set `metrics_path: /metrics` and target the service's **container** port on the compose network (which often differs from the published host port):

| Job | Target | Status |
|-----|--------|--------|
| `prometheus` | `localhost:9090` | OK |
| `rspamd` | `rspamd:11334` | OK |
| `api` | `api:8080` | OK (host port 8083) |
| `webhooks` | `webhooks:8081` | OK |
| `rate_limiter` | `rate_limiter:8082` | OK |
| `monitoring` | `monitoring:8085` | OK |
| `tracking` | `tracking:8086` | OK |
| `dashboard` | `dashboard:8088` | OK |
| `archiver` | `archiver:8083` | OK (host port 8089) |
| `encryption` | `encryption:8084` | OK (host port 8093) |
| `queue_manager` | `queue_manager:8090` | OK |
| `storage_usage` | `storage_usage:8092` | OK |
| `delivery_optimizer` | `delivery_optimizer:8088` | OK (host port 8094) |
| `jmap` / `migration` / `autoconfig` / `caldav` | `:8098` / `:8099` / `:8100` / `:8101` | OK |
| `analytics` | `analytics:8085` | OK (host port 8087; target fixed 2026-08-30) |
| `rag` | `rag:8090` | OK (host port 8091; target fixed 2026-08-30) |
| `templates` | `templates:8089` | OK (host port 8095; job added 2026-08-30) |
| `url_protection` | `url_protection:8090` | OK (host port 8096; job added 2026-08-30) |
| `oauth` | `oauth:8091` | OK (host port 8097; job added 2026-08-30) |
| `mysql` | `mysql-exporter:9104` | OK |
| `redis` | `redis-exporter:9121` | OK |

A `kafka` job exists but is commented out, as are the `postfix` (`postfix-exporter:9154`) and `dovecot` (`dovecot-exporter:9166`) jobs since 2026-08-30 — those exporter containers exist in no compose file, so the jobs only produced permanent `DOWN` noise. Uncomment them when the exporters are actually deployed.

### Metric sources

Most workers emit Prometheus text through the shared in-repo collector (`shared/metrics.py`) — you'll see `<service>_info`, `<service>_uptime_seconds`, and per-service counters/gauges (histograms are rendered as summaries). `queue_manager` is the one service instrumented with the real `prometheus_client` library (queue depth/worker gauges). The exporters provide the standard `mysql_*` and `redis_*` metric families. See [Prometheus Metrics](../reference/prometheus-metrics.md) for the metric inventory.

The mysql-exporter connects as `--mysqld.address=mysql:3306` with `MYSQLD_EXPORTER_PASSWORD=${DB_PASSWORD}` and deliberately passes `--no-collect.slave_status` (no replica exists, and the app user lacks `REPLICATION CLIENT`).

## Alert Rules

Two rule files ship in `monitoring/prometheus/rules/`:

**`mail_alerts.yml`** — group `mail_server_alerts`, six active rules (rewritten 2026-08-30 so every live expression matches a real series):

| Alert | Severity | For |
|-------|----------|-----|
| `ServiceDown` | critical | 2m |
| `HighErrorRate` | warning | 5m |
| `SpamSpike` | warning | 10m |
| `DatabaseConnectionPoolExhausted`, `SlowQueries` | warning | 5m / 10m |
| `RedisMemoryHigh` | warning | 10m |

The rest of the group (`MailQueueBackup`/`MailQueueCritical`, `HighBounceRate`/`CriticalBounceRate`, `DiskSpaceWarning`/`DiskSpaceCritical`, `KafkaConsumerLag`) and the whole `security_alerts` group (`BruteForceDetected`, `DLPViolation`) are commented out with dated notes — their series have no producer (no postfix/node/kafka exporter, no auth-failure or DLP counter), so as written they could never fire.

**`backup_alerts.yml`** — group `backup_alerts` (9 absence-first rules): `NoRecentFullBackup`, `NoRecentIncrementalBackup`, `BackupMonitoringGone`, `BackupLastRunFailed`, `BackupNeverRun`, `UnencryptedBackupsPresent`, `ArchiveSpoolNotDraining`, `ArchiveSpoolBacklogCritical`, `ArchiveStoreFailing`.

### Editing rules

1. Edit the files under `monitoring/prometheus/rules/`
2. Reload without a restart:

```bash
curl -X POST http://localhost:9090/-/reload
```

3. Confirm they loaded:

```bash
curl -s http://localhost:9090/api/v1/rules | python3 -m json.tool | head -50
```

## Alertmanager

`monitoring/alertmanager/alertmanager.yml` (mounted at `/etc/alertmanager/alertmanager.yml`) groups by `alertname`+`severity` and delivers **everything to the webhooks service** at `http://webhooks:8081/alertmanager` with `send_resolved: true` — the critical/warning split only changes timing (critical: 10s group wait, 1m interval, 1h repeat; warning: 30s/5m/4h). Two inhibit rules: critical suppresses same-name warnings, and `ServiceDown` suppresses everything else for that job. On the receiving side, the webhooks service's `POST /alertmanager` route (added 2026-08-30) forwards each alert through the global webhook dispatcher as a signed `system.alert.firing` / `system.alert.resolved` event to `WEBHOOK_URL`.

To route to Slack/email/PagerDuty directly, add your own receivers and routes to this file and restart the `alertmanager` container.

## Storage and Retention

Retention is set on the container command in `docker-compose.yml`:

```yaml
command:
  - '--config.file=/etc/prometheus/prometheus.yml'
  - '--storage.tsdb.path=/prometheus'
  - '--storage.tsdb.retention.time=30d'
  - '--web.enable-lifecycle'
```

Change `30d` (or add `--storage.tsdb.retention.size=5GB`) via a compose override, then recreate the container. Check current usage:

```bash
docker exec prometheus du -sh /prometheus/
curl -s http://localhost:9090/api/v1/status/tsdb | python3 -m json.tool
```

## Extending the Setup

The stack ships **without** node-exporter, cadvisor, or postfix/dovecot exporters. If you want host-level metrics, add an exporter as a compose override and a matching scrape job — for example:

```yaml
# docker-compose.override.yml
services:
  node-exporter:
    image: prom/node-exporter:v1.8.0
    container_name: node-exporter
    ports:
      - "127.0.0.1:9100:9100"
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
    command:
      - '--path.procfs=/host/proc'
      - '--path.sysfs=/host/sys'
      - '--path.rootfs=/rootfs'
    networks:
      - mailserver_network
```

plus a job in `monitoring/prometheus/prometheus.yml`:

```yaml
  - job_name: node
    static_configs:
      - targets: ["node-exporter:9100"]
```

then `curl -X POST http://localhost:9090/-/reload`.

## Verifying the Setup

```bash
# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -E '"health"|"job"'

# Check that metrics are flowing
curl -s "http://localhost:9090/api/v1/query?query=up" | python3 -m json.tool
```

All targets should show `"health": "up"` (the formerly permanently-DOWN postfix/dovecot exporter jobs are commented out since 2026-08-30). In production, run these through an SSH tunnel — Prometheus binds to loopback only.
