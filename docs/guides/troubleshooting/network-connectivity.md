---
title: "Troubleshooting: Network Connectivity"
description: Diagnose blocked ports, DNS resolution failures, firewall issues, and Docker networking problems.
---

# Network Connectivity

Email depends on the network more than most applications. Ports get blocked, DNS breaks, firewalls interfere, and Docker networking adds its own layer of complexity.

## Quick Connectivity Check

```bash
# Check all required ports from inside the server
for port in 25 587 465 993 995 80 443; do
  ss -tlnp | grep ":$port " > /dev/null && echo "Port $port: LISTENING" || echo "Port $port: NOT LISTENING"
done

# DNS resolution
docker exec -it postfix dig MX gmail.com +short

# Outbound SMTP
docker exec -it postfix nc -zv gmail-smtp-in.l.google.com 25
```

## Problem: Port 25 Blocked

Many cloud providers block outbound port 25 by default to prevent spam.

### Check If Blocked

```bash
# Test outbound port 25
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

If you can't unblock port 25, route outbound mail through a relay:

```bash
# config/mailer/postfix/custom/main.cf
relayhost = [smtp-relay.example.com]:587
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
smtp_sasl_security_options = noanonymous
smtp_use_tls = yes
```

## Problem: DNS Resolution Failing

### Check DNS from Containers

```bash
# Test from different containers
docker exec -it postfix dig A gmail.com +short
docker exec -it api dig A mysql +short
docker exec -it dovecot nslookup mysql
```

### Check Docker DNS

```bash
# Docker's default DNS
docker exec -it postfix cat /etc/resolv.conf
```

Docker containers use `127.0.0.11` (Docker's embedded DNS) to resolve other container names.

### Fix: Custom DNS

If external DNS resolution fails, add DNS servers to Docker:

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

Or per-container in docker-compose.yml:

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
sudo ufw allow 993/tcp   # IMAPS
sudo ufw allow 995/tcp   # POP3S
sudo ufw allow 80/tcp    # HTTP (Let's Encrypt)
sudo ufw allow 443/tcp   # HTTPS (API)

# iptables
sudo iptables -A INPUT -p tcp --dport 25 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 587 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 993 -j ACCEPT
```

### Internal Ports to Block

These should NOT be accessible from the internet:

```bash
# Block external access to internal services
sudo ufw deny from any to any port 3306  # MySQL
sudo ufw deny from any to any port 6379  # Redis
sudo ufw deny from any to any port 9090  # Prometheus
sudo ufw deny from any to any port 3000  # Grafana
sudo ufw deny from any to any port 6333  # Qdrant
```

## Problem: Docker Networking

### Containers Can't Talk to Each Other

```bash
# Check that all containers are on the same network
docker network inspect mailserver_network | grep -A3 "Containers"

# Ping between containers
docker exec -it api ping -c 3 mysql
docker exec -it postfix ping -c 3 redis
```

### Fix: Container Not on Network

```bash
# Manually connect a container to the network
docker network connect mailserver_network <container_name>
```

### DNS Resolution Between Containers

Docker containers resolve each other by container name. If `api` can't reach `mysql`:

```bash
# Check the container name matches what's in the config
docker ps --format "table {{.Names}}\t{{.Status}}"

# Test resolution
docker exec -it api getent hosts mysql
```

### Port Conflicts

```bash
# Check for port conflicts on the host
sudo ss -tlnp | grep -E ":(25|587|465|993|995|80|443|3306|6379|8080-8090) "
```

If another service is using a port, either stop it or change Mailyte's port mapping in `docker-compose.yml`.

## Problem: Outbound Email Blocked by ISP

Some ISPs block residential outbound port 25.

### Test

```bash
telnet smtp.gmail.com 25
```

If it hangs, your ISP is blocking it.

### Solutions

1. **Use a VPS** — run Mailyte on a cloud server, not at home
2. **Use a relay** — route through an SMTP relay service
3. **Use a VPN** — tunnel traffic through a VPN that doesn't block port 25

## Network Debugging Tools

```bash
# Trace the route to a mail server
traceroute gmail-smtp-in.l.google.com

# Check TCP connection
nc -zv mail.yourdomain.com 587

# Detailed TCP connection debug
curl -v telnet://mail.yourdomain.com:587

# Check if port is open externally (from another machine)
nmap -p 25,587,993 mail.yourdomain.com

# DNS trace
dig +trace MX yourdomain.com

# Monitor network traffic (briefly)
docker exec -it postfix tcpdump -i any -n port 25 -c 20
```

## Required Ports Reference

| Port | Protocol | Service | Direction | Required |
|------|----------|---------|-----------|----------|
| 25 | TCP | SMTP | In + Out | Yes |
| 465 | TCP | SMTPS | In | Recommended |
| 587 | TCP | Submission | In | Yes |
| 993 | TCP | IMAPS | In | Yes |
| 995 | TCP | POP3S | In | Optional |
| 4190 | TCP | ManageSieve | In | Optional |
| 80 | TCP | HTTP | In | Yes (Let's Encrypt) |
| 443 | TCP | HTTPS | In | Yes (API) |
