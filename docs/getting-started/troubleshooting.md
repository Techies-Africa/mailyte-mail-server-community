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

Container names match service names (`api`, `postfix`, `mysql`, ...):

```bash
docker inspect --format='{{.State.Health.Status}}' api
```

### Check which ports are in use

```bash
ss -tlnp | grep -E ':(25|587|465|143|993|110|995|8083|11334)\b'
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

**The secrets check failed.**
A `secrets-check` container validates your secrets before anything else starts, and a failure blocks the entire stack -- every other service just never comes up. Two containers are *supposed* to exit: `secrets-check` and `migrate` both show `Exited (0)` when healthy.

```bash
docker compose logs secrets-check
# If it names a weak/missing secret, fix it with:
./scripts/generate-secrets.sh
```

**A migration failed.**
The one-shot `migrate` container must complete successfully before the api and most workers will start.

```bash
docker compose logs migrate
```

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

**Another application is on one of the worker ports.**
The base compose file publishes host ports 8081-8104 (workers), 8083 (API), 8000 (docs), 3000 (Grafana), and more. If something on your machine already owns one of them, copy `docker-compose.override.yml.example` to `docker-compose.override.yml` and remap the conflicting port there -- Compose loads that file automatically, it is gitignored, and it uses the `!override` tag so the conflicting binding is actually replaced rather than appended to.

!!! warning "Do not change standard mail ports"
    Ports 25, 587, 465, 143, and 993 are internet standards. Other mail servers expect to reach you on these ports. Change them only if you are running behind a proxy that handles the mapping.

---

## Database connection failures

### Symptoms

The API or worker logs show errors like `Can't connect to MySQL server on 'mysql'` or `Access denied for user 'mailuser'`.

### Diagnosis

```bash
# Check if the database container is running
docker compose ps mysql

# Check database logs
docker compose logs mysql

# Try connecting manually from inside the network
docker compose exec mysql mysql -u mailuser -p
```

### Common causes

**The database has not finished initializing.**
On first run, MySQL initializes its data directory and the `migrate` container then applies the schema. This takes 30-60 seconds. The compose file gates the API on both, but a manually started container can race them.

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

    The root password lives in `secrets/db_root_password` (kept in sync with `.env`'s `DB_ROOT_PASSWORD` by `scripts/generate-secrets.sh`):

    ```bash
    docker compose exec mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)"'
    ```

    ```sql
    ALTER USER 'mailuser'@'%' IDENTIFIED BY 'your-new-password';
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
docker compose exec postfix ls -la /etc/ssl/certs/custom/

# Test the certificate from outside
openssl s_client -connect localhost:465 -quiet
```

### Common causes

**Certificate files are missing or have wrong permissions.**

Postfix and Dovecot mount `./storage/ssl_certs` and `./storage/ssl_private` from the host. In development, `start.sh` generates a self-signed pair there on first run (`server.crt` / `server.key`); in production, the `cert_manager` service writes real Let's Encrypt certificates into the same directories. Verify they exist on the host:

```bash
ls -la storage/ssl_certs/ storage/ssl_private/
```

The private key should be readable only by its owner:

```bash
chmod 600 storage/ssl_private/server.key
```

**Certificate has expired.**
Check the expiry date:

```bash
openssl x509 -enddate -noout -in storage/ssl_certs/server.crt
```

!!! tip "Regenerating the development certificate"
    Delete `storage/ssl_certs/server.crt` and `storage/ssl_private/server.key`, then run `./start.sh` again -- it recreates the self-signed pair automatically. For real certificates, see `cert_manager` in the [Production Setup](../deployment/production-setup.md) guide; do not run your own certbot next to it.

---

## DNS not resolving

### Symptoms

Outgoing emails get stuck in the queue. Postfix logs show `Host or domain name not found` or `Name service error`. External servers cannot reach your server.

### Diagnosis

```bash
# Check Postfix mail queue
docker compose exec postfix postqueue -p

# Test DNS resolution from inside the container
docker compose exec postfix getent hosts example.com

# Test DNS resolution from the host
dig mail.example.com
dig example.com MX
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
docker compose exec api python3 -c "import socket; print(socket.gethostbyname('google.com'))"
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
docker compose exec postfix postqueue -p

# Check the Postfix mail log for delivery attempts
# (the container logs to a file, not to stdout)
tail -100 logs/mailer/postfix/mail.log | grep "status="

# Check Rspamd logs for rejected messages
docker compose logs rspamd | grep "reject"
```

### Common causes

**Rspamd is rejecting the message.**
Check the Rspamd web UI at `http://localhost:11334` to see if your test messages are being flagged. During initial setup, Rspamd might be aggressive with messages from unconfigured domains.

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
3. **Search existing issues.** Someone else may have hit the same problem. Check the [GitHub issues](https://github.com/Techies-Africa/mailyte-email-server/issues).

!!! tip "When asking for help, include these three things"
    1. The output of `docker compose ps`
    2. The relevant container logs (`docker compose logs <service>`)
    3. Your `.env` file with passwords redacted
