# Service Monitoring

How to tell if each Mailyte service is healthy — quick checks you can run right now.

## At a Glance

```bash
# One-liner: check all services
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
```

If everything says "Up" and "healthy," you're good. If not, read on.

## Postfix (SMTP)

Postfix handles all email delivery. If it's down, no email goes in or out.

**Quick check:**

```bash
# Test SMTP connection
telnet localhost 25

# Or without telnet installed
echo "EHLO test" | nc -w 3 localhost 25

# Check the mail queue
docker compose exec postfix postqueue -p

# Count queued messages
docker compose exec postfix postqueue -p | tail -1
```

**What healthy looks like:**

```
220 mail.yourdomain.com ESMTP Postfix
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Queue size | < 100 | > 500 |
| Delivery delay | < 30s | > 5min |
| Bounce rate | < 3% | > 5% |
| Active connections | < 50 | > 100 |

**Common problems:**

- Queue growing: Check DNS resolution, recipient server availability
- High bounces: Check sender reputation, SPF/DKIM records
- Connection refused: Postfix service crashed, check logs

```bash
docker compose logs --tail=50 postfix
```

## Dovecot (IMAP/POP3)

Dovecot handles mailbox access. If it's down, users can't read email.

**Quick check:**

```bash
# Test IMAP
echo "a1 LOGIN testuser testpass" | nc -w 3 localhost 143

# Check active connections
docker compose exec dovecot doveadm who

# Verify user mailbox
docker compose exec dovecot doveadm mailbox list -u user@domain.com
```

**What healthy looks like:**

```
* OK [CAPABILITY IMAP4rev1 ...] Dovecot ready.
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Active connections | Stable | Sudden drop or spike |
| Auth failures | < 10/min | > 50/min (possible brute force) |
| Mailbox size | Within quota | Near quota limit |

## Rspamd (Spam Filter)

Rspamd scans incoming email for spam. If it's down, you'll either reject all mail or let everything through (depending on your Postfix config).

**Quick check:**

```bash
# Ping Rspamd
curl http://localhost:11334/ping

# Get statistics
curl http://localhost:11334/stat

# Check Rspamd status
curl http://localhost:11334/stat | python3 -m json.tool
```

**What healthy looks like:**

```
pong
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Scan time | < 500ms | > 2s |
| Spam rate | 10-40% | > 60% or < 5% |
| Memory usage | < 500MB | > 1GB |

## MySQL

MySQL stores user accounts, domains, aliases, and configuration. If it's down, the API can't function.

**Quick check:**

```bash
# Test connection
docker compose exec mysql mysqladmin -u root -p ping

# Check status
docker compose exec mysql mysqladmin -u root -p status

# Check process list
docker compose exec mysql mysql -u root -p -e "SHOW PROCESSLIST;"

# Check table sizes
docker compose exec mysql mysql -u root -p -e "
SELECT table_name,
       ROUND(data_length/1024/1024, 2) AS 'Size (MB)'
FROM information_schema.tables
WHERE table_schema = 'mailyte'
ORDER BY data_length DESC;"
```

**What healthy looks like:**

```
mysqld is alive
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Connections | < 80% of max | > 80% of max |
| Slow queries | 0 | Any |
| Replication lag | 0s | > 5s |
| Buffer pool hit rate | > 99% | < 95% |

## Redis

Redis handles caching, session storage, and worker job queues. If it's down, the API slows down and workers stop processing.

**Quick check:**

```bash
# Ping Redis
docker compose exec redis redis-cli ping

# Check memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human

# Check connected clients
docker compose exec redis redis-cli info clients | grep connected_clients

# Check queue lengths
docker compose exec redis redis-cli llen email_queue
docker compose exec redis redis-cli llen retry_queue
```

**What healthy looks like:**

```
PONG
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Memory usage | < 80% of max | > 85% |
| Connected clients | Stable | Climbing fast |
| Hit rate | > 90% | < 80% |
| Queue length | < 100 | > 1000 |

## FastAPI (API Server)

The API on port `5000` is how external systems interact with Mailyte.

**Quick check:**

```bash
# Health endpoint
curl http://localhost:5000/health

# Check response time
curl -w "Total time: %{time_total}s\n" -o /dev/null -s http://localhost:5000/health

# API docs (confirms the app is running)
curl -s http://localhost:5000/docs | head -5
```

**What healthy looks like:**

```json
{"status": "ok", "version": "1.0.0"}
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Response time (p95) | < 200ms | > 500ms |
| Error rate | < 1% | > 5% |
| Active requests | < 50 | > 100 |

## Workers

Workers process background jobs — sending emails, retrying deliveries, cleaning up.

**Quick check:**

```bash
# Check worker status
docker compose ps worker

# View recent worker logs
docker compose logs --tail=20 worker

# Check job queue depth
docker compose exec redis redis-cli llen email_queue
```

**Key things to watch:**

| Metric | Healthy | Investigate |
|--------|---------|-------------|
| Queue depth | Stable or decreasing | Growing steadily |
| Job failure rate | < 1% | > 5% |
| Processing time | < 5s per job | > 30s per job |
| Last heartbeat | < 60s ago | > 120s ago |

## Health Check Script

Save this as `check-all.sh` for a quick full-system check:

```bash
#!/bin/bash

echo "=== Mailyte Service Status ==="
echo ""

services=("postfix:25" "dovecot:143" "rspamd:11334" "mysql:3306" "redis:6379" "api:5000" "health-monitor:8080")

for svc in "${services[@]}"; do
    name="${svc%%:*}"
    port="${svc##*:}"
    if nc -z -w 2 localhost "$port" 2>/dev/null; then
        echo "  [OK]   $name (:$port)"
    else
        echo "  [FAIL] $name (:$port)"
    fi
done

echo ""
echo "=== Docker Containers ==="
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```

```bash
chmod +x check-all.sh
./check-all.sh
```
