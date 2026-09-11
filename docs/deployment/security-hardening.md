# Security Hardening

Lock down your Mailyte server so it only does what it should — and nothing else.

## Firewall Configuration

### UFW (Ubuntu/Debian)

```bash
# Reset to defaults
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (change port if you've moved it)
sudo ufw allow 22/tcp

# Allow mail services
sudo ufw allow 25/tcp     # SMTP
sudo ufw allow 587/tcp    # Submission
sudo ufw allow 465/tcp    # SMTPS
sudo ufw allow 143/tcp    # IMAP (drop if unused)
sudo ufw allow 993/tcp    # IMAPS
sudo ufw allow 995/tcp    # POP3S (drop if unused)
sudo ufw allow 443/tcp    # HTTPS (Traefik)
sudo ufw allow 80/tcp     # HTTP (ACME + redirect)

# Enable
sudo ufw enable
sudo ufw status numbered
```

> **Warning:** Docker publishes ports past ufw by writing its own iptables rules, so a host firewall is **not** what protects the internal services (MySQL, Redis, Grafana 3000, Prometheus 9090, the workers on 8081-8104). The protection is `docker-compose.prod.yml`, which binds every one of them to `127.0.0.1` (hardening applied in production 2026-08-22). Verify with `ss -tlnp | grep -v 127.0.0.1` — only the mail ports and 80/443 should show.

### iptables (Manual)

```bash
# Flush existing rules
sudo iptables -F

# Default policies
sudo iptables -P INPUT DROP
sudo iptables -P FORWARD DROP
sudo iptables -P OUTPUT ACCEPT

# Allow loopback
sudo iptables -A INPUT -i lo -j ACCEPT

# Allow established connections
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow SSH
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# Allow mail
sudo iptables -A INPUT -p tcp --dport 25 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 587 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 465 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 143 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 993 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 995 -j ACCEPT

# Allow HTTP/HTTPS
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# Rate limit SMTP connections (anti-abuse)
sudo iptables -A INPUT -p tcp --dport 25 -m connlimit --connlimit-above 20 -j DROP
sudo iptables -A INPUT -p tcp --dport 587 -m connlimit --connlimit-above 10 -j DROP

# Save rules
sudo iptables-save | sudo tee /etc/iptables/rules.v4
```

## Fail2ban

Fail2ban watches log files and bans IPs that show signs of brute-force attacks.

### Installation

```bash
sudo apt install -y fail2ban
sudo systemctl enable fail2ban
```

### Postfix Jail

Postfix logs to a real file on the host (`logs/mailer/postfix/mail.log`, bind-mounted), not to Docker's json logs — point fail2ban there:

```ini
# /etc/fail2ban/jail.d/mailyte-postfix.conf
[postfix]
enabled  = true
port     = smtp,submission,465
filter   = postfix
logpath  = /opt/mailyte/mailyte-email-server/logs/mailer/postfix/mail.log
maxretry = 5
findtime = 600
bantime  = 3600
action   = iptables-multiport[name=postfix, port="25,587,465"]
```

(Adjust the path to your checkout; on deploy-pipeline hosts, `logs/` is a symlink to the shared root — use the resolved path.)

### Postfix Filter

```ini
# /etc/fail2ban/filter.d/postfix.conf
[Definition]
failregex = warning: .*\[<HOST>\]: SASL .*authentication failed
            reject: RCPT from .*\[<HOST>\].*: 550
            reject: RCPT from .*\[<HOST>\].*: 554
            too many errors after AUTH from .*\[<HOST>\]
ignoreregex =
```

### Dovecot Jail

Dovecot's logs are likewise bind-mounted to `logs/mailer/dovecot/` on the host:

```ini
# /etc/fail2ban/jail.d/mailyte-dovecot.conf
[dovecot]
enabled  = true
port     = imap,imaps,pop3,pop3s
filter   = dovecot
logpath  = /opt/mailyte/mailyte-email-server/logs/mailer/dovecot/*.log
maxretry = 5
findtime = 600
bantime  = 3600
action   = iptables-multiport[name=dovecot, port="143,993,110,995"]
```

### API Brute-Force Protection Is Built In

Do not build a fail2ban jail for the API. The api service tracks failed authentication attempts in the database (`failed_auth_attempts`) and locks out offending client IPs itself — for both API-key and login attempts — and geo-blocking is available as its own service. Traefik terminates all HTTP at the edge, so the API port is not directly reachable in production anyway.

### Check Fail2ban Status

```bash
# Overall status
sudo fail2ban-client status

# Specific jail
sudo fail2ban-client status postfix

# Unban an IP
sudo fail2ban-client set postfix unbanip 203.0.113.50
```

## TLS Hardening

### Postfix TLS Settings

Postfix's `main.cf` is baked into the image at build time (`mailer/postfix/`) — a live `postconf -e` inside the container vanishes on the next recreate. Make changes in the build context and rebuild (`docker compose build postfix && docker compose up -d postfix`). The settings worth enforcing:

```ini
# mailer/postfix config -- baked into the image, rebuild to apply

# Enforce TLS for submission
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
smtpd_tls_mandatory_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtpd_tls_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtpd_tls_mandatory_ciphers = medium

# Opportunistic TLS for outbound
smtp_tls_security_level = may
smtp_tls_mandatory_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtp_tls_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1

# Enable TLS session caching
smtpd_tls_session_cache_database = btree:${data_directory}/smtpd_scache
smtp_tls_session_cache_database = btree:${data_directory}/smtp_scache
```

### Dovecot TLS Settings

Dovecot reads overrides from the bind-mounted `config/mailer/dovecot/` directory (mounted at `/etc/dovecot/custom`):

```ini
# config/mailer/dovecot/ overrides
ssl = required
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes
ssl_cipher_list = ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384
```

### Test Your TLS Configuration

```bash
# Test SMTP TLS
openssl s_client -connect mail.yourdomain.com:587 -starttls smtp </dev/null 2>/dev/null \
  | openssl x509 -noout -dates

# Check cipher suites
nmap --script ssl-enum-ciphers -p 993 mail.yourdomain.com
```

## Disable Unused Ports

If you don't need certain protocols, don't expose them. Put the change in a `docker-compose.override.yml` (copy the shipped `.example`) rather than editing the base file — and use `!override`, because Compose otherwise *appends* port lists, leaving the original binding in place:

```yaml
# docker-compose.override.yml
services:
  dovecot:
    ports: !override
      - "143:143"
      - "993:993"
      # 110/995 (POP3) and 4190 (ManageSieve) dropped
```

## Docker Security

Most of this is already built into the compose files — the list below is what to *verify*, not what to add:

### Internal Ports Bound to Localhost

`docker-compose.prod.yml` rebinds every internal service to `127.0.0.1` with the `!override` tag (applied in production 2026-08-22, after ~30 services were found publicly reachable). MySQL and Redis publish no host port at all, in any file. If you add a new service, follow the same pattern.

### Docker Socket Access Is Scoped

Nothing in the stack mounts `/var/run/docker.sock` writable except the `docker-proxy` container (`tecnativa/docker-socket-proxy`), which exposes exactly list/inspect/restart to the two services that need it (`monitoring` for auto-healing restarts, `cert_manager` for post-renewal reloads) — no exec, no image pulls, no volume or network access. It lives on its own `internal_only` network that the rest of the stack cannot reach. Traefik mounts the socket read-only for service discovery.

A `:ro` socket mount is **not** a real restriction on its own — the Docker API is an HTTP socket, and read-only mounting doesn't stop POST requests. The proxy's capability scoping is what does.

### Non-Root, Least Capability

- Worker images run as the unprivileged uid 10001 (`mailyte` user).
- `postfix`, `dovecot`, `rspamd`, `activesync`, and `docs` run with `cap_drop: ["ALL"]` plus only the specific capabilities their privilege-separation models need, and `no-new-privileges:true`.
- The `webmail` container runs with a read-only root filesystem and tmpfs mounts; `console` runs as uid 1001 with all capabilities dropped.

### Secrets as Files, Not Env Vars

The MySQL root password, the encryption KEK, and the archive identity are mounted files (`secrets/`), not environment variables — an env var on a running container is readable by anything with Docker API access for the container's entire lifetime; a 0400 file is not.

## Environment File Security

```bash
# Restrictive permissions (generate-secrets.sh sets this already)
chmod 600 .env

# Keep the secrets directory tight
chmod 700 secrets
ls -la secrets/
```

`.env` is already gitignored. Keep it owned by the deploy user (scripts and `start.sh` need to read it) — and remember it is one of the things `scripts/escrow-secrets.sh` bundles off-server, so re-run the escrow after changing secrets.

## SSH Hardening

```bash
# /etc/ssh/sshd_config
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
AllowUsers deploy-user

# Restart SSH
sudo systemctl restart sshd
```

## Automatic Security Updates

```bash
# Ubuntu / Debian
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades

# Verify it's working
cat /var/log/unattended-upgrades/unattended-upgrades.log
```

## Security Checklist

Run through this periodically:

- [ ] Firewall active with minimal open ports
- [ ] `ss -tlnp | grep -v 127.0.0.1` shows only mail ports + 80/443 (the prod override is in effect)
- [ ] Fail2ban running for SMTP and IMAP
- [ ] TLS 1.2+ only (no SSLv3, TLS 1.0, TLS 1.1)
- [ ] MySQL and Redis not exposed externally
- [ ] Console reachable only from `CONSOLE_ALLOWED_IPS`; Traefik dashboard behind basic auth
- [ ] `.env` permissions `600`, `secrets/` at `700`, escrow bundle current
- [ ] SSH uses key-only authentication
- [ ] Automatic security updates enabled
- [ ] DKIM, SPF, and DMARC configured
- [ ] No default passwords in use (`secrets-check` enforces the required set at startup; `rag`, `oauth`, `url_protection`, and `jmap` also fail closed individually on a missing or known-weak secret instead of falling back to built-in defaults)
- [ ] Certificates are valid and cert_manager is renewing them

> **Tip:** Run a security scan with tools like `lynis` (for the OS) and `testssl.sh` (for TLS configuration) to find issues you might have missed.
