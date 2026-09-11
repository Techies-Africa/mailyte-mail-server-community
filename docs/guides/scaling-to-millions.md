---
title: Scaling to Millions
description: What you can tune in today's Mailyte to handle higher volume — Postfix, MySQL, Redis, worker replicas — and which scaling patterns are architecture work, not configuration.
---

# Scaling to Millions

The default Mailyte setup handles tens of thousands of emails per day comfortably. This guide separates what you can do **today with configuration** from what would be **architecture work** — several patterns often suggested for mail-at-scale (multiple MTA instances, read replicas, sharding) are not built into Mailyte as of 2026-08-30, and pretending otherwise wastes your incident hours.

## When to Start Scaling

| Daily Volume | Approach |
|-------------|--------------|
| < 50,000 | Default single-server setup |
| 50,000 - 500,000 | Tune Postfix/MySQL/Redis, scale worker replicas, watch the queue |
| 500,000+ | Plan architecture work (below) — and talk to your capacity numbers first |

**Queue depth is your early-warning signal.** Watch it via the API — `GET /api/v1/queue/queue/status` and `GET /api/v1/queue/mail-queue/deferred` — or directly with `docker exec postfix postqueue -p`. A queue that keeps growing means you need more delivery capacity or receivers are throttling you.

## Tuning the Single Server

### Postfix Tuning

!!! warning "Postfix config is baked into the image"
    `main.cf` lives at `mailer/postfix/config/main.cf` and is **copied into the image at build time** — there is no mounted `custom/main.cf`, and `postconf -e` inside the container vanishes on the next recreate. To change Postfix settings: edit `mailer/postfix/config/main.cf`, rebuild (`docker compose build postfix`), and recreate the container.

Settings worth reviewing for throughput (standard Postfix knobs — set them in `mailer/postfix/config/main.cf`):

```
# Concurrent deliveries per destination
default_destination_concurrency_limit = 20
smtp_destination_concurrency_limit = 20

# Process limits
default_process_limit = 200

# Retry pacing
minimal_backoff_time = 60s
maximal_backoff_time = 600s

# Connection reuse to big receivers
smtp_connection_cache_on_demand = yes
smtp_connection_reuse_time_limit = 300s
```

!!! warning "Per-destination limits"
    Gmail and Microsoft rate-limit incoming SMTP connections. Don't set `smtp_destination_concurrency_limit` above 20 for external delivery or you'll get temporary blocks.

Also note the spool: `/var/spool/postfix` is a named volume (`postfix_spool`) shared with queue_manager — accepted-but-undelivered mail survives container recreation. Never delete that volume while mail is queued.

### MySQL Tuning

The base compose file runs `mysql:8.0.35` with **no tuning flags**. Add them via `docker-compose.override.yml` (dev) or a prod override so they survive updates:

```yaml
services:
  mysql:
    command: >
      --innodb-buffer-pool-size=2G
      --innodb-redo-log-capacity=1G
      --innodb-flush-log-at-trx-commit=2
      --max-connections=500
      --innodb-io-capacity=2000
```

Key settings:

- **`innodb-buffer-pool-size`** — set to 50-70% of the RAM you can dedicate to MySQL (production caps the container at 2G by default — raise the compose memory limit together with the pool size)
- **`innodb-flush-log-at-trx-commit=2`** — trades a second of durability for a large write-throughput win
- **`max-connections`** — enough for ~20 worker services plus Postfix/Dovecot lookups

### Redis Tuning

```yaml
services:
  redis:
    command: >
      redis-server
      --appendonly yes
      --maxmemory 1gb
      --maxmemory-policy allkeys-lru
```

### Worker Replicas

Production already runs the hot stateless services at two replicas (`docker-compose.prod.yml`: `api`, `webhooks`, `tracking` have `deploy: replicas: 2`, reachable through Traefik / the compose network's DNS round-robin). Raising a count is a one-line change in the prod override:

```yaml
services:
  api:
    deploy:
      replicas: 4
```

This works only for services without a fixed host-port binding — which is exactly why the prod file removes those bindings for the replicated services.

### Worker Concurrency Env Vars

The knobs that exist in code today:

| Variable | Service | Default |
|----------|---------|---------|
| `WEBHOOK_WORKERS` | webhooks | 5 |
| `STORAGE_API_WORKERS` | storage_usage | 4 |
| `RAG_WORKERS` | rag | 1 |
| `INDEXING_MAX_WORKERS` | rag indexing | 4 |

Set them in the service's `environment:` block. (There are no `TRACKING_WORKERS` / `ANALYTICS_WORKERS` variables — scaling those services means more replicas, not a thread knob.)

### Rate Limits as a Throughput Tool

Per-domain and per-mailbox sending limits (`/api/v1/rate-limiter/rate-limits/...`) and SMTP-credential `hourly_limit`/`daily_limit` let you shape which tenants consume delivery capacity — often the cheapest fix when one sender's burst is starving everyone else's queue.

## What is NOT Built In (Architecture Work)

Be clear-eyed about these — none of them is a configuration option today:

- **Multiple Postfix instances / inbound-outbound split.** There is one `postfix` service. The image has no `INSTANCE_TYPE` concept, and queue_manager does not load-balance across MTA instances. Running a second Postfix means designing spool ownership, SASL, and rspamd wiring yourself.
- **MySQL read replicas.** Every service reads and writes a single `DB_HOST`. There is no `DB_READ_HOST`/`DB_WRITE_HOST` split in any worker's code.
- **Database sharding by organization.** All tenants share one schema; nothing routes by org to different databases.
- **A separate queue database.** The Postfix spool plus the shared MySQL/Redis are the only queue stores.

If your volume genuinely demands these, treat them as engineering projects with the usual design/review cycle — and measure first: most deployments that think they need sharding actually need the Phase-1 tuning above plus list hygiene.

## Vertical Scaling Checklist

Before any architecture work:

1. **NVMe storage** — Maildir and InnoDB are IOPS-hungry; disk is the usual first wall
2. **RAM for the buffer pool** — raise MySQL's compose memory limit and `innodb-buffer-pool-size` together
3. **CPU for Rspamd** — spam scanning is the per-message CPU cost on the inbound path
4. **Watch the right metrics** — queue depth, deferral rate, MySQL `Threads_connected`, disk latency

## Monitoring at Scale

At higher volume, monitoring is not optional. The built-in stack covers it:

- **Prometheus + Grafana** run in the base compose file — see [Monitoring Setup](monitoring-setup.md)
- **Alerting** on queue depth (`MailQueueBackup`/`MailQueueCritical`), bounce rate, disk, and DB connection pressure ships in `monitoring/prometheus/rules/mail_alerts.yml`
- **Queue visibility** via `GET /api/v1/queue/queue/status`, `GET /api/v1/queue/queue/domain/{domain}`, and `POST /api/v1/queue/mail-queue/flush` for retrying deferred mail

## Key Takeaways

1. **Tune before you scale** — most setups never need more than the single-server tuning
2. **Queue depth is your early warning** — if it keeps growing, find out whether it's your capacity or receiver throttling
3. **Replicas are the supported horizontal axis** — stateless workers scale with `deploy.replicas`; the MTA and databases do not
4. **Shape traffic with rate limits** — per-tenant limits protect the queue from any one sender
5. **Monitor everything** — you can't optimize what you can't measure
