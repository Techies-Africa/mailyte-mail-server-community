# Mailyte Email Server

Enterprise email infrastructure built on Postfix, Dovecot, and Rspamd. Provides SMTP/IMAP/POP3 services with unlimited domains, cloud storage integration, email tracking, webhooks, and AI-powered search.

## Quick Start

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env with your domain, database, and cloud storage settings

# 2. Launch the interactive console
./start.sh
# Select option 1 (First-time setup) or option 2 (Start essential services)
```

That's it. The `start.sh` menu gives you access to everything — starting services, viewing logs, running migrations, health checks, backups, and more.

### CLI Mode

`start.sh` also works as a pass-through CLI:

```bash
./start.sh start mysql api       # Start specific services
./start.sh status                # Show service status
./start.sh logs postfix          # Follow Postfix logs
./start.sh health                # Run health checks
./start.sh help                  # See all commands
```

## Service Architecture

The full inventory lives in `docker-compose.yml` (~45 services). The important groups, with **host ports from the base compose file** (in production, `docker-compose.prod.yml` rebinds every internal port to `127.0.0.1` and routes HTTP through Traefik on 443):

### Essential Services (start these first)

| Service | Port(s) | Description |
|---------|---------|-------------|
| **redis** | — (internal) | Cache, rate limiting, Rspamd backend |
| **mysql** | — (internal) | Core database (MySQL 8.0.35, pinned) |
| **migrate** | — | Alembic schema migrations; runs once per `up`, gates the rest |
| **rspamd** | 11332, 11334 | Anti-spam filtering + DKIM signing (milter) |
| **postfix** | 25, 587, 465 | SMTP server |
| **dovecot** | 143, 993, 110, 995, 4190 | IMAP/POP3/ManageSieve server |
| **api** | 8083 (binds 8080) | FastAPI gateway — the whole management REST surface |

### Worker Services

| Service | Host port | Description |
|---------|-----------|-------------|
| **webhooks** | 8081 | Signed event delivery to external URLs |
| **rate_limiter** | 8082 | Rate limit counters; consulted by Postfix per message |
| **tracking** | 8086 | Email open/click tracking |
| **monitoring** | 8085 | Service health, container auto-restart via docker-proxy |
| **analytics** | 8087 | Aggregated analytics + scheduled reports |
| **dashboard** | 8088 | Analytics dashboard service |
| **archiver** | 8089 | Age-encrypted mail archive to S3 |
| **queue_manager** | 8090 | Postfix spool queue management (`postqueue`) |
| **rag** | 8091 | AI search worker |
| **storage_usage** | 8092 | Mailbox usage via Dovecot IMAP QUOTA |
| **encryption** | 8093 | S/MIME certificate operations |
| **delivery_optimizer** | 8094 | ISP throttling, IP warming, bounce processing |
| **templates** | 8095 | Email template management |
| **url_protection** | 8096 | Safe Links click-time URL verification |
| **oauth** | 8097 | OAuth2/XOAUTH2 for IMAP/SMTP |
| **jmap** | 8098 | JMAP protocol server |
| **migration** | 8099 | IMAP-to-IMAP mailbox migration |
| **autoconfig** | 8100 | Client auto-setup (autoconfig/autodiscover/MTA-STS) |
| **caldav** (+ **radicale**) | 8101 (5232) | CalDAV/CardDAV |
| **activesync** | 8084 | Mobile sync |
| **log_ingestor** | — | Tails Postfix's log into `mail_logs` + delivery webhooks |
| **dlp** / **totp** / **geo_blocking** | 8102 / 8103 / 8104 | Security services |

### Infrastructure & Frontends

| Service | Description |
|---------|-------------|
| **qdrant** | Vector DB for AI search (6333) |
| **kafka** + **zookeeper** | Provisioned broker — no service produces/consumes yet |
| **cert_manager** + **acme_webroot** | Let's Encrypt automation (webroot HTTP-01, SNI certs) |
| **prometheus** / **grafana** / **alertmanager** + exporters | Monitoring stack (9090 / 3000 / 9093) |
| **docs** | This handbook, MkDocs (8000) |
| **secrets-check** / **docker-proxy** | Fail-closed secrets validation; scoped Docker socket proxy |
| **webmail** / **console** / **roundcube** / **sogo** | Frontends behind compose profiles (`--profile webmail`, etc.) |

## Startup Order

Services have dependencies and should start in this order. The `start.sh` menu (option 2) handles this automatically, and Compose itself enforces the critical gates (`secrets-check` and `migrate` must succeed before anything that touches secrets or the schema starts):

1. **Gates** — secrets-check (fail-closed secrets validation), then mysql + redis, then migrate (Alembic)
2. **Anti-spam** — rspamd (needs redis)
3. **Mail** — postfix, dovecot (need mysql, redis, rspamd)
4. **API** — api (needs mysql, redis)
5. **Workers** — webhooks, tracking, rate_limiter, log_ingestor, etc.
6. **Optional** — rag, qdrant, cert_manager, frontends via profiles

## First Steps — Create Your First Mailbox

After the server is running, you need to set up an organization, domain, API key, and mailbox before you can send/receive email or log into Roundcube webmail.

### Option A: Use the setup script (recommended)

```bash
./scripts/setup-first-user.sh
```

This interactive script drives the one-time bootstrap endpoint and creates your first organization, domain, mailbox, and API key in a single call.

### Option B: Manual bootstrap via API

A fresh install has no API keys yet, so the API writes a **single-use bootstrap token** inside the api container. Use it once:

```bash
# 1. Read the single-use bootstrap token (only exists while no organization does)
TOKEN=$(docker compose exec -T api cat /app/data/bootstrap-token)

# 2. Bootstrap: creates org + domain + mailbox + API key in one call
curl -X POST http://localhost:8083/api/v1/bootstrap/ \
  -H "X-Bootstrap-Token: ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"organization_name": "my-org", "admin_email": "user@yourdomain.com", "admin_password": "your-password"}'
# → the response contains your API key. Save it.

# From here on, use the API key. For example, add another domain:
curl -X POST http://localhost:8083/api/v1/domains/ \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"domain": "yourdomain.com", "organization_id": "YOUR_ORG_ID"}'

# ...and another mailbox:
curl -X POST http://localhost:8083/api/v1/mailboxes/email-accounts \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "user2@yourdomain.com", "password": "another-password", "name": "Second User"}'
```

Interactive API docs live at `http://localhost:8083/api-docs` (Swagger) and `http://localhost:8083/api-reference` (ReDoc).

## Logging In

### Roundcube Webmail

Open `http://localhost:8880` and log in with your mailbox email and password.

### IMAP/POP3 (Thunderbird, Outlook, etc.)

- **IMAP Server:** `localhost` (port 993, SSL/TLS)
- **SMTP Server:** `localhost` (port 587, STARTTLS)
- **Username:** Your full email address
- **Password:** The password you set when creating the mailbox

### Webmail Options

Mailyte supports pluggable webmail via Docker Compose profiles:

```bash
# In .env — pick your webmail
COMPOSE_PROFILES=webmail          # Mailyte's own webmail (ghcr.io/techies-africa/mailyte-webmail)
COMPOSE_PROFILES=roundcube        # Roundcube (lightweight)
COMPOSE_PROFILES=sogo             # SOGo (webmail + calendar + contacts + ActiveSync)
COMPOSE_PROFILES=roundcube,sogo   # Both on different ports
# Omit for no webmail (API-only)
```

The `console` profile similarly enables the staff admin panel (ghcr.io/techies-africa/mailyte-console) — an operator tool that must sit behind an IP allowlist, never public DNS.

See [docs/guides/webmail-setup.md](docs/guides/webmail-setup.md) for adding custom webmail clients.

## Cloud Mode (Remote MySQL + Redis)

To use managed cloud databases (e.g., AWS RDS + ElastiCache) instead of local Docker containers:

1. Update `.env` with your remote hosts:

```bash
DB_HOST=your-instance.xxxx.us-east-1.rds.amazonaws.com
DB_PORT=3306
REDIS_HOST=your-cluster.xxxx.cache.amazonaws.com
REDIS_PORT=6379
```

2. Start with the cloud override:

```bash
./start.sh    # Select option 6 (Cloud DB/Redis)

# Or directly
docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d
```

This replaces local mysql/redis containers with no-op stubs (so dependency chains still resolve) and injects your remote DB_HOST/REDIS_HOST into every service.

You can combine it with other overrides:

```bash
# Cloud + dev (hot-reload)
docker compose -f docker-compose.yml -f docker-compose.cloud.yml -f docker-compose.dev.yml up -d

# Cloud + production
docker compose -f docker-compose.yml -f docker-compose.cloud.yml -f docker-compose.prod.yml up -d
```

## Development Setup

```bash
# Start with hot-reload enabled on all worker services
./start.sh    # Select option 4 (Development mode)

# Or via CLI
./start.sh dev
```

This uses `docker-compose.yml` + `docker-compose.dev.yml` which enables Flask debug mode and auto-reload for all Python workers.

### Running Tests

```bash
# Via the menu
./start.sh    # Select option 24

# Or directly
python3 -m pytest tests/unit/ -v          # Unit tests
python3 -m pytest tests/e2e/ -v           # End-to-end tests
python3 -m pytest tests/ -v --cov=worker  # All tests with coverage
```

Test dependencies: `pip install -r requirements-test.txt`

### Database Migrations

Uses Alembic, managed through `manage.py`:

```bash
python manage.py migrate              # Run pending migrations
python manage.py migrate:status       # Show migration status
python manage.py migrate:create name  # Create new migration
python manage.py migrate:rollback     # Roll back last migration
```

## Environment Configuration

Copy `.env.example` to `.env` and configure these key sections:

### Required

```bash
# Mail server identity
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
ADMIN_EMAIL=admin@yourdomain.com

# Database
DB_HOST=mysql
DB_PORT=3306
DB_NAME=mailserver
DB_USER=mailuser
DB_PASSWORD=<strong-password>
DB_ROOT_PASSWORD=<strong-password>

# Security
ADMIN_PASSWORD=<strong-password>
WEBHOOK_SECRET=<strong-secret>
```

### Optional

```bash
# Cloud storage (AWS S3 or Azure Blob)
CLOUD_PROVIDER=aws
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_DEFAULT_REGION=eu-west-2
AWS_BUCKET=your-bucket

# Email tracking
TRACKING_ENABLED=true
OPEN_TRACKING_ENABLED=true
CLICK_TRACKING_ENABLED=true

# RAG / AI search
RAG_MODE=local
EMBEDDING_PROVIDER=sentence_transformers
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2
```

See `.env.example` for the full list (~750 lines with documentation).

## DNS Configuration

Before going to production, configure these DNS records for each domain:

```dns
yourdomain.com.              IN  MX   10  mail.yourdomain.com.
mail.yourdomain.com.         IN  A        YOUR_SERVER_IP
yourdomain.com.              IN  TXT      "v=spf1 mx ip4:YOUR_SERVER_IP ~all"
_dmarc.yourdomain.com.       IN  TXT      "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com"
default._domainkey.yourdomain.com. IN TXT "v=DKIM1; k=rsa; p=YOUR_PUBLIC_KEY"
```

Generate DKIM keys: `./start.sh` > option 20, or `python3 scripts/generate_dkim.py`

## Project Structure

```
mailyte-email-server/
|-- start.sh                    # Interactive management console (start here)
|-- manage.py                   # Database migration CLI
|-- main.py                     # API entry point (non-Docker)
|
|-- docker-compose.yml          # Main service definitions
|-- docker-compose.dev.yml      # Development overrides (hot-reload)
|-- docker-compose.prod.yml     # Production overrides
|-- docker-compose.cloud.yml    # Cloud DB/Redis override (remote MySQL + Redis)
|
|-- mailer/                     # Core mail infrastructure
|   |-- postfix/                # SMTP server (config + policy services + tracking injector)
|   |-- dovecot/                # IMAP/POP3 server
|   |-- rspamd/                 # Anti-spam + DKIM signing
|   |-- cert_manager/           # SSL certificate management
|   |-- log_ingestor/           # Postfix log -> mail_logs + delivery webhooks
|   |-- intrusion_detection/    # Fail2ban configs (in-repo only; not deployed)
|   +-- log_analyzer/           # Log processing (in-repo only; not deployed)
|
|-- worker/                     # Microservices
|   |-- api/                    # FastAPI gateway (34 route modules)
|   |-- webhooks/               # Event dispatcher
|   |-- tracking/               # Open/click tracking
|   |-- rate_limiter/           # Rate limiting
|   |-- queue_manager/          # Postfix queue management
|   |-- analytics/              # Email analytics + reports
|   |-- monitoring/             # Service health + auto-restart
|   |-- rag/                    # AI search (Qdrant)
|   |-- storage_usage/          # Storage monitoring (IMAP QUOTA)
|   +-- ...                     # archiver, autoconfig, jmap, caldav, oauth,
|                               #   migration, templates, url_protection,
|                               #   delivery_optimizer, encryption, activesync,
|                               #   dashboard
|
|-- security/                   # DLP, TOTP, geo-blocking services
|-- shared/                     # Cross-service libs (config, db pool, webhook
|                               #   dispatcher, envelope encryption, S3 client)
|-- alembic/                    # Schema migrations (run by the migrate container)
|
|-- scripts/                    # Utility scripts
|   |-- mailyte-ctl.sh          # CLI management tool
|   |-- mailyte-monitor.sh      # Container monitoring
|   |-- staged-startup.sh       # Staged service startup
|   |-- quick-start.sh          # First-time setup wizard
|   |-- diagnostic.py           # System diagnostics
|   |-- container-health-monitor.py  # Auto-fix tool
|   |-- docker-health-check.sh  # In-container health checks
|   |-- generate_dkim.py        # DKIM key generation
|   |-- backup.sh               # Database backup
|   |-- restore.sh              # Database restore
|   +-- test_runner.py          # Test execution
|
|-- database/                   # Schema and migrations
|   |-- migrations/             # Alembic + SQL migrations
|   +-- models/                 # SQLAlchemy models
|
|-- storage/                    # Runtime data (gitignored)
|   |-- mail_data/              # Mailboxes
|   |-- ssl_certs/              # SSL certificates
|   |-- attachments/            # Email attachments
|   |-- dkim_keys/              # DKIM signing keys
|   +-- backups/                # Database backups
|
|-- tests/                      # Test suites
|   |-- unit/                   # Unit tests
|   |-- e2e/                    # End-to-end tests
|   +-- load/                   # Load tests
|
|-- docs/                       # MkDocs documentation site
|-- logs/                       # Service logs
+-- monitoring/                 # Prometheus/Grafana/Alertmanager configs
```

## Production Deployment

```bash
# 1. Configure production environment
cp .env.production.example .env
# Edit with production values (strong passwords, real domain, SSL settings)

# 2. Start in production mode
./start.sh    # Select option 5

# 3. Verify
./start.sh health
```

### Production Checklist

- [ ] Strong secrets generated (`scripts/generate-secrets.sh`) — the `secrets-check` gate refuses to start the stack on weak/missing values
- [ ] DNS records configured (MX, SPF, DKIM, DMARC) and verified via the API
- [ ] cert_manager configured (`ACME_STAGING=false` in production)
- [ ] Migrations applied — the `migrate` container runs Alembic automatically on every `up`; a failed migration stops the deploy
- [ ] `docker-compose.prod.yml` in use — it binds every internal service to `127.0.0.1`; only 25, 465, 587, 143, 993, 110, 995, 4190, 80, 443 face the internet (Docker bypasses ufw, so the bind address is the control)
- [ ] Monitoring enabled (Prometheus + Grafana)
- [ ] Backups configured and tested
- [ ] Health checks passing (`./start.sh health`)

## API Usage

Every endpoint is under `/api/v1/` and authenticates with an `X-API-Key` header (platform-scoped keys for cross-tenant operations; a fresh install gets its first key from the bootstrap flow above).

```bash
# List domains
curl -H "X-API-Key: your-api-key" http://localhost:8083/api/v1/domains/

# Add a domain (returns the DNS records to configure, including DKIM)
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com", "organization_id": "YOUR_ORG_ID"}' \
  http://localhost:8083/api/v1/domains/

# Add a mailbox
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "secure-password"}' \
  http://localhost:8083/api/v1/mailboxes/email-accounts
```

Full API reference: [docs/api/index.md](docs/api/index.md), or interactively at `http://localhost:8083/api-docs`.

## Troubleshooting

```bash
# Check what's running
./start.sh status

# View logs for a specific service
./start.sh logs postfix

# Run diagnostics (checks Docker, env, services, ports)
./start.sh    # Select option 11

# Auto-fix common container issues
./start.sh    # Select option 12

# Rebuild a broken service
./start.sh    # Select option 21
```

### Common Issues

**Port conflict on startup** — A local service is using the same port. Either stop it or change the port mapping in `.env` (e.g., `REDIS_PORT=6380`).

**MySQL won't start** — Check logs: `docker compose logs mysql`. Usually a password mismatch or existing volume with different credentials. Reset with `docker volume rm mailyte-email-server_mysql_data`.

**Postfix not sending** — Check DNS records, ensure port 25 is not blocked by your ISP/cloud provider, verify SSL certs are in place.

## License

Enterprise License - Contact for commercial use and enterprise support options.
