---
title: Compliance
description: GDPR data handling, data retention policies, right to erasure, and audit logging for Mailyte.
---

# Compliance

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


If you handle email for European users, GDPR applies. Even if it doesn't, these practices are good hygiene. This page covers what Mailyte does for compliance and what you need to configure.

## GDPR Considerations

Email contains personal data by definition — names, addresses, message content. Under GDPR:

- You need a **lawful basis** for processing email (usually legitimate interest or contract)
- Users have the **right to access** their data
- Users have the **right to erasure** (right to be forgotten)
- You must **report data breaches** within 72 hours
- Data should be **minimized** — don't keep what you don't need

## Data Retention Policies

Mailyte stores several categories of data. Set retention periods for each:

### Configurable Retention

| Data Type | Table | Suggested Retention | Notes |
|-----------|-------|-------------------|-------|
| Mail logs | `mail_logs` | 90 days | Needed for troubleshooting |
| Tracking events | `email_tracking` | 90 days | Opens, clicks, bounces |
| Tracking stats | `tracking_statistics` | 1 year | Aggregated, less sensitive |
| Webhook logs | `webhook_delivery_logs` | 30 days | Delivery confirmations |
| Health checks | `health_checks` | 7 days | System monitoring |
| Service metrics | `service_metrics` | 30 days | Performance data |
| Usage history | `usage_history` | 90 days | Billing data |
| AI transactions | `ai_transactions` | 90 days | RAG usage tracking |
| User sessions | `user_sessions` | Expire + 7 days | Login tracking |

### Implementing Cleanup

Set up a cron job or use the admin API to clean old data:

```bash
# Clean tracking data older than 90 days
docker exec -it mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "
DELETE FROM email_tracking WHERE timestamp < NOW() - INTERVAL 90 DAY LIMIT 50000;
DELETE FROM mail_logs WHERE timestamp < NOW() - INTERVAL 90 DAY LIMIT 50000;
DELETE FROM webhook_delivery_logs WHERE created_at < NOW() - INTERVAL 30 DAY LIMIT 50000;
DELETE FROM health_checks WHERE timestamp < NOW() - INTERVAL 7 DAY LIMIT 50000;
DELETE FROM service_metrics WHERE timestamp < NOW() - INTERVAL 30 DAY LIMIT 50000;
"
```

!!! tip "Batch deletes"
    Always use `LIMIT` to avoid long-running transactions. Run in a loop until zero rows are affected.

### Automated Cleanup Script

```bash
#!/bin/bash
# scripts/cleanup_retention.sh
# Run daily via cron

DB_CMD="docker exec mysql mysql -u root -p${DB_ROOT_PASSWORD} mailserver -e"

echo "Cleaning old data..."

# Delete in batches
for table_config in \
  "email_tracking:timestamp:90" \
  "mail_logs:timestamp:90" \
  "webhook_delivery_logs:created_at:30" \
  "health_checks:timestamp:7" \
  "service_metrics:timestamp:30" \
  "usage_history:recorded_at:90"; do

  table=$(echo $table_config | cut -d: -f1)
  column=$(echo $table_config | cut -d: -f2)
  days=$(echo $table_config | cut -d: -f3)

  while true; do
    deleted=$($DB_CMD "DELETE FROM $table WHERE $column < NOW() - INTERVAL $days DAY LIMIT 10000; SELECT ROW_COUNT();" | tail -1)
    echo "$table: deleted $deleted rows"
    [ "$deleted" -eq "0" ] && break
    sleep 1
  done
done

echo "Cleanup complete"
```

## Right to Erasure

When a user requests deletion of their data, you need to remove:

1. **Email content** — delete the mailbox and all stored emails
2. **Tracking data** — delete tracking events for their email address
3. **Log entries** — delete or anonymize log entries containing their address
4. **Suppression lists** — remove their entries
5. **Session data** — delete login sessions

### Erasure Script

```python
def erase_user_data(email: str, org_id: str):
    """Remove all data for a specific email address (GDPR Art. 17)."""

    # 1. Delete the mailbox (removes stored email from disk)
    requests.post(f"{API}/delete/mailbox", headers=HEADERS, json=[email])

    # 2. Delete tracking events
    db.execute(
        "DELETE FROM email_tracking WHERE recipient = %s AND organization_id = %s",
        (email, org_id)
    )

    # 3. Anonymize mail logs (keep for operational needs, remove PII)
    db.execute(
        "UPDATE mail_logs SET sender = 'redacted', recipient = 'redacted', subject = NULL "
        "WHERE (sender = %s OR recipient = %s) AND organization_id = %s",
        (email, email, org_id)
    )

    # 4. Delete suppression entries
    db.execute(
        "DELETE FROM email_suppressions WHERE email = %s AND organization_id = %s",
        (email, org_id)
    )

    # 5. Delete sessions
    db.execute(
        "DELETE s FROM user_sessions s "
        "JOIN email_accounts a ON s.email_account_id = a.id "
        "WHERE a.email = %s",
        (email,)
    )

    # 6. Log the erasure (for compliance records)
    db.execute(
        "INSERT INTO audit_log (action, entity_type, entity_id, performed_by, timestamp) "
        "VALUES ('erasure', 'email_account', %s, 'gdpr_request', NOW())",
        (email,)
    )
```

## Right to Access (Data Export)

Users can request a copy of all their data:

```python
def export_user_data(email: str) -> dict:
    """Export all data for a user (GDPR Art. 15)."""
    account = db.execute("SELECT * FROM email_accounts WHERE email = %s", (email,))
    tracking = db.execute("SELECT * FROM email_tracking WHERE recipient = %s", (email,))
    sessions = db.execute(
        "SELECT s.* FROM user_sessions s JOIN email_accounts a ON s.email_account_id = a.id WHERE a.email = %s",
        (email,)
    )

    return {
        "account": account,
        "tracking_events": tracking,
        "sessions": sessions,
        "exported_at": datetime.now().isoformat(),
    }
```

## Audit Logging

Track who did what and when. Log these events:

| Event | What to log |
|-------|-------------|
| API key created/revoked | Key ID, admin who created it |
| Organization created/deleted | Org ID, who performed it |
| Domain added/removed | Domain name, org, who |
| Mailbox created/deleted | Email, org, who |
| Password changed | Email, who (not the password!) |
| Login success/failure | Email, IP, timestamp, method |
| Data export requested | Email, who requested |
| Data erasure performed | Email, who requested |
| Configuration changed | What changed, who, old/new value |

### Audit Log Table

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    performed_by VARCHAR(255) NOT NULL,
    ip_address VARCHAR(45) NULL,
    details JSON NULL,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_action (action),
    INDEX idx_audit_entity (entity_type, entity_id),
    INDEX idx_audit_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

## Data Processing Agreement

If you're running Mailyte as a service for others, you need a DPA (Data Processing Agreement) with your customers. It should cover:

- What data you process
- Why you process it
- How long you keep it
- Security measures in place
- Sub-processors (cloud providers, etc.)
- Breach notification procedures

## Checklist

- [x] Data retention policies defined and documented
- [x] Automated cleanup scripts running
- [x] Right to erasure process implemented
- [x] Right to access (data export) implemented
- [x] Audit logging enabled
- [x] DPA prepared for customers
- [x] Breach notification process documented
- [x] Privacy policy updated
- [x] Consent mechanisms in place (where applicable)
