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
sudo ufw allow 993/tcp    # IMAPS
sudo ufw allow 443/tcp    # HTTPS (API reverse proxy)
sudo ufw allow 80/tcp     # HTTP (Let's Encrypt)

# DO NOT allow these from outside
# 3306 (MySQL), 6379 (Redis), 3000 (Grafana),
# 9090 (Prometheus), 8080 (Health Monitor), 5000 (API direct)

# Enable
sudo ufw enable
sudo ufw status numbered
```

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
sudo iptables -A INPUT -p tcp --dport 993 -j ACCEPT

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

```ini
# /etc/fail2ban/jail.d/mailyte-postfix.conf
[postfix]
enabled  = true
port     = smtp,submission,465
filter   = postfix
logpath  = /var/lib/docker/containers/*mailyte-postfix*/*-json.log
maxretry = 5
findtime = 600
bantime  = 3600
action   = iptables-multiport[name=postfix, port="25,587,465"]
```

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

```ini
# /etc/fail2ban/jail.d/mailyte-dovecot.conf
[dovecot]
enabled  = true
port     = imap,imaps,pop3,pop3s
filter   = dovecot
logpath  = /var/lib/docker/containers/*mailyte-dovecot*/*-json.log
maxretry = 5
findtime = 600
bantime  = 3600
action   = iptables-multiport[name=dovecot, port="143,993,110,995"]
```

### API Rate Limiting Jail

```ini
# /etc/fail2ban/jail.d/mailyte-api.conf
[mailyte-api]
enabled  = true
port     = 5000,443
filter   = mailyte-api
logpath  = /var/lib/docker/containers/*mailyte-api*/*-json.log
maxretry = 20
findtime = 60
bantime  = 600
```

```ini
# /etc/fail2ban/filter.d/mailyte-api.conf
[Definition]
failregex = "status_code":\s*(?:401|403).+"client":\s*"<HOST>"
ignoreregex =
```

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

```ini
# config/postfix/main.cf additions

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

```ini
# config/dovecot/10-ssl.conf
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

If you don't need certain protocols, don't expose them:

```yaml
# docker-compose.yml — only expose what you use
services:
  postfix:
    ports:
      - "25:25"
      - "587:587"
      # - "465:465"   # Uncomment only if needed

  dovecot:
    ports:
      - "993:993"
      # - "143:143"   # Don't expose unencrypted IMAP
      # - "995:995"   # POP3S — only if you have POP3 users
      # - "110:110"   # Never expose unencrypted POP3
```

## Docker Security

### Bind Internal Ports to Localhost

```yaml
services:
  api:
    ports:
      - "127.0.0.1:5000:5000"    # Not "5000:5000"

  mysql:
    # Don't expose the port at all — other containers
    # reach it via the Docker network
    # ports:
    #   - "3306:3306"  # NEVER do this

  redis:
    # Same — no external port mapping
    # ports:
    #   - "6379:6379"  # NEVER do this
```

### Limit Docker Socket Access

Only the health monitor needs the Docker socket (for auto-healing):

```yaml
health-monitor:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro  # Read-only
```

### Run Containers as Non-Root

```yaml
services:
  api:
    user: "1000:1000"   # Run as non-root user

  worker:
    user: "1000:1000"
```

### Read-Only Filesystems

```yaml
services:
  api:
    read_only: true
    tmpfs:
      - /tmp
      - /app/__pycache__
```

## Environment File Security

```bash
# Set restrictive permissions
chmod 600 .env
chown root:root .env

# Never commit .env to git
echo ".env" >> .gitignore

# Use Docker secrets for sensitive values (Swarm mode)
echo "my-secret-password" | docker secret create mysql_password -
```

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
- [ ] Fail2ban running for SMTP, IMAP, and API
- [ ] TLS 1.2+ only (no SSLv3, TLS 1.0, TLS 1.1)
- [ ] MySQL and Redis not exposed externally
- [ ] Monitoring ports behind authentication
- [ ] `.env` file permissions are `600`
- [ ] SSH uses key-only authentication
- [ ] Automatic security updates enabled
- [ ] Docker containers run as non-root where possible
- [ ] DKIM, SPF, and DMARC configured
- [ ] No default passwords in use
- [ ] Certificates are valid and auto-renewing

> **Tip:** Run a security scan with tools like `lynis` (for the OS) and `testssl.sh` (for TLS configuration) to find issues you might have missed.
