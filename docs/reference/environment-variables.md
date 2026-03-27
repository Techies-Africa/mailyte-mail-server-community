---
title: Environment Variables
description: Complete reference for every environment variable used by Mailyte, grouped by service.
---

# Environment Variables

All variables are set in your `.env` file at the project root or passed via `docker-compose.yml`. Required variables have no default — the server won't start without them.

## Database

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DB_HOST` | — | Yes | MySQL server hostname |
| `DB_PORT` | `3306` | No | MySQL server port |
| `DB_NAME` | `mailserver` | No | Database name |
| `DB_USER` | — | Yes | MySQL username |
| `DB_PASSWORD` | — | Yes | MySQL password |
| `DB_ROOT_PASSWORD` | — | Yes | MySQL root password |
| `DB_READ_HOST` | Same as `DB_HOST` | No | MySQL read replica host (for scaling) |
| `DB_POOL_SIZE` | `10` | No | Connection pool size |
| `DB_MAX_OVERFLOW` | `20` | No | Max pool overflow connections |
| `DB_POOL_RECYCLE` | `3600` | No | Recycle connections after N seconds |

!!! warning "Never commit passwords"
    Keep `DB_PASSWORD` and `DB_ROOT_PASSWORD` out of version control. Use a `.env` file listed in `.gitignore`.

## Server Identity

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `HOSTNAME` | `mail.example.com` | Yes | Server FQDN (must match PTR record) |
| `DOMAIN` | `example.com` | Yes | Primary email domain |
| `DEFAULT_TENANT_ID` | `default` | No | Default organization ID |
| `DEFAULT_DOMAIN_ID` | `default` | No | Default domain ID |

## SSL / TLS

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SSL_CERT_PATH` | — | Yes | SSL certificate file path |
| `SSL_KEY_PATH` | — | Yes | SSL private key file path |
| `ACME_EMAIL` | — | Yes | Let's Encrypt registration email |
| `ACME_STAGING` | `true` | No | Use LE staging CA (`false` for production) |
| `CERT_RENEWAL_DAYS` | `30` | No | Renew this many days before expiry |
| `CERT_CHECK_INTERVAL` | `21600` | No | Seconds between renewal checks |
| `WILDCARD_DOMAIN` | — | No | Domain for wildcard cert (e.g., `mailyte.com`) |
| `DNS_PROVIDER` | — | No | DNS provider for DNS-01 challenges |
| `SNI_CONFIG_PATH` | `/etc/ssl/sni` | No | SNI map file path |
| `DOCKER_RELOAD_ENABLED` | `true` | No | SIGHUP Postfix/Dovecot after cert changes |
| `POSTFIX_CONTAINER` | `postfix` | No | Postfix container name for reload |
| `DOVECOT_CONTAINER` | `dovecot` | No | Dovecot container name for reload |

## Redis

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `REDIS_HOST` | `redis` | No | Redis hostname |
| `REDIS_PORT` | `6379` | No | Redis port |

## API Server

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `API_HOST` | `0.0.0.0` | No | Bind address |
| `API_PORT` | `5000` | No | Listen port |
| `PORT` | `8080` | No | Alternative port (used in Docker) |
| `ADMIN_PASSWORD` | — | Yes | Admin authentication password |
| `ADMIN_TOKEN_SECRET` | — | Yes | JWT signing secret |
| `FLASK_ENV` | `production` | No | Flask environment mode |
| `FLASK_DEBUG` | `0` | No | Debug mode (set `1` for dev) |

## Postfix

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `POSTFIX_MESSAGE_SIZE_LIMIT` | `52428800` | No | Max email size in bytes (50 MB) |

## Tracking

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `TRACKING_ENABLED` | `false` | No | Enable open/click tracking |
| `TRACKING_DOMAIN` | — | If tracking on | Domain for tracking URLs |
| `TRACKING_SERVICE_URL` | `http://tracking:8086` | No | Internal tracking service URL |

## Webhooks

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `WEBHOOK_SECRET` | — | No | HMAC signing secret for webhooks |
| `WEBHOOK_URLS` | — | No | Comma-separated webhook endpoint URLs |
| `WEBHOOK_SERVICE_URL` | `http://webhooks:8081` | No | Internal webhook service URL |

## Cloud Provider

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `CLOUD_PROVIDER` | — | No | Cloud platform (`aws` or `azure`) |
| `AWS_ACCESS_KEY_ID` | — | If AWS | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | — | If AWS | AWS secret key |
| `AWS_DEFAULT_REGION` | `eu-west-2` | No | AWS region |
| `AWS_BUCKET` | — | If AWS | S3 bucket name |
| `AWS_S3_PREFIX` | `mailyte` | No | S3 key prefix |

## Worker Concurrency

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `TRACKING_WORKERS` | `2` | No | Tracking worker threads |
| `WEBHOOK_WORKERS` | `4` | No | Webhook delivery threads |
| `ANALYTICS_WORKERS` | `2` | No | Analytics processing threads |
| `QUEUE_MANAGER_WORKERS` | `4` | No | Queue processing threads |

## Monitoring

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `GRAFANA_PASSWORD` | `admin` | No | Grafana admin password |
| `PROMETHEUS_RETENTION` | `30d` | No | Prometheus data retention |

## RAG / AI Search

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `QDRANT_HOST` | `qdrant` | No | Qdrant vector database host |
| `QDRANT_PORT` | `6333` | No | Qdrant port |
| `OPENAI_API_KEY` | — | If RAG on | OpenAI API key for embeddings |

## Example `.env` File

```bash
# === Required ===
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
DB_HOST=mysql
DB_USER=mailuser
DB_PASSWORD=change-me-in-production
DB_ROOT_PASSWORD=change-me-too
ADMIN_PASSWORD=strong-admin-password
ADMIN_TOKEN_SECRET=random-32-char-string
ACME_EMAIL=admin@yourdomain.com

# === SSL ===
ACME_STAGING=false
SSL_CERT_PATH=/etc/ssl/certs
SSL_KEY_PATH=/etc/ssl/private

# === Features ===
TRACKING_ENABLED=true
TRACKING_DOMAIN=track.yourdomain.com
WEBHOOK_SECRET=your-webhook-signing-secret
WEBHOOK_URLS=https://your-app.com/webhooks/mailyte

# === Cloud (optional) ===
# AWS_ACCESS_KEY_ID=xxx
# AWS_SECRET_ACCESS_KEY=xxx
# AWS_BUCKET=your-bucket

# === Redis ===
REDIS_HOST=redis
REDIS_PORT=6379
```
