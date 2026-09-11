# Maintenance

Routine tasks that keep Mailyte healthy — schedule them and forget about them (mostly).

## Maintenance Schedule

| Task | Frequency | Automated? | Downtime? |
|------|-----------|-----------|-----------|
| Container log rotation | Continuous | Yes (prod compose logging limits) | No |
| `logs/` file rotation | Weekly | Yes (host logrotate, once configured) | No |
| Database cleanup | As needed | No | No |
| Queue flushing | As needed | No | No |
| Certificate renewal | Automatic | Yes (cert_manager, checks every 6h) | No |
| Base image refresh (`PULL_BASE=1`) | Weekly-monthly | No | Brief |
| OS security patches | Weekly | Yes (unattended-upgrades) | Maybe (reboot) |
| Disk usage check | Daily | Yes (alert) | No |
| Backup verification | Monthly | No | No |

## Log Rotation

There are two kinds of logs, handled differently:

### Container stdout/stderr

`docker-compose.prod.yml` already caps every service at 3 json-files of 10 MB each (30 MB per container) — no daemon.json change is needed for the production stack. If you also want a host-wide default for other containers:

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
sudo systemctl restart docker   # brief downtime for every container
```

### File logs under `logs/`

Postfix, Dovecot, Rspamd, and the workers write real log files to bind-mounted directories in the project tree — most importantly `logs/mailer/postfix/mail.log`, which is the only record of deliveries, deferrals, and bounces (and the input to the `log_ingestor` service). These are **not** rotated by Docker.

```bash
# See how much space they use
du -sh logs/*/*

# Rotate with host logrotate — copytruncate, so postfix keeps its open
# file handle and log_ingestor's read offset stays valid
cat <<'EOF' | sudo tee /etc/logrotate.d/mailyte
/opt/mailyte/mailyte-email-server/logs/mailer/*/*.log {
  weekly
  rotate 8
  compress
  delaycompress
  missingok
  notifempty
  copytruncate
}
EOF
```

(Adjust the path to your checkout; on deploy-pipeline hosts `logs/` is a symlink to the shared root — point logrotate at the resolved path.)

## Database Cleanup

Over time, MySQL accumulates data that's no longer needed — old mail logs, expired sessions, stale tracking rows. There is no bundled cleanup script yet; the growth tables and a safe manual pattern:

| Table | Grows with | Safe to prune |
|-------|-----------|---------------|
| `mail_logs` | Every message | Rows older than your log-retention policy |
| `email_tracking` | Every tracked open/click | Rows older than your analytics window |
| `user_sessions`, `web_sessions`, `mailbox_sessions` | Logins | Expired rows |
| `health_checks`, `service_metrics` | Monitoring | Anything old |

```bash
# Take a backup first -- always
./scripts/backup.sh --mysql-only

# Then prune, e.g. tracking rows older than 180 days
docker compose exec -T mysql sh -c \
  'mysql -u root -p"$(cat /run/secrets/db_root_password)" "${MYSQL_DATABASE:-mailserver}"' <<'SQL'
DELETE FROM email_tracking WHERE created_at < NOW() - INTERVAL 180 DAY LIMIT 100000;
SQL
```

Delete in bounded batches (`LIMIT`) and repeat — a single unbounded `DELETE` on a large table locks it for the duration.

## Queue Management

### Viewing the Queue

```bash
# See what's in the Postfix queue
docker compose exec postfix postqueue -p

# Count queued messages
docker compose exec postfix postqueue -p | grep -c "^[A-F0-9]"

# Count deferred messages (queue IDs without the active-queue '*' marker)
docker compose exec postfix postqueue -p | grep -c "^[A-F0-9]\{5,\}[^*]"
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
docker compose exec postfix postqueue -p | grep -B2 "@example.com" | \
  awk '/^[A-F0-9]/ {print $1}' | tr -d '*!' | \
  xargs -I {} docker compose exec postfix postsuper -d {}
```

> **Warning:** Only flush the full queue if you know what you're doing. If delivery is failing for a reason (DNS issue, blocked IP), flushing just generates more bounces.

### Queue Manager Service

The `queue_manager` service shares the Postfix spool volume and exposes queue operations through the API (`/api/v1/queue/...`) and the console. Permanently failed webhook deliveries land in the `webhook_dead_letters` MySQL table (not a Redis list), where they can be inspected and replayed through the webhooks API.

## Certificate Renewal

Renewal is fully automated by the `cert_manager` service — it checks every 6 hours (`CERT_CHECK_INTERVAL=21600`), renews certificates with fewer than `CERT_RENEWAL_DAYS=30` days left, and SIGHUPs Postfix and Dovecot through the docker-proxy when a certificate changes. **Do not install certbot on the host** — it will collide with cert_manager's ACME challenge path and locks.

Your job is only to verify it's working:

```bash
# What cert_manager has been doing
docker compose logs --since 24h cert_manager | tail -30

# Verify the served certificate's dates
echo | openssl s_client -connect mail.yourdomain.com:993 2>/dev/null \
  | openssl x509 -noout -dates

# Force a re-check by restarting the service (it evaluates on startup)
docker compose restart cert_manager
```

## Updating Mailyte

Most services build from source, so an update is a `git pull` plus a rebuild — not a `docker compose pull`:

```bash
git pull

# Rebuild the migrate image FIRST -- a stale one silently no-ops on new
# migrations (it still contains the previous release's migration files)
docker compose build migrate

# Rebuild and restart everything that changed
docker compose build
docker compose up -d
```

On deploy-pipeline hosts, run `./deployment/deploy.sh` instead — it does all of the above plus a pre-deploy backup and rolling updates of the replicated services. Set `PULL_BASE=1` occasionally (weekly is reasonable) to refresh base images for security fixes; `FORCE_REBUILD=1` rebuilds everything without cache.

Registry-pulled services (`console`, `webmail`, `roundcube`, `sogo`, infrastructure images) update via a bumped version pin in `.env` (`CONSOLE_VERSION`, `WEBMAIL_VERSION`) followed by `docker compose up -d <service>`.

### Cleaning Up Old Images

```bash
# Remove unused images
docker image prune -a --filter "until=168h"  # Older than 7 days

# Trim the build cache (keeps the last week hot -- what makes rebuilds fast)
docker builder prune -f --filter until=168h

# Safer general cleanup: dangling images and stopped containers only
docker system prune
```

> **Warning:** Never run `docker system prune --volumes` or `docker volume prune` on this host. Neither is scoped to the project, and pruning volumes can destroy the database of anything else running on the machine.

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

# Mail storage per domain / per mailbox (bind-mounted Maildirs)
sudo du -sh storage/mail_data/* | sort -rh | head -20
```

### When Disk Gets Low

1. Clean Docker: `docker system prune` and `docker builder prune -f --filter until=72h`
2. Rotate logs: check `logs/` and old local backup sets in `storage/backups/`
3. Clean mail queue: `docker compose exec postfix postsuper -d ALL deferred` (only if you know why they deferred)
4. Prune large database tables (see Database Cleanup above)
5. Check mail storage: enforce mailbox quotas if not already set
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
docker compose exec -T mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)" -e "
SELECT table_schema AS \"Database\",
  ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS \"Size (MB)\"
FROM information_schema.tables
GROUP BY table_schema;"' 2>/dev/null

echo ""
echo "--- Certificate Expiry ---"
echo | openssl s_client -connect localhost:993 2>/dev/null \
  | openssl x509 -noout -enddate

echo ""
echo "--- Backup Status ---"
ls -lht storage/backups/ | head -6
systemctl list-timers 'mailyte-backup-*' --no-pager 2>/dev/null
```
