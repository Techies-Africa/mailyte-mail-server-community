---
title: Security Monitoring
description: Detecting attacks against your Mailyte server — brute force, spam floods, unauthorized access, and log analysis.
---

# Security Monitoring

You can't stop what you can't see. This page covers detecting and responding to security threats against your email server.

## What to Monitor

### Authentication Events

| Event | Log Source | What to look for |
|-------|-----------|-----------------|
| Failed SMTP logins | Dovecot auth log | Repeated failures from same IP |
| Failed IMAP logins | Dovecot auth log | Credential stuffing patterns |
| Failed API auth | API logs | Invalid API keys, expired keys |
| Successful logins from unusual IPs | Dovecot, API | Geo-impossible travel |

### Email Flow Anomalies

| Event | What it means |
|-------|--------------|
| Sudden spike in outbound volume | Compromised account sending spam |
| High bounce rate | Bad list, or domain blacklisted |
| Spike in spam score | Content filtering issue |
| Queue growing without sending | Postfix or network issue |

### System Events

| Event | What it means |
|-------|--------------|
| Unexpected container restart | Possible exploit or OOM |
| Disk usage spike | Log bomb, large attachment, or data exfiltration |
| Network traffic anomaly | Port scan, DDoS, or data leak |
| Config file changes | Unauthorized access |

## Detecting Brute Force Attacks

### Pattern: SMTP Brute Force

```bash
# Count failed auth attempts per IP in the last hour
docker logs dovecot 2>&1 | grep "Password mismatch" | \
  awk '{print $NF}' | sort | uniq -c | sort -rn | head -20
```

If you see hundreds of failures from a single IP, it's a brute force attack. Fail2ban handles this automatically (see [Intrusion Detection](intrusion-detection.md)), but you should also alert on it.

### Pattern: API Key Brute Force

```bash
# Count 401 responses per IP in the last hour
docker logs api 2>&1 | grep "401" | \
  awk '{print $1}' | sort | uniq -c | sort -rn | head -20
```

### Prometheus Alert

```yaml
- alert: BruteForceDetected
  expr: rate(mailyte_auth_failures_total[5m]) > 10
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Brute force detected: {{ $value }} failures/sec on {{ $labels.service }}"
```

## Detecting Spam Floods

### Outbound Spam (Compromised Account)

A compromised account will suddenly send thousands of emails. Watch for:

```yaml
- alert: SuspiciousOutboundVolume
  expr: rate(mailyte_emails_sent_total[10m]) > 100  # per org
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Unusual outbound volume from org {{ $labels.organization_id }}: {{ $value }} emails/sec"
```

### Quick Response

```bash
# Find the sender
docker exec -it postfix grep "status=sent" /var/log/postfix/maillog | \
  awk -F'from=<' '{print $2}' | awk -F'>' '{print $1}' | sort | uniq -c | sort -rn | head -10

# Suspend the account
curl -X POST http://localhost:8083/api/v1/edit/mailbox \
  -H "X-API-Key: ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"items": ["compromised@domain.com"], "attr": {"status": "suspended"}}'

# Flush their queued messages
docker exec -it postfix postqueue -p | grep "compromised@" | awk '{print $1}' | \
  while read id; do docker exec -it postfix postsuper -d "$id"; done
```

### Inbound Spam Flood

A spike in inbound spam means Rspamd thresholds might need adjusting:

```bash
# Check recent spam scores
docker logs rspamd 2>&1 | grep "reject\|add header" | wc -l
```

## Detecting Unauthorized Access

### Unusual API Key Usage

```sql
-- Find keys with sudden activity spikes
SELECT key_id, name, usage_count, last_used
FROM api_keys
WHERE last_used > NOW() - INTERVAL 1 HOUR
ORDER BY usage_count DESC;
```

### Login from New Location

Track login IPs and alert on new ones:

```python
def check_unusual_login(email: str, ip: str):
    """Check if this IP has been used before for this account."""
    known_ips = db.execute(
        "SELECT DISTINCT ip_address FROM user_sessions WHERE email_account_id = "
        "(SELECT id FROM email_accounts WHERE email = %s) AND created_at > NOW() - INTERVAL 30 DAY",
        (email,)
    )

    if ip not in [row["ip_address"] for row in known_ips]:
        alert(f"New login IP for {email}: {ip}")
```

### File Integrity

Monitor config files for unauthorized changes:

```bash
# Create baseline hashes
find config/ -type f -exec md5sum {} \; > /opt/mailyte/config_hashes.md5

# Check for changes (run via cron)
md5sum -c /opt/mailyte/config_hashes.md5 2>&1 | grep "FAILED"
```

## Log Analysis

### Centralized Logging

Forward all logs to a central location for analysis:

```yaml
# docker-compose.yml - logging config
services:
  postfix:
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
        tag: "postfix"
```

### Useful Log Queries

```bash
# All authentication failures in the last hour
docker logs dovecot --since 1h 2>&1 | grep -i "auth.*fail\|password mismatch"

# All rejected emails
docker logs postfix --since 1h 2>&1 | grep "NOQUEUE: reject"

# All TLS errors
docker logs postfix --since 1h 2>&1 | grep -i "tls.*error\|ssl.*error"

# API errors
docker logs api --since 1h 2>&1 | grep "ERROR\|CRITICAL"

# Rspamd rejections
docker logs rspamd --since 1h 2>&1 | grep "reject"
```

### Automated Log Monitoring

Create a script that runs every 5 minutes:

```bash
#!/bin/bash
# scripts/security_check.sh

# Check for brute force
FAILED_LOGINS=$(docker logs dovecot --since 5m 2>&1 | grep -c "Password mismatch")
if [ "$FAILED_LOGINS" -gt 50 ]; then
  echo "ALERT: $FAILED_LOGINS failed logins in 5 minutes" | \
    curl -X POST -d @- "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK"
fi

# Check for spam floods
OUTBOUND=$(docker logs postfix --since 5m 2>&1 | grep -c "status=sent")
if [ "$OUTBOUND" -gt 500 ]; then
  echo "ALERT: $OUTBOUND emails sent in 5 minutes" | \
    curl -X POST -d @- "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK"
fi

# Check for configuration changes
if ! md5sum -c /opt/mailyte/config_hashes.md5 &>/dev/null; then
  echo "ALERT: Configuration files have been modified" | \
    curl -X POST -d @- "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK"
fi
```

## Security Dashboard

Create a Grafana dashboard with these panels:

| Panel | Query | Type |
|-------|-------|------|
| Failed Logins (5m rate) | `rate(mailyte_auth_failures_total[5m])` | Time series |
| Auth Success vs Failure | `mailyte_auth_failures_total` vs `mailyte_auth_successes_total` | Pie chart |
| Rejected Emails | `rate(mailyte_emails_rejected_total[5m])` | Time series |
| Banned IPs (Fail2ban) | `mailyte_fail2ban_banned_ips` | Stat |
| Active Sessions by Country | `mailyte_active_sessions{country!=""}` | Geo map |
| Spam Score Distribution | `mailyte_spam_score_bucket` | Heatmap |
