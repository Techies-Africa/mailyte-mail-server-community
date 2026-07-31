---
title: "Troubleshooting: Database Performance"
description: Diagnose and fix slow queries, connection limits, table locks, and indexing issues in MySQL.
---

# Database Performance

If the API is slow, emails are queuing up, or dashboards are timing out, the database is usually the first suspect. Here's how to find and fix the problem.

## Quick Health Check

```bash
# Check MySQL status
docker exec -it mysql mysqladmin -u root -p"${DB_ROOT_PASSWORD}" status

# Active connections
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" -e "SHOW PROCESSLIST;"

# InnoDB status (long output, lots of useful info)
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" -e "SHOW ENGINE INNODB STATUS\G" | head -100
```

## Problem: Slow Queries

### Enable the Slow Query Log

```bash
docker exec -it mysql mysql -u root -p"${DB_ROOT_PASSWORD}" -e "
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;
SET GLOBAL slow_query_log_file = '/var/lib/mysql/slow.log';
"
```

### Find the Slowest Queries

```bash
# View recent slow queries
docker exec -it mysql tail -50 /var/lib/mysql/slow.log
```

### Common Slow Queries and Fixes

**Tracking queries without proper indexes:**

```sql
-- If this is slow:
SELECT * FROM email_tracking WHERE organization_id = 'x' AND timestamp > '2025-01-01';

-- Check if the index exists:
SHOW INDEX FROM email_tracking WHERE Column_name = 'organization_id';

-- The schema already includes idx_org_time, but verify it's being used:
EXPLAIN SELECT * FROM email_tracking WHERE organization_id = 'x' AND timestamp > '2025-01-01';
```

If the EXPLAIN shows `type: ALL` (full table scan), the index isn't being used. Common reasons:

- The table statistics are stale: `ANALYZE TABLE email_tracking;`
- The query is selecting too many columns: use specific columns instead of `SELECT *`

**Mail queue table getting too large:**

```sql
-- Check table size
SELECT
  TABLE_NAME,
  ROUND(DATA_LENGTH / 1024 / 1024, 2) AS data_mb,
  ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS index_mb,
  TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'mailserver'
ORDER BY DATA_LENGTH DESC;
```

If `mail_queue` or `webhook_delivery_logs` are huge, old records need cleaning:

```sql
-- Delete processed queue entries older than 7 days
DELETE FROM mail_queue
WHERE status IN ('sent', 'delivered', 'bounced', 'rejected')
AND processed_at < NOW() - INTERVAL 7 DAY
LIMIT 10000;

-- Run in batches to avoid locking
```

## Problem: Too Many Connections

### Check Current Usage

```sql
-- Current connections vs limit
SHOW VARIABLES LIKE 'max_connections';
SHOW STATUS LIKE 'Threads_connected';
```

### Who's Using Them?

```sql
-- Group by source
SELECT
  USER,
  HOST,
  COUNT(*) as connections,
  GROUP_CONCAT(DISTINCT COMMAND) as commands
FROM information_schema.PROCESSLIST
GROUP BY USER, HOST;
```

### Fix: Increase Connection Limit

```yaml
# docker-compose.yml
mysql:
  command: --max-connections=500
```

### Fix: Connection Pooling

If workers are creating too many connections, ensure they use connection pooling. The API and workers should share pools:

```python
# In worker config
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 20
DB_POOL_RECYCLE = 3600
```

## Problem: Table Locks

### Detect Locks

```sql
-- Current locks
SELECT * FROM information_schema.INNODB_LOCKS;

-- Lock waits
SELECT * FROM information_schema.INNODB_LOCK_WAITS;

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
-- Tables without indexes (unlikely with Mailyte, but check)
SELECT TABLE_NAME
FROM information_schema.TABLES t
LEFT JOIN information_schema.STATISTICS s ON t.TABLE_NAME = s.TABLE_NAME
WHERE t.TABLE_SCHEMA = 'mailserver'
AND s.TABLE_NAME IS NULL;

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
-- Update stats for all tables
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

| Table | Safe to Truncate? | Retention |
|-------|-------------------|-----------|
| `mail_logs` | Old records, yes | Keep 30-90 days |
| `email_tracking` | Old records, yes | Keep 90 days |
| `webhook_delivery_logs` | Delivered records, yes | Keep 7-30 days |
| `usage_history` | Old records, yes | Keep 90 days |
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
    Always delete in batches (LIMIT 10000-50000) to avoid long locks. Run the DELETE in a loop with a 1-second sleep between batches.

## Performance Tuning Checklist

- [x] `innodb_buffer_pool_size` = 50-70% of available RAM
- [x] `innodb_log_file_size` = 256M-1G
- [x] `innodb_flush_log_at_trx_commit` = 2 (safe for email workloads)
- [x] `max_connections` = enough for all services (typically 200-500)
- [x] Slow query log enabled
- [x] Table statistics up to date (`ANALYZE TABLE`)
- [x] Old data cleaned regularly
- [x] Connection pooling in use by all workers
