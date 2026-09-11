# Production Setup

Step-by-step guide to deploying Mailyte in production — from a blank server to a working email system.

## Before You Start

Make sure you have:

- A server meeting the [requirements](requirements.md)
- A domain name with DNS access
- SSH access to your server
- About 30-60 minutes

## Step 1: Prepare the Server

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Log out and back in, then verify
docker --version
docker compose version   # must be v2.24+

# Install useful tools
sudo apt install -y git curl htop net-tools
```

## Step 2: Set Up DNS

Before your mail server can send or receive email, DNS needs to be right. Set these up with your DNS provider and wait for propagation (can take up to 48 hours, usually much less).

| Record | Type | Name | Value |
|--------|------|------|-------|
| A | `mail.yourdomain.com` | Your server's IP | |
| MX | `yourdomain.com` | `mail.yourdomain.com` (priority 10) | |
| TXT (SPF) | `yourdomain.com` | `v=spf1 mx ~all` | |
| PTR | Your IP | `mail.yourdomain.com` | Set via hosting provider |

Also create A records for the admin subdomains Traefik serves over HTTPS — `cert_manager` requests certificates for each of them, and a name that does not resolve to this server fails its certificate order:

```
api.yourdomain.com  autoconfig.yourdomain.com  jmap.yourdomain.com
caldav.yourdomain.com  docs.yourdomain.com  grafana.yourdomain.com
traefik.yourdomain.com  console.yourdomain.com
```

(That list is the default of `TRAEFIK_ADMIN_SUBDOMAINS`; trim it in `.env` if you serve fewer.)

DKIM and DMARC records come after initial setup — Mailyte generates the DKIM key when a domain is created through the API, and the API response tells you exactly what to publish.

```bash
# Verify DNS (from any machine)
dig +short mail.yourdomain.com A
dig +short yourdomain.com MX
dig +short yourdomain.com TXT
```

## Step 3: Clone and Configure

```bash
# Clone the repository
git clone https://github.com/Techies-Africa/mailyte-email-server.git
cd mailyte-email-server

# Generate .env (from .env.example) with strong random secrets.
# The stack refuses to boot with weak or missing secrets, so do not skip this.
./scripts/generate-secrets.sh

# Generate the key-encryption key for DKIM/PGP/S-MIME private keys.
# BACK THIS FILE UP -- losing secrets/encryption_kek makes every key
# encrypted under it permanently unrecoverable.
./scripts/generate_dkim_kek.sh
```

Then edit `.env` and set the deployment-specific values:

```bash
# .env — key settings to change
DOMAIN=yourdomain.com
HOSTNAME=mail.yourdomain.com
ADMIN_EMAIL=admin@yourdomain.com

# Let's Encrypt
ACME_EMAIL=admin@yourdomain.com
ACME_STAGING=false
# This server's public IP(s) -- cert_manager skips SAN candidates whose
# DNS points elsewhere, and cannot discover its own public address from
# inside a container.
CERT_SERVER_IPS=203.0.113.10

# Traefik dashboard basic auth (referenced by docker-compose.prod.yml)
TRAEFIK_DASHBOARD_USER=admin
TRAEFIK_DASHBOARD_PASS=a-strong-password

# Operator console access -- fails closed to loopback-only when unset.
# Comma-separated CIDRs of your operator networks.
CONSOLE_ALLOWED_IPS=203.0.113.0/24
```

> **Warning:** `scripts/generate-secrets.sh` already produced strong values for the database, webhook, OAuth, HMAC, admin, and Grafana secrets. Do not replace them with hand-typed passwords.

## Step 4: TLS Certificates — Do Nothing

There is no host-side certbot step. The `cert_manager` service obtains Let's Encrypt certificates automatically once the stack is up:

- ACME HTTP-01 challenges are served through Traefik (which owns ports 80/443) via the `acme_webroot` service.
- Certificates land in `storage/ssl_certs` / `storage/ssl_private`; Postfix and Dovecot are reloaded automatically when they change.
- Renewal is automatic (`CERT_RENEWAL_DAYS=30`, checked every 6 hours).

Until the first certificates are issued, Traefik serves its self-signed default certificate — that is expected for the first few minutes.

!!! danger "Do not install certbot on the host"
    A host-side certbot competes with cert_manager for the ACME challenge path and its lock files. One ACME client issues everything on this stack, and it runs in a container.

## Step 5: Start Mailyte

```bash
# Production mode = base file + production override
./start.sh prod

# ...which is equivalent to:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The production override adds Traefik, binds every internal service port to `127.0.0.1`, applies per-service memory limits and log rotation, runs `api`/`webhooks`/`tracking` at 2 replicas, and disables all debug flags.

```bash
# Watch the startup
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f

# Check that everything is running
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

`secrets-check` and `migrate` should show `Exited (0)` — they are one-shot jobs. Everything else should reach `Up (healthy)`.

!!! info "Deploy-pipeline hosts use deployment/deploy.sh instead"
    On hosts managed by the deploy pipeline, releases land in timestamped directories with a `current` symlink and `deployment/deploy.sh` runs the whole sequence: pre-deploy backup, image builds, singleton bring-up, and rolling updates of the replicated services. See the [Deployment index](index.md#how-production-deploys-actually-run). Hotfixes on such a host must be applied through the `current` symlink, never a pinned release directory.

## Step 6: Verify Services

The API has no host-published port in production (Traefik is the only way in), so verify via container health and the public hostname:

```bash
# Container health
docker inspect --format='{{.State.Health.Status}}' $(docker ps -q --filter "label=com.docker.compose.service=api") | sort | uniq -c

# Test SMTP
echo "QUIT" | nc -w 3 localhost 25

# Test IMAPS (should print the certificate chain)
openssl s_client -connect localhost:993 -quiet </dev/null

# Test the API through Traefik (from anywhere, once DNS resolves)
curl -s https://api.yourdomain.com/health | python3 -m json.tool
```

## Step 7: Bootstrap the First Organization

```bash
./scripts/setup-first-user.sh
```

This calls the API's one-time bootstrap endpoint and creates your first organization, domain, mailbox, and API key. The API response for the domain (and `GET /api/v1/domains/`) contains the exact DKIM and DMARC records to publish:

```
mail._domainkey.yourdomain.com  TXT  "v=DKIM1; k=rsa; p=..."
_dmarc.yourdomain.com           TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com"
```

For the operator console (`https://console.yourdomain.com`, gated by `CONSOLE_ALLOWED_IPS`), print its separate first-run token with:

```bash
./start.sh console-token
```

## Step 8: Configure Firewall

The production override already binds internal services to loopback, so the firewall's job is only the genuinely public ports:

```bash
sudo ufw allow 22/tcp    # SSH -- before enabling!
sudo ufw allow 25/tcp    # SMTP
sudo ufw allow 587/tcp   # Submission
sudo ufw allow 465/tcp   # SMTPS
sudo ufw allow 143/tcp   # IMAP
sudo ufw allow 993/tcp   # IMAPS
sudo ufw allow 110/tcp   # POP3 (drop if unused)
sudo ufw allow 995/tcp   # POP3S (drop if unused)
sudo ufw allow 4190/tcp  # ManageSieve (drop if unused)
sudo ufw allow 80/tcp    # HTTP (ACME + redirect)
sudo ufw allow 443/tcp   # HTTPS

sudo ufw enable
sudo ufw status
```

!!! warning "ufw does not see Docker-published ports"
    Docker writes its own iptables rules, so a `ufw deny` on a Docker-published port does nothing. The protection for internal services is the `127.0.0.1` bind address in `docker-compose.prod.yml` — which is why running the base compose file alone in production is dangerous.

## Step 9: Send a Test Email

```bash
# Interactive helper (option 20), or:
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postfix sendmail your-personal@gmail.com <<'EOF'
From: test@yourdomain.com
To: your-personal@gmail.com
Subject: Mailyte test

If you see this, your mail server is working.
EOF
```

Check your inbox (and spam folder). Then send an email *to* an address on the server and confirm it arrives — inbound and outbound are separate paths.

## Step 10: Install the Backup Timers

Backups run from host systemd, not from a container — a backup container that stops when the stack stops is missing exactly when it is needed.

```bash
sudo ./deployment/systemd/install-timers.sh          # mail role: mailyte-backup-full (daily 02:30),
                                                     # mailyte-backup-incremental (hourly at :15),
                                                     # mailyte-mail-sync (every 15 min)
sudo ./deployment/systemd/install-timers.sh --status
```

Offsite settings (S3 bucket, age encryption recipient) come from `secrets/dr.env` — see [Backup Strategies](backup-strategies.md). Then escrow the secrets that backups cannot recreate:

```bash
./scripts/escrow-secrets.sh
```

## What's Next

- Run through the [Production Checklist](production-checklist.md)
- Understand [Backups](backup-strategies.md) and [Disaster Recovery](disaster-recovery.md)
- Configure [Alerting](../monitoring/alerting.md)
- Review [Security Hardening](security-hardening.md)
