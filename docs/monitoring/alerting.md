# Alerting

Alerts tell you something is wrong before your users do — and only work when the alert expressions match series that actually exist.

## How Alerting Works

Prometheus evaluates alert rules every 15 seconds. When a rule fires, it sends the alert to Alertmanager (`prom/alertmanager:v0.27.0`). Alertmanager groups, deduplicates, and routes alerts to its configured webhook receiver.

```mermaid
graph LR
    PR[Prometheus] -->|fires alert| AM[Alertmanager]
    AM -->|webhook POST| WH[webhooks:8081/alertmanager]
```

## Alert Rules

Alert rules live in `monitoring/prometheus/rules/` and are mounted into the container at `/etc/prometheus/rules/`. Two files ship with the repo.

### `mail_alerts.yml`

The general service/mail/security rules. The active set (rewritten 2026-08-30 so every live expression matches a series that actually exists):

| Alert | Expression | Severity |
|-------|-----------|----------|
| `ServiceDown` | `up == 0` for 2m | critical |
| `HighErrorRate` | `sum by (job) (rate({__name__=~".+_http_errors_total"}[5m])) / sum by (job) (rate({__name__=~".+_http_requests_total"}[5m])) > 0.05` | warning |
| `DatabaseConnectionPoolExhausted` | MySQL connections > 80% of max | warning |
| `SlowQueries` | > 10 slow queries/sec | warning |
| `RedisMemoryHigh` | Redis memory > 80% of max | warning |

`SpamSpike` was commented out on 2026-08-31: a live probe showed this rspamd build's controller serves no `/metrics` endpoint at all, so no `rspamd_*` series exists for any expression to match. Re-enable together with an rspamd exporter or upgrade.

`HighErrorRate` uses a `__name__` regex because the workers export **service-prefixed** counters (`api_http_errors_total`, never bare `http_errors_total`) — see the naming note under `backup_alerts.yml` below.

!!! note "Rules commented out because their series have no producer (2026-08-30)"
    An alert whose expression matches no series **silently never fires** — it looks exactly like "everything is fine". Rather than leave that trap armed, the following rules are commented out in `mail_alerts.yml` with dated notes, to be restored when a producer exists:

    - `MailQueueBackup` / `MailQueueCritical` / `HighBounceRate` / `CriticalBounceRate` — `postfix_*` series; **no Postfix exporter is deployed**.
    - `DiskSpaceWarning` / `DiskSpaceCritical` — `node_filesystem_*`; **no node-exporter is deployed**.
    - `KafkaConsumerLag` — the kafka-exporter job is commented out in `prometheus.yml`.
    - `BruteForceDetected` / `DLPViolation` (the whole `security_alerts` group) — no service exports `auth_failures_total` / `dlp_violations_total` (auth failures are recorded in the `failed_auth_attempts` table, DLP violations in `dlp_violations`).

    `SpamSpike` was rewritten from the nonexistent `rspamd_actions_reject` to the labelled `rspamd_actions_total{type="reject"}` family; confirm the exact name against your live exposition (`curl http://localhost:11334/metrics | grep actions`). Disk pressure is covered separately by the monitoring service's own psutil checks (warning at 75%, critical at 85% — see [System Monitoring](system-monitoring.md)); everything in `backup_alerts.yml` is live.

### `backup_alerts.yml`

Backup and disaster-recovery rules — deliberately written against absence, so a backup that quietly stopped running looks exactly like one that failed loudly. These are written against the real, prefixed series and are live:

| Alert | Fires when | Severity |
|-------|-----------|----------|
| `NoRecentFullBackup` | `monitoring_backup_age_seconds{backup_type="full"} > 93600` (26 h) | critical |
| `NoRecentIncrementalBackup` | incremental age > 7200 s (2 h) | warning |
| `BackupMonitoringGone` | `absent(monitoring_backup_age_seconds)` for 15m | critical |
| `BackupLastRunFailed` | `monitoring_backup_last_status == 0` after a prior success | warning |
| `BackupNeverRun` | age gauge at its 10-year sentinel for 1h | critical |
| `UnencryptedBackupsPresent` | `monitoring_backup_unencrypted_runs > 0` | warning |
| `ArchiveSpoolNotDraining` | `archiver_archive_spool_depth > 0` for 30m | warning |
| `ArchiveSpoolBacklogCritical` | spool depth > 5000 for 15m | critical |
| `ArchiveStoreFailing` | `rate(archiver_archive_store_failures_total[15m]) > 0` | critical |

The file's own header records the naming lesson: `shared/metrics.py` prepends the emitting service's name to every metric, so rules must be written against `monitoring_*` / `archiver_*`, never the bare names.

## Alert Severity Levels

| Severity | Meaning | Response Time | Example |
|----------|---------|--------------|---------|
| `critical` | Service is down or data is at risk | Immediate | `ServiceDown`, `NoRecentFullBackup` |
| `warning` | Something needs attention soon | Within hours | `RedisMemoryHigh`, `SlowQueries` |

## Alertmanager Configuration

The real config, `monitoring/alertmanager/alertmanager.yml`:

```yaml
route:
  receiver: 'webhook-notifications'
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

  routes:
    - receiver: 'critical-alerts'
      match:
        severity: critical
      group_wait: 10s
      group_interval: 1m
      repeat_interval: 1h

    - receiver: 'webhook-notifications'
      match:
        severity: warning

receivers:
  - name: 'webhook-notifications'
    webhook_configs:
      - url: 'http://webhooks:8081/alertmanager'
        send_resolved: true

  - name: 'critical-alerts'
    webhook_configs:
      - url: 'http://webhooks:8081/alertmanager'
        send_resolved: true

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname']

  - source_match:
      alertname: 'ServiceDown'
    target_match_re:
      severity: '.*'
    equal: ['job']
```

There are no Slack/email/PagerDuty receivers configured — everything routes to the webhooks service. To add Slack or email, add the corresponding `slack_configs` / `email_configs` receivers per the upstream Alertmanager docs.

Both receivers POST to `http://webhooks:8081/alertmanager`, which is served by the webhooks service's `POST /alertmanager` route (`worker/webhooks/app.py`, added 2026-08-30 — before that the route didn't exist and every notification 404'd). The route unpacks the standard Alertmanager payload and forwards **each alert individually** through the global webhook dispatcher (`shared/webhook_dispatcher.py`) as a signed `system.alert.firing` or `system.alert.resolved` event to the configured `WEBHOOK_URL`, with the alert's labels, annotations, timestamps, and fingerprint in `data`. If `WEBHOOK_URL` is unset in the webhooks container, dispatch silently no-ops and firing alerts remain visible only in the Prometheus (`:9090/alerts`) and Alertmanager (`:9093`) UIs. The monitoring service's own [webhook notifications](webhook-notifications.md) are a separate path for health events.

> **Note:** The `inhibit_rules` section prevents warning alerts from firing when a critical alert for the same issue is already active, and suppresses everything else for a job whose `ServiceDown` is firing.

## Silencing Alerts

During maintenance, silence alerts so you don't get flooded:

```bash
# Silence all alerts for 2 hours
amtool silence add --alertmanager.url=http://localhost:9093 \
  --duration=2h \
  --comment="Planned maintenance window"

# Silence a specific alert
amtool silence add --alertmanager.url=http://localhost:9093 \
  alertname=RedisMemoryHigh \
  --duration=1h \
  --comment="Known cache warm-up"

# List active silences
amtool silence query --alertmanager.url=http://localhost:9093
```

## Testing Alerts

Don't wait for things to break. Test your alert pipeline:

```bash
# Send a test alert to Alertmanager
curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "TestAlert",
      "severity": "warning"
    },
    "annotations": {
      "summary": "This is a test alert",
      "description": "Testing the alert pipeline"
    }
  }]'
```

Then check it arrived (`http://localhost:9093`) and watch what the receiver did with it:

```bash
docker compose logs alertmanager | tail -20
docker compose logs webhooks | grep -i alertmanager
```
