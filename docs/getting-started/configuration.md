---
title: Configuration
description: Understand the environment variables that control how your Mailyte server behaves.
---

# Configuration

This page walks you through the environment variables in your `.env` file, grouped by what they control. You do not need to set every single one right now -- the defaults are reasonable for development -- but you should understand what is available.

## How configuration works

Mailyte reads all its settings from environment variables. These are defined in the `.env` file at the root of the project, and Docker Compose passes them into each container automatically.

```
┌──────────┐     ┌──────────────────┐     ┌────────────┐
│  .env    │────>│  docker compose  │────>│ containers │
└──────────┘     └──────────────────┘     └────────────┘
```

After changing a variable, restart the affected service (or the whole stack):

```bash
# Restart everything
docker compose up -d

# Restart just the API
docker compose restart api
```

!!! tip "No rebuild needed"
    Environment variable changes do not require rebuilding images. A restart is enough.

---

## Database

These variables tell the API and workers how to connect to MySQL.

| Variable | Description | Default |
|---|---|---|
| `DB_HOST` | Hostname of the MySQL server. Inside Docker Compose this is usually the service name, like `db`. | `db` |
| `DB_USER` | MySQL user the application connects as. | `mailyte` |
| `DB_PASSWORD` | Password for the MySQL user. **Change this before first run.** | `changeme` |

!!! warning "Set a real password"
    The default `DB_PASSWORD` exists so the stack can start without errors. It is not secure. Set a strong password in `.env` before you run `docker compose up` for the first time. Changing it later means you also need to update the password inside the running MySQL container or recreate the volume.

**Example:**

```dotenv
DB_HOST=db
DB_USER=mailyte
DB_PASSWORD=Kj8#mPq!2xVnL9
```

---

## Mail server

These variables control Postfix and Dovecot -- the services that actually send and receive email.

| Variable | Description | Default |
|---|---|---|
| `HOSTNAME` | The fully-qualified domain name of the mail server itself, e.g. `mail.example.com`. This appears in SMTP banners and HELO/EHLO. | `mail.localhost` |
| `DOMAIN` | The primary mail domain, e.g. `example.com`. Used as the default domain for new accounts. | `localhost` |
| `POSTFIX_MESSAGE_SIZE_LIMIT` | Maximum size of a single email message in bytes. `25000000` is roughly 25 MB. | `25000000` |

!!! note "HOSTNAME vs DOMAIN"
    Think of `DOMAIN` as the part after the `@` in an email address (`user@example.com`). `HOSTNAME` is the name of the server that handles mail for that domain (`mail.example.com`). They are related but not the same thing.

**Example:**

```dotenv
HOSTNAME=mail.example.com
DOMAIN=example.com
POSTFIX_MESSAGE_SIZE_LIMIT=25000000
```

---

## Security and SSL

These variables handle TLS certificates and secrets used for authentication.

| Variable | Description | Default |
|---|---|---|
| `SSL_CERT_PATH` | Path to the directory containing your TLS certificate and private key. Inside the container, this is where Postfix and Dovecot look for `fullchain.pem` and `privkey.pem`. | `/etc/ssl/certs/mailyte` |
| `WEBHOOK_SECRET` | A shared secret used to sign outgoing webhook payloads. Consumers verify this signature to confirm the payload came from your server. | _(empty)_ |

!!! warning "Always set WEBHOOK_SECRET in production"
    Without a webhook secret, anyone who discovers your webhook consumer endpoints could forge events. Generate a random string of at least 32 characters:

    ```bash
    openssl rand -hex 32
    ```

**Example:**

```dotenv
SSL_CERT_PATH=/etc/ssl/certs/mailyte
WEBHOOK_SECRET=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
```

### Mounting certificates

Your TLS files need to be accessible inside the containers. The `docker-compose.yml` mounts the host path into the container at `SSL_CERT_PATH`. Place your certificate files on the host:

```
/etc/mailyte/ssl/
  fullchain.pem
  privkey.pem
```

!!! tip "Let's Encrypt works great"
    If you use Certbot or another ACME client, point `SSL_CERT_PATH` at the directory where it writes the live certificate. Set up a cron job to reload Postfix and Dovecot after renewal:

    ```bash
    certbot renew --post-hook "docker compose restart postfix dovecot"
    ```

---

## Redis

Redis provides caching and message brokering for the worker services.

| Variable | Description | Default |
|---|---|---|
| `REDIS_HOST` | Hostname of the Redis server. Inside Docker Compose, this is the service name. | `redis` |
| `REDIS_PORT` | Port Redis listens on. | `6379` |

Unless you are running Redis externally, you probably never need to change these.

**Example:**

```dotenv
REDIS_HOST=redis
REDIS_PORT=6379
```

---

## Feature flags

These variables turn optional features on or off.

| Variable | Description | Default |
|---|---|---|
| `TRACKING_ENABLED` | Set to `true` to enable open and click tracking for outgoing emails. When enabled, the tracking worker rewrites links and injects tracking pixels. | `false` |
| `CLOUD_PROVIDER` | Which cloud storage provider to use for backups and cloud sync. Supported values: `aws`, `gcp`, `azure`, `none`. | `none` |

**Example:**

```dotenv
TRACKING_ENABLED=true
CLOUD_PROVIDER=aws
```

!!! info "Tracking and privacy"
    Email tracking inserts a tiny invisible image and rewrites links to pass through the tracking service. This is standard practice for transactional and marketing email, but be aware of privacy regulations (GDPR, CAN-SPAM) in your jurisdiction. You can always leave it off.

---

## All variables at a glance

For quick reference, here is every variable covered on this page in one block:

```dotenv
# Database
DB_HOST=db
DB_USER=mailyte
DB_PASSWORD=your-strong-password

# Mail server
HOSTNAME=mail.example.com
DOMAIN=example.com
POSTFIX_MESSAGE_SIZE_LIMIT=25000000

# Security
SSL_CERT_PATH=/etc/ssl/certs/mailyte
WEBHOOK_SECRET=your-random-secret

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Features
TRACKING_ENABLED=false
CLOUD_PROVIDER=none
```

!!! note "This is not every variable"
    Mailyte has more configuration options than what is covered here. The [Configuration Reference](../reference/configuration-reference.md) page in the Reference section has the exhaustive list. This page focuses on the ones you need to understand during initial setup.

## Next step

Your server is running and configured. Head to [First Steps](first-steps.md) to create your first organization, domain, and mailbox.
