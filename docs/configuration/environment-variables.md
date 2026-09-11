# Environment Variables

The variables you need for setup, how they actually reach each service, and the traps to avoid.

---

## How Configuration Loading Works

Three facts shape everything else:

1. **`.env` is consumed only by Docker Compose interpolation.** No service loads `.env` itself, and no compose file uses `env_file:`. A variable reaches a container only when that service's `environment:` block in `docker-compose.yml` references it (`DB_PASSWORD=${DB_PASSWORD}`). Adding a new name to `.env` without a matching compose entry does nothing.
2. **There is no shared Settings object.** Every service calls `os.getenv()` with its own defaults. The complete per-service inventory (with defaults) is in [the reference](../reference/environment-variables.md).
3. **Secrets are gated.** The `secrets-check` service runs first and refuses to start the stack if any required secret (`DB_ROOT_PASSWORD`, `DB_PASSWORD`, `WEBHOOK_SECRET`, `OAUTH_TOKEN_SECRET`, `URL_HMAC_SECRET`, `ADMIN_PASSWORD`, `ADMIN_TOKEN_SECRET`, `GRAFANA_ADMIN_PASSWORD`) is missing, shorter than 16 characters, or a known-weak value.

> [!TIP]
> Don't write `.env` by hand. `./scripts/generate-secrets.sh` produces a complete `.env` with strong random values for all eight required secrets, kept in sync with `secrets/db_root_password`.

## The Minimum Setup Set

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `COMPOSE_PROJECT_NAME` | Pins the compose project name so timestamped deploy directories share volumes/containers | `mailyte-prod` in `.env.example` | Yes in production |
| `HOSTNAME` | Server FQDN — must match your PTR record | `mail.example.com` | Yes |
| `DOMAIN` | Base domain for admin hostnames (`api.$DOMAIN`, `grafana.$DOMAIN`, …) | `example.com` | Yes |
| `MAIL_HOSTNAME` | Advertised mail hostname, when different from `HOSTNAME` | falls back to `HOSTNAME` | No |
| `ADMIN_EMAIL` | Operator contact / alert sender fallback | — | Recommended |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` | MySQL connection | `mysql` / `3306` / `mailserver` / `mailuser` | Defaults fine |
| `DB_PASSWORD` / `DB_ROOT_PASSWORD` | MySQL passwords | — | Yes (generated) |
| `ADMIN_PASSWORD` / `ADMIN_TOKEN_SECRET` | API admin auth | — | Yes (generated) |
| `WEBHOOK_SECRET` / `OAUTH_TOKEN_SECRET` / `URL_HMAC_SECRET` / `GRAFANA_ADMIN_PASSWORD` | Service secrets | — | Yes (generated) |
| `ACME_EMAIL` | Let's Encrypt account email | `admin@localhost` | Yes |
| `ACME_STAGING` | `true` = staging CA (untrusted certs) | `true` (prod compose: `false`) | Set `false` for production |
| `DOVEADM_API_KEY` | Enables auth-cache flushing when SMTP credentials are revoked/rotated | — (empty = revocations take up to 1 h) | Strongly recommended |
| `WEBHOOK_URLS` | Global webhook endpoint(s), comma-separated | — | If you consume webhooks |

> [!WARNING]
> Never commit `.env`. It holds every secret in the stack, including the database root password.

## Server Identity

`HOSTNAME` should match your server's reverse DNS (PTR record) — typically `mail.yourdomain.com`. `DOMAIN` is the base for the admin/Traefik hostnames and for compose interpolation (`api.${DOMAIN}`, `webmail.${DOMAIN}`, …); it is **not** necessarily a customer mail domain — customer domains live in the database.

`MAIL_HOSTNAME` exists because the two can legitimately differ (e.g. `DOMAIN=courier.mailyte.com` for the PTR identity while `mail.mailyte.com` is what clients configure). The API's generated DNS records and the autoconfig service both prefer `MAIL_HOSTNAME`.

## Redis

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_HOST` / `REDIS_PORT` | Redis connection | `redis` / `6379` |

Redis serves Rspamd (Bayes, greylisting, per-org settings), the rate limiter, Dovecot's auth policy, and several caches. Different services use different Redis DB numbers by default (0/1/2/4) — see the reference.

## SSL / TLS

Certificates are fully managed by the `cert_manager` container (certbot webroot HTTP-01). The important variables:

```bash
ACME_EMAIL=admin@yourdomain.com
ACME_STAGING=false
CERT_RENEWAL_DAYS=30        # renew this many days before expiry
CERT_CHECK_INTERVAL=21600   # check every 6 hours
```

`SSL_CERT_PATH` / `SSL_KEY_PATH` are **directories** (`/etc/ssl/certs`, `/etc/ssl/private`) where cert_manager deploys per-domain certs — not file paths to a single certificate. Postfix and Dovecot read the shared `server.crt`/`server.key` plus per-domain SNI maps that cert_manager generates. See [SSL Certificates](ssl-certificates.md).

## Tracking

| Variable | Description | Default |
|----------|-------------|---------|
| `TRACKING_ENABLED` | Open/click tracking master switch | `true` |
| `TRACKING_BASE_URL` | Base URL for pixel/click links | compose: `https://api.${DOMAIN}` |
| `BODY_CAPTURE_ENABLED` | Capture outbound bodies into `email_bodies` (enriches delivery webhooks) | `true` |

Outgoing mail on ports 587/465 passes through the tracking content filter; with tracking disabled the filter passes messages through unchanged.

## Webhooks

| Variable | Description | Default |
|----------|-------------|---------|
| `WEBHOOK_SECRET` | HMAC-SHA256 signing secret | — (required) |
| `WEBHOOK_URLS` | Global endpoint URL(s) | — |
| `WEBHOOK_TIMEOUT` / `WEBHOOK_MAX_RETRIES` | Delivery tuning | `15` s / `7` attempts |

See [Webhook Events](../reference/webhook-events.md) for event names, payloads, and verification.

## Feature Toggles Worth Knowing

| Variable | Default | Effect |
|----------|---------|--------|
| `MTA_STS_MODE` | `testing` (dev) / `enforce` (prod compose) | MTA-STS policy served by autoconfig |
| `TRANSPORT_RULES_ENABLED` | `false` | Rspamd transport-rules engine |
| `AUTO_SUSPEND_ENABLED` | `false` | Auto-suspend SMTP credentials with mostly-failing sends |
| `MAILYTE_EDITION` | compose default `enterprise` | Capability flags reported by the API |
| `POSTFIX_DEV_MODE` | unset | Set by `docker-compose.dev.yml` |

## Known Traps

> [!WARNING]
> - **`.env.example` contains dead blocks.** The `POSTFIX_MYHOSTNAME`/`POSTFIX_VIRTUAL_*`/`POSTFIX_MESSAGE_SIZE_LIMIT`/`POSTFIX_CONTENT_FILTER` and `DOVECOT_*` sections are read by nothing — the entrypoints use different names (see the reference) or bake the values.
> - **`DEFAULT_TENANT_ID`** is set in compose but read by nothing; the code reads `DEFAULT_ORGANIZATION_ID`, which compose never sets. Both fall back to `"default"`.
> - **Overlays merge `environment:` per key -- but only for keys the base file sets.** `docker-compose.prod.yml` carries no `environment:` block for `rag`, `oauth`, `url_protection`, or `jmap`; that is fine, because Compose merges the base file's block through (verify with `docker compose -f docker-compose.yml -f docker-compose.prod.yml config <service>`). The trap is a variable **no** compose file passes to its consumer: `jmap`'s `ADMIN_TOKEN_SECRET` was validated by `secrets-check` but delivered to nothing, so jmap ran on its old `tokensecret` code default in every environment. Fixed 2026-08-30: the base file now wires it through, and `rag`/`oauth`/`url_protection`/`jmap` refuse to start on a missing or known-weak secret instead of silently falling back to code defaults.
> - **Same name, different defaults.** `WEBHOOK_TIMEOUT`, `REDIS_DB`, `AWS_DEFAULT_REGION`, and ~20 other names default differently per service — the [reference tables](../reference/environment-variables.md) note each case.

## Full Reference

Every variable actually read in code — grouped by consumer, with per-service defaults — is in [Reference → Environment Variables](../reference/environment-variables.md).
