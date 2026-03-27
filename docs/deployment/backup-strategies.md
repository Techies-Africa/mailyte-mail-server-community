# Backup Strategies

What to back up, how often, and where to store it — because the question isn't *if* you'll need a backup, it's *when*.

## What Needs Backing Up

| Data | Priority | Size | Changes How Often |
|------|----------|------|-------------------|
| MySQL database | Critical | Medium | Constantly |
| Mail storage (Maildir) | Critical | Large | Constantly |
| Configuration files | High | Small | Rarely |
| TLS certificates | High | Tiny | Every 60-90 days |
| DKIM keys | High | Tiny | Rarely |
| Rspamd learned data | Medium | Small | Daily |
| Docker Compose files | Medium | Tiny | Occasionally |
| `.env` file | High | Tiny | Occasionally |

Things you don't need to back up:
- Redis data (rebuilt from the database on restart)
- Postfix queue (transient by nature)
- Prometheus data (nice to have, not critical)
- Docker images (pulled from registry)

## Backup Schedule

| Backup Type | Frequency | Retention |
|-------------|-----------|-----------|
| MySQL full dump | Daily | 30 days |
| MySQL binary log | Continuous | 7 days |
| Mail storage incremental | Daily | 30 days |
| Mail storage full | Weekly | 90 days |
| Configuration snapshot | On change + weekly | 90 days |

## MySQL Backup

### Option 1: mysqldump (Simple, good for small databases)

```bash
#!/bin/bash
# scripts/backup-mysql.sh

BACKUP_DIR="/opt/mailyte/backups/mysql"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/mailyte_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

# Dump and compress
docker compose exec -T mysql mysqldump \
  -u root -p"${MYSQL_ROOT_PASSWORD}" \
  --single-transaction \
  --routines \
  --triggers \
  --databases mailyte \
  | gzip > "$BACKUP_FILE"

# Verify the backup isn't empty
if [ -s "$BACKUP_FILE" ]; then
  echo "$(date): MySQL backup created: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"
else
  echo "$(date): ERROR: MySQL backup is empty!"
  rm -f "$BACKUP_FILE"
  exit 1
fi

# Remove backups older than 30 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete
```

### Option 2: Percona XtraBackup (Better for large databases)

```bash
# Install xtrabackup in the MySQL container
docker compose exec mysql apt-get install -y percona-xtrabackup-80

# Full backup
docker compose exec mysql xtrabackup \
  --backup \
  --target-dir=/var/backups/mysql/full \
  --user=root \
  --password="${MYSQL_ROOT_PASSWORD}"

# Incremental backup (based on the last full)
docker compose exec mysql xtrabackup \
  --backup \
  --target-dir=/var/backups/mysql/inc1 \
  --incremental-basedir=/var/backups/mysql/full \
  --user=root \
  --password="${MYSQL_ROOT_PASSWORD}"
```

## Mail Storage Backup

### Using rsync (Incremental)

```bash
#!/bin/bash
# scripts/backup-mail.sh

BACKUP_DIR="/opt/mailyte/backups/mail"
TIMESTAMP=$(date +%Y%m%d)
MAIL_VOLUME=$(docker volume inspect mailyte_mail-data --format '{{ .Mountpoint }}')

mkdir -p "$BACKUP_DIR"

# Incremental backup with rsync
rsync -av --delete \
  "$MAIL_VOLUME/" \
  "$BACKUP_DIR/latest/"

# Create a dated snapshot using hard links (saves space)
cp -al "$BACKUP_DIR/latest" "$BACKUP_DIR/snapshot_${TIMESTAMP}"

# Remove snapshots older than 30 days
find "$BACKUP_DIR" -maxdepth 1 -name "snapshot_*" -mtime +30 -exec rm -rf {} +

echo "$(date): Mail backup complete"
```

### Using tar (Full archive)

```bash
# Weekly full backup
MAIL_VOLUME=$(docker volume inspect mailyte_mail-data --format '{{ .Mountpoint }}')
tar czf /opt/mailyte/backups/mail/mail_full_$(date +%Y%m%d).tar.gz \
  -C "$MAIL_VOLUME" .
```

## Configuration Backup

```bash
#!/bin/bash
# scripts/backup-config.sh

BACKUP_DIR="/opt/mailyte/backups/config"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

tar czf "$BACKUP_DIR/config_${TIMESTAMP}.tar.gz" \
  --exclude='.env' \
  docker-compose.yml \
  docker-compose.monitoring.yml \
  config/ \
  monitoring/ \
  scripts/

# Back up .env separately (encrypted)
gpg --symmetric --cipher-algo AES256 \
  --output "$BACKUP_DIR/env_${TIMESTAMP}.gpg" \
  .env

# Keep 90 days
find "$BACKUP_DIR" -mtime +90 -delete

echo "$(date): Config backup complete"
```

## Remote Storage

Don't keep backups only on the same server. Use remote storage.

### Amazon S3

```bash
#!/bin/bash
# scripts/sync-to-s3.sh

AWS_BUCKET="s3://your-bucket/mailyte-backups"
BACKUP_DIR="/opt/mailyte/backups"

# Sync all backups to S3
aws s3 sync "$BACKUP_DIR" "$AWS_BUCKET" \
  --storage-class STANDARD_IA \
  --delete

# For older backups, use Glacier
aws s3api put-bucket-lifecycle-configuration \
  --bucket your-bucket \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "archive-old-backups",
      "Filter": {"Prefix": "mailyte-backups/"},
      "Status": "Enabled",
      "Transitions": [{
        "Days": 30,
        "StorageClass": "GLACIER"
      }],
      "Expiration": {"Days": 365}
    }]
  }'
```

### Azure Blob Storage

```bash
# Install Azure CLI
# az login

az storage blob upload-batch \
  --destination mailyte-backups \
  --source /opt/mailyte/backups \
  --account-name yourstorageaccount \
  --overwrite
```

### Rsync to Remote Server

```bash
# Sync to a backup server
rsync -avz --delete \
  /opt/mailyte/backups/ \
  backup-user@backup-server:/backups/mailyte/
```

## Full Backup Script

Wraps everything together:

```bash
#!/bin/bash
# scripts/backup.sh — Full Mailyte backup

set -e
LABEL="${1:-daily}"
BACKUP_ROOT="/opt/mailyte/backups"
LOG="/var/log/mailyte-backup.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG"; }

log "Starting $LABEL backup"

# 1. MySQL
log "Backing up MySQL..."
./scripts/backup-mysql.sh >> "$LOG" 2>&1

# 2. Mail storage
log "Backing up mail storage..."
./scripts/backup-mail.sh >> "$LOG" 2>&1

# 3. Configuration
log "Backing up configuration..."
./scripts/backup-config.sh >> "$LOG" 2>&1

# 4. Sync to remote (uncomment the one you use)
# log "Syncing to S3..."
# ./scripts/sync-to-s3.sh >> "$LOG" 2>&1

# log "Syncing to remote server..."
# rsync -avz $BACKUP_ROOT/ backup-user@backup-server:/backups/mailyte/ >> "$LOG" 2>&1

log "Backup complete. Total size: $(du -sh $BACKUP_ROOT | cut -f1)"
```

Schedule it:

```bash
# Daily at 2 AM
echo "0 2 * * * root /opt/mailyte/scripts/backup.sh daily >> /var/log/mailyte-backup.log 2>&1" \
  | sudo tee /etc/cron.d/mailyte-backup

# Weekly full on Sundays at 1 AM
echo "0 1 * * 0 root /opt/mailyte/scripts/backup.sh weekly >> /var/log/mailyte-backup.log 2>&1" \
  | sudo tee -a /etc/cron.d/mailyte-backup
```

## Verifying Backups

A backup you haven't tested is not a backup.

```bash
# Test MySQL restore to a temporary database
docker run --rm -v /opt/mailyte/backups/mysql:/backups mysql:8.0 \
  bash -c "
    mysqld --skip-grant-tables &
    sleep 10
    zcat /backups/mailyte_latest.sql.gz | mysql
    mysql -e 'SELECT COUNT(*) FROM mailyte.users;'
    echo 'Restore test passed'
  "

# Test config archive
tar tzf /opt/mailyte/backups/config/config_latest.tar.gz | head -20
```

> **Tip:** Schedule a monthly "restore drill" where you restore from backup to a test environment. It takes an hour and saves you days of panic during a real disaster.
