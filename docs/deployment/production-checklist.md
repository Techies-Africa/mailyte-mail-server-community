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

- [ ] `cert_manager` issued real certificates (not the self-signed dev pair, not Traefik's default cert): `ls -l storage/ssl_certs/`
- [ ] Certificate covers `mail.yourdomain.com`
- [ ] `ACME_STAGING=false` (the production override forces it) and `CERT_SERVER_IPS` is set
- [ ] Postfix and Dovecot serve the issued certificate
- [ ] API served over HTTPS via Traefik (`https://api.yourdomain.com`)
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

- [ ] Ports 25, 587, 465 open (SMTP)
- [ ] Ports 143, 993 (and 110/995/4190 if used) open (IMAP/POP3/Sieve)
- [ ] Ports 80 and 443 open (Traefik: ACME + HTTPS)
- [ ] Stack started with `docker-compose.prod.yml` — that is what binds every internal service (Grafana 3000, Prometheus 9090, Rspamd 11334, workers 8081-8104, Kafka 9092, Qdrant 6333, ...) to `127.0.0.1`
- [ ] Database port (3306) not published at all (the base file never publishes it)
- [ ] Redis port (6379) not published at all

```bash
sudo ufw status verbose

# The real check: what is actually listening on non-loopback addresses?
ss -tlnp | grep -v '127.0.0.1\|\[::1\]'
```

> **Warning:** Docker publishes ports past ufw with its own iptables rules. The loopback bind addresses in `docker-compose.prod.yml` are the protection; a ufw deny on a Docker-published port does nothing.

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
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps -a
# Every long-running service should show "Up (healthy)";
# secrets-check and migrate should show "Exited (0)"
```

- [ ] `secrets-check` and `migrate` exited 0 (they are one-shot jobs)
- [ ] Postfix accepting connections on port 25
- [ ] Dovecot accepting connections on port 993
- [ ] Rspamd responding on `127.0.0.1:11334` (loopback-bound in production)
- [ ] MySQL healthy, Redis responding to PING
- [ ] Both `api` replicas healthy
- [ ] Traefik routing `api.yourdomain.com`, `docs.`, `grafana.`, the autoconfig/autodiscover hosts, webmail, and console

```bash
# Quick check (on the server)
curl -s http://127.0.0.1:11334/ > /dev/null && echo rspamd ok
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# From anywhere
curl -s https://api.yourdomain.com/health | python3 -m json.tool
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

- [ ] systemd backup timers installed and active: `systemctl list-timers 'mailyte-backup-*'`
- [ ] `secrets/dr.env` configured (S3 destination + age encryption recipient)
- [ ] Secrets escrowed off-server: `./scripts/escrow-secrets.sh` (covers `encryption_kek`, mail_crypt keys, `.env`)
- [ ] A full backup completed in the last 24h and uploaded offsite
- [ ] Backup restoration tested (at least once): `./scripts/restore.sh --latest --dry-run`

```bash
# Take a full backup right now
./scripts/backup.sh --full

# List what exists, then rehearse a restore
./scripts/restore.sh --list
./scripts/restore.sh --latest --dry-run
```

## Monitoring

- [ ] Prometheus scraping all targets
- [ ] Grafana accessible (`https://grafana.yourdomain.com`) and dashboards loading
- [ ] Alert rules configured
- [ ] Alert notifications tested (Slack, email, webhook)
- [ ] `monitoring` service healthy (it restarts failed containers via the docker-proxy)

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
curl -w "API response: %{time_total}s\n" -o /dev/null -s https://api.yourdomain.com/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postfix postqueue -p | tail -1
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
# Take a full backup before declaring production
./scripts/backup.sh --full

# Run the full health check one more time
curl -s https://api.yourdomain.com/health | python3 -m json.tool
```

> **Tip:** Bookmark this page. Run through it again after every major update or infrastructure change.
