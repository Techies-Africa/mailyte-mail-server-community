# Environment Variables

Complete reference for every environment variable used by Mailyte, grouped by category.

---

All variables are set in your `.env` file at the project root or passed directly through `docker-compose.yml`. Required variables have no default — the server won't start without them.

## Database

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_HOST` | MySQL server hostname | — | Yes |
| `DB_USER` | MySQL username | — | Yes |
| `DB_PASSWORD` | MySQL password | — | Yes |
| `DB_NAME` | Database name | `mailserver` | No |

> [!WARNING]
> Never commit `DB_PASSWORD` to version control. Use Docker secrets or a `.env` file that's in your `.gitignore`.

## Server Identity

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HOSTNAME` | Fully qualified hostname for the mail server | — | Yes |
| `DOMAIN` | Primary domain for this mail server instance | — | Yes |

The `HOSTNAME` should match your server's reverse DNS (PTR record). A typical value is `mail.yourdomain.com`. The `DOMAIN` is the part after the `@` in email addresses — usually `yourdomain.com`.

## SSL / TLS

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SSL_CERT_PATH` | Path to the SSL certificate file inside the container | — | Yes |
| `SSL_KEY_PATH` | Path to the SSL private key file inside the container | — | Yes |
| `ACME_EMAIL` | Email address for Let's Encrypt registration and renewal notices | — | Yes |

> [!TIP]
> If you're using the built-in cert-manager container, certificates land at `/etc/letsencrypt/live/$HOSTNAME/`. Set your paths accordingly:
> ```
> SSL_CERT_PATH=/etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
> SSL_KEY_PATH=/etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
> ```

## Redis

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `REDIS_HOST` | Redis server hostname | `redis` | No |
| `REDIS_PORT` | Redis server port | `6379` | No |

Redis is used by Rspamd for Bayesian statistics and by the API for rate limiting and caching. The defaults work out of the box if you're using the bundled Redis container.

## Postfix

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `POSTFIX_MESSAGE_SIZE_LIMIT` | Maximum email size in bytes | `52428800` (50 MB) | No |

The default 50 MB limit is generous for most setups. If your users send large attachments regularly, bump it up. Keep in mind that base64 encoding increases attachment size by about 33%, so a 50 MB limit actually caps raw attachments at roughly 37 MB.

## Cloud Provider

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `CLOUD_PROVIDER` | Cloud platform: `aws` or `azure` | — | No |
| `AWS_S3_BUCKET` | S3 bucket name for backups and large attachment storage | — | Only if `CLOUD_PROVIDER=aws` |

> [!NOTE]
> When `CLOUD_PROVIDER` is set, Mailyte uses platform-specific features like S3 for attachment offloading. Leave it unset if you're running on bare metal or a provider without special integration.

## Tracking

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `TRACKING_ENABLED` | Enable open/click tracking | `false` | No |
| `TRACKING_DOMAIN` | Domain used for tracking pixel and redirect URLs | — | Only if tracking is enabled |

When tracking is enabled, Postfix pipes outgoing mail through a tracking filter that rewrites links and injects a tracking pixel. The `TRACKING_DOMAIN` should point to your API server.

## Webhooks

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `WEBHOOK_SECRET` | Shared secret for signing webhook payloads (HMAC-SHA256) | — | No |
| `WEBHOOK_URLS` | Comma-separated list of URLs to receive event notifications | — | No |

Webhook events include delivery confirmations, bounces, spam complaints, and tracking events. The secret is used to sign payloads so your endpoint can verify they came from Mailyte.

## API Server

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `API_HOST` | Host the FastAPI server binds to | `0.0.0.0` | No |
| `API_PORT` | Port the FastAPI server listens on | `5000` | No |

The API server handles domain management, mailbox provisioning, and webhook delivery. In production, put it behind a reverse proxy and don't expose port 5000 directly to the internet.

## Example `.env` File

```bash
# Database
DB_HOST=mysql
DB_USER=mailyte
DB_PASSWORD=your-secure-password-here
DB_NAME=mailserver

# Server identity
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com

# SSL
SSL_CERT_PATH=/etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
SSL_KEY_PATH=/etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
ACME_EMAIL=admin@yourdomain.com

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Postfix
POSTFIX_MESSAGE_SIZE_LIMIT=52428800

# Cloud (optional)
# CLOUD_PROVIDER=aws
# AWS_S3_BUCKET=mailyte-backups

# Tracking (optional)
# TRACKING_ENABLED=true
# TRACKING_DOMAIN=track.yourdomain.com

# Webhooks (optional)
# WEBHOOK_SECRET=your-webhook-secret
# WEBHOOK_URLS=https://app.yourdomain.com/webhooks/mail

# API
API_HOST=0.0.0.0
API_PORT=5000
```

> [!WARNING]
> This is a template. Replace all placeholder values before starting the server. The `.env` file should have `600` permissions and be owned by root or the Docker user.
