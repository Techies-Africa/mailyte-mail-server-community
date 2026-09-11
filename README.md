# Mailyte Mail Server — Community Edition

A self-hosted mail server you can run with one command and drive from a REST API.
Postfix, Dovecot and Rspamd underneath; domains, mailboxes, aliases, DKIM, filters
and webhooks over HTTP.

AGPL-3.0. Free to self-host, no seat limits, no feature keys.

---

## What you get

- **SMTP** — Postfix with TLS, DKIM signing, SPF and DMARC checks
- **IMAP/POP3** — Dovecot with SSL, Sieve filters, quotas
- **Spam filtering** — Rspamd with greylisting and DNSBL
- **Webmail** — Mailyte Webmail, Roundcube or SOGo, whichever you prefer
- **REST API** — domains, mailboxes, aliases, filters, SMTP credentials, certificates
- **Tracking** — opens and clicks, with unsubscribe handling
- **Webhooks** — delivery events, HMAC-signed
- **Rate limiting** — per organization, domain and mailbox
- **TLS** — Let's Encrypt, provisioned and renewed for you
- **Autoconfig** — Thunderbird, Outlook and Apple Mail find their own settings

## Requirements

- Docker with Compose v2
- A host with port 25 open — most cloud providers block it by default, and you
  have to ask them to unblock it
- A domain you control DNS for
- 2 GB RAM minimum, 4 GB comfortable

---

## Run it

```bash
git clone https://github.com/Techies-Africa/mailyte-mail-server-community.git
cd mailyte-mail-server-community

cp .env.example .env
```

Edit `.env`. At minimum:

```bash
HOSTNAME=mail.yourdomain.com     # this server's name
DOMAIN=yourdomain.com            # your mail domain
DB_ROOT_PASSWORD=<strong-password>
DB_PASSWORD=<strong-password>
ADMIN_PASSWORD=<strong-password>
ADMIN_TOKEN_SECRET=<random 32+ chars>
WEBHOOK_SECRET=<random 32+ chars>
```

The server refuses to start on placeholder or weak secrets — that is deliberate,
not a bug. Generate them with `openssl rand -base64 32`.

Then:

```bash
./start.sh              # or: docker compose up -d
docker compose ps       # everything healthy after ~30s
```

## Create your first mailbox

```bash
./scripts/setup-first-user.sh
```

Interactive: creates an organization, an API key, your domain and your first
mailbox. **Save the API key it prints** — it is not stored anywhere you can read
it back.

<details>
<summary>Prefer to do it by hand?</summary>

```bash
# 1. Organization
curl -X POST http://localhost:8083/api/v1/organizations/ \
  -H "X-Admin-Token: $ADMIN_TOKEN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}'

# 2. API key — save what comes back
curl -X POST http://localhost:8083/api/v1/organizations/my-org/api-keys \
  -H "X-Admin-Token: $ADMIN_TOKEN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-key"}'

# 3. Domain
curl -X POST http://localhost:8083/api/v1/domains/ \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"domain": "yourdomain.com", "organization_id": "my-org"}'

# 4. Mailbox
curl -X POST http://localhost:8083/api/v1/mailboxes/ \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"email": "you@yourdomain.com", "password": "...", "name": "Your Name"}'
```

</details>

## Pick a webmail

All three are opt-in, so nothing you do not want is running:

```bash
docker compose --profile webmail   up -d    # Mailyte Webmail   → :3200
docker compose --profile roundcube up -d    # Roundcube         → :8880
docker compose --profile sogo      up -d    # SOGo (+ calendar) → :8881
```

Log in with the mailbox address and password you just created.

## Connect a mail app

| | |
|---|---|
| IMAP | `mail.yourdomain.com`, port **993**, SSL/TLS |
| SMTP | `mail.yourdomain.com`, port **587**, STARTTLS |
| Username | your full email address |

Autoconfig is served, so Thunderbird and Apple Mail usually just need the address
and password.

> Mailbox passwords do **not** authenticate SMTP submission. To send through this
> server from an app or a script, create an SMTP credential:
> `POST /api/v1/smtp-credentials/`.

## DNS

Mail will not work with the outside world until these exist:

| Type | Name | Value |
|------|------|-------|
| A | `mail.yourdomain.com` | your server's IP |
| MX | `yourdomain.com` | `mail.yourdomain.com` (priority 10) |
| TXT | `yourdomain.com` | `v=spf1 mx -all` |
| TXT | `_dmarc.yourdomain.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com` |
| TXT | `mail._domainkey.yourdomain.com` | your DKIM public key |

Generate DKIM with `./start.sh` → option 21, then publish the key it prints.
Also set a PTR (reverse DNS) record with your host — without it most providers
will treat your mail as spam.

## Back it up

Before you put real mail on it:

```bash
sudo ./deployment/systemd/install-timers.sh
```

Nightly full and hourly incremental backups to local disk. Local copies protect
you from a bad migration, not from losing the machine — add an S3-compatible
bucket (four values in `.env`) for that. See
[docs/operations/backups.md](docs/operations/backups.md).

> Backups contain your DKIM private keys and `.env`. Keep them at mode 700 and
> encrypt them before they go anywhere shared.

---

## Ports

| Service | Port | |
|---|---|---|
| Postfix | 25, 587, 465 | SMTP |
| Dovecot | 143, 993, 110, 995, 4190 | IMAP/POP3, ManageSieve |
| API | 8083 | REST API |
| Rspamd | 11332, 11334 | spam filtering |
| Webhooks | 8081 | event delivery |
| Rate limiter | 8082 | sending limits |
| Tracking | 8086 | opens, clicks, unsubscribe |
| Analytics | 8087 | volume and deliverability |
| Templates | 8095 | template rendering |
| Autoconfig | 8100 | client auto-discovery |
| Docs | 8000 | this handbook, rendered |
| Webmail | 3200 / 8880 / 8881 | Mailyte / Roundcube / SOGo |

API reference: [`/api-docs`](http://localhost:8083/api-docs) (Swagger) or
[`/api-reference`](http://localhost:8083/api-reference) (Redoc).

## Known limits of this edition

Stated plainly so you are not debugging them:

- **`/api/v1/queue/*` returns 503.** The Postfix queue viewer needs a
  queue-manager service this edition does not ship. Inspect the queue on the host
  with `docker exec postfix postqueue -p`.
- **Security policies are editable but not enforced.** The DLP and geo-blocking
  screens store rules and record violations; the workers that act on them are
  Enterprise-only.
- **DKIM rotation is a script, not an API call** — `scripts/generate_dkim.py
  --rotate-all`.

## Tests

```bash
python -m pytest tests/unit -q
```

Integration tests need the stack running: `./start.sh test`.

## What Enterprise adds

Shared mailboxes and distribution groups · IMAP-to-IMAP migration · AI semantic
search · GDPR export and erasure tooling · engagement and scheduled reporting ·
queue management and delivery optimisation · Prometheus and Grafana · ActiveSync,
JMAP, CalDAV, OAuth · DLP and reputation enforcement · email archiving, PGP and
S/MIME · reseller and white-label.

[mailyte.com](https://mailyte.com) — or `http://localhost:8083/features` while
this server is running.

---

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports: [SECURITY.md](SECURITY.md).

**Note for contributors:** this repository is generated from the Mailyte Email
Server source. Code changes are made upstream and flow down here on release, so
a pull request against application code will be applied there rather than merged
here directly. Documentation, packaging and this README are maintained in this
repository.

## License

AGPL-3.0 — see [LICENSE](LICENSE).

Built by [Techies Africa](https://techies.africa).
