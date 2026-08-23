# Mailyte Email Server — Community Edition

A self-hosted, programmable email server built on Postfix, Dovecot, and Rspamd. Full SMTP/IMAP/POP3 with email tracking, webhooks, and a REST API.

## Features

- **SMTP** — Postfix with TLS, DKIM signing, SPF/DMARC validation
- **IMAP/POP3** — Dovecot with SSL, Sieve filters, quota support
- **Spam filtering** — Rspamd with greylisting and DNSBL checks
- **Email tracking** — Open and click tracking with pixel injection and URL rewriting
- **Webhooks** — 50+ event types (delivered, bounced, opened, clicked, etc.) with HMAC signatures
- **REST API** — Manage domains, mailboxes, aliases, filters, rate limits, and certificates
- **Rate limiting** — Per-organization, per-domain, and per-mailbox sending limits
- **SSL/TLS** — Let's Encrypt auto-provisioning with SNI support
- **Autoconfig** — Email client auto-discovery (Thunderbird, Outlook, Apple Mail)
- **Webmail** — Roundcube or SOGo (pluggable via Docker Compose profiles)
- **Docker** — Single `docker compose up` to run everything

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/Techies-Africa/mailyte-mail-server-community.git
cd mailyte-mail-server-community

# 2. Copy and configure environment
cp .env.example .env
# Edit .env — at minimum change passwords and set your domain:
#   HOSTNAME=mail.yourdomain.com
#   DOMAIN=yourdomain.com
#   DB_PASSWORD=<strong-password>
#   DB_ROOT_PASSWORD=<strong-password>
#   ADMIN_TOKEN_SECRET=<random-32-char-string>
#   WEBHOOK_SECRET=<random-secret>

# 3. Start all services
./start.sh
# Or directly: docker compose up -d

# 4. Wait for all services to be healthy (~30 seconds)
docker compose ps
```

## First Steps — Create Your First Mailbox

After the server is running, you need to set up an organization, domain, API key, and mailbox before you can send/receive email or log into Roundcube webmail.

### Option A: Use the setup script (recommended)

```bash
./scripts/setup-first-user.sh
```

This interactive script creates your first organization, API key, domain, and mailbox.

### Option B: Manual setup via API

```bash
# The admin token is set in your .env as ADMIN_TOKEN_SECRET

# 1. Create an organization
curl -X POST http://localhost:8083/api/v1/organizations/ \
  -H "X-Admin-Token: YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}'

# 2. Create an API key (save the returned key!)
curl -X POST http://localhost:8083/api/v1/organizations/my-org/api-keys \
  -H "X-Admin-Token: YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-key"}'

# 3. Add your domain
curl -X POST http://localhost:8083/api/v1/domains/ \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"domain": "yourdomain.com", "organization_id": "my-org"}'

# 4. Create a mailbox
curl -X POST http://localhost:8083/api/v1/mailboxes/ \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "user@yourdomain.com", "password": "your-password", "name": "Your Name"}'
```

### Option C: Direct database seed (development only)

```bash
# Quick seed for local development/testing
docker exec mysql mysql -u root -p<ROOT_PASSWORD> mailserver -e "
  INSERT INTO organizations (id, name, active) VALUES ('dev-org', 'dev-org', 1);
  INSERT INTO api_keys (key_id, key_hash, name, permissions, organization_id, active)
    VALUES ('dev-api-key', SHA2('dev-api-key', 256), 'dev', '{\"read\": true, \"write\": true}', 'dev-org', 1);
  INSERT INTO domains (organization_id, domain, active) VALUES ('dev-org', 'test.local', 1);
"

# Create a mailbox (needs bcrypt hash)
docker exec -it api python3 -c "
from utils.database import get_db_connection
from utils.auth import hash_password
conn = get_db_connection()
cur = conn.cursor()
cur.execute(
    'INSERT INTO email_accounts (email, local_part, domain_id, organization_id, password, name, status) VALUES (%s, %s, (SELECT id FROM domains WHERE domain=%s), %s, %s, %s, %s)',
    ('user@test.local', 'user', 'test.local', 'dev-org', hash_password('testpass123'), 'Test User', 'active')
)
conn.commit()
print('Mailbox created: user@test.local / testpass123')
"
```

## Set Up Backups

**Do this before you put real mail on the server.** One command:

```bash
sudo ./deployment/systemd/install-timers.sh
```

That enables a nightly full backup and an hourly incremental, to local disk, via
systemd timers. `scripts/backup.sh` has always existed — this is what actually
runs it.

Local backups protect you from a bad migration or a mistaken deletion. They do
**not** survive losing the machine: for that, add an S3-compatible bucket (four
values in `.env`). Both, plus how to test a restore before you need one, are in
[docs/operations/backups.md](docs/operations/backups.md).

> Backups contain your DKIM private keys and `.env`. Keep them at mode 700, and
> encrypt them before putting them anywhere shared.

## Logging In

### Roundcube Webmail

Open `http://localhost:8880` and log in with the email and password you created above.

### IMAP/POP3 (Thunderbird, Outlook, etc.)

- **IMAP Server:** `localhost` (port 993, SSL/TLS)
- **SMTP Server:** `localhost` (port 587, STARTTLS)
- **Username:** Your full email address (e.g., `user@yourdomain.com`)
- **Password:** The password you set when creating the mailbox

### API

All API requests require the `X-API-Key` header:

```bash
curl http://localhost:8083/api/v1/domains/ -H "X-API-Key: YOUR_API_KEY"
```

Interactive docs: [localhost:8083/api-docs](http://localhost:8083/api-docs) (Swagger) or [localhost:8083/api-reference](http://localhost:8083/api-reference) (Redoc).

## DNS Setup (Production)

For email to work with external providers, configure these DNS records for your domain:

| Type | Name | Value |
|------|------|-------|
| MX | `yourdomain.com` | `mail.yourdomain.com` (priority 10) |
| A | `mail.yourdomain.com` | Your server's IP |
| TXT | `yourdomain.com` | `v=spf1 mx -all` |
| TXT | `_dmarc.yourdomain.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com` |
| TXT | `mail._domainkey.yourdomain.com` | *(DKIM public key — generate with `./start.sh` option 21)* |

## Services

| Service | Port | Description |
|---------|------|-------------|
| Postfix | 25, 587, 465 | SMTP server |
| Dovecot | 143, 993, 110, 995 | IMAP/POP3 server |
| Rspamd | 11332, 11334 | Spam filtering |
| API | 8083 | REST management API |
| Tracking | 8086 | Email open/click tracking |
| Webhooks | 8081 | Event notifications |
| Rate Limiter | 8082 | Sending rate control |
| Roundcube | 8880 | Webmail (profile: roundcube) |
| SOGo | 8881 | Groupware — webmail + calendar + contacts (profile: sogo) |
| Autoconfig | 8100 | Email client auto-discovery |
| Cert Manager | 80 | Let's Encrypt certificates |

## API Endpoints

```
POST   /api/v1/organizations/     Create organization
POST   /api/v1/domains/           Add domain
POST   /api/v1/mailboxes/         Create mailbox
POST   /api/v1/aliases/           Create alias
GET    /api/v1/filters/           List mail filters
POST   /api/v1/tracking/          Configure tracking
GET    /api/v1/webhooks/          List webhook endpoints
GET    /api/v1/rate-limiter/      View rate limits
POST   /api/v1/ssl/               Upload certificate
```

Full interactive docs at `/api-docs` (Swagger) or `/api-reference` (Redoc).

## Running Tests

```bash
# Unit tests (no Docker required)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim \
  bash -c "pip install -q pytest bcrypt fastapi && python -m pytest tests/unit/ -v"

# Integration tests (requires running services)
./start.sh test
```

## Enterprise Edition

Need more? [Mailyte Enterprise](https://mailyte.com) adds:

- Analytics and reporting dashboards
- AI-powered semantic email search (RAG)
- GDPR compliance tools (data export, erasure)
- IMAP-to-IMAP migration
- Shared mailboxes and distribution groups
- Multi-tenancy with tenant isolation
- Queue management and delivery optimization
- Prometheus/Grafana monitoring
- ActiveSync, JMAP, CalDAV, OAuth
- Reseller and white-label support
- Audit logging and DLP
- Cloud storage sync (S3/Azure/GCS)
- Email archiving and encryption (PGP/S/MIME)

See the full comparison at [localhost:8083/features](http://localhost:8083/features) when the server is running.

## License

AGPL-3.0 — See [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Security

To report vulnerabilities, see [SECURITY.md](SECURITY.md).

## Built by

[Techies Africa](https://techies.africa)
