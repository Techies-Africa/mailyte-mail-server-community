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
docker compose version

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

DKIM and DMARC records come after initial setup — Mailyte generates the DKIM key for you.

```bash
# Verify DNS (from any machine)
dig +short mail.yourdomain.com A
dig +short yourdomain.com MX
dig +short yourdomain.com TXT
```

## Step 3: Clone and Configure

```bash
# Clone the repository
git clone https://github.com/TechiesAfrica/mailyte-email-server.git
cd mailyte-email-server

# Create your environment file
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
# .env — key settings to change
DOMAIN=yourdomain.com
HOSTNAME=mail.yourdomain.com
SERVER_IP=203.0.113.10

# Database
MYSQL_ROOT_PASSWORD=generate-a-strong-password-here
MYSQL_DATABASE=mailyte
MYSQL_USER=mailyte
MYSQL_PASSWORD=another-strong-password

# Redis
REDIS_PASSWORD=yet-another-strong-password

# API
API_SECRET_KEY=random-64-character-string
API_ADMIN_EMAIL=admin@yourdomain.com
API_ADMIN_PASSWORD=initial-admin-password

# Monitoring
GRAFANA_PASSWORD=grafana-admin-password

# TLS (Let's Encrypt)
LETSENCRYPT_EMAIL=admin@yourdomain.com
```

> **Warning:** Use strong, unique passwords for every service. Never reuse passwords. A password manager can generate these for you.

## Step 4: Set Up TLS Certificates

```bash
# Install certbot
sudo apt install -y certbot

# Get certificates
sudo certbot certonly --standalone \
  -d mail.yourdomain.com \
  --email admin@yourdomain.com \
  --agree-tos \
  --no-eff-email

# The certificates will be in:
# /etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
# /etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
```

Update your `.env` to point to the certificates:

```bash
TLS_CERT_PATH=/etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
TLS_KEY_PATH=/etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
```

## Step 5: Start Mailyte

```bash
# Pull the latest images
docker compose pull

# Start all services
docker compose up -d

# Watch the startup logs
docker compose logs -f
```

Wait for all services to initialize. MySQL might take a minute on first run to set up the database.

```bash
# Check that everything is running
docker compose ps
```

You should see all containers with status `Up (healthy)`.

## Step 6: Verify Services

```bash
# Health check — should return all services healthy
curl -s http://localhost:8080/health | python3 -m json.tool

# Test SMTP
echo "EHLO test" | nc -w 3 localhost 25

# Test IMAP
echo "a1 CAPABILITY" | nc -w 3 localhost 993

# Test API
curl http://localhost:5000/health
```

## Step 7: Set Up DKIM

After Mailyte is running, generate your DKIM key:

```bash
# Generate DKIM key (Mailyte does this automatically on first run)
docker compose exec api python3 -m mailyte.cli dkim generate --domain yourdomain.com

# Get the DNS record to add
docker compose exec api python3 -m mailyte.cli dkim show --domain yourdomain.com
```

Add the DKIM TXT record to your DNS. Then add the DMARC record:

```
_dmarc.yourdomain.com  TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com; pct=100"
```

## Step 8: Configure Firewall

```bash
# Allow mail ports
sudo ufw allow 25/tcp    # SMTP
sudo ufw allow 587/tcp   # Submission
sudo ufw allow 993/tcp   # IMAPS
sudo ufw allow 443/tcp   # HTTPS (API proxy)
sudo ufw allow 80/tcp    # HTTP (cert renewal)

# Block monitoring ports from outside
# (They should only be accessible locally or via VPN)
sudo ufw deny 3000/tcp   # Grafana
sudo ufw deny 9090/tcp   # Prometheus
sudo ufw deny 8080/tcp   # Health monitor

# Enable the firewall
sudo ufw enable
sudo ufw status
```

## Step 9: Send a Test Email

```bash
# Send via API
curl -X POST http://localhost:5000/api/v1/emails/send \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "from": "test@yourdomain.com",
    "to": "your-personal@gmail.com",
    "subject": "Mailyte test",
    "body": "If you see this, your mail server is working."
  }'
```

Check your inbox (and spam folder). If the email arrived, you're live.

## Step 10: Set Up Auto-Renewal for Certificates

```bash
# Test renewal
sudo certbot renew --dry-run

# Add a cron job for auto-renewal
echo "0 3 * * * root certbot renew --quiet --deploy-hook 'docker compose -f /path/to/docker-compose.yml restart postfix dovecot'" \
  | sudo tee /etc/cron.d/certbot-renew
```

## Step 11: Enable Monitoring

If you didn't start the monitoring stack with the main compose file:

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

Open Grafana at `http://your-server:3000` (through a VPN or SSH tunnel — don't expose it publicly).

## What's Next

- Run through the [Production Checklist](production-checklist.md)
- Set up [Backups](backup-strategies.md)
- Configure [Alerting](../monitoring/alerting.md)
- Review [Security Hardening](security-hardening.md)
