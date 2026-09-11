---
title: Configuration
description: Understand the environment variables that control how your Mailyte server behaves.
---

# Configuration

This page walks you through the environment variables in your `.env` file, grouped by what they control. You do not need to set every single one right now -- `scripts/generate-secrets.sh` plus a handful of edits is enough for development -- but you should understand what is available.

## How configuration works

Mailyte reads all its settings from environment variables. These are defined in the `.env` file at the root of the project, and Docker Compose passes them into each container.

```
┌──────────┐     ┌──────────────────┐     ┌────────────┐
│  .env    │────>│  docker compose  │────>│ containers │
└──────────┘     └──────────────────┘     └────────────┘
```

After changing a variable, **recreate** the affected service (or the whole stack):

```bash
# Recreate whatever changed
docker compose up -d

# Recreate just the API
docker compose up -d api
```

!!! warning "`restart` is not enough"
    `docker compose restart` restarts the existing container **without re-reading `.env`**. To pick up an environment change you must `docker compose up -d`, which recreates the container with the new values. No image rebuild is needed either way.

!!! note "Compose only injects variables it is told about"
    A variable set in `.env` reaches a container only if that service's `environment:` block in `docker-compose.yml` names it. Setting a new variable in `.env` alone does nothing for a service that does not pass it through.

---

## Project name

| Variable | Description | Default |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | Pins the Compose project name so every invocation -- `start.sh`, `deployment/deploy.sh`, plain `docker compose` -- resolves to the same project and the same named volumes. Without it, Compose derives the project from the current directory name, which orphans volumes on hosts that deploy into timestamped release directories. | `mailyte-prod` |

---

## Database

These variables tell every service how to connect to MySQL.

| Variable | Description | Default |
|---|---|---|
| `DB_HOST` | Hostname of the MySQL server. Inside Docker Compose this is the service name `mysql`. | `mysql` |
| `DB_PORT` | MySQL port. | `3306` |
| `DB_NAME` | Database name. | `mailserver` |
| `DB_USER` | MySQL user the application connects as. | `mailuser` |
| `DB_PASSWORD` | Password for the application user. **Required** -- the stack refuses to start with a weak or missing value. | _(none)_ |
| `DB_ROOT_PASSWORD` | MySQL root password. Mirrored into `secrets/db_root_password` by `scripts/generate-secrets.sh`; MySQL itself boots from that file, not the env var. | _(none)_ |

!!! warning "Set real passwords -- the stack enforces it"
    The `secrets-check` container aborts startup if `DB_PASSWORD` (or any of the other required secrets) is missing, under 16 characters, or a known-weak placeholder. The `rag`, `oauth`, `url_protection`, and `jmap` containers additionally refuse to start on their own if their secret is missing -- they no longer fall back to built-in defaults, so a container run outside the compose stack fails closed too. Run `./scripts/generate-secrets.sh` instead of inventing values by hand. Changing `DB_PASSWORD` after the database volume exists also means updating the user inside MySQL (or recreating the volume).

**Example:**

```dotenv
DB_HOST=mysql
DB_NAME=mailserver
DB_USER=mailuser
DB_PASSWORD=Kj8#mPq!2xVnL9aQ
```

---

## Mail server identity

These variables control Postfix, Dovecot, and the DNS records the API generates for customers.

| Variable | Description | Default |
|---|---|---|
| `HOSTNAME` | The fully-qualified name of the mail server itself, e.g. `mail.example.com`. Appears in SMTP banners, HELO/EHLO, and generated DNS records. | `mail.example.com` |
| `DOMAIN` | The server's base domain, e.g. `example.com`. Also the default domain for admin subdomains (`api.`, `grafana.`, ...) in production. | `example.com` |
| `ADMIN_EMAIL` | Operator contact address; also the default sender for alert and report mail. | _(unset)_ |
| `MAIL_HOSTNAME` | The **customer-facing** mail hostname handed to email clients and used as the MX in generated records -- distinct from `HOSTNAME` when the server's PTR identity differs from the brand name. | _(unset, falls back to `HOSTNAME`)_ |
| `MAIL_SPF_HOST` | The host customers `include:` in their SPF records. Kept separate from `MAIL_HOSTNAME` on purpose, so renaming the mail host does not silently repoint every customer's SPF. | _(unset)_ |
| `POSTFIX_MESSAGE_SIZE_LIMIT` | Maximum size of a single message in bytes. `52428800` is 50 MB. | `52428800` |

!!! note "HOSTNAME vs DOMAIN vs MAIL_HOSTNAME"
    `DOMAIN` is the part after the `@`. `HOSTNAME` is this server's own identity (matches its PTR record). `MAIL_HOSTNAME` is what customers type into their mail clients. On a single-domain setup, `MAIL_HOSTNAME` can stay unset.

---

## Security secrets

Eight secrets are required before the stack will boot at all (see [Installation](installation.md)). The ones you will interact with most:

| Variable | Description |
|---|---|
| `WEBHOOK_SECRET` | Signs outgoing webhook payloads (HMAC). Consumers verify the signature. |
| `ADMIN_TOKEN_SECRET` | Admin token secret for platform-level API access. The `jmap` container requires it at startup (wired through compose since 2026-08-30 -- before that it silently ran on a built-in default). |
| `ADMIN_PASSWORD` | Legacy alias -- keep in sync with `ADMIN_TOKEN_SECRET`. |
| `OAUTH_TOKEN_SECRET` | Signing secret for the OAuth 2.0 / XOAUTH2 service. |
| `URL_HMAC_SECRET` | Signs Safe-Links URLs in the url_protection service. |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin login (user: `GRAFANA_ADMIN_USER`, default `admin`). |
| `DOVEADM_API_KEY` | Optional but recommended: authenticates the internal doveadm HTTP listener so revoking an SMTP credential flushes Dovecot's auth cache immediately instead of after up to an hour. Generate with `openssl rand -hex 24`. |

Generate a random secret when you need one by hand:

```bash
openssl rand -base64 32
```

---

## TLS certificates

In development, `start.sh` generates a self-signed certificate into `storage/ssl_certs/server.crt` and `storage/ssl_private/server.key` on first run. Postfix and Dovecot mount those directories.

In production, the `cert_manager` service obtains and renews Let's Encrypt certificates automatically (webroot HTTP-01 via Traefik) and reloads Postfix/Dovecot when they change. It is driven by:

| Variable | Description | Default |
|---|---|---|
| `ACME_EMAIL` | Email for Let's Encrypt expiry notices. | `admin@example.com` |
| `ACME_STAGING` | Use the Let's Encrypt staging CA. `true` in the base file; `docker-compose.prod.yml` forces `false`. | `true` |
| `CERT_SERVER_IPS` | This server's public IP(s), so cert_manager can skip SAN candidates whose DNS points elsewhere. Must be set explicitly in production. | _(unset)_ |
| `CERT_RENEWAL_DAYS` | Renew when fewer than this many days remain. | `30` |

There is no host-side certbot to configure -- do not set up your own certbot cron on the host, it will collide with cert_manager's locks.

---

## Redis

Redis provides caching, rate limiting, queues, and the Rspamd backend.

| Variable | Description | Default |
|---|---|---|
| `REDIS_HOST` | Hostname of the Redis server -- the service name inside Compose. | `redis` |
| `REDIS_PORT` | Port Redis listens on. | `6379` |
| `REDIS_PASSWORD` | Optional. When set, the Redis container starts with `--requirepass`. | _(empty)_ |

Unless you are running Redis externally (see `docker-compose.cloud.yml`), you probably never need to change these.

---

## Feature flags

| Variable | Description | Default |
|---|---|---|
| `TRACKING_ENABLED` | Open and click tracking for outgoing mail. The tracking injector rewrites links and adds a pixel on the submission path. | `true` |
| `ARCHIVE_OUTBOUND_ENABLED` | Archive every outbound message to the archiver service. | `true` |
| `TRANSPORT_RULES_ENABLED` | Enforce transport rules in Rspamd. Off by default -- read your rules before turning every draft rule into policy at once. | `false` |
| `AUTO_SUSPEND_ENABLED` | Automatic suspension of abusive SMTP API keys by the log ingestor. | `false` |
| `COMPOSE_PROFILES` | Optional webmail clients: `roundcube`, `sogo`, or both (comma-separated). The bundled `webmail` and `console` services are behind the `webmail` / `console` profiles, which `start.sh` and `deployment/deploy.sh` pass automatically. | _(unset)_ |

!!! info "Tracking and privacy"
    Email tracking inserts a tiny invisible image and rewrites links to pass through the tracking service. This is standard practice for transactional and marketing email, but be aware of privacy regulations (GDPR, CAN-SPAM) in your jurisdiction. Set `TRACKING_ENABLED=false` to turn it off.

---

## All variables at a glance

For quick reference, the variables you are most likely to touch during initial setup:

```dotenv
# Project
COMPOSE_PROJECT_NAME=mailyte-prod

# Database
DB_HOST=mysql
DB_NAME=mailserver
DB_USER=mailuser
DB_PASSWORD=<generated>
DB_ROOT_PASSWORD=<generated>

# Mail server identity
HOSTNAME=mail.example.com
DOMAIN=example.com
ADMIN_EMAIL=admin@example.com

# Secrets (generated by scripts/generate-secrets.sh)
WEBHOOK_SECRET=<generated>
OAUTH_TOKEN_SECRET=<generated>
URL_HMAC_SECRET=<generated>
ADMIN_PASSWORD=<generated>
ADMIN_TOKEN_SECRET=<generated>
GRAFANA_ADMIN_PASSWORD=<generated>

# TLS (production)
ACME_EMAIL=admin@example.com
ACME_STAGING=false

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Features
TRACKING_ENABLED=true
```

!!! note "This is not every variable"
    `.env.example` documents the exhaustive list, section by section, and the [Configuration Reference](../reference/configuration-reference.md) covers the rest. This page focuses on the ones you need to understand during initial setup.

## Next step

Your server is running and configured. Head to [First Steps](first-steps.md) to create your first organization, domain, and mailbox.
