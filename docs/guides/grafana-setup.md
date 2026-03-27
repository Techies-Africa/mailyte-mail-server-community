---
title: Grafana Dashboards
description: Import pre-built dashboards, create custom panels, and set up Grafana alerts for your Mailyte server.
---

# Grafana Setup

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Grafana turns your Prometheus metrics into visual dashboards. This guide covers importing the bundled dashboards, building custom panels, and setting up alerts.

## Initial Setup

### Access Grafana

After starting the monitoring stack, open `http://your-server:3000`.

- Default username: `admin`
- Default password: whatever you set in `GRAFANA_PASSWORD` (default: `admin`)

!!! warning "Change the default password"
    Grafana will prompt you to change the password on first login. Do it.

### Add Prometheus Data Source

If provisioning didn't set this up automatically:

1. Go to **Connections > Data Sources > Add data source**
2. Select **Prometheus**
3. Set the URL to `http://prometheus:9090`
4. Click **Save & Test**

The URL uses the Docker service name because Grafana and Prometheus are on the same Docker network.

## Import Pre-Built Dashboards

Mailyte ships with dashboard JSON files in `monitoring/grafana/dashboards/`.

### Via the UI

1. Go to **Dashboards > Import**
2. Click **Upload JSON file**
3. Select a dashboard file from `monitoring/grafana/dashboards/`
4. Choose your Prometheus data source
5. Click **Import**

### Via Provisioning (Automatic)

If you mounted the provisioning directory, dashboards load automatically:

```yaml
# monitoring/grafana/provisioning/dashboards/default.yml
apiVersion: 1
providers:
  - name: "Mailyte Dashboards"
    orgId: 1
    folder: "Mailyte"
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: true
```

## Bundled Dashboards

### 1. Overview Dashboard

The big picture — everything at a glance.

**Panels:**

- Email throughput (sent/received per minute)
- Active mail queue depth
- Bounce rate trend
- Service health status (up/down for each container)
- Storage usage across all domains
- Top 5 sending domains

### 2. Mail Flow Dashboard

Deep dive into email processing.

**Panels:**

- Inbound vs outbound volume
- Delivery status breakdown (sent, bounced, deferred, rejected)
- Average delivery latency
- Queue age histogram
- Top recipients by volume
- Spam score distribution

### 3. Infrastructure Dashboard

Server resources and database health.

**Panels:**

- CPU usage by container
- Memory usage by container
- Disk I/O
- Network traffic
- MySQL connections and query rate
- Redis memory usage and hit rate

## Creating Custom Panels

### Email Volume Over Time

```
# PromQL query
rate(mailyte_emails_sent_total[5m]) * 60
```

Panel settings:

- Visualization: **Time series**
- Legend: `{{ organization_id }}`
- Unit: **emails/min**

### Bounce Rate Gauge

```
# PromQL query
(rate(mailyte_emails_bounced_total[1h]) / rate(mailyte_emails_sent_total[1h])) * 100
```

Panel settings:

- Visualization: **Gauge**
- Unit: **percent (0-100)**
- Thresholds: green < 1%, yellow < 3%, red >= 3%

### Queue Depth with Trend

```
# Current depth
mailyte_mail_queue_size

# 1-hour average for comparison
avg_over_time(mailyte_mail_queue_size[1h])
```

Panel settings:

- Visualization: **Time series**
- Two queries — current (solid) and average (dashed)

### Storage by Organization

```
# PromQL query
mailyte_storage_used_bytes{level="organization"}
```

Panel settings:

- Visualization: **Bar gauge**
- Unit: **bytes (IEC)**
- Sort: descending

### API Latency Heatmap

```
# PromQL query
rate(mailyte_api_request_duration_seconds_bucket[5m])
```

Panel settings:

- Visualization: **Heatmap**
- Data format: **Time series buckets**

### Service Health Table

```
# PromQL query
up{component=~"worker|mail|core"}
```

Panel settings:

- Visualization: **Table**
- Value mappings: 1 = "UP" (green), 0 = "DOWN" (red)

## Setting Up Alerts in Grafana

Grafana can send alerts through Slack, email, PagerDuty, and many other channels.

### Step 1: Configure a Contact Point

1. Go to **Alerting > Contact points**
2. Click **Add contact point**
3. Name it (e.g., "Slack Alerts")
4. Choose the integration type (Slack, Email, etc.)
5. Fill in the details and test it

### Step 2: Create Alert Rules

1. Go to **Alerting > Alert rules**
2. Click **New alert rule**
3. Add a PromQL query
4. Set the threshold
5. Choose the notification contact point

### Example Alert Rules

**Queue Backlog:**

- Query: `mailyte_mail_queue_size`
- Condition: IS ABOVE 500
- Evaluate every: 1m, for: 5m

**High API Error Rate:**

- Query: `rate(mailyte_api_requests_total{status=~"5.."}[5m]) / rate(mailyte_api_requests_total[5m])`
- Condition: IS ABOVE 0.05
- Evaluate every: 1m, for: 5m

**SSL Certificate Expiring:**

- Query: `mailyte_ssl_cert_expiry_days`
- Condition: IS BELOW 14
- Evaluate every: 1h, for: 1h

## Dashboard Variables

Make dashboards interactive with template variables:

### Organization Filter

1. Go to **Dashboard settings > Variables**
2. Add a variable:
    - Name: `organization`
    - Type: **Query**
    - Query: `label_values(mailyte_emails_sent_total, organization_id)`
3. Use it in panels: `mailyte_emails_sent_total{organization_id="$organization"}`

### Time Range Comparison

Add a variable for comparing to previous periods:

- Name: `comparison`
- Type: **Custom**
- Values: `1h,6h,24h,7d`

Then in your panel query, add a second query with offset:

```
# Current
rate(mailyte_emails_sent_total{organization_id="$organization"}[5m])

# Previous period
rate(mailyte_emails_sent_total{organization_id="$organization"}[5m] offset $comparison)
```

## Tips

- **Use recording rules** for complex queries that run on every dashboard load — they're pre-computed by Prometheus
- **Set realistic thresholds** — tune alerts after a week of baseline data
- **Organize dashboards** into folders: Overview, Mail, Infrastructure, per-Organization
- **Share read-only** — set up a viewer role for ops teams that shouldn't edit dashboards
- **Auto-refresh** — set dashboards to refresh every 30s for ops screens
