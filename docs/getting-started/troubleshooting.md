---
title: Troubleshooting Setup
description: Fix the most common problems you will hit when setting up Mailyte for the first time.
---

# Troubleshooting Setup

Something broke during setup. That is completely normal -- email servers have a lot of moving parts. This page covers the problems people hit most often and how to fix them.

## Your best friends: diagnostic commands

Before diving into specific issues, learn these commands. They solve 90% of debugging.

### See what is running

```bash
docker compose ps
```

This shows every container, its state, and its ports. Look for anything that is not `running` or `Up`.

### Read the logs

```bash
# All containers
docker compose logs

# A specific container (e.g., the API)
docker compose logs api

# Follow logs in real time
docker compose logs -f api

# Last 100 lines of a specific container
docker compose logs --tail=100 postfix
```

!!! tip "Always check logs first"
    Nine times out of ten, the logs tell you exactly what went wrong. Start here before trying anything else.

### Check container health

```bash
docker inspect --format='{{.State.Health.Status}}' mailyte-api
```

### Check which ports are in use

```bash
ss -tlnp | grep -E '25|587|465|143|993|110|995|5000|8080'
```

### Test if a port is reachable

=== "Using curl"

    ```bash
    curl -v telnet://localhost:25
    ```

=== "Using netcat"

    ```bash
    nc -zv localhost 25
    ```

=== "Using telnet"

    ```bash
    telnet localhost 25
    ```

---

## Containers not starting

### Symptoms

`docker compose ps` shows containers in `restarting` or `exited` state.

### Diagnosis

```bash
# Find the unhappy container
docker compose ps

# Read its logs
docker compose logs <container-name>
```

### Common causes

**The container depends on a service that is not ready yet.**
MySQL and Redis need a few seconds to initialize. Most Mailyte services have health-check-based dependencies, but if you see "connection refused" errors in the logs, the dependency just was not ready in time.

```bash
# Restart the failing container after the dependency is up
docker compose restart <container-name>
```

**Missing or invalid `.env` file.**
If you forgot to copy `.env.example` to `.env`, or a required variable is empty, containers will fail on startup.

```bash
# Check that .env exists and is not empty
ls -la .env
cat .env | head -20
```

**Out of disk space.**
Docker images and volumes eat disk. Check available space:

```bash
df -h /var/lib/docker
```

!!! note "Docker disk cleanup"
    If disk is the problem, reclaim space with:

    ```bash
    docker system prune -a --volumes
    ```

    This removes unused images, containers, and volumes. Do not run this in production if you have data you want to keep.

---

## Port conflicts

### Symptoms

A container starts but immediately exits, or `docker compose up` prints an error like `Bind for 0.0.0.0:25 failed: port is already allocated`.

### Diagnosis

Find what is already using the port:

```bash
sudo ss -tlnp | grep ':25'
```

### Common causes

**Another mail server is running.**
Many Linux distributions ship with a basic MTA (like Exim or Sendmail) that listens on port 25.

```bash
# Stop the system mail service
sudo systemctl stop exim4    # Debian/Ubuntu
sudo systemctl stop sendmail # CentOS/RHEL
sudo systemctl stop postfix  # if system Postfix is installed

# Prevent it from starting on boot
sudo systemctl disable exim4
```

**Another application is on port 5000.**
On macOS, AirPlay Receiver uses port 5000. Disable it in System Settings > AirDrop & Handoff, or change the API port in your `.env`.

!!! warning "Do not change standard mail ports"
    Ports 25, 587, 465, 143, and 993 are internet standards. Other mail servers expect to reach you on these ports. Change them only if you are running behind a proxy that handles the mapping.

---

## Database connection failures

### Symptoms

The API or worker logs show errors like `Can't connect to MySQL server on 'db'` or `Access denied for user 'mailyte'`.

### Diagnosis

```bash
# Check if the database container is running
docker compose ps db

# Check database logs
docker compose logs db

# Try connecting manually from inside the network
docker compose exec db mysql -u mailyte -p
```

### Common causes

**The database has not finished initializing.**
On first run, MySQL creates the database schema. This takes 30-60 seconds. If the API starts before MySQL is ready, it fails.

```bash
# Wait for MySQL to be healthy, then restart the API
docker compose restart api
```

**Wrong password.**
If you changed `DB_PASSWORD` in `.env` after the database was already created, the database still has the old password. You have two options:

=== "Option A: Reset the volume (destroys data)"

    ```bash
    docker compose down -v
    docker compose up -d
    ```

=== "Option B: Update the password inside MySQL"

    ```bash
    docker compose exec db mysql -u root -p
    ```

    ```sql
    ALTER USER 'mailyte'@'%' IDENTIFIED BY 'your-new-password';
    FLUSH PRIVILEGES;
    ```

---

## SSL / TLS issues

### Symptoms

Email clients refuse to connect. Browser shows certificate warnings when accessing the API. Postfix logs show `TLS library problem` or `cannot load certificate`.

### Diagnosis

```bash
# Check Postfix TLS logs
docker compose logs postfix | grep -i tls

# Verify the certificate files exist inside the container
docker compose exec postfix ls -la /etc/ssl/certs/mailyte/

# Test the certificate from outside
openssl s_client -connect localhost:465 -quiet
```

### Common causes

**Certificate files are missing or have wrong permissions.**

The container expects `fullchain.pem` and `privkey.pem` at the path specified by `SSL_CERT_PATH`. Verify they exist on the host and are mounted correctly:

```bash
ls -la /etc/mailyte/ssl/
```

The private key must be readable by the container process:

```bash
# Fix permissions if needed
sudo chmod 644 /etc/mailyte/ssl/fullchain.pem
sudo chmod 600 /etc/mailyte/ssl/privkey.pem
```

**Certificate has expired.**
Check the expiry date:

```bash
openssl x509 -enddate -noout -in /etc/mailyte/ssl/fullchain.pem
```

!!! tip "Self-signed certificates for development"
    If you just need TLS working locally and do not care about browser trust:

    ```bash
    openssl req -x509 -newkey rsa:4096 -keyout privkey.pem -out fullchain.pem \
      -sha256 -days 365 -nodes -subj "/CN=mail.localhost"
    ```

    Place the files where `SSL_CERT_PATH` points and restart the mail services.

---

## DNS not resolving

### Symptoms

Outgoing emails get stuck in the queue. Postfix logs show `Host or domain name not found` or `Name service error`. External servers cannot reach your server.

### Diagnosis

```bash
# Check Postfix mail queue
docker compose exec postfix mailq

# Test DNS resolution from inside the container
docker compose exec postfix dig example.com MX

# Test DNS resolution from the host
dig mail.example.com
nslookup mail.example.com
```

### Common causes

**DNS records are not set up yet.**
Before other mail servers will deliver to you, your domain needs:

- An **MX record** pointing to your server (`example.com MX 10 mail.example.com`)
- An **A record** for the mail hostname (`mail.example.com A 203.0.113.1`)
- An **SPF record** (`example.com TXT "v=spf1 ip4:203.0.113.1 ~all"`)

!!! info "DNS propagation takes time"
    After adding records, it can take anywhere from a few minutes to 48 hours for them to propagate. Use a tool like [dnschecker.org](https://dnschecker.org) to check propagation status.

**The container cannot reach external DNS.**
Some Docker configurations use internal DNS that cannot resolve public domains.

```bash
# Test from inside a container
docker compose exec api ping -c 2 google.com
```

If this fails, add a DNS server to your Docker daemon configuration. Create or edit `/etc/docker/daemon.json`:

```json
{
    "dns": ["8.8.8.8", "1.1.1.1"]
}
```

Then restart Docker:

```bash
sudo systemctl restart docker
docker compose up -d
```

---

## Services are running but email is not delivered

### Symptoms

Everything looks healthy. The API accepts send requests. But emails never arrive.

### Diagnosis

```bash
# Check the Postfix queue
docker compose exec postfix mailq

# Check Postfix logs for delivery attempts
docker compose logs postfix | grep "status="

# Check Rspamd logs for rejected messages
docker compose logs rspamd | grep "reject"
```

### Common causes

**Rspamd is rejecting the message.**
Check the Rspamd web UI at `http://localhost:8080` to see if your test messages are being flagged. During initial setup, Rspamd might be aggressive with messages from unconfigured domains.

**Postfix relay restrictions.**
If you are trying to send to an external address and Postfix logs show `Relay access denied`, the sending account may not be properly authenticated. For local-to-local delivery (sending to a mailbox on the same server), this should not happen.

**The message is in the deferred queue.**
If the receiving server is temporarily unavailable, Postfix holds the message and retries later.

```bash
# See deferred messages
docker compose exec postfix postqueue -p

# Force a retry
docker compose exec postfix postqueue -f
```

---

## Still stuck?

If none of the above solved your problem:

1. **Collect the logs.** Run `docker compose logs > mailyte-logs.txt` and save the output.
2. **Check the full documentation.** The [Guides > Troubleshooting](../guides/troubleshooting/email-delivery-issues.md) section covers more advanced scenarios.
3. **Search existing issues.** Someone else may have hit the same problem. Check the [GitHub issues](https://github.com/TechiesAfrica/mailyte-email-server/issues).

!!! tip "When asking for help, include these three things"
    1. The output of `docker compose ps`
    2. The relevant container logs (`docker compose logs <service>`)
    3. Your `.env` file with passwords redacted
