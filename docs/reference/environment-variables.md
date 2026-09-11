---
title: Environment Variables
description: Reference for the environment variables Mailyte's services actually read, with code defaults and the services that consume each.
---

# Environment Variables

## How Variables Reach Services

Understanding the plumbing matters more here than in most stacks:

- **Compose interpolation is the only `.env` channel.** No compose file uses `env_file:`, and no service loads `.env` with dotenv. A variable in `.env` reaches a container **only if that service's `environment:` block in `docker-compose.yml` (or the prod overlay) names it.** Setting something in `.env` that no block references does nothing.
- **There is no central Settings object.** Each service reads its own `os.getenv()` calls — often with *different defaults for the same name*. Tables below note per-service defaults where they differ.
- **Secrets** live in `secrets/` and are bind-mounted read-only: `MYSQL_ROOT_PASSWORD_FILE=/run/secrets/db_root_password` (mysql), `DB_ROOT_PASSWORD_FILE` (secrets-check), `ENCRYPTION_KEK_PATH` (default `/run/secrets/encryption_kek`), `ARCHIVE_AGE_IDENTITY_FILE` (default `/run/secrets/archive_age_identity`).
- **The `secrets-check` gate** runs before everything else (dev compose) and refuses to start the stack if any of `DB_ROOT_PASSWORD`, `DB_PASSWORD`, `WEBHOOK_SECRET`, `OAUTH_TOKEN_SECRET`, `URL_HMAC_SECRET`, `ADMIN_PASSWORD`, `ADMIN_TOKEN_SECRET`, `GRAFANA_ADMIN_PASSWORD` is missing, shorter than 16 chars, or a known-weak value. `scripts/generate-secrets.sh` produces a compliant `.env`.
- **Postfix pipe/spawn scripts** (tracking injector, policy daemons) don't inherit container env; the entrypoint writes `/etc/postfix/runtime.env` with the `DB_*`, `REDIS_*`, `TRACKING_*`, `DELIVERY_OPTIMIZER_*`, `WEBHOOK_SECRET`, `HOSTNAME` set, and the scripts load that file.

## Database

Consumed by nearly every service; set per-service in compose (`DB_HOST=mysql`, `DB_PORT=3306`).

| Variable | Default in code | Required | Description |
|----------|----------------|----------|-------------|
| `DB_HOST` | `mysql` (most services; `localhost` in a few standalone tools) | Yes | MySQL hostname |
| `DB_PORT` | `3306` | No | |
| `DB_NAME` | `mailserver` | No | |
| `DB_USER` | `mailuser` (some legacy paths default `root`) | Yes | |
| `DB_PASSWORD` | — (weak fallbacks exist in code but are rejected by secrets-check) | Yes | |
| `DB_ROOT_PASSWORD` | — | Yes | Root password; kept in sync with `secrets/db_root_password` by `generate-secrets.sh` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` / `DB_POOL_RECYCLE` | `10` / `20` / `30` / `3600` | No | Pool tuning (storage_usage, rate_limiter) |
| `DB_TIMEOUT` | `5` | No | Postfix tracking-injector connect timeout |

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | Compose sets these on every consumer |
| `REDIS_URL` | — | Full URL override; compose sets `redis://redis:6379/0` (rate_limiter) and `/1` (queue_manager) |
| `REDIS_PASSWORD` / `REDIS_SSL` | — / `false` | |
| `REDIS_DB` | varies: `0` most, `1` rate_limiter, `2` storage_usage & Dovecot auth policy, `4` delivery_optimizer | Per-service DB separation |

## Server Identity & Mail

| Variable | Default | Consumers | Description |
|----------|---------|-----------|-------------|
| `HOSTNAME` | `mail.example.com` | postfix, autoconfig, cert_manager, api | Server FQDN (must match PTR) |
| `DOMAIN` | `example.com` | cert_manager, autoconfig, compose interpolation | Base domain for admin hostnames (`api.$DOMAIN`, …) |
| `MAIL_HOSTNAME` | — (falls back to `HOSTNAME`) | api (DNS records), autoconfig, cert_manager | The advertised mail hostname when it differs from `HOSTNAME` |
| `MAIL_SPF_HOST` | `spf.<server hostname>` | api | Host named in generated SPF `include:` records |
| `ADMIN_EMAIL` | — | api (alert sender fallback) | |
| `MTA_STS_MODE` | `testing` (code & dev compose); **`enforce` in prod compose** | autoconfig | MTA-STS policy mode |
| `DEFAULT_ORGANIZATION_ID` / `DEFAULT_DOMAIN_ID` | `default` | postfix tracking injector | Fallback attribution for tracked mail |

## API Service

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` / `PORT` | `0.0.0.0` / `5000` (compose sets `PORT=8080`; host port 8083) | Bind address/port — there are no `API_HOST`/`API_PORT` variables |
| `ADMIN_PASSWORD` / `ADMIN_TOKEN_SECRET` | — | Admin auth; validated by secrets-check |
| `FLASK_ENV` / `FLASK_DEBUG` | `development` / `0` | Environment mode flags (naming is legacy; the app is FastAPI) |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated origins |
| `MAILYTE_COOKIE_SECURE` | `true` | Set `false` only for plain-HTTP dev |
| `MAILYTE_EDITION` | `community` in code; compose default `enterprise` | Capability flags |
| `TOTP_SERVICE_URL` | `http://totp:8103` | 2FA service |
| `SIEVE_HOST` / `SIEVE_PORT` | `dovecot` (via `IMAP_HOST`) / `4190` | ManageSieve |
| `MAIL_SUBMIT_HOST` / `MAIL_SUBMIT_PORT` | `postfix` / `10587` | Internal submission listener |
| `MONITORING_SERVICE_URL` / `QUEUE_SERVICE_URL` / `STORAGE_SERVICE_URL` / `RAG_SERVICE_URL` / `ANALYTICS_API_BASE` / `MIGRATION_SERVICE_URL` / `RATE_LIMITER_API_BASE` / `TRACKING_API_BASE` / `PROMETHEUS_URL` | in-network service URLs | Gateway targets |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` / `AI_TIMEOUT` | — / — / `gpt-4o-mini` / `45` | Mailbox AI features (503 `ai_not_configured` when unset). Passed through to the `api` container by compose since 2026-08-31 — before that, a value in `.env` never reached the service |
| `MIN_CLIENT_VERSION_<PLATFORM>` / `LATEST_CLIENT_VERSION_<PLATFORM>` / `UPDATE_URL_<PLATFORM>` / `FORCE_UPDATE_<PLATFORM>` | — (unset = unrestricted) | Client version negotiation for `ios`/`android`/`web`/`macos`/`windows`/`linux` clients; answered as `X-Min-Client-Version`, `X-Latest-Client-Version`, `X-Update-Url`, `X-Update-Required` on every response. Advisory only — the client enforces. An unparseable version never forces; `FORCE_UPDATE_*=1` always does |

## Dovecot / doveadm / IMAP

| Variable | Default | Consumers | Description |
|----------|---------|-----------|-------------|
| `DOVEADM_URL` | `http://dovecot:24180` | api, log_ingestor | doveadm HTTP API (auth-cache flush) |
| `DOVEADM_API_KEY` | — (empty = flushing disabled with a warning) | dovecot, api, log_ingestor | Key for the doveadm listener |
| `IMAP_HOST` / `IMAP_PORT` | `dovecot` / `993` | api, jmap, migration, storage_usage | |
| `IMAP_MASTER_USER` / `IMAP_MASTER_PASSWORD` | — | jmap, api, storage_usage | Dovecot master-user credentials (`<mailbox>*<master>` login) |

## SSL / cert_manager

| Variable | Default | Description |
|----------|---------|-------------|
| `ACME_EMAIL` | `admin@localhost` | Let's Encrypt account email (`ACME_EMAILS` for a comma-separated account pool) |
| `ACME_STAGING` | `true` (prod compose sets `false`) | Staging CA toggle |
| `SSL_CERT_PATH` / `SSL_KEY_PATH` | `/etc/ssl/certs` / `/etc/ssl/private` | **Directories** where cert_manager deploys certs (not file paths) |
| `MAIL_SSL_CERT_PATH` / `MAIL_SSL_KEY_PATH` | `/etc/ssl/certs/custom` / `/etc/ssl/private/custom` | Path prefixes as seen from Postfix/Dovecot mounts (used inside SNI maps) |
| `SNI_CONFIG_PATH` | `/etc/ssl/sni` | Where the Postfix/Dovecot/Traefik SNI outputs are written |
| `CERT_RENEWAL_DAYS` / `CERT_CHECK_INTERVAL` | `30` / `21600` | Renewal threshold / check loop seconds |
| `CERT_WORKER_THREADS` / `CERT_SERVER_IPS` / `MAX_DOMAINS_PER_CERT` | `3` / — / `100` | Issuance tuning |
| `TRAEFIK_ADMIN_SUBDOMAINS` | `api,autoconfig,jmap,caldav,docs,grafana,traefik,console` | Admin hostnames to certify under `$DOMAIN` |
| `TRAEFIK_EXTRA_HOSTNAMES` | — | Extra fully-qualified hostnames to certify |
| `WILDCARD_DOMAIN` / `DNS_PROVIDER` / `USE_WILDCARD_CERTS` | — / — / `false` | DNS-01 wildcard issuance |
| `DOCKER_RELOAD_ENABLED` | compose default `true` (code default `false`) | SIGHUP Postfix/Dovecot after deploys |
| `DOCKER_PROXY_URL` / `POSTFIX_CONTAINER` / `DOVECOT_CONTAINER` | `http://docker-proxy:2375` / `postfix` / `dovecot` | Reload plumbing |

## Postfix Overrides (read by its entrypoint)

`POSTFIX_MYNETWORKS` (default `172.25.0.0/16` appended), `POSTFIX_DEV_MODE`, `POSTFIX_TLS_SECURITY_LEVEL`, `POSTFIX_SMTP_TLS_SECURITY_LEVEL`, `POSTFIX_TLS_AUTH_ONLY`, `POSTFIX_TLS_PROTOCOLS`, `POSTFIX_TLS_CIPHERS`, `POSTFIX_TLS_EXCLUDE_CIPHERS`, `POSTFIX_TLS_LOG_LEVEL`, `POSTFIX_TLS_CACHE_TIMEOUT`, `POSTFIX_CONNECTION_RATE_LIMIT`, `POSTFIX_CONNECTION_COUNT_LIMIT`, `POSTFIX_MESSAGE_RATE_LIMIT`, `POSTFIX_RECIPIENT_RATE_LIMIT`, `POSTFIX_RATE_TIME_UNIT`, `POSTFIX_SMTP_TIMEOUT`, `POSTFIX_HELO_TIMEOUT`, `POSTFIX_MAIL_TIMEOUT`, `POSTFIX_RCPT_TIMEOUT`, `POSTFIX_DATA_TIMEOUT`, `POSTFIX_SOFT_ERROR_LIMIT`, `POSTFIX_HARD_ERROR_LIMIT`, `POSTFIX_JUNK_COMMAND_LIMIT`, `POSTFIX_DNS_TIMEOUT`.

!!! warning "Dead variables in `.env.example`"
    The `POSTFIX_MYHOSTNAME`, `POSTFIX_MYDOMAIN`, `POSTFIX_VIRTUAL_*`, `POSTFIX_MESSAGE_SIZE_LIMIT`, `POSTFIX_CONTENT_FILTER`, `POSTFIX_HEADER_CHECKS`, and `DOVECOT_*` blocks in `.env.example` are **not read by any code** — the entrypoints use the different names above (or bake the value). Likewise `DEFAULT_TENANT_ID` is set in compose but read by nothing (the code reads `DEFAULT_ORGANIZATION_ID`).

## Tracking

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACKING_ENABLED` | `true` | Open/click tracking master switch (compose default `true`) |
| `OPEN_TRACKING_ENABLED` / `CLICK_TRACKING_ENABLED` | `true` / `true` | Per-feature toggles |
| `TRACKING_DOMAIN` | falls back to `HOSTNAME` | Domain for tracking URLs |
| `TRACKING_BASE_URL` | compose: `https://api.${DOMAIN}` | Explicit base URL for pixel/click links |
| `TRACKING_SERVICE_URL` | `http://tracking:8086` | Used by the Postfix injector |
| `BODY_CAPTURE_ENABLED` / `BODY_CAPTURE_MAX_BYTES` / `BODY_RETENTION_DAYS` | `true` / `262144` / `30` | Outbound body capture into `email_bodies` |
| ~30 more `TRACKING_*` tuning vars | see `worker/tracking/config.py` | Retention, rate limits, UA/geo capture toggles |

## Webhooks

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_URL` / `WEBHOOK_URLS` | — | Global endpoint(s); compose maps `WEBHOOK_URL=${WEBHOOK_URLS}` |
| `WEBHOOK_SECRET` | — | HMAC-SHA256 signing secret (required by secrets-check) |
| `WEBHOOK_TIMEOUT` | `15` (dispatcher; notification sender uses `30`) | Per-attempt timeout |
| `WEBHOOK_MAX_RETRIES` | `7` (dispatcher; notification sender `3`) | Total attempts |
| `WEBHOOK_WORKERS` / `WEBHOOK_QUEUE_SIZE` | `4` / `10000` | Dispatcher threads / in-memory queue |
| `WEBHOOK_SERVICE_URL` | `http://webhooks:8081` | Postfix bounce/webhook scripts |
| `WEBHOOK_CLEANUP_*` | enabled, every 4 h, keep 24 h success / 72 h failure | Delivery-log retention (webhooks service) |
| `SERVICE_NAME` | `unknown` | Sets the dispatcher's `source`; compose sets `log_ingestor` where needed |

## Log Ingestor & SMTP Credentials

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTFIX_LOG_PATH` | `/var/log/postfix/mail.log` | Log file to tail |
| `INGESTOR_STATE_PATH` | `/var/lib/log_ingestor/state` | Tail-position state |
| `INGESTOR_POLL_SECONDS` / `INGESTOR_QID_CACHE` | `2` / `20000` | Poll interval / queue-ID cache |
| `INGESTOR_INTERNAL_RELAYS` | `tracking-filter,webhook-filter` | Internal hops to skip |
| `LAST_USED_THROTTLE_SECONDS` | `60` | Throttle for `smtp_credentials.last_used_at` writes |
| `AUTO_SUSPEND_ENABLED` | `false` | Auto-suspend failing SMTP credentials |
| `AUTO_SUSPEND_MIN_MESSAGES` / `AUTO_SUSPEND_FAILURE_RATIO` / `AUTO_SUSPEND_WINDOW_HOURS` | `20` / `0.5` / `1` | Suspension thresholds |

## Rate Limiter

Service basics: `RATE_LIMITER_HOST`/`RATE_LIMITER_PORT` (`0.0.0.0`/`8082`), `RATE_LIMITER_URL` (`http://rate_limiter:8082`, Postfix policy client), `RATE_LIMITER_TIMEOUT` (`5`), `RATE_LIMIT_CACHE_TTL` (`300`), warning/critical/exceeded thresholds (`80`/`95`/`100`).

Default quotas are 36 vars of the form `{ORG|DOMAIN|MAILBOX}_{INBOUND|OUTBOUND}_{SECOND|MINUTE|HOURLY|DAILY|MONTHLY|BURST}_DEFAULT` — e.g. `ORG_OUTBOUND_HOURLY_DEFAULT=10000`, `DOMAIN_OUTBOUND_HOURLY_DEFAULT=2000`, `MAILBOX_OUTBOUND_HOURLY_DEFAULT=200`. See `worker/rate_limiter/config.py` for the full matrix; per-SMTP-credential limits live on the credential row, not in env.

## Storage / Archive / DR

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` / `AWS_BUCKET` / `AWS_S3_PREFIX` | — / — / `eu-west-2` / `development-local-1` / `mailyte` | S3 for attachments/exports (api, storage_usage) |
| `ARCHIVE_S3_BUCKET` / `ARCHIVE_S3_PREFIX` | `mailyte-mail-archive` / `mail-archive` | Archiver's bucket (separate credentials via `ARCHIVE_AWS_*` in compose) |
| `S3_ENDPOINT_URL` | — | Non-AWS S3 endpoints |
| `ARCHIVE_SPOOL_PATH` / `ARCHIVE_SPOOL_DRAIN_SECONDS` | `/app/storage/archive-spool` / `300` | Local spool when S3 is down |
| `DR_AGE_RECIPIENT` / `ARCHIVE_AGE_RECIPIENT` / `ARCHIVE_AGE_IDENTITY_FILE` | — / — / `/run/secrets/archive_age_identity` | age encryption for archives/backups |
| `ARCHIVE_SERVICE_URL` / `ARCHIVE_TIMEOUT` / `ARCHIVE_OUTBOUND_ENABLED` | `http://archiver:8083` / `3` / `true` | Sieve/injector archive hooks |
| `DEFAULT_RETENTION_DAYS` | `2555` | Archive retention (7 years) |
| `BACKUP_EXPECTED_HOSTS` | — | Hosts the backup-freshness gauges must cover |
| `MAIL_DATA_PATH` / `ATTACHMENT_PATH` | `/var/mail/vhosts` / `/var/attachments` | storage_usage scan roots |

## Security Services & Misc Secrets

| Variable | Default | Description |
|----------|---------|-------------|
| `OAUTH_TOKEN_SECRET` / `OAUTH_ISSUER` | — (weak default rejected) / `https://auth.mailyte.com` | OAuth service |
| `URL_HMAC_SECRET` / `SAFE_LINK_BASE_URL` | — / `https://safe.mailyte.com` | URL protection |
| `TOTP_ISSUER` | `Mailyte` | TOTP label |
| `GEOIP_DB_PATH` | `/usr/share/GeoIP/GeoLite2-Country.mmdb` | geo_blocking |
| `TRANSPORT_RULES_ENABLED` | `false` | Rspamd transport-rules Lua module |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` | `admin` / — (required) | Grafana login |
| `MAX_AUTH_FAILURES_PER_IP` / `MAX_AUTH_FAILURES_PER_USER` / `AUTH_FAILURE_WINDOW_SECS` / `AUTH_BLOCK_DURATION_SECS` | `10` / `5` / `900` / `3600` | Dovecot auth policy |

## RAG / AI Search

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` / `QDRANT_PORT` | `localhost` in code; compose sets `qdrant` / `6333` | Vector DB |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL_NAME` | `sentence_transformers` / `all-MiniLM-L6-v2` | Local embeddings by default |
| `OPENAI_API_KEY` | — | Only needed when `EMBEDDING_PROVIDER=openai` |
| `RAG_PORT` / `RAG_HOST` | `8090` / `0.0.0.0` | |
| 60+ further `RAG_*` / `EMBEDDING_*` / `CHUNK_*` / `SEARCH_*` vars | see `worker/rag/config.py` | Chunking, indexing, caching, tenancy |

## Logging & Runtime Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Per-service log level |
| `FLASK_ENV` / `ENVIRONMENT` | `development` | `production` marks prod mode |
| `FLASK_DEBUG` / `DEBUG` | `0` | |
| `MAILYTE_LOG_DIR` | `logs` | Root for `GET /platform/logs` |
| `COMPOSE_PROJECT_NAME` | `mailyte-prod` (from `.env`) | Pins the compose project name across deploy directories — do not remove |

## Removed / Nonexistent Variables

These appeared in older docs but are read by nothing: `API_HOST`, `API_PORT`, `DB_READ_HOST` (reader module is unimported), `TRACKING_WORKERS`, `ANALYTICS_WORKERS`, `QUEUE_MANAGER_WORKERS`, `GRAFANA_PASSWORD`, `PROMETHEUS_RETENTION` (retention is the `--storage.tsdb.retention.time=30d` flag in compose), `CLOUD_PROVIDER`, `AWS_S3_BUCKET`, `DEFAULT_TENANT_ID`.

## Example `.env`

Generate it rather than writing it by hand:

```bash
./scripts/generate-secrets.sh   # writes a complete .env with strong secrets
```

Then set the identity values:

```bash
COMPOSE_PROJECT_NAME=mailyte-prod
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
ADMIN_EMAIL=admin@yourdomain.com
ACME_EMAIL=admin@yourdomain.com
ACME_STAGING=false
MAIL_HOSTNAME=mail.yourdomain.com
DOVEADM_API_KEY=<random string — enables SMTP-credential cache flushing>
WEBHOOK_URLS=https://your-app.com/webhooks/mailyte
```
