---
title: Grafana Dashboards
description: Access the provisioned Grafana instance, use the two bundled dashboards, build custom panels, and set up Grafana alerts.
edition: enterprise
---

# Grafana Setup

Grafana turns your Prometheus metrics into visual dashboards. Mailyte provisions Grafana automatically — data source and dashboards load on first start. This guide covers access, the bundled dashboards, custom panels, and alerts.

## Initial Setup

### Access Grafana

- **Development**: `http://your-server:3000`
- **Production**: `https://grafana.<your-domain>` (Traefik router; the port itself is bound to loopback only)

Credentials come from `.env`:

- Username: `GRAFANA_ADMIN_USER` (default `admin`)
- Password: `GRAFANA_ADMIN_PASSWORD` — **required, no default**; the stack's startup secrets check fails without it

Self-service sign-up is disabled (`GF_USERS_ALLOW_SIGN_UP=false`).

### Data Source (Provisioned)

The Prometheus data source is provisioned from `monitoring/grafana/provisioning/datasources/prometheus.yml` — name `Prometheus`, UID `prometheus`, URL `http://prometheus:9090`, set as default, and non-editable in the UI. You should not need to add one by hand. The explicit `uid: prometheus` matters: the bundled dashboard JSONs reference the datasource by that UID in every panel (it was missing until 2026-08-30, which made every panel show "datasource not found" — restart Grafana after pulling the fix so provisioning reapplies).

## Bundled Dashboards

Dashboards are provisioned from `monitoring/grafana/dashboards/` into a **Mailyte** folder (editable, deletable). Two ship today:

### Mailyte - Mail Server Overview

UID `mailyte-mail-overview`. Panels:

- Service Health Status
- Email Send/Receive Rate
- Mail Queue Depth and Mail Queue Over Time
- Bounce Rate
- Top Senders / Top Recipients (Last Hour)
- Storage Usage
- Infrastructure Resource Usage

### Mailyte - Security Dashboard

UID `mailyte-security`. Panels:

- Authentication Failures Over Time
- Brute Force Attempts and Total Blocked IPs
- Auth Failures (24h)
- Geo-Blocking Events by Country (24h)
- Top Blocked IPs (24h)
- DLP & Policy Violations
- Security Alerts Timeline

!!! note "Some panels depend on missing scrape targets"
    Panels built on Postfix/Dovecot exporter metrics have no data source feeding them as of 2026-08-30 (those exporters are not deployed — see [Prometheus Configuration](prometheus-configuration.md)). Empty panels there are expected, not a Grafana problem.

### Adding your own dashboards

Drop a dashboard JSON into `monitoring/grafana/dashboards/` and restart the `grafana` container — the file provider picks it up. Dashboards created in the UI are stored in the `grafana_data` volume instead and survive restarts.

## Creating Custom Panels

Build queries from metrics that actually exist — check what's live with the Prometheus explore view or `curl -s "http://localhost:9090/api/v1/label/__name__/values"`. Useful starting points:

### Service Availability Table

```
# PromQL query
up
```

Panel settings:

- Visualization: **Table**
- Value mappings: 1 = "UP" (green), 0 = "DOWN" (red)
- All jobs should be green since the 2026-08-30 target cleanup (the dead postfix/dovecot exporter jobs are commented out; analytics/rag now scrape their real container ports)

### Service Uptime

Every worker exports an uptime gauge named after itself:

```
api_uptime_seconds
```

- Visualization: **Stat**, unit **seconds (s)**

### MySQL Connections

```
mysql_global_status_threads_connected
  / mysql_global_variables_max_connections
```

- Visualization: **Gauge**, unit **percent (0.0-1.0)**
- Thresholds: green < 0.6, yellow < 0.8, red >= 0.8

### Redis Memory

```
redis_memory_used_bytes
```

- Visualization: **Time series**, unit **bytes (IEC)**

### Queue Manager Workers and Depth

The queue_manager service is instrumented with prometheus_client:

```
queue_workers
```

- Visualization: **Time series**, legend `{{ queue_name }}`

See [Prometheus Metrics](../reference/prometheus-metrics.md) for the full metric inventory per service.

## Setting Up Alerts in Grafana

Prometheus-side alert rules (in `monitoring/prometheus/rules/`) route through Alertmanager to the webhooks service — that is the primary alerting path. Grafana alerting is a reasonable second channel when you want Slack/email directly:

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

**Service down:**

- Query: `up{job="api"}`
- Condition: IS BELOW 1
- Evaluate every: 1m, for: 2m

**MySQL connection pressure:**

- Query: `mysql_global_status_threads_connected / mysql_global_variables_max_connections`
- Condition: IS ABOVE 0.8
- Evaluate every: 1m, for: 5m

**Redis memory:**

- Query: `redis_memory_used_bytes`
- Condition: IS ABOVE your maxmemory threshold
- Evaluate every: 5m, for: 10m

## Dashboard Variables

Make dashboards interactive with template variables:

### Job Filter

1. Go to **Dashboard settings > Variables**
2. Add a variable:
    - Name: `job`
    - Type: **Query**
    - Query: `label_values(up, job)`
3. Use it in panels: `up{job="$job"}`

### Time Range Comparison

Add a variable for comparing to previous periods:

- Name: `comparison`
- Type: **Custom**
- Values: `1h,6h,24h,7d`

Then in your panel query, add a second query with offset:

```
# Current
up{job="$job"}

# Previous period
up{job="$job"} offset $comparison
```

## Tips

- **Use recording rules** for complex queries that run on every dashboard load — they're pre-computed by Prometheus (add them to a file in `monitoring/prometheus/rules/` and reload)
- **Set realistic thresholds** — tune alerts after a week of baseline data
- **Organize dashboards** into folders: the provisioned ones live in "Mailyte"; keep your own in a separate folder so a provisioning change never clobbers them
- **Share read-only** — set up a viewer role for ops teams that shouldn't edit dashboards
- **Auto-refresh** — set dashboards to refresh every 30s for ops screens
