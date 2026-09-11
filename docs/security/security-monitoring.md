---
title: Security Monitoring
description: Detecting attacks against your Mailyte server — brute force, spam floods, unauthorized access, and log analysis.
---

# Security Monitoring

You can't stop what you can't see. This page covers detecting and responding to security threats against your email server — using the data sources that actually exist.

## Where Security Data Lives

| Signal | Source of truth | How to query |
|--------|----------------|--------------|
| Failed SMTP/IMAP logins | `failed_auth_attempts` table (written by the Dovecot auth-policy server) | `/api/v1/security/failed-auth` + `/summary` |
| Auth events, key lifecycle, compliance actions | `audit_logs` table | `/api/v1/compliance/audit-log` |
| Per-message mail flow | `mail_logs` / `delivery_events` (produced by `log_ingestor` since 2026-08-22) | `/api/v1/analytics`, message trace |
| DLP policy hits | `dlp_violations` table | `/api/v1/security/dlp/violations` (content redacted by design) |
| IP reputation | `ip_reputation` table | direct SQL |
| Service/system anomalies | Monitoring service + Prometheus | see [Monitoring](../monitoring/index.md) |
| Raw service logs | Docker stdout + `logs/<service>/` rotating files | `docker compose logs` |

!!! warning "Prometheus is not the security data plane"
    The provisioned Grafana security dashboard and the `BruteForceDetected` / `DLPViolation` alert rules query series (`auth_failures_total`, `dlp_violations_total`, `blocked_ips_total`, `geo_blocked_total`) that **no deployed service exports** — those panels and alerts are silent regardless of what's happening. Until exporters for them exist, monitor security through the tables and endpoints above.

## What to Monitor

### Authentication Events

| Event | Where to look | What to look for |
|-------|--------------|-----------------|
| Failed SMTP/IMAP logins | `failed_auth_attempts` | Repeated failures from same IP; distributed failures against one account |
| Failed API auth | API logs; per-IP limiter blocks | Invalid API keys, `429` bursts |
| Auth successes from unusual IPs | `audit_logs`, `user_logins` | Geo-impossible travel |

### Email Flow Anomalies

| Event | What it means |
|-------|--------------|
| Sudden spike in outbound volume | Compromised account or leaked SMTP credential sending spam |
| High bounce rate | Bad list, or domain blacklisted |
| Spike in Rspamd rejects | Inbound campaign, or filtering misconfiguration |
| Queue growing without sending | Postfix or network issue |
| **Queue empty and nothing arriving** | Routing fault — a stale transport map once loop-bounced all inbound for 13 domains while every service reported healthy (fixed 2026-08-27) |

### System Events

| Event | What it means |
|-------|--------------|
| Unexpected container restart | Possible exploit or OOM — check `docker events` and monitoring webhook history |
| Disk usage spike | Log bomb, large attachment, or data exfiltration |
| Config file changes | Unauthorized access |

## Detecting Brute Force Attacks

The [auth-policy server](intrusion-detection.md) already delays and blocks automatically. To *see* what it's fighting:

```bash
# Hourly summary with top offender IPs
curl -s -H "X-API-Key: $PLATFORM_KEY" \
  "https://<api-host>/api/v1/security/failed-auth/summary?hours=24" | python3 -m json.tool
```

```sql
-- Raw view: attempts per IP, last hour
SELECT client_ip, service, SUM(attempt_count) AS attempts
FROM failed_auth_attempts
WHERE last_attempt_at >= NOW() - INTERVAL 1 HOUR
GROUP BY client_ip, service
ORDER BY attempts DESC LIMIT 20;
```

Log-side confirmation:

```bash
docker compose logs dovecot --since 1h 2>&1 | grep -ci "auth failed"
```

## Detecting Spam Floods

### Outbound Spam (Compromised Account or Credential)

```sql
-- Top senders in the last hour, from the delivery log
SELECT sender, COUNT(*) AS msgs
FROM mail_logs
WHERE created_at >= NOW() - INTERVAL 1 HOUR
GROUP BY sender ORDER BY msgs DESC LIMIT 10;
```

### Quick Response

```bash
# 1. Suspend the mailbox via the API
curl -X PUT "https://<api-host>/api/v1/mailboxes/email-accounts/<account_id>" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"status": "suspended"}'

# 2. CRITICAL: flush the Dovecot auth cache, or the account keeps
#    authenticating for up to 1 hour (auth_cache_ttl)
docker compose exec dovecot doveadm auth cache flush compromised@domain.com

# 3. If it's an SMTP API-key credential, revoke it -- the API flushes the cache for you
curl -X POST "https://<api-host>/api/v1/smtp-credentials/<id>/revoke" -H "X-API-Key: $KEY"

# 4. Flush their queued messages
docker compose exec postfix postqueue -p | awk -v s="compromised@domain.com" '$7==s {print $1}' | \
  tr -d '*!' | while read id; do docker compose exec postfix postsuper -d "$id"; done
```

### Inbound Spam Flood

```bash
# Rspamd is scraped at rspamd:11334/metrics -- check what your version names
# its action counters, then query them
curl -s http://localhost:11334/metrics | grep -i action

# Log-side count of rejects in the last hour
docker compose logs rspamd --since 1h 2>&1 | grep -c "reject"
```

## Detecting Unauthorized Access

### Unusual API Key Usage

```sql
-- Recently active keys
SELECT id, name, organization_id, last_used
FROM api_keys
WHERE last_used > NOW() - INTERVAL 1 HOUR
ORDER BY last_used DESC;
```

!!! danger "api_keys rows are secrets"
    The `key_id` column holds the raw credential (see [Authentication](authentication.md#api-key-authentication)). Anyone with read access to this table holds every active API key — restrict database access accordingly, and rotate keys after any suspected exposure.

### Audit Trail

```bash
# Query the audit log through the compliance API
curl -s -H "X-API-Key: $PLATFORM_KEY" \
  "https://<api-host>/api/v1/compliance/audit-log?event_type=auth.failed&hours=24" \
  | python3 -m json.tool
```

### File Integrity

Monitor config files for unauthorized changes:

```bash
# Create baseline hashes
find mailer/*/config config/ -type f -exec sha256sum {} \; > /opt/mailyte/config_hashes.sha256

# Check for changes (run via cron)
sha256sum -c /opt/mailyte/config_hashes.sha256 2>&1 | grep -v ": OK$"
```

## Log Analysis

### Log format and redaction

Service logs are pipe-delimited text (`timestamp | service | level | module:line | message`) from `shared/logging_config.py`. A redaction filter scrubs values whose keys look like `password`, `secret`, `token`, or `api_key` before any handler sees them — but treat logs as sensitive anyway.

### Useful Log Queries

```bash
# All authentication failures in the last hour
docker compose logs dovecot --since 1h 2>&1 | grep -i "auth.*fail"

# All rejected emails
docker compose logs postfix --since 1h 2>&1 | grep "NOQUEUE: reject"

# All TLS errors
docker compose logs postfix --since 1h 2>&1 | grep -i "tls.*error\|ssl.*error"

# API errors
docker compose logs api --since 1h 2>&1 | grep "ERROR\|CRITICAL"

# Rspamd rejections
docker compose logs rspamd --since 1h 2>&1 | grep "reject"
```

### Container log rotation

Production caps Docker logs per container (`json-file`, `max-size: 10m`, `max-file: 3`) so a log flood cannot fill the disk through stdout alone.

## Automated Watch Script

A simple cron-driven check against the real data sources:

```bash
#!/bin/bash
# scripts-local/security_check.sh -- run every 5 minutes via cron

MYSQL="docker compose exec -T mysql mysql -N -u root -p${DB_ROOT_PASSWORD} mailserver -e"

FAILED=$($MYSQL "SELECT COALESCE(SUM(attempt_count),0) FROM failed_auth_attempts
                 WHERE last_attempt_at >= NOW() - INTERVAL 5 MINUTE;")
if [ "${FAILED:-0}" -gt 50 ]; then
  echo "ALERT: $FAILED failed logins in 5 minutes" | your-notify-command
fi

OUTBOUND=$($MYSQL "SELECT COUNT(*) FROM mail_logs
                   WHERE created_at >= NOW() - INTERVAL 5 MINUTE;")
if [ "${OUTBOUND:-0}" -gt 500 ]; then
  echo "ALERT: $OUTBOUND messages logged in 5 minutes" | your-notify-command
fi
```

## Security Dashboard

The provisioned Grafana security dashboard exists (`monitoring/grafana/dashboards/security_dashboard.json`) but most of its panels await exporters — see the warning at the top of this page. The working security view today is the **console's security screens**, backed by `/api/v1/security/*`, plus the SQL above.
