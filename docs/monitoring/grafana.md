---
edition: enterprise
---

# Grafana

Grafana turns Prometheus metrics into dashboards you can actually understand at a glance.

## Setup

Grafana (`grafana/grafana:10.4.0`) runs on port `3000` as part of the main compose stack. On first launch, it auto-provisions its datasource and dashboards from files in `monitoring/grafana/`.

```yaml
# docker-compose.yml (relevant section)
grafana:
  image: grafana/grafana:10.4.0
  ports:
    - "3000:3000"           # 127.0.0.1:3000 in docker-compose.prod.yml
  environment:
    - GF_SECURITY_ADMIN_USER=${GRAFANA_ADMIN_USER:-admin}
    - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
    - GF_USERS_ALLOW_SIGN_UP=false
  volumes:
    - grafana_data:/var/lib/grafana
    - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
    - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro
```

`GRAFANA_ADMIN_PASSWORD` is one of the eight secrets that `secrets-check` validates before the stack will start — there is no default password.

### Datasource Provisioning

`monitoring/grafana/provisioning/datasources/prometheus.yml` points Grafana at the Prometheus container:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

### Dashboard Provisioning

`monitoring/grafana/provisioning/dashboards/dashboards.yml` loads every JSON file in `monitoring/grafana/dashboards/`.

## Provisioned Dashboards

Two dashboards ship with the repo.

### 1. Mailyte — Mail Server Overview

`monitoring/grafana/dashboards/mail_overview.json`

| Row | Panels |
|-----|--------|
| Service Health | Service health status (`up` per job) |
| Email Delivery | Send/receive rate, queue depth gauge, bounce rate, queue over time |
| Bounce & Spam Analysis | Bounce rate over time, Rspamd action breakdown (`rspamd_actions_*`) |
| SMTP Performance | SMTP response-time heatmap, active connections |
| Traffic & Storage | Top senders/recipients, storage usage, MySQL/Redis resource usage |

### 2. Mailyte — Security Dashboard

`monitoring/grafana/dashboards/security_dashboard.json`

| Row | Panels |
|-----|--------|
| Authentication & Brute Force | Auth failures over time, brute-force attempt rate |
| IP Blocking & Geo Analysis | Blocked IP totals, geo-blocking events, top blocked IPs |
| DLP & Policy Violations | DLP violations by type, violation detail table |
| Spam Analysis | Spam score distribution, top blocked senders |
| Security Alerts Timeline | Alert list |

!!! warning "Many panels currently show *No data*"
    Both dashboards were built ahead of the exporters that would feed them. Panels querying `postfix_*`, `dovecot_*`, `node_filesystem_*`, `auth_failures_total`, `dlp_violations_total`, `geo_blocked_total`, `blocked_ips_total`, or `rspamd_spam_score_bucket` have **no producing exporter deployed today** — no Postfix/Dovecot/node exporter exists, and the security services do not export those Prometheus series. The panels that do work are the ones built on `up`, `mysql_*`, and `redis_*`. The Rspamd panels query flattened names like `rspamd_actions_reject` — verify those against what your Rspamd version actually exposes (`curl http://localhost:11334/metrics | grep actions`; 3.x typically labels a single family instead). Treat empty panels as "producer not deployed", not "all quiet". Live security data is in the database instead: `failed_auth_attempts`, `dlp_violations`, and the `/api/v1/security/*` endpoints.

## Creating Custom Dashboards

1. Open Grafana at `http://localhost:3000`
2. Click **+** in the sidebar, then **Dashboard**
3. Click **Add visualization**
4. Select **Prometheus** as the datasource
5. Write your PromQL query — remember worker metrics are prefixed with the service name (`api_http_requests_total`, not `http_requests_total`)
6. Pick a visualization type (time series, gauge, stat, table, etc.)
7. Save the dashboard

### Example: API traffic panel

```json
{
  "title": "API Requests Per Minute",
  "type": "timeseries",
  "datasource": "Prometheus",
  "targets": [
    {
      "expr": "sum(rate(api_http_requests_total[5m])) * 60",
      "legendFormat": "Requests"
    },
    {
      "expr": "sum(rate(api_http_errors_total[5m])) * 60",
      "legendFormat": "Errors"
    }
  ]
}
```

!!! note "Provisioned dashboards are read-only on disk"
    The dashboards directory is mounted `:ro`. Edits made in the UI to a provisioned dashboard cannot be saved back to the repo file — export the JSON and commit it to `monitoring/grafana/dashboards/` instead.

## Access Control

In production, lock down Grafana:

```ini
# environment variables
GF_SECURITY_ADMIN_PASSWORD=use-a-strong-password-here   # enforced non-empty by secrets-check
GF_USERS_ALLOW_SIGN_UP=false                            # already set in compose
GF_AUTH_ANONYMOUS_ENABLED=false
GF_SECURITY_COOKIE_SECURE=true
GF_SERVER_ROOT_URL=https://grafana.yourdomain.com
```

Since 2026-08-22, production publishes Grafana on `127.0.0.1:3000` only. If you need remote access, use SSH port-forwarding or put it behind Traefik with TLS — never re-expose the raw port.

> **Tip:** For team access, set up OAuth or LDAP authentication instead of sharing the admin password. Grafana supports Google, GitHub, Okta, and others out of the box.

## Exporting and Sharing

```bash
# Export a dashboard as JSON
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/dashboards/uid/DASHBOARD_UID \
  | python3 -m json.tool > dashboard.json

# Import it on another instance
curl -X POST -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  -d @dashboard.json \
  http://localhost:3000/api/dashboards/db
```
