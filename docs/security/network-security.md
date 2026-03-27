---
title: Network Security
description: Firewall rules, port exposure, Docker network isolation, and reverse proxy configuration.
---

# Network Security

The network is the first line of defense. Mailyte exposes a minimal set of ports and isolates internal services within Docker's network.

## Port Exposure

### Public Ports (must be accessible from the internet)

| Port | Service | Protocol | Purpose |
|------|---------|----------|---------|
| 25 | SMTP | TCP | Receiving email from other servers |
| 465 | SMTPS | TCP | Sending email from clients (implicit TLS) |
| 587 | Submission | TCP | Sending email from clients (STARTTLS) |
| 993 | IMAPS | TCP | Reading email (IMAP over TLS) |
| 995 | POP3S | TCP | Reading email (POP3 over TLS) |
| 80 | HTTP | TCP | Let's Encrypt ACME challenges |
| 443 | HTTPS | TCP | API, web interfaces |

### Internal Ports (block from external access)

| Port | Service | Why it must be internal |
|------|---------|----------------------|
| 3306 | MySQL | Database access — full data exposure |
| 6379 | Redis | Cache — no authentication by default |
| 6333 | Qdrant | Vector DB — no authentication |
| 9090 | Prometheus | Metrics — exposes system internals |
| 3000 | Grafana | Dashboards — has its own auth but still |
| 8080-8090 | Workers | Internal APIs — no auth required |
| 11332 | Rspamd | Milter — can control spam filtering |
| 11334 | Rspamd UI | Web UI — can modify spam rules |

## Firewall Configuration

### UFW (Ubuntu/Debian)

```bash
# Reset to defaults
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow public mail ports
sudo ufw allow 25/tcp    comment 'SMTP'
sudo ufw allow 465/tcp   comment 'SMTPS'
sudo ufw allow 587/tcp   comment 'Submission'
sudo ufw allow 993/tcp   comment 'IMAPS'
sudo ufw allow 995/tcp   comment 'POP3S'

# Allow web (cert renewal + API)
sudo ufw allow 80/tcp    comment 'HTTP - ACME'
sudo ufw allow 443/tcp   comment 'HTTPS - API'

# Allow SSH (don't lock yourself out!)
sudo ufw allow 22/tcp    comment 'SSH'

# Enable
sudo ufw enable
```

### iptables

```bash
# Flush existing rules
iptables -F

# Default policies
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# Allow loopback
iptables -A INPUT -i lo -j ACCEPT

# Allow established connections
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow SSH
iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# Allow mail ports
iptables -A INPUT -p tcp --dport 25 -j ACCEPT
iptables -A INPUT -p tcp --dport 465 -j ACCEPT
iptables -A INPUT -p tcp --dport 587 -j ACCEPT
iptables -A INPUT -p tcp --dport 993 -j ACCEPT
iptables -A INPUT -p tcp --dport 995 -j ACCEPT

# Allow web
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# Save
iptables-save > /etc/iptables/rules.v4
```

## Docker Network Isolation

All Mailyte containers run on an internal Docker network (`mailserver_network`). Containers can talk to each other by name, but external access requires explicit port mapping.

### Default Network

```yaml
# docker-compose.yml
networks:
  mailserver_network:
    driver: bridge
```

### Don't Bind Internal Ports to Host

In `docker-compose.yml`, only external-facing services should map ports:

```yaml
# Good — only accessible within Docker network
mysql:
  ports: []  # No port mapping

# Or bind to localhost only
mysql:
  ports:
    - "127.0.0.1:3306:3306"  # Only accessible from the host
```

!!! danger "Never bind MySQL or Redis to 0.0.0.0"
    The default `docker-compose.yml` may bind internal ports for development convenience. In production, remove those port mappings or bind to `127.0.0.1`.

### Production Port Mappings

```yaml
# Production docker-compose.override.yml
services:
  mysql:
    ports: []  # No external access

  redis:
    ports: []  # No external access

  prometheus:
    ports:
      - "127.0.0.1:9090:9090"  # localhost only

  grafana:
    ports:
      - "127.0.0.1:3000:3000"  # localhost only
```

## Reverse Proxy

Put the API behind a reverse proxy for TLS termination, rate limiting, and access control.

### Nginx Configuration

```nginx
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /etc/ssl/certs/api.crt;
    ssl_certificate_key /etc/ssl/private/api.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload";

    location /api/ {
        limit_req zone=api burst=20 nodelay;
        proxy_pass http://api:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Block access to internal endpoints
    location /metrics {
        deny all;
        return 403;
    }
}
```

### Grafana Access

If you need external Grafana access, use the reverse proxy with authentication:

```nginx
server {
    listen 443 ssl http2;
    server_name grafana.yourdomain.com;

    # Restrict to specific IPs
    allow 10.0.0.0/8;
    allow YOUR_OFFICE_IP;
    deny all;

    location / {
        proxy_pass http://grafana:3000;
    }
}
```

## IP Whitelisting

### API Keys

Restrict API keys to specific IPs:

```bash
curl -X POST http://localhost:8083/api/v1/admin/api-keys \
  -H "X-Admin-Password: ADMIN_PASS" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Production key",
    "ip_whitelist": ["203.0.113.1", "10.0.0.0/24"]
  }'
```

### Postfix mynetworks

Restrict which networks can send without authentication:

```
# Only localhost and Docker network
mynetworks = 127.0.0.0/8 [::1]/128 172.16.0.0/12
```

Never add public IPs to `mynetworks` — that creates an open relay.

## DNS Security

### DNSSEC

If your DNS provider supports it, enable DNSSEC. It prevents DNS spoofing attacks that could redirect your MX records.

### CAA Records

Control which certificate authorities can issue certs for your domain:

```
example.com.  IN  CAA  0 issue "letsencrypt.org"
example.com.  IN  CAA  0 iodef "mailto:security@example.com"
```
