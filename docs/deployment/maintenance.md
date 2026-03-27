# Maintenance

Routine tasks that keep Mailyte healthy — schedule them and forget about them (mostly).

## Maintenance Schedule

| Task | Frequency | Automated? | Downtime? |
|------|-----------|-----------|-----------|
| Log rotation | Daily | Yes | No |
| Database cleanup | Weekly | Yes (cron) | No |
| Queue flushing | As needed | No | No |
| Certificate renewal | Every 60-90 days | Yes (certbot) | No |
| Docker image updates | Monthly | No | Brief |
| OS security patches | Weekly | Yes (unattended-upgrades) | Maybe (reboot) |
| Disk usage check | Daily | Yes (alert) | No |
| Backup verification | Monthly | No | No |

## Log Rotation

Docker logs can grow fast, especially for Postfix. Configure Docker's log driver to handle this automatically.

### Docker Daemon Config

```json
// /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
```

```bash
# Apply the config
sudo systemctl restart docker
```

This limits each container to 5 log files of 50MB each (250MB max per container).

### Per-Service Log Limits

Override in Docker Compose for chatty services:

```yaml
services:
  postfix:
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "10"
  api:
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

### Manual Log Cleanup

```bash
# See how much space logs are using
sudo du -sh /var/lib/docker/containers/*/

# Truncate a specific container's log (doesn't stop logging)
sudo truncate -s 0 /var/lib/docker/containers/<container-id>/<container-id>-json.log
```

## Database Cleanup

Over time, MySQL accumulates data that's no longer needed — old logs, expired sessions, soft-deleted records.

### Automated Cleanup Script

```bash
#!/bin/bash
# scripts/db-cleanup.sh

MYSQL_CMD="docker compose exec -T mysql mysql -u root -p${MYSQL_ROOT_PASSWORD} ${MYSQL_DATABASE}"

echo "$(date): Starting database cleanup"

# Remove expired sessions (older than 30 days)
$MYSQL_CMD -e "DELETE FROM sessions WHERE expires_at < NOW() - INTERVAL 30 DAY;"

# Clean up old API logs (older than 90 days)
$MYSQL_CMD -e "DELETE FROM api_logs WHERE created_at < NOW() - INTERVAL 90 DAY;"

# Remove old email tracking data (older than 180 days)
$MYSQL_CMD -e "DELETE FROM email_events WHERE created_at < NOW() - INTERVAL 180 DAY;"

# Clean up soft-deleted records (older than 30 days)
$MYSQL_CMD -e "DELETE FROM emails WHERE deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL 30 DAY;"

# Optimize tables after large deletes
$MYSQL_CMD -e "OPTIMIZE TABLE sessions, api_logs, email_events, emails;"

echo "$(date): Database cleanup complete"
```

Schedule it:

```bash
# Run weekly on Sundays at 3 AM
echo "0 3 * * 0 root /opt/mailyte/scripts/db-cleanup.sh >> /var/log/mailyte-cleanup.log 2>&1" \
  | sudo tee /etc/cron.d/mailyte-cleanup
```

## Queue Management

### Viewing the Queue

```bash
# See what's in the Postfix queue
docker compose exec postfix postqueue -p

# Count queued messages
docker compose exec postfix postqueue -p | grep -c "^[A-F0-9]"

# See the deferred queue
docker compose exec postfix postqueue -p | grep -c "MAILER-DAEMON"
```

### Flushing the Queue

```bash
# Retry all deferred messages now
docker compose exec postfix postqueue -f

# Delete all messages in the queue (nuclear option)
docker compose exec postfix postsuper -d ALL

# Delete only deferred messages
docker compose exec postfix postsuper -d ALL deferred

# Delete messages to a specific domain
docker compose exec postfix mailq | grep "example.com" | awk '{print $1}' | \
  xargs -I {} docker compose exec postfix postsuper -d {}
```

> **Warning:** Only flush the full queue if you know what you're doing. If delivery is failing for a reason (DNS issue, blocked IP), flushing just generates more bounces.

### Worker Queue Management

```bash
# Check worker queue depth
docker compose exec redis redis-cli -a $REDIS_PASSWORD llen email_queue
docker compose exec redis redis-cli -a $REDIS_PASSWORD llen retry_queue
docker compose exec redis redis-cli -a $REDIS_PASSWORD llen dead_letter_queue

# Clear the dead letter queue
docker compose exec redis redis-cli -a $REDIS_PASSWORD del dead_letter_queue

# Requeue dead letters for retry
docker compose exec redis redis-cli -a $REDIS_PASSWORD --pipe <<'EOF'
RPOPLPUSH dead_letter_queue retry_queue
EOF
```

## Certificate Renewal

### Automated (Recommended)

```bash
# Certbot auto-renewal with post-hook to reload services
sudo certbot renew --deploy-hook "
  docker compose -f /opt/mailyte/docker-compose.yml exec postfix postfix reload
  docker compose -f /opt/mailyte/docker-compose.yml exec dovecot doveadm reload
"
```

### Manual Renewal

```bash
# Renew the certificate
sudo certbot renew

# Restart services to pick up the new cert
docker compose restart postfix dovecot

# Verify the new certificate
echo | openssl s_client -connect mail.yourdomain.com:993 2>/dev/null \
  | openssl x509 -noout -dates
```

## Docker Updates

### Updating Mailyte Images

```bash
# Pull the latest images
docker compose pull

# Restart with new images (one at a time for zero downtime)
docker compose up -d --no-deps postfix
docker compose up -d --no-deps dovecot
docker compose up -d --no-deps api
docker compose up -d --no-deps worker

# Or all at once (brief downtime)
docker compose up -d
```

### Cleaning Up Old Images

```bash
# Remove unused images
docker image prune -a --filter "until=168h"  # Older than 7 days

# Full cleanup (images, containers, volumes, networks)
docker system prune -a --volumes  # CAREFUL: removes unused volumes too

# Safer: just dangling images and stopped containers
docker system prune
```

## OS Updates

```bash
# Install security updates only
sudo apt update && sudo apt upgrade -y --only-upgrade

# Set up automatic security updates
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## Disk Space Management

```bash
# Check overall disk usage
df -h

# Docker-specific usage
docker system df

# Find the biggest offenders
sudo du -sh /var/lib/docker/volumes/* | sort -rh | head -10

# Mail storage per user (if using Maildir)
du -sh /var/mail/*/Maildir/ | sort -rh | head -20
```

### When Disk Gets Low

1. Clean Docker: `docker system prune`
2. Rotate logs: truncate or delete old log files
3. Clean mail queue: `docker compose exec postfix postsuper -d ALL deferred`
4. Run database cleanup: `./scripts/db-cleanup.sh`
5. Check mail storage: enforce user quotas if not already set
6. Add more disk: expand the volume or add a new one

## Health Check Script

Run this as part of your weekly maintenance:

```bash
#!/bin/bash
# scripts/weekly-check.sh

echo "=== Mailyte Weekly Health Check ==="
echo "Date: $(date)"
echo ""

echo "--- Services ---"
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo ""
echo "--- Disk Usage ---"
df -h / /var/lib/docker 2>/dev/null

echo ""
echo "--- Docker Resources ---"
docker system df

echo ""
echo "--- Mail Queue ---"
docker compose exec -T postfix postqueue -p | tail -1

echo ""
echo "--- Database Size ---"
docker compose exec -T mysql mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "
SELECT table_schema AS 'Database',
  ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size (MB)'
FROM information_schema.tables
GROUP BY table_schema;" 2>/dev/null

echo ""
echo "--- Certificate Expiry ---"
echo | openssl s_client -connect localhost:993 2>/dev/null \
  | openssl x509 -noout -enddate

echo ""
echo "--- Backup Status ---"
ls -lh /opt/mailyte/backups/ | tail -5
```
