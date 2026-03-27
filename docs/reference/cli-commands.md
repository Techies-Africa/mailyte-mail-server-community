---
title: CLI Commands
description: Handy Docker exec commands for managing Postfix, Dovecot, Rspamd, MySQL, and Redis from the command line.
---

# CLI Commands

All commands run via `docker exec` since services run inside containers. Replace `<container_name>` with the actual name (usually the service name from docker-compose.yml).

## Postfix

### Queue Management

```bash
# View the mail queue
docker exec -it postfix postqueue -p

# Count messages in queue
docker exec -it postfix postqueue -p | tail -1

# Flush the queue (retry all deferred messages)
docker exec -it postfix postqueue -f

# View a specific queued message
docker exec -it postfix postcat -q QUEUE_ID

# Delete a specific message
docker exec -it postfix postsuper -d QUEUE_ID

# Delete ALL messages (careful!)
docker exec -it postfix postsuper -d ALL

# Delete all deferred messages only
docker exec -it postfix postsuper -d ALL deferred

# Put a message on hold
docker exec -it postfix postsuper -h QUEUE_ID

# Release a held message
docker exec -it postfix postsuper -H QUEUE_ID

# Requeue a message (reprocesses it)
docker exec -it postfix postsuper -r QUEUE_ID
```

### Configuration

```bash
# Show all Postfix config (effective values)
docker exec -it postfix postconf

# Show a specific setting
docker exec -it postfix postconf smtpd_tls_cert_file

# Show non-default settings only
docker exec -it postfix postconf -n

# Check config for errors
docker exec -it postfix postfix check

# Reload config without restarting
docker exec -it postfix postfix reload
```

### Logs and Diagnostics

```bash
# View recent mail log
docker exec -it postfix tail -100 /var/log/postfix/maillog

# Search for a specific email
docker exec -it postfix grep "user@example.com" /var/log/postfix/maillog

# Search by message ID
docker exec -it postfix grep "MESSAGE_ID" /var/log/postfix/maillog

# Check TLS
docker exec -it postfix postconf smtpd_tls_protocols
```

## Dovecot

### User Management

```bash
# List all authenticated users
docker exec -it dovecot doveadm who

# Check a user's mail location
docker exec -it dovecot doveadm user user@example.com

# Check a user's quota
docker exec -it dovecot doveadm quota get -u user@example.com

# Recalculate quota
docker exec -it dovecot doveadm quota recalc -u user@example.com
```

### Mailbox Operations

```bash
# List a user's mailboxes
docker exec -it dovecot doveadm mailbox list -u user@example.com

# Check mailbox status
docker exec -it dovecot doveadm mailbox status -u user@example.com messages INBOX

# Search for messages
docker exec -it dovecot doveadm search -u user@example.com mailbox INBOX subject "test"

# Force mailbox index rebuild
docker exec -it dovecot doveadm index -u user@example.com INBOX

# Purge expunged messages
docker exec -it dovecot doveadm purge -u user@example.com
```

### Diagnostics

```bash
# Check Dovecot config
docker exec -it dovecot doveconf -n

# Check a specific setting
docker exec -it dovecot doveconf mail_location

# List active connections
docker exec -it dovecot doveadm who

# Check for errors
docker exec -it dovecot doveadm log errors

# Reload config
docker exec -it dovecot doveadm reload
```

## Rspamd

### Status and Statistics

```bash
# Overall status
docker exec -it rspamd rspamc stat

# Detailed statistics
docker exec -it rspamd rspamc counters

# Check a specific symbol
docker exec -it rspamd rspamc counters | grep DKIM

# History of recent scans
docker exec -it rspamd rspamc history
```

### Spam Learning

```bash
# Learn a message as spam
docker exec -it rspamd rspamc learn_spam < spam_message.eml

# Learn a message as ham (not spam)
docker exec -it rspamd rspamc learn_ham < good_message.eml

# Check Bayesian statistics
docker exec -it rspamd rspamc stat | grep -A5 "Statfile"
```

### DKIM

```bash
# List DKIM keys
docker exec -it rspamd ls -la /var/lib/rspamd/dkim/

# Check DKIM signing config
docker exec -it rspamd cat /etc/rspamd/local.d/dkim_signing.conf
```

### Configuration

```bash
# Dump effective config
docker exec -it rspamd rspamadm configdump

# Check config for errors
docker exec -it rspamd rspamadm configtest

# Reload without restart
docker exec -it rspamd rspamc reload
```

## MySQL

### Quick Queries

```bash
# Connect to MySQL shell
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" mailserver

# One-liner queries
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" mailserver -e "SELECT COUNT(*) FROM domains;"
```

### Useful Queries

```bash
# Count all entities
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" mailserver -e "
SELECT
  (SELECT COUNT(*) FROM organizations) AS orgs,
  (SELECT COUNT(*) FROM domains) AS domains,
  (SELECT COUNT(*) FROM email_accounts) AS mailboxes,
  (SELECT COUNT(*) FROM aliases) AS aliases;
"

# Check mail queue
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" mailserver -e "
SELECT status, COUNT(*) AS count
FROM mail_queue
GROUP BY status;
"

# Storage usage by domain
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" mailserver -e "
SELECT domain, total_storage_used, total_email_accounts
FROM domains
ORDER BY total_storage_used DESC
LIMIT 10;
"

# Recent bounces
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" mailserver -e "
SELECT sender, recipient, bounce_reason, timestamp
FROM mail_logs
WHERE status = 'bounced'
ORDER BY timestamp DESC
LIMIT 10;
"

# Database size
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" -e "
SELECT TABLE_NAME, ROUND(DATA_LENGTH/1024/1024, 2) AS data_mb, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'mailserver'
ORDER BY DATA_LENGTH DESC;
"
```

### Maintenance

```bash
# Check table integrity
docker exec -it mysql mysqlcheck -u root -p"${DB_ROOT_PASSWORD}" mailserver

# Optimize tables
docker exec -it mysql mysqlcheck -u root -p"${DB_ROOT_PASSWORD}" --optimize mailserver

# Backup
docker exec mysql mysqldump -u root -p"${DB_ROOT_PASSWORD}" --single-transaction mailserver > backup.sql
```

## Redis

```bash
# Connect to Redis CLI
docker exec -it redis redis-cli

# Check memory usage
docker exec -it redis redis-cli info memory

# List all keys (careful in production — blocks)
docker exec -it redis redis-cli --scan --pattern "mailyte:*" | head -20

# Check rate limit counters
docker exec -it redis redis-cli --scan --pattern "rate:*" | head -10

# Get a specific key
docker exec -it redis redis-cli GET "some:key"

# Monitor commands in real time
docker exec -it redis redis-cli monitor

# Flush all data (nuclear option)
docker exec -it redis redis-cli FLUSHALL
```

## Docker Compose

```bash
# Start everything
docker compose up -d

# Stop everything
docker compose down

# Restart a specific service
docker compose restart postfix

# View logs for a service
docker compose logs -f postfix

# Rebuild and restart a service
docker compose up -d --build api

# Check resource usage
docker stats --no-stream
```
