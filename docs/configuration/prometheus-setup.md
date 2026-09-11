---
edition: enterprise
---

# Prometheus Setup

The shipped prometheus.yml, how it's mounted, retention, and the known target corrections.

---

Prometheus (`prom/prometheus:v2.51.0`) collects metrics from every worker service and the infrastructure exporters. The active configuration is `monitoring/prometheus/prometheus.yml`, mounted read-only into the container together with the rules directory:

```yaml
# docker-compose.yml (shipped)
prometheus:
  image: prom/prometheus:v2.51.0
  volumes:
    - ./monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    - ./monitoring/prometheus/rules:/etc/prometheus/rules:ro
    - prometheus_data:/prometheus
  command:
    - '--config.file=/etc/prometheus/prometheus.yml'
    - '--storage.tsdb.path=/prometheus'
    - '--storage.tsdb.retention.time=30d'
    - '--web.console.libraries=/etc/prometheus/console_libraries'
    - '--web.console.templates=/etc/prometheus/consoles'
    - '--web.enable-lifecycle'
  ports:
    - "9090:9090"        # production binds 127.0.0.1:9090
```

## prometheus.yml — As Shipped

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

rule_files:
  - "/etc/prometheus/rules/*.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

scrape_configs:
  - job_name: 'prometheus'
    static_configs: [{ targets: ['localhost:9090'] }]

  # Mail services
  # (postfix-exporter/dovecot-exporter jobs are commented out in the real file
  # since 2026-08-30 — neither exporter exists in any compose file; the rspamd
  # job followed on 2026-08-31 — this build's controller serves no /metrics)

  # Worker services — targets use internal container ports
  - job_name: 'api'
    metrics_path: /metrics
    static_configs: [{ targets: ['api:8080'] }]
  - job_name: 'webhooks'
    static_configs: [{ targets: ['webhooks:8081'] }]
  - job_name: 'rate_limiter'
    static_configs: [{ targets: ['rate_limiter:8082'] }]
  - job_name: 'monitoring'
    static_configs: [{ targets: ['monitoring:8085'] }]
  - job_name: 'tracking'
    static_configs: [{ targets: ['tracking:8086'] }]
  - job_name: 'analytics'
    static_configs: [{ targets: ['analytics:8085'] }]          # container port (host-mapped to 8087)
  - job_name: 'dashboard'
    static_configs: [{ targets: ['dashboard:8088'] }]
  - job_name: 'archiver'
    static_configs: [{ targets: ['archiver:8083'] }]
  - job_name: 'encryption'
    static_configs: [{ targets: ['encryption:8084'] }]
  - job_name: 'queue_manager'
    static_configs: [{ targets: ['queue_manager:8090'] }]
  - job_name: 'rag'
    static_configs: [{ targets: ['rag:8090'] }]                # container port (host-mapped to 8091)
  - job_name: 'storage_usage'
    static_configs: [{ targets: ['storage_usage:8092'] }]
  - job_name: 'delivery_optimizer'
    static_configs: [{ targets: ['delivery_optimizer:8088'] }]
  - job_name: 'jmap'
    static_configs: [{ targets: ['jmap:8098'] }]
  - job_name: 'migration'
    static_configs: [{ targets: ['migration:8099'] }]
  - job_name: 'autoconfig'
    static_configs: [{ targets: ['autoconfig:8100'] }]
  - job_name: 'caldav'
    static_configs: [{ targets: ['caldav:8101'] }]
  - job_name: 'templates'
    static_configs: [{ targets: ['templates:8089'] }]
  - job_name: 'url_protection'
    static_configs: [{ targets: ['url_protection:8090'] }]
  - job_name: 'oauth'
    static_configs: [{ targets: ['oauth:8091'] }]

  # Infrastructure exporters
  - job_name: 'mysql'
    static_configs: [{ targets: ['mysql-exporter:9104'] }]
  - job_name: 'redis'
    static_configs: [{ targets: ['redis-exporter:9121'] }]
```

(A `kafka-exporter:9308` job is present but commented out — no kafka-exporter service exists yet.)

> [!WARNING]
> Targets must use **container** ports, not host-published ports — the service-to-service hop inside the compose network never crosses the host port mapping. Two jobs originally got this wrong (`analytics` targeted 8087 instead of 8085, `rag` targeted 8091 instead of 8090) and scraped nothing; both were fixed, the dead `postfix`/`dovecot` exporter jobs commented out, and the missing `templates`/`url_protection`/`oauth` jobs added on 2026-08-30.

## Retention

Retention is fixed by the compose command flag: `--storage.tsdb.retention.time=30d` (there is no env var for it). To change it, edit the `command:` list; you can also add `--storage.tsdb.retention.size=10GB` — whichever limit is hit first triggers cleanup.

Rough sizing for this stack's metric volume: ~2 GB for 30 days at the shipped 15 s interval. For longer horizons use remote write (Thanos/Mimir) rather than growing local retention.

## Exporters

Only two ship:

```yaml
mysql-exporter:
  image: prom/mysqld-exporter:v0.15.1
  environment:
    - MYSQLD_EXPORTER_PASSWORD=${DB_PASSWORD}
  command:
    - '--mysqld.address=mysql:3306'
    - '--mysqld.username=${DB_USER:-mailuser}'
    - '--no-collect.slave_status'   # no replica; the app user lacks REPLICATION CLIENT

redis-exporter:
  image: oliver006/redis_exporter:v1.58.0
  environment:
    - REDIS_ADDR=redis://redis:6379
```

If you add postfix/dovecot/node exporters, give them the service names the commented-out scrape jobs in `prometheus.yml` already expect (`postfix-exporter`, `dovecot-exporter`), uncomment those jobs, and add a `node` job — then restore the matching commented-out rules in `rules/mail_alerts.yml`.

## Reloading Configuration

`--web.enable-lifecycle` is set, so a hot reload works after config edits:

```bash
curl -X POST http://localhost:9090/-/reload    # dev; use 127.0.0.1 on the prod host
# or
docker compose restart prometheus
```

Validate before reloading:

```bash
docker exec prometheus promtool check config /etc/prometheus/prometheus.yml
docker exec prometheus promtool check rules /etc/prometheus/rules/mail_alerts.yml /etc/prometheus/rules/backup_alerts.yml
```

## Verifying Targets

1. Open the Prometheus UI — `http://localhost:9090` in development, `ssh -L 9090:127.0.0.1:9090` in production (the port is loopback-bound there).
2. **Status → Targets** — every worker job should be UP.
3. Expect `postfix` and `dovecot` DOWN (no exporters), and `analytics`/`rag` DOWN until the port fix above is applied.

If a worker target is DOWN: is the container running (`docker compose ps`)? Does `docker exec prometheus wget -qO- http://<service>:<port>/metrics` work from inside the network? Remember metric names come back prefixed with the service name — see [Prometheus Metrics](../reference/prometheus-metrics.md).
