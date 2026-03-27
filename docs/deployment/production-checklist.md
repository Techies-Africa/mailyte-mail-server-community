# Production Checklist

Go through this before declaring your Mailyte deployment production-ready — every item matters.

## DNS

- [ ] **A record** for `mail.yourdomain.com` points to your server IP
- [ ] **MX record** for `yourdomain.com` points to `mail.yourdomain.com`
- [ ] **SPF record** set: `v=spf1 mx ~all` (or stricter)
- [ ] **DKIM record** published with correct public key
- [ ] **DMARC record** set: `v=DMARC1; p=quarantine; ...`
- [ ] **PTR (reverse DNS)** matches your mail hostname
- [ ] DNS propagation verified from external location

```bash
# Verify all DNS records
dig +short mail.yourdomain.com A
dig +short yourdomain.com MX
dig +short yourdomain.com TXT
dig +short mail._domainkey.yourdomain.com TXT
dig +short _dmarc.yourdomain.com TXT
dig -x YOUR_SERVER_IP +short
```

## TLS / SSL

- [ ] Valid TLS certificate installed (not self-signed)
- [ ] Certificate covers `mail.yourdomain.com`
- [ ] Certificate auto-renewal configured (certbot cron job)
- [ ] Postfix configured to use TLS
- [ ] Dovecot configured to use TLS
- [ ] API served over HTTPS (via reverse proxy)
- [ ] TLS 1.2+ only (TLS 1.0 and 1.1 disabled)

```bash
# Test TLS on SMTP
openssl s_client -connect mail.yourdomain.com:587 -starttls smtp

# Test TLS on IMAP
openssl s_client -connect mail.yourdomain.com:993

# Check certificate expiry
echo | openssl s_client -connect mail.yourdomain.com:993 2>/dev/null \
  | openssl x509 -noout -dates
```

## Firewall

- [ ] Port 25 open (SMTP)
- [ ] Port 587 open (Submission)
- [ ] Port 993 open (IMAPS)
- [ ] Port 443 open (HTTPS, if using reverse proxy)
- [ ] Port 80 open (Let's Encrypt renewal)
- [ ] Monitoring ports (3000, 8080, 9090) **NOT** exposed to the internet
- [ ] Database port (3306) **NOT** exposed to the internet
- [ ] Redis port (6379) **NOT** exposed to the internet

```bash
sudo ufw status verbose
```

## Security

- [ ] All default passwords changed
- [ ] `.env` file permissions set to `600`
- [ ] Docker socket not exposed to untrusted containers
- [ ] fail2ban installed and configured for SMTP/IMAP
- [ ] SSH hardened (key-only auth, no root login)
- [ ] Automatic security updates enabled
- [ ] No unnecessary ports open

```bash
# Check .env permissions
ls -la .env
# Should show: -rw------- (600)

# Set if needed
chmod 600 .env
```

## Services

- [ ] All containers running and healthy

```bash
docker compose ps
# Every service should show "Up (healthy)"
```

- [ ] Postfix accepting connections on port 25
- [ ] Dovecot accepting connections on port 993
- [ ] Rspamd responding on port 11334
- [ ] MySQL accepting connections
- [ ] Redis responding to PING
- [ ] API responding on port 5000
- [ ] Workers processing jobs
- [ ] Health monitor running on port 8080

```bash
# Quick check
curl -s http://localhost:8080/health | python3 -m json.tool
```

## Email Delivery

- [ ] Test email sent and received successfully
- [ ] Test email doesn't land in spam
- [ ] SPF passes (check email headers)
- [ ] DKIM passes (check email headers)
- [ ] DMARC passes (check email headers)
- [ ] Inbound email delivery works
- [ ] Bounce handling works

```bash
# Check your mail setup with an external tool
# Visit: https://www.mail-tester.com
# Send an email to the address they give you
# Aim for a score of 9/10 or higher
```

## Backups

- [ ] MySQL backup configured and tested
- [ ] Mail storage backup configured
- [ ] Configuration files backed up
- [ ] Backup schedule set (daily minimum)
- [ ] Backup restoration tested (at least once)
- [ ] Backups stored off-server (S3, Azure, etc.)

```bash
# Test a backup right now
./scripts/backup.sh

# Test a restore to a temporary location
./scripts/restore.sh --dry-run
```

## Monitoring

- [ ] Prometheus scraping all targets
- [ ] Grafana accessible and dashboards loading
- [ ] Alert rules configured
- [ ] Alert notifications tested (Slack, email, webhook)
- [ ] Health monitor running and checking all services
- [ ] Auto-healing enabled

```bash
# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    print(f\"  [{t['health']}] {t['labels']['job']}\")
"

# Test alerting
curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{"labels":{"alertname":"TestAlert","severity":"info"},"annotations":{"summary":"Checklist test"}}]'
```

## Performance

- [ ] API response time < 500ms (p95)
- [ ] Mail queue size < 100 under normal load
- [ ] Disk usage < 70%
- [ ] Memory usage < 80%
- [ ] CPU usage < 70% under normal load

```bash
# Quick performance check
curl -w "API response: %{time_total}s\n" -o /dev/null -s http://localhost:5000/health
docker compose exec -T postfix postqueue -p | tail -1
df -h / | tail -1
free -h | head -2
```

## Documentation

- [ ] Admin credentials stored securely (password manager)
- [ ] Recovery procedures documented and accessible
- [ ] On-call rotation set up (if applicable)
- [ ] Runbook for common incidents written

## Final Sign-Off

Once everything passes:

```bash
# Take a snapshot/backup before declaring production
./scripts/backup.sh --label "pre-production"

# Run the full health check one more time
curl -s http://localhost:8080/health | python3 -m json.tool
```

> **Tip:** Bookmark this page. Run through it again after every major update or infrastructure change.
