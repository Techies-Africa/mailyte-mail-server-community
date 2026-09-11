# Log Analyzer -- Aggregate Log Reports

!!! warning "Not deployed"
    `mailer/log_analyzer/` appears in **no compose file** -- no container runs it. It is a standalone script kept in the tree. Per-message log ingestion, `mail_logs` production, and delivery webhooks are the job of the [log ingestor](log-ingestor.md) (since 2026-08-22); do not mistake this component for that producer.

The log analyzer (`mailer/log_analyzer/app.py`) is a Python script that, when run, parses Postfix and Rspamd logs over a trailing window (default 24 hours), computes aggregate delivery and spam statistics, stores an hourly summary report, and optionally POSTs the report to a webhook.

## What It Actually Does

- Parses `/var/log/mail.log` and `/var/log/rspamd/rspamd.log` with compiled regex patterns
- Computes delivery statistics: sent / bounced / deferred / rejected counts, delivery and bounce rates
- Computes spam statistics: spam vs. ham counts and spam rate
- Generates plain-text recommendations from those rates
- Writes one **aggregate report row per run** to the `mail_analysis_reports` table (created on demand) -- never per-message rows
- Optionally sends the report to `WEBHOOK_URLS`
- Loops hourly (`time.sleep(3600)`) when run as a process

Dovecot login patterns are defined but the main loop only analyzes Postfix and Rspamd logs; GeoIP lookup support is initialized when the GeoLite2 database is present.

## Log Patterns

| Pattern | What It Matches |
|---------|----------------|
| `postfix_sent` | `postfix/smtp[...]: QUEUE_ID: to=<r>, relay=..., status=sent` |
| `postfix_bounced` | same, `status=bounced` |
| `postfix_deferred` | same, `status=deferred` |
| `postfix_rejected` | `postfix/smtpd[...]: NOQUEUE: reject: ... from host[IP]` |
| `dovecot_login` / `dovecot_failed` | IMAP logins / SQL password mismatches (defined, unused by the main loop) |
| `rspamd_spam` / `rspamd_ham` | Rspamd task verdicts with scores |

## Data Written

| Table | Purpose |
|-------|---------|
| `mail_analysis_reports` | One aggregate JSON report per run (auto-created) |

It does **not** write `mail_logs`, `analytics_daily`, `analytics_hourly`, or `security_events`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `localhost` / `3306` / `mailserver` / `root` / (empty) | MySQL for report storage |
| `GEOIP_DB_PATH` | `/usr/share/GeoIP/GeoLite2-Country.mmdb` | Optional GeoLite2 database |
| `WEBHOOK_URLS` | (empty) | Report webhook target |

Log paths are hardcoded: `/var/log/mail.log`, `/var/log/dovecot.log`, `/var/log/rspamd/rspamd.log`.

## Relationship to the Rest of the Stack

- **Per-message records**: [log ingestor](log-ingestor.md) -- deployed, idempotent, rotation-safe
- **Tenant-facing analytics**: the [analytics worker](../worker/analytics.md), reading `mail_logs`
- **Reactive blocking**: [intrusion detection](intrusion-detection.md) (Fail2ban)

If you want the aggregate reports, run the script manually (it needs the log files and MySQL reachable) or add a compose service for it -- none exists as of 2026-08-30.
