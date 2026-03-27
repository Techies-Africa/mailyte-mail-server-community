# Enterprise Metrics

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Business-level numbers that matter to your organization — not just whether servers are up, but how they're being used.

## Why Enterprise Metrics

Infrastructure metrics tell you the server is running. Enterprise metrics tell you the server is *useful*. They answer questions from stakeholders, help with billing, and catch abuse early.

## Emails Per Organization

Track send and receive volume broken down by organization.

```promql
# Emails sent by each org in the last 24 hours
increase(api_emails_sent_total[24h]) by (org_id)

# Top 10 sending organizations
topk(10, increase(api_emails_sent_total[24h]) by (org_id))

# Single org's sending trend over time
rate(api_emails_sent_total{org_id="acme-corp"}[1h]) * 3600
```

**Grafana panel setup:**

| Setting | Value |
|---------|-------|
| Visualization | Bar gauge |
| Query | `increase(api_emails_sent_total[24h])` |
| Group by | `org_id` |
| Sort | Descending |

## Delivery Rates

Delivery success rate is the most important email metric. A drop here means something is wrong — bad reputation, DNS issues, or content problems.

```promql
# Overall delivery success rate
(
  rate(postfix_delivery_total[1h])
  - rate(postfix_bounce_total[1h])
)
/ rate(postfix_delivery_total[1h]) * 100

# Per-org delivery rate
(
  rate(api_emails_delivered_total{org_id="acme-corp"}[1h])
  / rate(api_emails_sent_total{org_id="acme-corp"}[1h])
) * 100
```

**Healthy delivery rates:**

| Metric | Healthy | Concerning | Critical |
|--------|---------|-----------|----------|
| Delivery rate | > 95% | 90-95% | < 90% |
| Bounce rate | < 3% | 3-5% | > 5% |
| Spam complaint rate | < 0.1% | 0.1-0.3% | > 0.3% |

> **Warning:** If a single org's bounce rate spikes, they might be sending to a bad list. That can hurt your entire server's reputation. Catch it early.

## Storage Usage Trends

Track how much disk space each organization is consuming and predict when you'll need more.

```promql
# Total mail storage used
sum(dovecot_storage_bytes) by (org_id)

# Storage growth rate (bytes per day)
deriv(sum(dovecot_storage_bytes) by (org_id)[7d:1h]) * 86400

# Days until disk is full (at current growth rate)
(node_filesystem_avail_bytes{mountpoint="/var/mail"}
/ deriv(node_filesystem_size_bytes{mountpoint="/var/mail"}[7d:1h]))
/ 86400
```

**Storage summary table:**

```promql
# For a Grafana table panel
sort_desc(
  sum by (org_id) (dovecot_storage_bytes)
)
```

| Org | Current Usage | 30-Day Growth | Projected Full |
|-----|--------------|---------------|---------------|
| acme-corp | 12.4 GB | +2.1 GB | 85 days |
| widgets-inc | 8.7 GB | +0.8 GB | 210 days |
| startup-co | 1.2 GB | +0.3 GB | 560 days |

## API Request Volume

Track who's using the API and how much.

```promql
# Total API requests by org (last 24h)
increase(http_requests_total[24h]) by (org_id)

# API requests by endpoint
topk(10, increase(http_requests_total[24h]) by (endpoint))

# Error rate by org
rate(http_requests_total{status=~"5.."}[1h]) by (org_id)
/ rate(http_requests_total[1h]) by (org_id) * 100
```

## Usage Reports

Build a monthly usage report with these queries:

```promql
# Monthly email volume
increase(api_emails_sent_total[30d]) by (org_id)

# Monthly API calls
increase(http_requests_total[30d]) by (org_id)

# Average daily active users
avg_over_time(dovecot_active_connections[30d])

# Peak concurrent connections
max_over_time(dovecot_active_connections[30d])
```

### Exporting Reports

Pull data from Prometheus for external reporting:

```bash
# Get monthly email totals per org
curl -s 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=increase(api_emails_sent_total[30d]) by (org_id)' \
  | python3 -m json.tool

# Export to CSV
curl -s 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=increase(api_emails_sent_total[30d]) by (org_id)' \
  | python3 -c "
import json, sys, csv
data = json.load(sys.stdin)
writer = csv.writer(sys.stdout)
writer.writerow(['org_id', 'emails_sent'])
for result in data['data']['result']:
    writer.writerow([result['metric']['org_id'], int(float(result['value'][1]))])
"
```

## Rate Limiting Metrics

Track which organizations are hitting their limits:

```promql
# Rate limit hits by org
increase(api_rate_limit_exceeded_total[1h]) by (org_id)

# Orgs closest to their sending limit
(api_emails_sent_total / api_sending_limit) by (org_id)
```

## Grafana Dashboard Layout

A suggested layout for the enterprise metrics dashboard:

```
+----------------------------+----------------------------+
|   Total Emails Today       |   Active Organizations     |
|   (single stat)            |   (single stat)            |
+----------------------------+----------------------------+
|   Emails by Organization (bar chart, 24h)               |
|                                                         |
+---------------------------------------------------------+
|   Delivery Rate Trend      |   Storage Usage by Org     |
|   (time series)            |   (bar gauge)              |
+----------------------------+----------------------------+
|   API Requests by Org      |   Top Endpoints            |
|   (table)                  |   (pie chart)              |
+----------------------------+----------------------------+
```

> **Tip:** Set up a scheduled Grafana report to email these stats to your team weekly. Go to a dashboard, click the share icon, and configure the report schedule.
