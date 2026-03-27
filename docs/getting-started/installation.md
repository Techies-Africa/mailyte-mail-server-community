---
title: Installation
description: Get the Mailyte email server running on your machine in about ten minutes.
---

# Installation

This page gets you from a bare server to a running Mailyte stack. If everything goes smoothly, you will be done in about ten minutes.

## Prerequisites

You need three things installed before you start.

### Docker

Docker runs every Mailyte service inside containers so you do not have to install Postfix, Dovecot, MySQL, or anything else directly on your host.

=== "Ubuntu / Debian"

    ```bash
    sudo apt-get update
    sudo apt-get install -y docker.io
    sudo systemctl enable --now docker
    ```

=== "CentOS / RHEL"

    ```bash
    sudo yum install -y docker
    sudo systemctl enable --now docker
    ```

=== "macOS (development only)"

    Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and make sure it is running.

Verify Docker is working:

```bash
docker --version
```

### Docker Compose

Docker Compose orchestrates the full stack -- databases, mail services, API, workers -- with a single command.

```bash
docker compose version
```

!!! note "Compose V2"
    Mailyte uses the `docker compose` plugin (V2), not the older standalone `docker-compose` binary. If your `docker compose version` output starts with `v2`, you are good to go.

### Git

You need Git to clone the repository.

```bash
git --version
```

## Clone the repository

```bash
git clone https://github.com/TechiesAfrica/mailyte-email-server.git
cd mailyte-email-server
```

## Create your environment file

The repository ships with a `.env.example` that contains every variable the stack needs, filled with sensible defaults. Copy it:

```bash
cp .env.example .env
```

Open `.env` in your editor and set at least these values:

| Variable | What to put |
|---|---|
| `HOSTNAME` | The FQDN of your mail server, e.g. `mail.example.com` |
| `DOMAIN` | Your primary mail domain, e.g. `example.com` |
| `DB_PASSWORD` | A strong password for the MySQL database |

!!! warning "Do not skip the password"
    The default `DB_PASSWORD` in `.env.example` is intentionally weak. Change it before you start the stack, even in development. Changing it later means recreating the database volume.

The [Configuration](configuration.md) page explains every variable in detail. For now, the three above are enough to get started.

## Start the stack

```bash
docker compose up -d
```

This pulls images (the first run may take a few minutes), creates containers, and starts everything in the background. The `-d` flag means "detached" -- your terminal stays free.

!!! tip "Watch the startup in real time"
    If you want to see what is happening, drop the `-d` flag or follow the logs after starting:

    ```bash
    docker compose logs -f
    ```

    Press ++ctrl+c++ to stop following without stopping the containers.

## Verify services are running

### Check container status

```bash
docker compose ps
```

You should see every container in a `running` or `healthy` state. If any container shows `restarting` or `exited`, something is wrong -- jump to [Troubleshooting](troubleshooting.md).

### Check container health

Most Mailyte containers define health checks. You can inspect them individually:

```bash
docker inspect --format='{{.State.Health.Status}}' mailyte-api
```

Or see all containers with their health status at once:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```

### Verify ports

Mailyte exposes the following ports. A quick way to check that they are listening is with `ss` or `netstat`:

```bash
ss -tlnp | grep -E '25|587|465|143|993|110|995|5000|8080'
```

Here is what each port does:

| Port | Protocol | Service | Purpose |
|------|----------|---------|---------|
| 25 | SMTP | Postfix | Receiving mail from other servers |
| 587 | SMTP (STARTTLS) | Postfix | Sending mail from clients (submission) |
| 465 | SMTPS | Postfix | Sending mail over implicit TLS |
| 143 | IMAP | Dovecot | Reading mail (plaintext / STARTTLS) |
| 993 | IMAPS | Dovecot | Reading mail over implicit TLS |
| 110 | POP3 | Dovecot | Reading mail via POP3 |
| 995 | POP3S | Dovecot | Reading mail via POP3 over TLS |
| 5000 | HTTP | FastAPI | REST API |
| 8080 | HTTP | Rspamd | Rspamd web interface |

### Quick API health check

The API exposes a health endpoint:

```bash
curl -s http://localhost:5000/health | python3 -m json.tool
```

You should get back a JSON response with the status of each subsystem.

!!! info "Port 5000 is the API"
    Throughout this handbook, API examples use `localhost:5000`. In production you will put a reverse proxy (Nginx, Caddy, etc.) in front of it with TLS. But for getting started, hitting the port directly is fine.

## What just happened

When you ran `docker compose up -d`, Docker Compose:

1. Created an internal network so the containers can talk to each other.
2. Started MySQL and Redis first (other services depend on them).
3. Started Postfix, Dovecot, and Rspamd -- the core mail services.
4. Started the FastAPI application and all worker services (tracking, webhooks, rate limiter, analytics, RAG/AI search, queue manager, storage, backup, cloud sync).
5. Ran health checks to make sure each service is ready.

Think of it like starting a car -- the engine (databases) fires first, then the transmission (mail services), then the dashboard (API and workers).

## Stopping and restarting

Stop everything:

```bash
docker compose down
```

Stop everything and delete stored data (databases, mail storage):

```bash
docker compose down -v
```

!!! warning "The `-v` flag deletes volumes"
    This destroys all your data. Only use it if you want a completely fresh start.

Restart a single service without touching the rest:

```bash
docker compose restart api
```

## Next step

Now that the stack is running, head to [Configuration](configuration.md) to understand the environment variables that control how Mailyte behaves.
