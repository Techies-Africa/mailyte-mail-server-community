---
title: Prometheus Configuration
description: Detailed Prometheus configuration for scraping every Mailyte service, with recording rules and alert definitions.
---

# Prometheus Configuration

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


This guide covers the full Prometheus configuration for monitoring every Mailyte component — mail services, workers, databases, and system resources.

## Full Configuration

```yaml
# monitoring/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

  external_labels:
    cluster: "mailyte-production"
    environment: "production"

rule_files:
  - "alerts/*.yml"
  - "recording_rules/*.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  # ==========================================================
  # Mailyte Services
  # ==========================================================

  - job_name: "mailyte-api"
    metrics_path: /metrics
    scrape_interval: 10s
    static_configs:
      - targets: ["api:8080"]
        labels:
          service: "api"
          component: "core"

  - job_name: "mailyte-tracking"
    metrics_path: /metrics
    static_configs:
      - targets: ["tracking:8086"]
        labels:
          service: "tracking"
          component: "worker"

  - job_name: "mailyte-webhooks"
    metrics_path: /metrics
    static_configs:
      - targets: ["webhooks:8081"]
        labels:
          service: "webhooks"
          component: "worker"

  - job_name: "mailyte-analytics"
    metrics_path: /metrics
    static_configs:
      - targets: ["analytics:8082"]
        labels:
          service: "analytics"
          component: "worker"

  - job_name: "mailyte-rate-limiter"
    metrics_path: /metrics
    static_configs:
      - targets: ["rate-limiter:8084"]
        labels:
          service: "rate-limiter"
          component: "worker"

  - job_name: "mailyte-queue-manager"
    metrics_path: /metrics
    scrape_interval: 10s
    static_configs:
      - targets: ["queue-manager:8085"]
        labels:
          service: "queue-manager"
          component: "worker"

  - job_name: "mailyte-storage"
    metrics_path: /metrics
    static_configs:
      - targets: ["storage-usage:8087"]
        labels:
          service: "storage"
          component: "worker"

  - job_name: "mailyte-monitoring"
    metrics_path: /metrics
    static_configs:
      - targets: ["monitoring:8088"]
        labels:
          service: "monitoring"
          component: "worker"

  - job_name: "mailyte-rag"
    metrics_path: /metrics
    static_configs:
      - targets: ["rag:8089"]
        labels:
          service: "rag"
          component: "worker"

  # ==========================================================
  # Mail Services
  # ==========================================================

  - job_name: "rspamd"
    metrics_path: /metrics
    scrape_interval: 30s
    static_configs:
      - targets: ["rspamd:11334"]
        labels:
          service: "rspamd"
          component: "mail"

  # Postfix metrics via postfix-exporter (if installed)
  - job_name: "postfix"
    metrics_path: /metrics
    scrape_interval: 30s
    static_configs:
      - targets: ["postfix-exporter:9154"]
        labels:
          service: "postfix"
          component: "mail"

  # ==========================================================
  # Data Stores
  # ==========================================================

  - job_name: "mysql"
    metrics_path: /metrics
    scrape_interval: 30s
    static_configs:
      - targets: ["mysql-exporter:9104"]
        labels:
          service: "mysql"
          component: "database"

  - job_name: "redis"
    metrics_path: /metrics
    scrape_interval: 15s
    static_configs:
      - targets: ["redis-exporter:9121"]
        labels:
          service: "redis"
          component: "database"

  # ==========================================================
  # System
  # ==========================================================

  - job_name: "node"
    metrics_path: /metrics
    static_configs:
      - targets: ["node-exporter:9100"]
        labels:
          service: "host"
          component: "system"

  # Prometheus self-monitoring
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
```

## Optional Exporters

To get metrics from MySQL and Redis, add these exporters:

```yaml
# docker-compose.monitoring.yml
services:
  mysql-exporter:
    image: prom/mysqld-exporter:latest
    container_name: mysql-exporter
    environment:
      DATA_SOURCE_NAME: "${DB_USER:-mailuser}:${DB_PASSWORD:-mailpassword}@tcp(mysql:3306)/${DB_NAME:-mailserver}"
    ports:
      - "9104:9104"
    depends_on:
      mysql:
        condition: service_healthy
    networks:
      - mailserver_network

  redis-exporter:
    image: oliver006/redis_exporter:latest
    container_name: redis-exporter
    environment:
      REDIS_ADDR: "redis://redis:6379"
    ports:
      - "9121:9121"
    networks:
      - mailserver_network

  postfix-exporter:
    image: kumina/postfix-exporter:latest
    container_name: postfix-exporter
    ports:
      - "9154:9154"
    volumes:
      - ./logs/mailer/postfix:/var/log/postfix:ro
    command:
      - '--postfix.logfile_path=/var/log/postfix/maillog'
    networks:
      - mailserver_network
```

## Recording Rules

Pre-compute expensive queries for faster dashboards:

```yaml
# monitoring/prometheus/recording_rules/mailyte.yml
groups:
  - name: mailyte_recording_rules
    interval: 30s
    rules:
      # Email throughput per minute
      - record: mailyte:emails_sent:rate5m
        expr: rate(mailyte_emails_sent_total[5m]) * 60

      - record: mailyte:emails_received:rate5m
        expr: rate(mailyte_emails_received_total[5m]) * 60

      # Bounce rate
      - record: mailyte:bounce_rate:5m
        expr: >
          rate(mailyte_emails_bounced_total[5m])
          / rate(mailyte_emails_sent_total[5m])

      # API request rate
      - record: mailyte:api_requests:rate5m
        expr: rate(mailyte_api_requests_total[5m]) * 60

      # API error rate
      - record: mailyte:api_error_rate:5m
        expr: >
          rate(mailyte_api_requests_total{status=~"5.."}[5m])
          / rate(mailyte_api_requests_total[5m])

      # Average API latency
      - record: mailyte:api_latency:avg5m
        expr: >
          rate(mailyte_api_request_duration_seconds_sum[5m])
          / rate(mailyte_api_request_duration_seconds_count[5m])

      # Queue depth trend
      - record: mailyte:queue_depth:avg5m
        expr: avg_over_time(mailyte_mail_queue_size[5m])
```

## Alert Rules

```yaml
# monitoring/prometheus/alerts/mailyte.yml
groups:
  - name: mailyte_service_alerts
    rules:
      - alert: MailyteServiceDown
        expr: up{component="worker"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Mailyte service {{ $labels.service }} is down"
          description: "The {{ $labels.service }} service has been unreachable for more than 1 minute."

      - alert: MailQueueBacklog
        expr: mailyte_mail_queue_size > 500
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Mail queue backlog: {{ $value }} messages"

      - alert: MailQueueCritical
        expr: mailyte_mail_queue_size > 5000
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Critical mail queue backlog: {{ $value }} messages"

      - alert: HighBounceRate
        expr: mailyte:bounce_rate:5m > 0.05
        for: 15m
        labels:
          severity: critical
        annotations:
          summary: "Bounce rate is {{ $value | humanizePercentage }}"

      - alert: APIHighLatency
        expr: mailyte:api_latency:avg5m > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "API average latency is {{ $value }}s"

      - alert: APIHighErrorRate
        expr: mailyte:api_error_rate:5m > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "API error rate is {{ $value | humanizePercentage }}"

  - name: mailyte_infrastructure_alerts
    rules:
      - alert: HighDiskUsage
        expr: >
          (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Disk usage is {{ $value | humanizePercentage }}"

      - alert: HighMemoryUsage
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) > 0.9
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Memory usage is {{ $value | humanizePercentage }}"

      - alert: MySQLConnectionsHigh
        expr: mysql_global_status_threads_connected / mysql_global_variables_max_connections > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "MySQL connections at {{ $value | humanizePercentage }} of max"

      - alert: RedisMemoryHigh
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Redis memory at {{ $value | humanizePercentage }} of max"

      - alert: SSLCertExpiringSoon
        expr: mailyte_ssl_cert_expiry_days < 14
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "SSL cert for {{ $labels.domain }} expires in {{ $value }} days"

      - alert: SSLCertExpiryCritical
        expr: mailyte_ssl_cert_expiry_days < 3
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "SSL cert for {{ $labels.domain }} expires in {{ $value }} days"
```

## Storage and Retention

### Disk Usage Estimation

Prometheus uses about 1-2 bytes per sample. With the config above:

- ~50 metrics per service x 10 services = 500 metrics
- 15s scrape interval = 4 samples/minute per metric
- 500 x 4 x 60 x 24 = ~2.9M samples/day
- ~5-6 MB/day of storage

At 30 days retention, you'll use about 150-180 MB.

### Adjust Retention

```yaml
# In prometheus service command
command:
  - '--storage.tsdb.retention.time=30d'    # Keep 30 days
  - '--storage.tsdb.retention.size=5GB'    # Or cap at 5GB
```

## Verifying the Setup

```bash
# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -E '"state"|"job"'

# Check that metrics are flowing
curl -s http://localhost:9090/api/v1/query?query=up | python3 -m json.tool

# Check alert rules are loaded
curl -s http://localhost:9090/api/v1/rules | python3 -m json.tool | head -50
```

All targets should show `state: "up"`. If any show `"down"`, check that the service is running and the port is accessible within the Docker network.
