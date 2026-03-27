# Performance Tuning

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Postfix queue tuning, Dovecot connection limits, MySQL optimization, Redis memory, and Rspamd workers.

---

Mailyte's defaults work well for small to medium deployments. Once you start handling thousands of mailboxes or high message volumes, you'll want to tune individual components. This page covers the knobs that matter most.

## Postfix Queue Tuning

Postfix uses a queue-based architecture. Messages flow through several queues before delivery. Tuning these affects throughput and latency.

### Delivery Concurrency

```ini
# /etc/postfix/main.cf

# Max simultaneous deliveries to any single destination
default_destination_concurrency_limit = 20

# Max simultaneous deliveries total
default_process_limit = 100

# Max simultaneous deliveries to a remote SMTP server
smtp_destination_concurrency_limit = 20

# Max simultaneous local deliveries
local_destination_concurrency_limit = 5
```

- **`default_process_limit`** controls how many delivery processes Postfix runs at once. The default of 100 works for most servers. Bump it to 200-300 for high-volume setups.
- **`smtp_destination_concurrency_limit`** controls how many connections Postfix opens to a single remote server. Don't go above 20 — many servers will rate-limit or block you.

### Queue Retry Schedule

```ini
# How long to wait before retrying a deferred message
minimal_backoff_time = 300s
maximal_backoff_time = 4000s

# How long to keep trying before bouncing
maximal_queue_lifetime = 5d
bounce_queue_lifetime = 5d

# How often to scan the deferred queue
queue_run_delay = 300s
```

The defaults are conservative. For time-sensitive mail, reduce `minimal_backoff_time` to 60 seconds. For high-volume servers where most mail succeeds on the first try, increase `queue_run_delay` to 600 seconds to reduce disk I/O.

### Queue Directory on Fast Storage

Postfix's queue is I/O intensive. If you can put it on an SSD or NVMe drive, do it:

```yaml
# docker-compose.yml
services:
  postfix:
    volumes:
      - /fast-storage/postfix-queue:/var/spool/postfix
```

> [!TIP]
> Run `mailq` (or `docker exec mailyte-postfix mailq`) to see what's in the queue. A healthy queue is mostly empty. If messages are piling up, check the deferred queue logs to find out why.

## Dovecot Connection Limits

Dovecot handles all IMAP and POP3 connections. Mobile devices are especially chatty — a single phone can maintain multiple persistent connections.

### Process Limits

```ini
# /etc/dovecot/conf.d/10-master.conf

service imap-login {
  process_min_avail = 3
  service_count = 1
  process_limit = 256

  inet_listener imap {
    port = 143
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}

service imap {
  process_limit = 1024
}
```

- **`service_count = 1`** — Each login process handles one connection, then exits. This is the most secure setting. Set it to `0` (unlimited) for better performance if you trust your users.
- **`process_limit`** for `imap-login` — Max simultaneous login attempts.
- **`process_limit`** for `imap` — Max simultaneous IMAP sessions. Each connected user uses at least one process.

### Per-User Connection Limits

```ini
# /etc/dovecot/conf.d/20-imap.conf

protocol imap {
  mail_max_userip_connections = 20
}

protocol pop3 {
  mail_max_userip_connections = 5
}
```

This limits how many simultaneous connections a single user can have from one IP. The default of 20 for IMAP covers most multi-device setups (phone, laptop, tablet, desktop, each with multiple folders open).

### vsz_limit (Memory per Process)

```ini
service imap {
  vsz_limit = 512M
}
```

If users have very large mailboxes (50k+ messages), Dovecot processes may need more memory. Increase `vsz_limit` if you see processes getting killed.

## MySQL Query Optimization

Postfix and Dovecot query MySQL on every message delivery and every login. These queries need to be fast.

### Connection Pooling

Postfix opens a new MySQL connection for each lookup by default. Use connection caching:

```ini
# /etc/postfix/main.cf
# Proxy maps cache MySQL connections across Postfix processes
proxy_read_maps =
  proxy:mysql:/etc/postfix/mysql-virtual-mailbox-domains.cf
  proxy:mysql:/etc/postfix/mysql-virtual-mailbox-maps.cf
  proxy:mysql:/etc/postfix/mysql-virtual-alias-maps.cf

virtual_mailbox_domains = proxy:mysql:/etc/postfix/mysql-virtual-mailbox-domains.cf
virtual_mailbox_maps = proxy:mysql:/etc/postfix/mysql-virtual-mailbox-maps.cf
virtual_alias_maps = proxy:mysql:/etc/postfix/mysql-virtual-alias-maps.cf
```

The `proxy:` prefix routes queries through Postfix's `proxymap` daemon, which keeps persistent connections to MySQL.

### Indexes

Make sure these indexes exist on your MySQL tables:

```sql
ALTER TABLE virtual_domains ADD INDEX idx_name_active (name, active);
ALTER TABLE virtual_users ADD INDEX idx_email_active (email, active);
ALTER TABLE virtual_aliases ADD INDEX idx_source_active (source, active);
```

### MySQL Server Tuning

```ini
# /etc/mysql/conf.d/mailyte.cnf

[mysqld]
innodb_buffer_pool_size = 256M
innodb_log_file_size = 64M
innodb_flush_log_at_trx_commit = 2
max_connections = 200
query_cache_type = 1
query_cache_size = 32M
table_open_cache = 400
thread_cache_size = 16
```

- **`innodb_buffer_pool_size`** — Set this to about 50-70% of available RAM on a dedicated database server. For a shared setup, 256 MB is a good starting point.
- **`innodb_flush_log_at_trx_commit = 2`** — Slightly less durable than the default (1), but much faster. Acceptable for a mail database where you're not tracking financial transactions.

> [!WARNING]
> Don't set `innodb_buffer_pool_size` larger than your available RAM. If MySQL starts swapping, performance will be far worse than with a smaller buffer pool.

## Redis Memory

Redis is used by Rspamd (Bayesian statistics, greylisting data) and by the Mailyte API (rate limiting, caching).

### Memory Limits

```ini
# /etc/redis/redis.conf

maxmemory 512mb
maxmemory-policy allkeys-lru
```

- **`maxmemory`** — Hard cap on Redis memory usage. 512 MB is plenty for most setups.
- **`allkeys-lru`** — When memory is full, evict the least recently used keys. This is safe for caching data. Rspamd's Bayesian data survives eviction because the most-used statistics are always recently accessed.

### Persistence

```ini
save 900 1
save 300 10
save 60 10000
```

These lines tell Redis to snapshot to disk if at least 1 key changed in the last 15 minutes, 10 keys in the last 5 minutes, or 10,000 keys in the last minute. This protects Bayesian training data from being lost on restart.

> [!NOTE]
> If Redis runs out of memory and the `maxmemory-policy` starts evicting keys, Rspamd's Bayesian accuracy will gradually decrease as training data gets cleared. Monitor Redis memory usage and increase `maxmemory` if you see evictions climbing.

## Rspamd Workers

Rspamd uses worker processes to handle scanning in parallel.

`/etc/rspamd/local.d/worker-normal.inc`:

```ini
count = 4;
```

`/etc/rspamd/local.d/worker-proxy.inc`:

```ini
count = 2;
```

- **Normal workers** do the actual spam scanning. Set this to the number of CPU cores you want dedicated to spam filtering. 4 workers can handle a few hundred messages per minute.
- **Proxy workers** accept connections from Postfix. 2 is enough unless you're processing very high volumes.

### Rspamd Hyperscan

If your CPU supports it, enable Hyperscan for faster regex matching:

```bash
# Check if Hyperscan is available
docker exec mailyte-rspamd rspamd --version
# Look for "hyperscan" in the output
```

Hyperscan compiles regex patterns into optimized machine code. It can speed up scanning by 2-5x on Intel/AMD CPUs with SSE4.2 support.

## General Recommendations by Scale

| Mailboxes | Messages/Day | CPU Cores | RAM | Notes |
|-----------|-------------|-----------|-----|-------|
| < 100 | < 1,000 | 2 | 4 GB | Defaults work fine |
| 100-1,000 | 1,000-10,000 | 4 | 8 GB | Increase Postfix process limit to 200 |
| 1,000-10,000 | 10,000-100,000 | 8 | 16 GB | Tune MySQL, increase Rspamd workers to 8 |
| 10,000+ | 100,000+ | 16+ | 32+ GB | Split services across multiple hosts |

> [!TIP]
> Monitor first, tune second. Use Prometheus metrics (see [Monitoring Configuration](monitoring-configuration.md)) to identify actual bottlenecks before changing defaults. Random tuning often makes things worse.
