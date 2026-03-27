---
title: Backup Automation
description: Set up automated backups with cron, sync to S3, and manage retention policies for your Mailyte server.
---

# Backup Automation

Losing email data is catastrophic. This guide sets up automated backups for every piece of your Mailyte deployment — database, mail storage, DKIM keys, SSL certs, and configuration.

## What Needs Backing Up

| Component | Location | Priority | Frequency |
|-----------|----------|----------|-----------|
| MySQL database | Docker volume `mysql_data` | Critical | Every 6 hours |
| Mail storage | `./storage/mail_data/` | Critical | Daily |
| DKIM keys | `./storage/dkim_keys/` | Critical | Daily |
| SSL certificates | `./storage/ssl_certs/`, `./storage/ssl_private/` | High | Daily |
| Configuration | `./config/` | High | On change |
| Redis data | Docker volume `redis_data` | Medium | Daily |
| Rspamd data | Docker volume `rspamd_data` | Medium | Weekly |
| Qdrant vectors | Docker volume `qdrant_data` | Low | Weekly |

## Backup Script

Create a comprehensive backup script:

```bash
#!/bin/bash
# scripts/backup.sh — Mailyte automated backup

set -euo pipefail

# Configuration
BACKUP_DIR="/opt/mailyte/backups"
MAILYTE_DIR="/home/confidence/Workspace/Projects/TechiesAfrica/Internal/Mailyte/mailyte-email-server"
RETENTION_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${DATE}"

# S3 configuration (optional)
S3_BUCKET="${AWS_BUCKET:-}"
S3_PREFIX="${AWS_S3_PREFIX:-mailyte}/backups"

echo "Starting backup: ${DATE}"
mkdir -p "${BACKUP_PATH}"

# -----------------------------------------------
# 1. MySQL Database
# -----------------------------------------------
echo "Backing up MySQL..."
docker exec mysql mysqldump \
  -u root \
  -p"${DB_ROOT_PASSWORD:-rootpassword}" \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  "${DB_NAME:-mailserver}" \
  | gzip > "${BACKUP_PATH}/mysql_${DATE}.sql.gz"

echo "MySQL backup: $(du -sh ${BACKUP_PATH}/mysql_${DATE}.sql.gz | cut -f1)"

# -----------------------------------------------
# 2. Mail Storage
# -----------------------------------------------
echo "Backing up mail storage..."
tar czf "${BACKUP_PATH}/mail_data_${DATE}.tar.gz" \
  -C "${MAILYTE_DIR}/storage" mail_data/

echo "Mail storage backup: $(du -sh ${BACKUP_PATH}/mail_data_${DATE}.tar.gz | cut -f1)"

# -----------------------------------------------
# 3. DKIM Keys
# -----------------------------------------------
echo "Backing up DKIM keys..."
tar czf "${BACKUP_PATH}/dkim_keys_${DATE}.tar.gz" \
  -C "${MAILYTE_DIR}/storage" dkim_keys/

# -----------------------------------------------
# 4. SSL Certificates
# -----------------------------------------------
echo "Backing up SSL certificates..."
tar czf "${BACKUP_PATH}/ssl_${DATE}.tar.gz" \
  -C "${MAILYTE_DIR}/storage" ssl_certs/ ssl_private/

# -----------------------------------------------
# 5. Configuration
# -----------------------------------------------
echo "Backing up configuration..."
tar czf "${BACKUP_PATH}/config_${DATE}.tar.gz" \
  -C "${MAILYTE_DIR}" config/ .env docker-compose.yml

# -----------------------------------------------
# 6. Redis (optional)
# -----------------------------------------------
echo "Backing up Redis..."
docker exec redis redis-cli BGSAVE
sleep 2
docker cp redis:/data/dump.rdb "${BACKUP_PATH}/redis_${DATE}.rdb" 2>/dev/null || echo "Redis backup skipped"

# -----------------------------------------------
# 7. Create manifest
# -----------------------------------------------
echo "Creating manifest..."
cat > "${BACKUP_PATH}/manifest.json" <<EOF
{
  "date": "${DATE}",
  "hostname": "$(hostname)",
  "components": {
    "mysql": "mysql_${DATE}.sql.gz",
    "mail_data": "mail_data_${DATE}.tar.gz",
    "dkim_keys": "dkim_keys_${DATE}.tar.gz",
    "ssl": "ssl_${DATE}.tar.gz",
    "config": "config_${DATE}.tar.gz",
    "redis": "redis_${DATE}.rdb"
  },
  "sizes": {
$(cd "${BACKUP_PATH}" && find . -type f -not -name manifest.json -exec sh -c 'echo "    \"$(basename {})\": \"$(du -sh {} | cut -f1)\"," ' \;)
    "_total": "$(du -sh ${BACKUP_PATH} | cut -f1)"
  }
}
EOF

# -----------------------------------------------
# 8. Sync to S3 (if configured)
# -----------------------------------------------
if [ -n "${S3_BUCKET}" ]; then
  echo "Syncing to S3..."
  aws s3 sync "${BACKUP_PATH}" "s3://${S3_BUCKET}/${S3_PREFIX}/${DATE}/" \
    --storage-class STANDARD_IA
  echo "S3 sync complete"
fi

# -----------------------------------------------
# 9. Clean old backups
# -----------------------------------------------
echo "Cleaning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -maxdepth 1 -type d -mtime +${RETENTION_DAYS} -exec rm -rf {} \;

# S3 lifecycle handles remote retention (see below)

echo "Backup complete: ${BACKUP_PATH}"
echo "Total size: $(du -sh ${BACKUP_PATH} | cut -f1)"
```

Make it executable:

```bash
chmod +x scripts/backup.sh
```

## Cron Schedule

```bash
# Edit crontab
crontab -e
```

Add these entries:

```cron
# Database backup every 6 hours
0 */6 * * * /opt/mailyte/scripts/backup.sh >> /var/log/mailyte-backup.log 2>&1

# Full backup daily at 2am
0 2 * * * FULL_BACKUP=1 /opt/mailyte/scripts/backup.sh >> /var/log/mailyte-backup.log 2>&1
```

!!! tip "Log rotation"
    Add a logrotate config for the backup log:
    ```
    /var/log/mailyte-backup.log {
        weekly
        rotate 4
        compress
        missingok
        notifempty
    }
    ```

## S3 Sync Configuration

### AWS CLI Setup

```bash
# Install AWS CLI
pip install awscli

# Configure credentials
aws configure
# Or use environment variables:
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
```

### S3 Lifecycle Policy

Set up automatic S3 lifecycle rules for cost-effective long-term storage:

```json
{
  "Rules": [
    {
      "ID": "MailyteBackupLifecycle",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "mailyte/backups/"
      },
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "STANDARD_IA"
        },
        {
          "Days": 90,
          "StorageClass": "GLACIER"
        }
      ],
      "Expiration": {
        "Days": 365
      }
    }
  ]
}
```

Apply it:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket your-backup-bucket \
  --lifecycle-configuration file://lifecycle.json
```

### Cost Estimation

| Volume | Daily Backup Size | Monthly S3 Cost (approx.) |
|--------|------------------|---------------------------|
| 10K mailboxes | ~5 GB | ~$3 |
| 50K mailboxes | ~25 GB | ~$15 |
| 100K mailboxes | ~50 GB | ~$30 |

Glacier storage is ~$0.004/GB/month, so 90+ day old backups cost almost nothing.

## Restore Procedures

### Restore MySQL

```bash
# Stop services that write to the database
docker compose stop api tracking webhooks analytics queue-manager

# Restore
gunzip -c backups/20260325_020000/mysql_20260325_020000.sql.gz | \
  docker exec -i mysql mysql -u root -p"${DB_ROOT_PASSWORD}" "${DB_NAME:-mailserver}"

# Restart services
docker compose start api tracking webhooks analytics queue-manager
```

### Restore Mail Storage

```bash
# Stop Dovecot to prevent file conflicts
docker compose stop dovecot

# Restore
tar xzf backups/20260325_020000/mail_data_20260325_020000.tar.gz \
  -C /path/to/mailyte/storage/

# Fix permissions
chown -R 5000:5000 /path/to/mailyte/storage/mail_data/

# Restart Dovecot
docker compose start dovecot
```

### Restore DKIM Keys

```bash
tar xzf backups/20260325_020000/dkim_keys_20260325_020000.tar.gz \
  -C /path/to/mailyte/storage/

# Restart Rspamd to pick up keys
docker compose restart rspamd
```

### Restore from S3

```bash
# Download a specific backup
aws s3 sync "s3://your-bucket/mailyte/backups/20260325_020000/" \
  /opt/mailyte/backups/20260325_020000/

# Then follow the restore procedures above
```

## Monitoring Backups

### Health Check Script

```bash
#!/bin/bash
# scripts/check_backup.sh — verify backup health

BACKUP_DIR="/opt/mailyte/backups"
MAX_AGE_HOURS=12

latest=$(ls -td ${BACKUP_DIR}/*/ 2>/dev/null | head -1)

if [ -z "$latest" ]; then
  echo "CRITICAL: No backups found"
  exit 2
fi

age_hours=$(( ($(date +%s) - $(stat -c %Y "$latest")) / 3600 ))

if [ $age_hours -gt $MAX_AGE_HOURS ]; then
  echo "WARNING: Latest backup is ${age_hours}h old (max: ${MAX_AGE_HOURS}h)"
  exit 1
fi

# Check that key files exist
for f in mysql_*.sql.gz mail_data_*.tar.gz dkim_keys_*.tar.gz; do
  if ! ls ${latest}/${f} &>/dev/null; then
    echo "WARNING: Missing ${f} in latest backup"
    exit 1
  fi
done

echo "OK: Latest backup is ${age_hours}h old"
exit 0
```

### Prometheus Integration

Expose backup age as a metric:

```bash
# Add to your node-exporter textfile collector
echo "mailyte_backup_age_hours $age_hours" > /var/lib/node-exporter/textfile/backup.prom
```

Alert if backups are stale:

```yaml
- alert: BackupStale
  expr: mailyte_backup_age_hours > 24
  for: 1h
  labels:
    severity: critical
  annotations:
    summary: "No successful backup in {{ $value }} hours"
```

## Backup Checklist

- [x] Backup script created and tested
- [x] Cron jobs scheduled
- [x] S3 sync configured (if using cloud storage)
- [x] S3 lifecycle policy applied
- [x] Restore procedures tested (at least once!)
- [x] Backup monitoring in place
- [x] Alert on stale backups
- [x] Log rotation configured
