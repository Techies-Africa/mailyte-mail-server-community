# Monitoring Configuration

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Prometheus scrape targets, metric endpoints, and alert rules for the Mailyte email server.

---

Mailyte exposes metrics from every component through Prometheus-compatible endpoints. This page covers what metrics are available, how to scrape them, and how to set up alerts for the things that matter.

## Metric Endpoints

Each service exposes metrics at a specific endpoint:

| Service | Endpoint | Default Port | Metrics |
|---------|----------|-------------|---------|
| Postfix (exporter) | `/metrics` | 9154 | Queue size, delivery rates, bounces, connection counts |
| Dovecot (exporter) | `/metrics` | 9166 | Active connections, auth attempts, mailbox operations |
| Rspamd | `/metrics` | 11334 | Messages scanned, spam scores, action counts |
| FastAPI | `/metrics` | 5000 | API request rates, latencies, error counts |
| MySQL (exporter) | `/metrics` | 9104 | Query rates, connection pool, replication lag |
| Redis (exporter) | `/metrics` | 9121 | Memory usage, commands/sec, key counts |
| ClamAV (exporter) | `/metrics` | 9810 | Scan rates, virus detections, signature freshness |

> [!NOTE]
> Postfix and Dovecot don't natively export Prometheus metrics. Mailyte includes sidecar exporters (`postfix_exporter` and `dovecot_exporter`) that read log files and service stats, then expose them as Prometheus metrics.

## Scrape Configuration

Add these targets to your `prometheus.yml`. See [Prometheus Setup](prometheus-setup.md) for the complete config file.

```yaml
scrape_configs:
  - job_name: 'postfix'
    static_configs:
      - targets: ['postfix-exporter:9154']

  - job_name: 'dovecot'
    static_configs:
      - targets: ['dovecot-exporter:9166']

  - job_name: 'rspamd'
    static_configs:
      - targets: ['rspamd:11334']
    metrics_path: /metrics

  - job_name: 'mailyte-api'
    static_configs:
      - targets: ['api:5000']
    metrics_path: /metrics

  - job_name: 'mysql'
    static_configs:
      - targets: ['mysql-exporter:9104']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']

  - job_name: 'clamav'
    static_configs:
      - targets: ['clamav-exporter:9810']
```

## Key Metrics to Watch

Not all metrics are equally important. Here are the ones that tell you if things are healthy.

### Postfix

| Metric | What It Means | Healthy Range |
|--------|--------------|---------------|
| `postfix_queue_size{queue="active"}` | Messages currently being delivered | < 100 |
| `postfix_queue_size{queue="deferred"}` | Messages waiting for retry | < 50 |
| `postfix_delivery_delays_seconds` | Time from queue entry to delivery | < 30s (p95) |
| `postfix_smtpd_connects_total` | Inbound connection count | Varies |
| `postfix_bounce_total` | Bounced messages | < 5% of total |

A growing deferred queue usually means a remote server is down or your IP is being rate-limited.

### Dovecot

| Metric | What It Means | Healthy Range |
|--------|--------------|---------------|
| `dovecot_active_connections` | Current IMAP/POP3 connections | Depends on user count |
| `dovecot_auth_failures_total` | Failed login attempts | Low and steady |
| `dovecot_auth_successes_total` | Successful logins | Correlates with user activity |
| `dovecot_disk_usage_bytes` | Mail storage used | Below quota thresholds |

A spike in `auth_failures` often indicates a brute-force attack.

### Rspamd

| Metric | What It Means | Healthy Range |
|--------|--------------|---------------|
| `rspamd_scanned_total` | Total messages scanned | Matches inbound volume |
| `rspamd_actions_total{action="reject"}` | Messages rejected as spam | < 30% of scanned |
| `rspamd_actions_total{action="add header"}` | Messages flagged as probable spam | < 20% of scanned |
| `rspamd_actions_total{action="no action"}` | Clean messages | > 50% of scanned |

If your reject rate is very high, you might be getting targeted by spam campaigns. If it's very low, check that Rspamd is actually running.

### FastAPI

| Metric | What It Means | Healthy Range |
|--------|--------------|---------------|
| `http_requests_total` | Total API requests | Varies |
| `http_request_duration_seconds` | Response time | < 200ms (p95) |
| `http_requests_total{status="5xx"}` | Server errors | 0 |

## Alert Rules

Alerts fire when something needs attention. Put these in your Prometheus alert rules file.

### Critical Alerts

```yaml
groups:
  - name: mailyte-critical
    rules:
      - alert: PostfixQueueBacklog
        expr: postfix_queue_size{queue="deferred"} > 500
        for: 15m
        labels:
          severity: critical
        annotations:
          summary: "Postfix deferred queue is large ({{ $value }} messages)"
          description: "More than 500 messages in the deferred queue for 15 minutes. Check delivery logs."

      - alert: DovecotDown
        expr: up{job="dovecot"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Dovecot exporter is down"
          description: "Cannot scrape Dovecot metrics. Users may not be able to read mail."

      - alert: RspamdDown
        expr: up{job="rspamd"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Rspamd is down"
          description: "Spam filtering is not running. Depending on milter_default_action, mail may be accepted unfiltered."

      - alert: HighBounceRate
        expr: rate(postfix_bounce_total[1h]) / rate(postfix_delivery_total[1h]) > 0.10
        for: 30m
        labels:
          severity: critical
        annotations:
          summary: "Bounce rate above 10%"
          description: "High bounce rate may indicate a compromised account or bad mailing list."

      - alert: ClamAVSignaturesStale
        expr: (time() - clamav_signature_timestamp) > 86400
        for: 1h
        labels:
          severity: critical
        annotations:
          summary: "ClamAV signatures are older than 24 hours"
          description: "Virus definitions haven't updated. Check freshclam."
```

### Warning Alerts

```yaml
      - alert: PostfixQueueGrowing
        expr: postfix_queue_size{queue="deferred"} > 100
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "Postfix deferred queue is growing ({{ $value }} messages)"

      - alert: HighSpamRate
        expr: rate(rspamd_actions_total{action="reject"}[1h]) / rate(rspamd_scanned_total[1h]) > 0.50
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "More than 50% of inbound mail is being rejected as spam"

      - alert: DovecotAuthFailureSpike
        expr: rate(dovecot_auth_failures_total[5m]) > 10
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "High rate of authentication failures"
          description: "Possible brute-force attack. Consider enabling fail2ban."

      - alert: APIHighLatency
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job="mailyte-api"}[5m])) > 1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "API p95 latency above 1 second"

      - alert: MySQLConnectionsHigh
        expr: mysql_global_status_threads_connected / mysql_global_variables_max_connections > 0.8
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "MySQL connections at {{ $value | humanizePercentage }} of max"

      - alert: RedisMemoryHigh
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.9
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Redis memory usage above 90%"
```

> [!TIP]
> Start with these alerts and adjust thresholds based on your actual traffic patterns. An alert that fires constantly gets ignored. An alert that never fires might have thresholds set too high.

## Alert Routing

To actually receive alert notifications, configure Alertmanager. A minimal setup:

```yaml
# alertmanager.yml
route:
  receiver: 'default'
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

  routes:
    - match:
        severity: critical
      receiver: 'pagerduty'
      repeat_interval: 1h

receivers:
  - name: 'default'
    email_configs:
      - to: 'ops@yourdomain.com'
        from: 'alerts@yourdomain.com'
        smarthost: 'localhost:587'

  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: 'your-pagerduty-key'
```

> [!WARNING]
> If you route alert emails through Mailyte itself, you won't get alerts when Mailyte is down. Use an external notification channel (PagerDuty, Slack, OpsGenie) for critical alerts.

## Dashboard Recommendations

If you're using Grafana, import these community dashboards as starting points:

- **Postfix:** Search for "Postfix" in Grafana dashboards (ID: 10013).
- **MySQL:** "MySQL Overview" (ID: 7362).
- **Redis:** "Redis Dashboard" (ID: 11835).

Then create a custom Mailyte dashboard that combines the most important metrics from each service onto a single pane.
