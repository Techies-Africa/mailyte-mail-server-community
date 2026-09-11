---
title: "Troubleshooting: Database Performance"
description: Diagnose and fix slow queries, connection limits, table locks, and indexing issues in MySQL.
---

# Database Performance

If the API is slow, emails are queuing up, or dashboards are timing out, the database is usually the first suspect. Here's how to find and fix the problem.

!!! note "Root credentials"
    The MySQL root password is supplied to the container as a **file**, `/run/secrets/db_root_password` (from `secrets/db_root_password` on the host) — there is no `DB_ROOT_PASSWORD` variable inside the container. The commands below read it from the mounted file. MySQL is not published on any host port; everything goes through `docker exec`.

## Quick Health Check

```bash
# Check MySQL status
docker exec mysql sh -c 'mysqladmin -u root -p"$(cat /run/secrets/db_root_password)" status'

# Active connections
docker exec mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)" -e "SHOW PROCESSLIST;"'

# InnoDB status (long output, lots of useful info)
docker exec mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)" -e "SHOW ENGINE INNODB STATUS\G"' | head -100
```

For the SQL snippets below, open an interactive session once:

```bash
docker exec -it mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)" mailserver'
```

## Problem: Slow Queries

### Enable the Slow Query Log

```sql
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;
SET GLOBAL slow_query_log_file = '/var/lib/mysql/slow.log';
```

### Find the Slowest Queries

```bash
docker exec mysql tail -50 /var/lib/mysql/slow.log
```

### Common Slow Queries and Fixes

**Tracking queries without proper indexes:**

```sql
-- If this is slow:
SELECT * FROM email_tracking WHERE organization_id = 'x' AND `timestamp` > '2026-01-01';

-- Check what indexes exist:
SHOW INDEX FROM email_tracking;

-- Verify the index is being used:
EXPLAIN SELECT * FROM email_tracking WHERE organization_id = 'x' AND `timestamp` > '2026-01-01';
```

If the EXPLAIN shows `type: ALL` (full table scan), the index isn't being used. Common reasons:

- The table statistics are stale: `ANALYZE TABLE email_tracking;`
- The query is selecting too many columns: use specific columns instead of `SELECT *`

**Log tables getting too large:**

```sql
-- Check table sizes
SELECT
  TABLE_NAME,
  ROUND(DATA_LENGTH / 1024 / 1024, 2) AS data_mb,
  ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS index_mb,
  TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'mailserver'
ORDER BY DATA_LENGTH DESC;
```

If `mail_queue`, `mail_logs`, or `webhook_delivery_logs` are huge, old records need cleaning:

```sql
-- Delete processed queue entries older than 7 days, in batches
DELETE FROM mail_queue
WHERE status IN ('sent', 'delivered', 'bounced', 'rejected')
AND processed_at < NOW() - INTERVAL 7 DAY
LIMIT 10000;
```

## Problem: Too Many Connections

### Check Current Usage

```sql
SHOW VARIABLES LIKE 'max_connections';
SHOW STATUS LIKE 'Threads_connected';
```

The mysql-exporter also feeds these into Prometheus as `mysql_global_status_threads_connected` / `mysql_global_variables_max_connections` — the `DatabaseConnectionPoolExhausted` alert fires on sustained pressure.

### Who's Using Them?

```sql
SELECT
  USER,
  HOST,
  COUNT(*) as connections,
  GROUP_CONCAT(DISTINCT COMMAND) as commands
FROM information_schema.PROCESSLIST
GROUP BY USER, HOST;
```

### Fix: Increase Connection Limit

The base compose file sets no MySQL tuning flags — add them via an override so they survive updates:

```yaml
# docker-compose.override.yml
services:
  mysql:
    command: --max-connections=500
```

### Fix: Connection Pooling

Most route modules use SQLAlchemy engines; a few older ones still build a connection per request. If one service dominates the PROCESSLIST by host, that's the one to look at.

## Problem: Table Locks

MySQL 8 moved lock introspection to performance_schema:

```sql
-- Current locks
SELECT * FROM performance_schema.data_locks;

-- Lock waits (who blocks whom)
SELECT * FROM performance_schema.data_lock_waits;

-- Long-running transactions
SELECT
  trx_id,
  trx_state,
  trx_started,
  TIMESTAMPDIFF(SECOND, trx_started, NOW()) as age_seconds,
  trx_query
FROM information_schema.INNODB_TRX
ORDER BY trx_started;
```

### Common Causes

1. **Large DELETE or UPDATE** — delete in batches instead
2. **ALTER TABLE on big tables** — use `pt-online-schema-change` or run during low traffic
3. **Forgotten transactions** — a worker crashed mid-transaction

### Fix: Kill a Blocking Query

```sql
-- Find the blocking thread
SHOW PROCESSLIST;

-- Kill it
KILL <thread_id>;
```

## Problem: Missing or Stale Indexes

### Check Index Usage

```sql
-- Index cardinality (low = less useful)
SELECT
  TABLE_NAME,
  INDEX_NAME,
  COLUMN_NAME,
  CARDINALITY
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'mailserver'
ORDER BY TABLE_NAME, INDEX_NAME;
```

### Refresh Statistics

```sql
ANALYZE TABLE organizations;
ANALYZE TABLE domains;
ANALYZE TABLE email_accounts;
ANALYZE TABLE mail_queue;
ANALYZE TABLE email_tracking;
ANALYZE TABLE webhook_delivery_logs;
ANALYZE TABLE mail_logs;
```

## Problem: Disk Space

### Check Database Size

```sql
SELECT
  TABLE_SCHEMA,
  ROUND(SUM(DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS total_mb
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'mailserver'
GROUP BY TABLE_SCHEMA;
```

### Largest Tables

```sql
SELECT
  TABLE_NAME,
  TABLE_ROWS,
  ROUND(DATA_LENGTH / 1024 / 1024, 2) AS data_mb,
  ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS index_mb
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'mailserver'
ORDER BY DATA_LENGTH DESC
LIMIT 10;
```

### Cleanup Candidates

| Table | Safe to trim? | Retention |
|-------|---------------|-----------|
| `mail_logs` | Old records, yes | Keep 30-90 days |
| `email_tracking` | Old records, yes | Keep 90 days |
| `webhook_delivery_logs` | Delivered records, yes | Keep 7-30 days |
| `mail_queue` | Terminal-status rows, yes | Keep 7 days |
| `health_checks` | Old records, yes | Keep 7 days |
| `service_metrics` | Old records, yes | Keep 30 days |

```sql
-- Example: clean tracking data older than 90 days
DELETE FROM email_tracking
WHERE `timestamp` < NOW() - INTERVAL 90 DAY
LIMIT 50000;
-- Repeat until 0 rows affected
```

!!! tip "Batch deletes"
    Always delete in batches (LIMIT 10000-50000) to avoid long locks. Run the DELETE in a loop with a 1-second sleep between batches. Take a backup first — `./scripts/backup.sh --mysql-only` (see [Backup Automation](../backup-automation.md)).

## Performance Tuning Checklist

- [x] `innodb_buffer_pool_size` = 50-70% of the RAM given to MySQL (raise the container's compose memory limit together with it — production caps it at 2G by default)
- [x] `innodb_flush_log_at_trx_commit` = 2 (safe trade-off for email workloads)
- [x] `max_connections` = enough for ~20 worker services plus Postfix/Dovecot lookups (typically 200-500)
- [x] Slow query log enabled
- [x] Table statistics up to date (`ANALYZE TABLE`)
- [x] Old data cleaned regularly
- [x] Recent backup verified before any large cleanup
