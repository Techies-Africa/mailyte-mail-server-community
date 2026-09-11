---
title: "Troubleshooting: Network Connectivity"
description: Diagnose blocked ports, DNS resolution failures, firewall issues, and Docker networking problems.
---

# Network Connectivity

Email depends on the network more than most applications. Ports get blocked, DNS breaks, firewalls interfere, and Docker networking adds its own layer of complexity.

!!! note "Where to run diagnostics"
    The mail containers are intentionally minimal — `dig`, `nc`, `ping`, and `tcpdump` are **not installed** inside them. Run network diagnostics from the Docker host (or an external machine); for name resolution *inside* a container, use `getent hosts`, which is always present.

## Quick Connectivity Check

Run on the host:

```bash
# Check all required ports are listening
for port in 25 587 465 143 993 995 80 443; do
  ss -tlnp | grep ":$port " > /dev/null && echo "Port $port: LISTENING" || echo "Port $port: NOT LISTENING"
done

# External DNS resolution
dig MX gmail.com +short

# Outbound SMTP reachability
nc -zv gmail-smtp-in.l.google.com 25 -w 5
```

## Problem: Port 25 Blocked

Many cloud providers block outbound port 25 by default to prevent spam.

### Check If Blocked

```bash
# Test outbound port 25 from the host
nc -zv gmail-smtp-in.l.google.com 25 -w 5

# If it times out, port 25 is blocked outbound
```

### Fix by Provider

| Provider | How to Unblock |
|----------|---------------|
| **AWS** | Request removal from the EC2 SMTP throttle via Support Center |
| **Google Cloud** | Port 25 is permanently blocked. Use port 587 with a relay |
| **Azure** | Request via support ticket |
| **DigitalOcean** | Open a ticket, usually approved for legitimate use |
| **Hetzner** | Unblocked by default |
| **OVH** | Unblocked by default |

### Workaround: Use a Relay

If you can't unblock port 25, route outbound mail through a relay. These are standard Postfix settings in `mailer/postfix/config/main.cf` — note the file is baked into the image, so rebuild the postfix container after editing (`docker compose build postfix && docker compose up -d postfix`):

```
relayhost = [smtp-relay.example.com]:587
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
smtp_sasl_security_options = noanonymous
smtp_use_tls = yes
```

## Problem: DNS Resolution Failing

### Check DNS from Containers

```bash
# Container-name resolution (Docker's embedded DNS at 127.0.0.11)
docker exec api getent hosts mysql
docker exec postfix getent hosts redis

# External resolution from inside a container
docker exec postfix getent hosts gmail.com

# The container's resolver config
docker exec postfix cat /etc/resolv.conf
```

### Fix: Custom DNS

If external DNS resolution fails inside containers, add DNS servers to Docker:

```json
// /etc/docker/daemon.json
{
  "dns": ["8.8.8.8", "1.1.1.1"]
}
```

Then restart Docker:

```bash
sudo systemctl restart docker
```

Or per-container in docker-compose.override.yml:

```yaml
services:
  postfix:
    dns:
      - 8.8.8.8
      - 1.1.1.1
```

## Problem: Firewall Blocking Connections

### Check iptables

```bash
# List all rules
sudo iptables -L -n --line-numbers

# Check for REJECT or DROP rules on mail ports
sudo iptables -L -n | grep -E "25|587|465|993|995"
```

### Check ufw (Ubuntu)

```bash
sudo ufw status verbose
```

### Open Required Ports

```bash
# ufw
sudo ufw allow 25/tcp    # SMTP
sudo ufw allow 587/tcp   # Submission
sudo ufw allow 465/tcp   # SMTPS
sudo ufw allow 143/tcp   # IMAP (STARTTLS)
sudo ufw allow 993/tcp   # IMAPS
sudo ufw allow 995/tcp   # POP3S
sudo ufw allow 4190/tcp  # ManageSieve (optional)
sudo ufw allow 80/tcp    # HTTP (Let's Encrypt challenges, redirect)
sudo ufw allow 443/tcp   # HTTPS (API, webmail, autoconfig — via Traefik)
```

### Internal Ports

Internal services (MySQL, Redis, Prometheus, Grafana, Qdrant, the worker HTTP ports) must not be reachable from the internet. Since 2026-08-22, `docker-compose.prod.yml` rebinds all of them to `127.0.0.1` — Traefik on 443 is the only web entry point. Verify nothing else leaks:

```bash
# Anything listening on 0.0.0.0 beyond the mail/web ports is a problem
sudo ss -tlnp | grep '0.0.0.0' | grep -vE ':(25|587|465|143|993|995|4190|80|443) '
```

!!! warning "Docker bypasses ufw"
    Docker publishes ports with its own iptables rules, **in front of** ufw. A `ufw deny 3306` does not protect a port that compose publishes on `0.0.0.0` — the fix is the loopback binding in the compose file, not a ufw rule.

## Problem: Docker Networking

### Containers Can't Talk to Each Other

```bash
# Check that both containers are on the same network
docker network inspect mailyte-email-server_mailserver_network | grep -A3 "Containers"

# Test resolution + reachability from one container
docker exec api getent hosts mysql
```

(The network name is prefixed with the compose project name — `docker network ls | grep mailserver` finds the exact name on your machine.)

### Fix: Container Not on Network

```bash
docker network connect <network_name> <container_name>
```

Better: fix the service's `networks:` entry in the compose file and recreate it.

### Reaching the Host from a Container

`host.docker.internal` does not resolve on a custom bridge network unless the service declares it:

```yaml
services:
  myservice:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

### Port Conflicts

```bash
# Check for port conflicts on the host
sudo ss -tlnp | grep -E ":(25|587|465|993|995|80|443) "
```

If another service owns a port, either stop it or remap Mailyte's binding in `docker-compose.override.yml` with `ports: !override` — a plain `ports:` entry **appends** to the base file's list instead of replacing it, leaving the conflict in place.

## Problem: Outbound Email Blocked by ISP

Some ISPs block residential outbound port 25.

### Test

```bash
telnet smtp.gmail.com 25
```

If it hangs, your ISP is blocking it.

### Solutions

1. **Use a VPS** — run Mailyte on a cloud server, not at home
2. **Use a relay** — route through an SMTP relay service (see above)
3. **Use a VPN** — tunnel traffic through a VPN that doesn't block port 25

## Network Debugging Tools

All from the host:

```bash
# Trace the route to a mail server
traceroute gmail-smtp-in.l.google.com

# Check TCP connection
nc -zv mail.yourdomain.com 587

# Check if ports are open externally (from another machine)
nmap -p 25,587,993 mail.yourdomain.com

# DNS trace
dig +trace MX yourdomain.com

# Monitor SMTP traffic briefly
sudo tcpdump -i any -n port 25 -c 20
```

## Required Ports Reference

| Port | Protocol | Service | Direction | Required |
|------|----------|---------|-----------|----------|
| 25 | TCP | SMTP | In + Out | Yes |
| 465 | TCP | SMTPS | In | Recommended |
| 587 | TCP | Submission | In | Yes |
| 143 | TCP | IMAP (STARTTLS) | In | Recommended |
| 993 | TCP | IMAPS | In | Yes |
| 995 | TCP | POP3S | In | Optional |
| 4190 | TCP | ManageSieve | In | Optional |
| 80 | TCP | HTTP | In | Yes (Let's Encrypt) |
| 443 | TCP | HTTPS | In | Yes (API, webmail, autoconfig) |
