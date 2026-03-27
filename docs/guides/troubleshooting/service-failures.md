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
```

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

A service started before its dependency (MySQL, Redis) was healthy.

```bash
# Check dependency health
docker inspect mysql --format='{{.State.Health.Status}}'
docker inspect redis --format='{{.State.Health.Status}}'
```

Fix: ensure `depends_on` with `condition: service_healthy` in docker-compose.yml.

**Configuration error:**

```bash
# Check for config parsing errors
docker logs <container_name> 2>&1 | head -30
```

A typo in config files or missing environment variables often causes immediate crashes.

**Port already in use:**

```bash
# Check for port conflicts
docker logs <container_name> 2>&1 | grep -i "address already in use"

# Find what's using the port on the host
sudo ss -tlnp | grep :<port>
```

## Problem: OOM Killed (Exit Code 137)

### Confirm OOM Kill

```bash
# Check kernel logs
dmesg | grep -i "oom\|killed" | tail -10

# Check Docker's memory stats
docker stats --no-stream
```

### Which Container Got Killed?

```bash
# Recent OOM events
dmesg | grep -i "oom" | tail -20
```

### Fix: Increase Memory Limits

```yaml
# docker-compose.yml
services:
  mysql:
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 2G
```

### Fix: Reduce Memory Usage

**MySQL:**

```yaml
command: >
  --innodb-buffer-pool-size=1G
  --max-connections=200
  --table-open-cache=400
```

**Redis:**

```yaml
command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
```

**Rspamd:**

Rspamd can use a lot of memory for Bayesian learning. Limit it:

```
# config/mailer/rspamd/local.d/options.inc
max_memory = 512M;
```

## Problem: Disk Full

### Check Disk Space

```bash
# Overall
df -h /

# Docker-specific
docker system df

# Which containers use the most
docker system df -v | head -30
```

### Common Disk Hogs

| Path | What's there | Safe to clean? |
|------|-------------|----------------|
| `/var/lib/docker/` | Container images and volumes | Yes, carefully |
| `logs/` | Service log files | Yes, rotate them |
| `storage/mail_data/` | User email | No, back up first |
| MySQL volume | Database files | No, clean via SQL |

### Quick Cleanup

```bash
# Remove unused Docker resources
docker system prune -f

# Remove unused images
docker image prune -a -f

# Rotate logs
truncate -s 0 logs/mailer/postfix/maillog
truncate -s 0 logs/mailer/dovecot/*.log
truncate -s 0 logs/mailer/rspamd/rspamd.log
```

!!! warning "Don't truncate while services are running without care"
    It's safer to set up proper log rotation. See below.

### Set Up Log Rotation

```bash
# /etc/logrotate.d/mailyte
/path/to/mailyte/logs/mailer/postfix/maillog
/path/to/mailyte/logs/mailer/dovecot/*.log
/path/to/mailyte/logs/mailer/rspamd/*.log
/path/to/mailyte/logs/worker/*/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

## Problem: Service Won't Start

### Check Prerequisites

```bash
# Are dependencies running?
docker compose ps mysql redis

# Is the network created?
docker network ls | grep mailserver

# Are volumes present?
docker volume ls | grep mailyte
```

### Check Image Build

```bash
# Rebuild the image
docker compose build <service_name>

# Check for build errors
docker compose build <service_name> 2>&1 | tail -30
```

### Check Environment Variables

```bash
# Verify env vars are set
docker compose config | grep -A20 "<service_name>"

# Check for missing required vars
docker logs <container_name> 2>&1 | grep -i "missing\|required\|not set"
```

### Check File Permissions

```bash
# Mail storage needs specific ownership
ls -la storage/mail_data/

# DKIM keys need to be readable by Rspamd
ls -la storage/dkim_keys/

# SSL certs need correct permissions
ls -la storage/ssl_certs/ storage/ssl_private/
```

## Problem: Health Check Failing

### Check Health Status

```bash
docker inspect <container_name> --format='{{json .State.Health}}' | python3 -m json.tool
```

### Test Health Endpoint Manually

```bash
# Test from inside the container
docker exec -it api curl -s http://localhost:8080/health

# Test from another container
docker exec -it postfix curl -s http://api:8080/health
```

### Common Health Check Issues

- **Service is starting up** — the `start_period` might be too short
- **Database connection failing** — MySQL isn't ready yet
- **Redis connection failing** — Redis is down or unreachable
- **Port mismatch** — the health check hits the wrong port

## Recovery Commands

```bash
# Restart a single service
docker compose restart <service_name>

# Recreate a service (pulls fresh config)
docker compose up -d --force-recreate <service_name>

# Nuclear option: restart everything
docker compose down && docker compose up -d

# Rebuild and restart
docker compose build <service_name> && docker compose up -d <service_name>
```

## Preventing Future Failures

1. **Set memory limits** on every container
2. **Set up log rotation** before logs fill the disk
3. **Monitor disk space** with Prometheus alerts
4. **Use health checks** on every service
5. **Set `restart: unless-stopped`** so services recover from transient failures
6. **Back up regularly** — see [Backup Automation](../backup-automation.md)
