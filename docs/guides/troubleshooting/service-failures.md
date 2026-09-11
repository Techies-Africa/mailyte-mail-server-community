---
title: "Troubleshooting: Service Failures"
description: Diagnose container crashes, OOM kills, disk full errors, and services that won't start.
---

# Service Failures

A container is crashing, restarting in a loop, or just won't start. Here's how to figure out what's wrong and fix it.

## Quick Status Check

```bash
# Overview of all containers
docker compose ps

# Look for unhealthy or restarting containers
docker compose ps | grep -E "Exit|Restarting|unhealthy"

# Recent events
docker compose events --since 10m

# The bundled status script (defaults to `status`)
./scripts/mailyte-monitor.sh
```

Two built-in helpers go beyond a status listing:

- `./scripts/container-health-monitor.py` — detects common issues (DB connection, permissions, port conflicts, missing deps) and **auto-fixes them by default**; pass `--no-fix` to only report, `--continuous --interval 60` to keep watching
- The monitoring service can restart containers itself — with authentication, through the API: `POST /api/v1/monitoring/services/{service_name}/restart` and `POST /api/v1/monitoring/auto-heal` (operator role plus the `X-Admin-Token` header)

## Problem: Container Keeps Restarting

### Check Logs

```bash
# Last 100 lines of logs
docker logs <container_name> --tail 100

# Follow logs in real time
docker logs <container_name> -f

# Check for specific errors
docker logs <container_name> 2>&1 | grep -i "error\|fatal\|exception\|panic"
```

### Check Exit Code

```bash
docker inspect <container_name> --format='{{.State.ExitCode}}'
```

| Exit Code | Meaning |
|-----------|---------|
| 0 | Clean exit (shouldn't be restarting) |
| 1 | Application error |
| 137 | OOM killed (SIGKILL) |
| 139 | Segmentation fault |
| 143 | Graceful termination (SIGTERM) |

### Common Restart Causes

**Dependency not ready:**

A service started before its dependency (MySQL, Redis, migrations) was ready.

```bash
docker inspect mysql --format='{{.State.Health.Status}}'
docker inspect redis --format='{{.State.Health.Status}}'

# The migrate one-shot must have completed successfully
docker inspect migrate --format='{{.State.ExitCode}}'
```

Most services gate on `condition: service_healthy` for MySQL/Redis **and** `condition: service_completed_successfully` for the `migrate` service — a failed migration deliberately stops everything that touches the schema. Check `docker logs migrate` when half the stack won't start.

**Missing secrets:**

The `secrets-check` one-shot validates required secrets (e.g. `DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) before anything else starts. If it fails, nothing does:

```bash
docker logs secrets-check
```

**Configuration error:**

```bash
docker logs <container_name> 2>&1 | head -30
```

A typo in config files or missing environment variables often causes immediate crashes.

**Port already in use:**

```bash
docker logs <container_name> 2>&1 | grep -i "address already in use"
sudo ss -tlnp | grep :<port>
```

Fix host-port conflicts in `docker-compose.override.yml` with `ports: !override` (a plain `ports:` list appends rather than replaces).

**Capability problems (mailer containers):**

postfix, dovecot, and rspamd run under `cap_drop: ALL` with a curated `cap_add` list. If a mailer container fatals with "Operation not permitted"/"Permission denied" on chroot, chown, or log files right after an image or compose change, compare its `cap_add` list against the base compose file before debugging anything else — those lists are load-bearing and documented inline in `docker-compose.yml`.

## Problem: OOM Killed (Exit Code 137)

### Confirm OOM Kill

```bash
# Check kernel logs
dmesg | grep -i "oom\|killed" | tail -10

# Check Docker's memory stats
docker stats --no-stream
```

### Fix: Increase Memory Limits

Production sets per-service memory limits in `docker-compose.prod.yml` (e.g. MySQL 2G, rspamd 1G). Raise the limit for the killed service there:

```yaml
services:
  mysql:
    deploy:
      resources:
        limits:
          memory: 4G
```

### Fix: Reduce Memory Usage

**MySQL:**

```yaml
command: >
  --innodb-buffer-pool-size=1G
  --max-connections=200
```

**Redis:**

```yaml
command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
```

**Rspamd:**

Rspamd's config is baked into its image from `mailer/rspamd/config/` — edit there and rebuild (`docker compose build rspamd`).

## Problem: Disk Full

### Check Disk Space

```bash
# Overall
df -h /

# Docker-specific
docker system df

# Which images/volumes use the most
docker system df -v | head -30
```

### Common Disk Hogs

| Path | What's there | Safe to clean? |
|------|-------------|----------------|
| `/var/lib/docker/` | Container images and volumes | Yes, carefully (`docker system prune`) |
| `logs/mailer/` | Postfix/Rspamd log files | Yes, rotate them |
| `storage/backups/` | Local backups | Old ones — retention should handle this; see [Backup Automation](../backup-automation.md) |
| `storage/mail_data/` | User email | No, back up first |
| MySQL volume | Database files | No, clean via SQL — see [Database Performance](database-performance.md) |

### Quick Cleanup

```bash
# Remove unused Docker resources
docker system prune -f

# Remove unused images
docker image prune -a -f
```

### Log Rotation

Container stdout/stderr logs are capped in production (`json-file`, 10 MB × 3 files per service). The file-based mailer logs are what grow unbounded — rotate them on the host:

```bash
# /etc/logrotate.d/mailyte
/path/to/mailyte/logs/mailer/postfix/mail.log
/path/to/mailyte/logs/mailer/rspamd/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

(Dovecot logs to stderr — `docker logs dovecot` — so the container log cap covers it.)

## Problem: Service Won't Start

### Check Prerequisites

```bash
# Are dependencies running?
docker compose ps mysql redis

# Did one-shots succeed?
docker logs secrets-check
docker logs migrate

# Is the network created?
docker network ls | grep mailserver

# Are volumes present? (names carry the compose project prefix)
docker volume ls | grep -E "mysql_data|postfix_spool"
```

### Check Image Build

```bash
docker compose build <service_name>
docker compose build <service_name> 2>&1 | tail -30
```

### Check Environment Variables

```bash
# What compose resolved for the service
docker compose config | grep -A20 "<service_name>:"

# Missing required vars usually show in the first log lines
docker logs <container_name> 2>&1 | grep -i "missing\|required\|not set"
```

### Check File Permissions

```bash
# Mail storage is owned by vmail (uid/gid 5000)
ls -la storage/mail_data/

# DKIM keys must be readable by Rspamd's _rspamd user
ls -la storage/dkim_keys/

# SSL certs
ls -la storage/ssl_certs/ storage/ssl_private/
```

## Problem: Health Check Failing

### Check Health Status

```bash
docker inspect <container_name> --format='{{json .State.Health}}' | python3 -m json.tool
```

### Test Health Endpoint Manually

Worker services answer `/health` on their published host port (dev bindings — see [Monitoring Setup](../monitoring-setup.md) for the port table):

```bash
curl -s http://localhost:8083/health   # api (host 8083 → container 8080)
curl -s http://localhost:8081/health   # webhooks

# From inside the network (prometheus ships wget)
docker exec prometheus wget -qO- http://api:8080/health
```

### Common Health Check Issues

- **Service is starting up** — the `start_period` might be too short
- **Database connection failing** — MySQL isn't ready yet, or `migrate` failed
- **Redis connection failing** — Redis is down or unreachable
- **Port mismatch** — several services listen on a container port that differs from the host port; health checks run against the **container** port

## Recovery Commands

```bash
# Restart a single service
docker compose restart <service_name>

# Recreate a service (picks up compose changes)
docker compose up -d --force-recreate <service_name>

# Rebuild and restart (needed for baked-in config: postfix, rspamd, dovecot)
docker compose build <service_name> && docker compose up -d <service_name>

# Nuclear option: restart everything
docker compose down && docker compose up -d
```

!!! warning "`down` is safe for mail, but only because of the named spool volume"
    The Postfix spool lives in the `postfix_spool` named volume, so queued mail survives `docker compose down`. Never add `-v`/`--volumes` to `down` on a mail host — that deletes queued mail and the database.

## Preventing Future Failures

1. **Set memory limits** on every container (production compose already does)
2. **Set up log rotation** for the file-based mailer logs before they fill the disk
3. **Monitor disk space** — the `DiskSpaceWarning`/`DiskSpaceCritical` alerts ship in `monitoring/prometheus/rules/`
4. **Use health checks** on every service (all bundled services have them)
5. **Set `restart: unless-stopped`** so services recover from transient failures
6. **Back up regularly** — see [Backup Automation](../backup-automation.md)
