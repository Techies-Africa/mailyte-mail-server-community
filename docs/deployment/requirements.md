# System Requirements

What you need before deploying Mailyte — hardware, software, and network.

## Hardware Requirements

### Minimum (Development / Testing)

| Resource | Minimum |
|----------|---------|
| CPU | 2 cores |
| RAM | 4 GB |
| Disk | 40 GB SSD |
| Network | 100 Mbps |

### Recommended (Production, up to 50k emails/day)

| Resource | Recommended |
|----------|-------------|
| CPU | 4 cores |
| RAM | 8 GB |
| Disk | 100 GB SSD |
| Network | 1 Gbps |

### Large Scale (100k+ emails/day)

| Resource | Recommended |
|----------|-------------|
| CPU | 8+ cores |
| RAM | 16+ GB |
| Disk | 500 GB+ NVMe SSD |
| Network | 1 Gbps+ |

> **Note:** Disk requirements grow with user count and retention policy. Budget roughly 1 GB per 100 active mailboxes as a starting point, then monitor actual usage.

## Memory Breakdown by Service

Each service has its own memory needs. Here's where the RAM goes:

| Service | Minimum | Recommended | Notes |
|---------|---------|-------------|-------|
| Postfix | 256 MB | 512 MB | Scales with queue size |
| Dovecot | 256 MB | 512 MB | Scales with active connections |
| Rspamd | 512 MB | 1 GB | Needs memory for spam rules |
| MySQL | 512 MB | 2 GB | Buffer pool is the big one |
| Redis | 128 MB | 512 MB | Depends on cache/queue size |
| FastAPI | 256 MB | 512 MB | Scales with worker count |
| Workers | 256 MB | 512 MB | Per worker process |
| Health Monitor | 64 MB | 128 MB | Lightweight |
| Prometheus | 256 MB | 1 GB | Scales with metrics count and retention |
| Grafana | 128 MB | 256 MB | Mostly idle |

**Total:** 2.6 GB minimum, 6.4 GB recommended.

## Software Requirements

### Operating System

| OS | Version | Status |
|----|---------|--------|
| Ubuntu | 22.04 LTS, 24.04 LTS | Recommended |
| Debian | 11, 12 | Supported |
| RHEL / Rocky / Alma | 8, 9 | Supported |
| Amazon Linux | 2, 2023 | Supported |

### Docker

| Component | Minimum Version |
|-----------|----------------|
| Docker Engine | 24.0+ |
| Docker Compose | v2.20+ |

```bash
# Check your versions
docker --version
docker compose version
```

Install Docker if needed:

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in

# Verify
docker run hello-world
```

### Additional Tools

These aren't required but make life easier:

| Tool | Purpose |
|------|---------|
| `git` | Clone the repository |
| `curl` | Test endpoints |
| `htop` | Monitor system resources |
| `nc` (netcat) | Test port connectivity |
| `openssl` | TLS certificate management |
| `certbot` | Let's Encrypt certificates |

## Network Requirements

### Ports

These ports need to be open (inbound):

| Port | Protocol | Service | Required |
|------|----------|---------|----------|
| 25 | TCP | SMTP (mail delivery) | Yes |
| 587 | TCP | SMTP (submission) | Yes |
| 465 | TCP | SMTPS (implicit TLS) | Optional |
| 993 | TCP | IMAPS | Yes |
| 143 | TCP | IMAP (STARTTLS) | Optional |
| 995 | TCP | POP3S | Optional |
| 110 | TCP | POP3 (STARTTLS) | Optional |
| 5000 | TCP | API | Yes (internal or proxied) |
| 8080 | TCP | Health Monitor | Internal only |
| 80 | TCP | HTTP (cert renewal) | If using Let's Encrypt |
| 443 | TCP | HTTPS (API proxy) | If using reverse proxy |

> **Warning:** Never expose ports `8080` (health monitor), `9090` (Prometheus), or `3000` (Grafana) to the public internet without authentication.

### DNS Records

Before deployment, set up these DNS records:

| Record | Type | Value | Purpose |
|--------|------|-------|---------|
| `mail.yourdomain.com` | A | Your server IP | Mail server hostname |
| `yourdomain.com` | MX | `mail.yourdomain.com` (priority 10) | Mail routing |
| `yourdomain.com` | TXT | `v=spf1 mx -all` | SPF |
| `mail._domainkey.yourdomain.com` | TXT | DKIM public key | DKIM |
| `_dmarc.yourdomain.com` | TXT | `v=DMARC1; p=quarantine; ...` | DMARC |
| `mail.yourdomain.com` | PTR | Reverse DNS (set via hosting provider) | Deliverability |

### Outbound Connectivity

Your server needs to reach:

- Port 25 outbound (SMTP delivery to other mail servers)
- Port 53 outbound (DNS lookups)
- Port 443 outbound (Let's Encrypt, package updates, Docker pulls)

> **Note:** Some cloud providers (AWS, GCP, Azure) block outbound port 25 by default. You'll need to request an exception or use a relay service like SES or SendGrid.

## Disk Layout Recommendations

For production, separate your data onto different volumes:

```
/                   20 GB   (OS and containers)
/var/lib/docker     30 GB   (Docker images and layers)
/var/lib/mysql      50 GB+  (Database — size depends on usage)
/var/mail           100 GB+ (Mail storage — grows over time)
/var/log            10 GB   (Logs)
```

Use SSDs for everything. Mail storage can use slower storage if cost is a concern, but MySQL and Redis should always be on fast disks.
