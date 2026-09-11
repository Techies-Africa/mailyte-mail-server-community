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

The stack is ~30 containers. `docker-compose.prod.yml` caps each one; the big consumers:

| Service | Production limit | Notes |
|---------|-----------------|-------|
| MySQL | 2 GB | Buffer pool is the big one |
| Postfix | 1 GB | Scales with queue size |
| Dovecot | 1 GB | Scales with active connections |
| Rspamd | 1 GB | Needs memory for spam rules |
| Kafka | 1 GB | Plus Zookeeper at 512 MB |
| Qdrant | 1 GB | Vector store for RAG search |
| Prometheus | 1 GB | Scales with retention |
| api | 512 MB | x2 replicas in production |
| webhooks / tracking | 512 MB | x2 replicas each in production |
| Redis, Grafana, monitoring | 512 MB | Each |
| Other workers (~15 services) | 256-512 MB | Each |
| Traefik | 256 MB | Reverse proxy |

The limits sum to well over the recommended 8 GB, but services do not all hit their caps simultaneously -- 8 GB runs the full stack comfortably at moderate volume. 4 GB works for development if you start only the essential services (`./start.sh` option 2).

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
| Docker Compose | v2.24+ (the override files use the `!override` / `!reset` YAML tags) |

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
| `openssl` | Secret generation, TLS inspection |
| `age` + `aws` CLI | Encrypted offsite backups (`scripts/backup.sh`) |

## Network Requirements

### Ports

These ports need to be open (inbound):

| Port | Protocol | Service | Required |
|------|----------|---------|----------|
| 25 | TCP | SMTP (mail delivery) | Yes |
| 587 | TCP | SMTP (submission) | Yes |
| 465 | TCP | SMTPS (implicit TLS) | Yes |
| 993 | TCP | IMAPS | Yes |
| 143 | TCP | IMAP (STARTTLS) | Optional |
| 995 | TCP | POP3S | Optional |
| 110 | TCP | POP3 (STARTTLS) | Optional |
| 4190 | TCP | ManageSieve | Optional |
| 80 | TCP | Traefik (HTTP, ACME HTTP-01 + redirect) | Yes |
| 443 | TCP | Traefik (HTTPS -- API, webmail, console, docs, Grafana) | Yes |

> **Warning:** In production, every internal service port -- the API's 8083, Prometheus's 9090, Grafana's 3000, the worker ports 8081-8104, and the rest -- is bound to `127.0.0.1` by `docker-compose.prod.yml` (hardening applied 2026-08-22). Everything HTTP reaches the internet only through Traefik on 443. Docker publishes ports past ufw by writing its own iptables rules, so the bind address -- not a host firewall -- is what actually protects these.

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

Almost everything lives in two places:

```
/var/lib/docker           50 GB+  (images, build cache, and the named volumes:
                                   mysql_data, redis_data, rspamd_data,
                                   postfix_spool, prometheus_data, ...)
<project root>/storage/   100 GB+ (bind-mounted data: mail_data/ -- the Maildirs,
                                   dkim_keys/, ssl_certs/, backups/, archive-spool/)
<project root>/logs/      10 GB   (Postfix/Dovecot/Rspamd and worker logs)
```

Keeping mail data, backups, and secrets under the project root is deliberate -- moving the server is "move one folder", and on deploy-pipeline hosts `storage/`, `secrets/`, and `logs/` are anchored outside the timestamped release directories so they survive every deploy.

Use SSDs for everything. Mail storage can use slower storage if cost is a concern, but MySQL and Redis should always be on fast disks.
