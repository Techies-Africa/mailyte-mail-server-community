# Performance Tuning

Postfix queue tuning, Dovecot connection limits, MySQL optimization, and Redis memory — with the stack's real defaults as the starting point.

---

Mailyte's defaults work well for small to medium deployments. Once you handle thousands of mailboxes or high message volumes, tune the components below. Remember that Postfix/Dovecot/Rspamd configs are **baked into the images** — persistent tuning changes go into `mailer/*/config/` followed by a rebuild (see each component's configuration page).

## Postfix

### What's Already Set

The shipped `main.cf` already includes the important throughput settings:

```ini
default_process_limit = 100
smtpd_client_connection_rate_limit = 30      # POSTFIX_CONNECTION_RATE_LIMIT
smtpd_client_connection_count_limit = 50     # POSTFIX_CONNECTION_COUNT_LIMIT
smtpd_client_message_rate_limit = 100        # POSTFIX_MESSAGE_RATE_LIMIT
smtpd_client_recipient_rate_limit = 200      # POSTFIX_RECIPIENT_RATE_LIMIT
anvil_rate_time_unit = 60s
```

The per-client limits are env-tunable (the names in comments) without a rebuild. MySQL lookups already go through `proxy:` maps (persistent connections via `proxymap`) — no change needed there.

### Delivery Concurrency

Postfix's own defaults apply where main.cf is silent (`default_destination_concurrency_limit = 20`, etc.). For high volume, raise in `mailer/postfix/config/main.cf`:

```ini
default_process_limit = 200            # more parallel deliveries overall
smtp_destination_concurrency_limit = 20  # per remote destination — don't exceed ~20
```

Going above ~20 connections to a single remote server invites rate-limiting or blocking by the receiver.

### Queue Retry Schedule

Postfix defaults (`minimal_backoff_time = 300s`, `maximal_backoff_time = 4000s`, `maximal_queue_lifetime = 5d`, `queue_run_delay = 300s`) apply. For time-sensitive mail, reduce `minimal_backoff_time` to `60s`; for high-volume servers, raise `queue_run_delay` to `600s` to reduce disk I/O.

### Queue on Fast Storage

The Postfix spool lives in the `postfix_spool` named volume. If your Docker data root isn't on SSD/NVMe, move it — the queue is the most I/O-sensitive piece of the stack.

```bash
docker exec postfix postqueue -p | tail -1   # a healthy queue is mostly empty
```

## Dovecot

### Shipped Process Limits

The baked `dovecot.conf` already scales well past a thousand concurrent users:

```ini
service imap-login {
  process_min_avail = 2
  process_limit = 256
  client_limit = 1000
  service_count = 1        # one connection per login process (most secure)
  vsz_limit = 64M
}
service imap {
  process_limit = 1024
  client_limit = 1
  service_count = 0
  vsz_limit = 512M
}
service pop3-login { process_limit = 128 }
service pop3       { process_limit = 512 }
service lmtp       { process_limit = 50 }
service auth       { client_limit = 4096 }
service auth-worker { process_limit = 30; process_min_avail = 5 }
default_process_limit = 1000
default_client_limit = 1000
```

Each connected IMAP user consumes one `imap` process; raise `service imap { process_limit }` first when users hit connection errors. Mobile devices are chatty — a single phone can hold several persistent connections.

### Memory per Process

`service imap { vsz_limit = 512M }` is already generous. If users with very large mailboxes (50k+ messages) see processes killed, raise it further.

### Auth Throughput

`auth_cache_size = 10M` / `auth_cache_ttl = 1 hour` keep MySQL off the hot path for repeat logins. Remember the flip side: manual credential changes need a cache flush to take effect promptly.

## MySQL

The stack runs stock `mysql:8.0.35` with no custom tuning file. For a busy server, add one via a volume mount:

```ini
# my-tuning.cnf → mounted at /etc/mysql/conf.d/mailyte.cnf
[mysqld]
innodb_buffer_pool_size = 1G          # ~50-70% of RAM on a dedicated DB host
innodb_log_file_size = 256M
innodb_flush_log_at_trx_commit = 2    # faster, slightly less durable — fine for mail metadata
max_connections = 300
table_open_cache = 800
thread_cache_size = 16
```

(Don't copy `query_cache_*` settings from old guides — the query cache was removed in MySQL 8.)

Indexes are managed by the Alembic migrations — the hot lookup paths (`domains(domain, active)`, `email_accounts(email, status)`, `aliases(source, active)`, `mail_logs(timestamp, status)`, `mail_logs(sasl_username, timestamp)`, …) are already covered. Don't hand-create indexes; add a migration if profiling shows a missing one.

> [!WARNING]
> Don't set `innodb_buffer_pool_size` larger than available RAM. A swapping MySQL is far slower than a smaller buffer pool.

## Redis

Redis serves Rspamd (Bayes, greylisting, fuzzy, neural), the rate limiter, Dovecot's auth policy, and several caches. The shipped service runs stock `redis` with no memory cap.

To cap and protect training data, mount a config or add command flags:

```ini
maxmemory 512mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
```

If evictions climb (`docker exec redis redis-cli info stats | grep evicted`), raise `maxmemory` — evicting Bayes/greylist keys degrades spam filtering accuracy.

## Rspamd

The shipped worker config doesn't pin process counts (Rspamd's default is one scanner per CPU by default behavior). To pin them, add to `mailer/rspamd/config/local.d/worker-normal.inc`:

```ini
count = 4;     # scanning workers ≈ CPU cores you want on filtering
```

If your CPU supports SSE4.2, Rspamd's Hyperscan engine accelerates regex matching automatically — check `docker exec rspamd rspamd --version` for `hyperscan`.

## Service Replicas

Stateless workers can scale horizontally with compose:

```bash
docker compose up -d --scale webhooks=2 --scale tracking=2
```

(Production already runs some services at `replicas: 2` — check `docker-compose.prod.yml` before assuming a "duplicate" container is a bug.)

## General Recommendations by Scale

| Mailboxes | Messages/Day | CPU Cores | RAM | Notes |
|-----------|-------------|-----------|-----|-------|
| < 100 | < 1,000 | 2 | 4 GB | Defaults work fine |
| 100–1,000 | 1,000–10,000 | 4 | 8 GB | Raise Postfix `default_process_limit` to 200 |
| 1,000–10,000 | 10,000–100,000 | 8 | 16 GB | Tune MySQL buffer pool, pin Rspamd workers |
| 10,000+ | 100,000+ | 16+ | 32+ GB | Split services across hosts; managed DB/Redis (`docker-compose.cloud.yml`) |

> [!TIP]
> Monitor first, tune second. Use the Prometheus metrics (reference (Enterprise Edition)) and `mail_logs` to identify actual bottlenecks before changing defaults. Random tuning often makes things worse.
