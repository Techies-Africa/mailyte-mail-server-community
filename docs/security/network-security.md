---
title: Network Security
description: Port exposure, the 2026-08-22 loopback lockdown, Docker network isolation, and ingress configuration.
---

# Network Security

The network is the first line of defense. Mailyte exposes a minimal set of ports and isolates internal services within Docker's network.

## Port Exposure

### Public Ports (accessible from the internet)

| Port | Service | Protocol | Purpose |
|------|---------|----------|---------|
| 25 | SMTP | TCP | Receiving email from other servers |
| 465 | SMTPS | TCP | Sending email from clients (implicit TLS) |
| 587 | Submission | TCP | Sending email from clients (STARTTLS) |
| 143 / 993 | IMAP / IMAPS | TCP | Reading email |
| 110 / 995 | POP3 / POP3S | TCP | Reading email |
| 80 | HTTP (Traefik) | TCP | ACME challenges, redirect to HTTPS |
| 443 | HTTPS (Traefik) | TCP | API, webmail, console, autoconfig |

### Everything else is loopback-only in production

Since **2026-08-22**, `docker-compose.prod.yml` uses `ports: !override` to republish **every internal service on `127.0.0.1`**. Before that date, roughly 30 internal services were published on `0.0.0.0` — including **Prometheus (9090) and Qdrant (6333), both of which have no authentication at all** — and were verified reachable from the public internet. The published ports bypassed TLS and, for several services, all authentication.

The current production posture:

| Binding | Services |
|---------|----------|
| `127.0.0.1` only | All workers (8082–8104), Prometheus 9090, Grafana 3000, Alertmanager 9093, mysql-exporter 9104, redis-exporter 9121, Qdrant 6333, Rspamd 11332/11334, Kafka 9092, Radicale 5232, Traefik dashboard 8080, rspamd reinjection 10026 |
| No host port at all (`ports: !reset []`) | `api`, `webhooks`, `analytics` — they run with replicas behind Traefik, reachable only via 443 |
| No host port | MySQL 3306, Redis 6379 — internal Docker network only |

To reach an internal service in production, use SSH port-forwarding:

```bash
ssh -L 9090:127.0.0.1:9090 devops@<mail-host>   # then open http://localhost:9090
```

!!! warning "The dev compose file publishes on 0.0.0.0"
    The base `docker-compose.yml` maps worker ports without a bind address for development convenience. Never run the base file alone on an internet-facing host — always layer `docker-compose.prod.yml` (or replicate its `ports: !override` blocks).

## Firewall Configuration

Host firewalls are defense-in-depth on top of the loopback bindings — with the bindings in place, there is nothing on the internal ports for the firewall to protect, but keep both.

### UFW (Ubuntu/Debian)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Public mail ports
sudo ufw allow 25/tcp    comment 'SMTP'
sudo ufw allow 465/tcp   comment 'SMTPS'
sudo ufw allow 587/tcp   comment 'Submission'
sudo ufw allow 143/tcp   comment 'IMAP'
sudo ufw allow 993/tcp   comment 'IMAPS'
sudo ufw allow 110/tcp   comment 'POP3'
sudo ufw allow 995/tcp   comment 'POP3S'

# Web (cert renewal + HTTPS ingress)
sudo ufw allow 80/tcp    comment 'HTTP - ACME'
sudo ufw allow 443/tcp   comment 'HTTPS'

# SSH (don't lock yourself out!)
sudo ufw allow 22/tcp    comment 'SSH'

sudo ufw enable
```

!!! danger "Docker bypasses ufw for published ports"
    Docker programs its own iptables NAT rules, so a `0.0.0.0` port publish is reachable regardless of ufw's INPUT policy. This is exactly why the production compose binds to `127.0.0.1` at the publish level rather than relying on the host firewall — the binding is enforced where the hole is made.

## Docker Network Isolation

All Mailyte containers run on the `mailserver_network` bridge. Containers talk to each other by service name; external access requires an explicit port publish. A second network, `internal_only`, carries the Docker socket proxy so that `docker-proxy` is not reachable from the general service network.

### The Docker socket

No application container mounts `/var/run/docker.sock`. The monitoring service's restart capability goes through `docker-proxy` (`tecnativa/docker-socket-proxy`) which allows only `CONTAINERS`, `POST`, and `ALLOW_RESTARTS` — no exec, no images, no volumes, no secrets.

### Verifying the posture

```bash
# On the production host: anything listening on a public interface?
ss -tlnp | grep -v '127.0.0.1\|\[::1\]'
# Expect only: 25, 80, 110, 143, 443, 465, 587, 993, 995 (and sshd)
```

!!! note "Probing gotcha"
    `bash`'s `/dev/tcp` probes resolve through the loopback happily — test public reachability from a *different* host (`nc -vz <public-ip> 9090`), not from the server itself.

## Ingress: Traefik

Traefik terminates TLS for all HTTP surfaces and routes by hostname (API, webmail, console, autoconfig, JMAP, CalDAV, Grafana where enabled). Notes that matter for security:

- The Traefik dashboard is bound to `127.0.0.1:8080` — reach it via SSH forwarding, and via the authenticated `traefik.${DOMAIN}` router, never a raw port
- Traefik only routes to containers whose Docker healthcheck passes
- The console's IP allowlist middleware (`CONSOLE_ALLOWED_IPS`) defaults to `127.0.0.1/32` unless overridden — as of 2026-08-22 the deployed value is deliberately open (`0.0.0.0/0`) with authentication carried by the app itself

## IP Restrictions

### Per-organization SMTP allowlists

Organizations can restrict which client IPs may relay mail for their domains. Enforced live in Postfix's submission path via the `policy-ip-access` policy service, managed at `/api/v1/security/ip-rules` (table `ip_access_rules`, CIDR-capable).

### SMTP API-key credentials

Each SMTP credential can carry an `allowed_ips` list, enforced inside the Dovecot passdb query — see [Authentication](authentication.md#smtp-api-key-credentials).

### Postfix mynetworks

```
# main.cf -- deliberately minimal
mynetworks = 127.0.0.0/8 [::1]/128
```

Never add public IPs (or the whole Docker subnet) to `mynetworks` — `permit_mynetworks` short-circuits every later restriction, which is open-relay territory.

## DNS Security

### DNSSEC

If your DNS provider supports it, enable DNSSEC. It prevents DNS spoofing attacks that could redirect your MX records.

### CAA Records

Control which certificate authorities can issue certs for your domain:

```
example.com.  IN  CAA  0 issue "letsencrypt.org"
example.com.  IN  CAA  0 iodef "mailto:security@example.com"
```
