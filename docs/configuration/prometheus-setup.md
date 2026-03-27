# Prometheus Setup

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Complete prometheus.yml configuration, scrape intervals, targets for each service, and retention settings.

---

Prometheus collects metrics from all Mailyte components. This page walks through the full configuration file and explains each section.

## prometheus.yml — Full Configuration

Here's the complete Prometheus config used by Mailyte. It lives at `/etc/prometheus/prometheus.yml` inside the Prometheus container.

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

  external_labels:
    environment: 'production'
    cluster: 'mailyte'

rule_files:
  - '/etc/prometheus/rules/*.yml'

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - 'alertmanager:9093'

scrape_configs:
  # -----------------------------------------------
  # Prometheus self-monitoring
  # -----------------------------------------------
  - job_name: 'prometheus'
    scrape_interval: 30s
    static_configs:
      - targets: ['localhost:9090']

  # -----------------------------------------------
  # Postfix metrics (via postfix_exporter sidecar)
  # -----------------------------------------------
  - job_name: 'postfix'
    scrape_interval: 15s
    static_configs:
      - targets: ['postfix-exporter:9154']
        labels:
          service: 'smtp'

  # -----------------------------------------------
  # Dovecot metrics (via dovecot_exporter sidecar)
  # -----------------------------------------------
  - job_name: 'dovecot'
    scrape_interval: 15s
    static_configs:
      - targets: ['dovecot-exporter:9166']
        labels:
          service: 'imap'

  # -----------------------------------------------
  # Rspamd metrics (native endpoint)
  # -----------------------------------------------
  - job_name: 'rspamd'
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ['rspamd:11334']
        labels:
          service: 'antispam'

  # -----------------------------------------------
  # Mailyte FastAPI (native Prometheus middleware)
  # -----------------------------------------------
  - job_name: 'mailyte-api'
    scrape_interval: 10s
    metrics_path: /metrics
    static_configs:
      - targets: ['api:5000']
        labels:
          service: 'api'

  # -----------------------------------------------
  # MySQL metrics (via mysqld_exporter)
  # -----------------------------------------------
  - job_name: 'mysql'
    scrape_interval: 30s
    static_configs:
      - targets: ['mysql-exporter:9104']
        labels:
          service: 'database'

  # -----------------------------------------------
  # Redis metrics (via redis_exporter)
  # -----------------------------------------------
  - job_name: 'redis'
    scrape_interval: 15s
    static_configs:
      - targets: ['redis-exporter:9121']
        labels:
          service: 'cache'

  # -----------------------------------------------
  # ClamAV metrics (via clamav_exporter)
  # -----------------------------------------------
  - job_name: 'clamav'
    scrape_interval: 60s
    static_configs:
      - targets: ['clamav-exporter:9810']
        labels:
          service: 'antivirus'

  # -----------------------------------------------
  # Node exporter (host-level metrics)
  # -----------------------------------------------
  - job_name: 'node'
    scrape_interval: 30s
    static_configs:
      - targets: ['node-exporter:9100']
        labels:
          service: 'host'
```

## Scrape Intervals Explained

Different services get different scrape intervals based on how quickly their metrics change and how resource-intensive scraping is.

| Job | Interval | Why |
|-----|----------|-----|
| `prometheus` | 30s | Self-monitoring doesn't need to be frequent |
| `postfix` | 15s | Queue sizes can change rapidly during delivery spikes |
| `dovecot` | 15s | Connection counts fluctuate with user activity |
| `rspamd` | 15s | Spam patterns can shift quickly |
| `mailyte-api` | 10s | API latency and errors need fast detection |
| `mysql` | 30s | Database metrics are relatively stable |
| `redis` | 15s | Memory and command rates are useful to track closely |
| `clamav` | 60s | Signature updates and scan rates don't change fast |
| `node` | 30s | CPU, memory, and disk change slowly |

> [!TIP]
> Don't set scrape intervals below 10 seconds unless you have a specific reason. More frequent scraping means more storage, more CPU, and more network traffic — with diminishing returns for most metrics.

## Retention Settings

Prometheus stores metric data locally. Configure how long to keep it.

### Time-Based Retention

Add this to your Prometheus startup command (in `docker-compose.yml`):

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
      - '--web.enable-lifecycle'
    volumes:
      - prometheus-data:/prometheus
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/rules:/etc/prometheus/rules:ro
    ports:
      - '9090:9090'
```

`--storage.tsdb.retention.time=30d` keeps 30 days of metrics. Adjust based on your disk space and how far back you need to look.

### Size-Based Retention

You can also cap storage by size:

```yaml
command:
  - '--storage.tsdb.retention.time=30d'
  - '--storage.tsdb.retention.size=10GB'
```

When both are set, whichever limit is hit first triggers cleanup.

### How Much Disk Space Do You Need?

A rough estimate for Mailyte's full metric set:

| Retention | Estimated Storage |
|-----------|------------------|
| 7 days | ~500 MB |
| 30 days | ~2 GB |
| 90 days | ~6 GB |
| 365 days | ~20 GB |

These estimates assume default scrape intervals and a moderate-traffic server. High-cardinality labels (like per-recipient metrics) can increase storage significantly.

> [!NOTE]
> For long-term storage (months to years), consider using a remote write backend like Thanos, Cortex, or Grafana Mimir. Prometheus local storage is designed for weeks, not years.

## Exporter Configuration

### postfix_exporter

The Postfix exporter reads Postfix log files and exposes metrics.

```yaml
# docker-compose.yml
postfix-exporter:
  image: kumina/postfix-exporter:latest
  command:
    - '--postfix.logfile_path=/var/log/mail.log'
    - '--postfix.showq_path=/var/spool/postfix/public/showq'
  volumes:
    - postfix-logs:/var/log:ro
    - postfix-spool:/var/spool/postfix:ro
  ports:
    - '9154:9154'
```

### dovecot_exporter

The Dovecot exporter connects to Dovecot's stats socket.

```yaml
dovecot-exporter:
  image: kumina/dovecot-exporter:latest
  command:
    - '--dovecot.socket_path=/var/run/dovecot/stats'
  volumes:
    - dovecot-run:/var/run/dovecot:ro
  ports:
    - '9166:9166'
```

### mysqld_exporter

```yaml
mysql-exporter:
  image: prom/mysqld-exporter:latest
  environment:
    DATA_SOURCE_NAME: "exporter:exporter-password@(mysql:3306)/mailserver"
  ports:
    - '9104:9104'
```

> [!WARNING]
> Create a dedicated MySQL user for the exporter with read-only permissions:
> ```sql
> CREATE USER 'exporter'@'%' IDENTIFIED BY 'exporter-password';
> GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';
> ```

### redis_exporter

```yaml
redis-exporter:
  image: oliver006/redis_exporter:latest
  environment:
    REDIS_ADDR: "redis://redis:6379"
  ports:
    - '9121:9121'
```

## Recording Rules

Recording rules pre-compute frequently used queries so dashboards load faster.

Create `/etc/prometheus/rules/recording.yml`:

```yaml
groups:
  - name: mailyte-recording
    interval: 30s
    rules:
      - record: mailyte:postfix_delivery_rate_5m
        expr: rate(postfix_delivery_total[5m])

      - record: mailyte:postfix_bounce_rate_5m
        expr: rate(postfix_bounce_total[5m])

      - record: mailyte:rspamd_spam_ratio_1h
        expr: >
          rate(rspamd_actions_total{action=~"reject|add header"}[1h])
          /
          rate(rspamd_scanned_total[1h])

      - record: mailyte:api_error_rate_5m
        expr: >
          rate(http_requests_total{job="mailyte-api", status=~"5.."}[5m])
          /
          rate(http_requests_total{job="mailyte-api"}[5m])

      - record: mailyte:dovecot_connections_by_protocol
        expr: sum by (protocol) (dovecot_active_connections)
```

## Reloading Configuration

Prometheus supports hot-reloading if you started it with `--web.enable-lifecycle`:

```bash
# Reload via API
curl -X POST http://localhost:9090/-/reload

# Or send SIGHUP to the process
docker exec mailyte-prometheus kill -HUP 1
```

> [!TIP]
> Always validate your config before reloading:
> ```bash
> docker exec mailyte-prometheus promtool check config /etc/prometheus/prometheus.yml
> docker exec mailyte-prometheus promtool check rules /etc/prometheus/rules/recording.yml
> ```
> A bad config file will prevent Prometheus from reloading and may cause it to fail on next restart.

## Verifying Targets

After configuring everything, check that all targets are being scraped successfully:

1. Open the Prometheus web UI at `http://your-server:9090`.
2. Go to **Status > Targets**.
3. Every target should show state **UP** with a recent last scrape time.

If a target shows **DOWN**, check:
- Is the exporter container running? (`docker ps`)
- Can Prometheus reach the exporter? (network connectivity within Docker)
- Is the exporter throwing errors? (`docker logs exporter-name`)
