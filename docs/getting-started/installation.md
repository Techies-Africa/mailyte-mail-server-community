---
title: Installation
description: Get the Mailyte email server running on your machine in about ten minutes.
---

# Installation

This page gets you from a bare server to a running Mailyte stack. If everything goes smoothly, you will be done in about ten minutes.

## Prerequisites

You need these installed before you start.

### Docker

Docker runs every Mailyte service inside containers so you do not have to install Postfix, Dovecot, MySQL, or anything else directly on your host.

=== "Ubuntu / Debian"

    ```bash
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    # Log out and back in
    ```

=== "CentOS / RHEL"

    ```bash
    curl -fsSL https://get.docker.com | sh
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

!!! note "Compose V2, reasonably recent"
    Mailyte uses the `docker compose` plugin (V2), not the older standalone `docker-compose` binary. The production and local override files use the `!override` / `!reset` YAML tags, which need Docker Compose **v2.24 or newer** -- older v2 releases fail to parse them.

### Git, OpenSSL, Python 3

```bash
git --version       # clone the repository
openssl version     # generate secrets and dev TLS certificates
python3 --version   # used by manage.py and several scripts/
```

## Clone the repository

```bash
git clone https://github.com/Techies-Africa/mailyte-email-server.git
cd mailyte-email-server
```

## Generate secrets and your environment file

Nothing in `docker-compose.yml` has a security-relevant default. A `secrets-check` container runs before everything else on every `docker compose up` and **aborts the whole stack** if any required secret is missing, shorter than 16 characters, or a known-weak placeholder (`changeme...`, `your-...`, `password`, ...).

The repository ships a script that does the right thing:

```bash
./scripts/generate-secrets.sh
```

This:

1. Copies `.env.example` to `.env` (if `.env` does not exist yet).
2. Generates strong random values for the eight required secrets: `DB_ROOT_PASSWORD`, `DB_PASSWORD`, `WEBHOOK_SECRET`, `OAUTH_TOKEN_SECRET`, `URL_HMAC_SECRET`, `ADMIN_PASSWORD`, `ADMIN_TOKEN_SECRET`, `GRAFANA_ADMIN_PASSWORD`.
3. Writes `secrets/db_root_password` (the file MySQL boots from) in sync with `.env`.

It is safe to re-run -- secrets you have already set are left alone.

Then generate the key-encryption key that protects DKIM/PGP/S-MIME private keys at rest:

```bash
./scripts/generate_dkim_kek.sh
```

!!! danger "Back up `secrets/encryption_kek`"
    Losing this file makes every private key encrypted under it permanently unrecoverable. Back it up somewhere separate from your database backups, and never overwrite an existing one.

Finally, open `.env` in your editor and set at least these values:

| Variable | What to put |
|---|---|
| `HOSTNAME` | The FQDN of your mail server, e.g. `mail.example.com` |
| `DOMAIN` | Your primary mail domain, e.g. `example.com` |
| `ADMIN_EMAIL` | The operator contact address |

The [Configuration](configuration.md) page explains the rest. For now, this is enough to get started.

## Start the stack

The recommended entry point is the interactive console:

```bash
./start.sh
```

Select **option 1** (First-time setup wizard) or **option 2** (Start essential services: redis, mysql, rspamd, postfix, dovecot, api, console). `start.sh` also creates the required `storage/` and `logs/` directories and a self-signed development TLS certificate on first run.

It works as a CLI too:

```bash
./start.sh start          # start all services
./start.sh dev            # development mode with hot-reload
./start.sh status         # service status
./start.sh help           # all commands
```

Or use Docker Compose directly:

```bash
docker compose up -d
```

This builds/pulls images (the first run takes a while -- most of the ~30 services build from source), creates containers, and starts everything in the background.

!!! info "Migrations run automatically"
    A one-shot `migrate` container runs the Alembic migrations after MySQL is healthy and **before** anything that touches the schema starts. If a migration fails, dependent services deliberately do not start -- check `docker compose logs migrate`.

!!! tip "Watch the startup in real time"
    ```bash
    docker compose logs -f
    ```

    Press ++ctrl+c++ to stop following without stopping the containers.

## Verify services are running

### Check container status

```bash
docker compose ps
```

You should see every container in a `running` or `healthy` state. The `secrets-check` and `migrate` containers are supposed to show `Exited (0)` -- they are one-shot jobs. If anything else shows `restarting` or `exited`, jump to [Troubleshooting](troubleshooting.md).

### Check container health

Most containers define health checks. Container names match service names (`api`, `postfix`, `mysql`, ...):

```bash
docker inspect --format='{{.State.Health.Status}}' api
```

Or see all containers with their health status at once:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```

### Verify ports

A quick way to check that the key ports are listening:

```bash
ss -tlnp | grep -E ':(25|587|465|143|993|110|995|8083|11334)\b'
```

The most important host ports in the base (development) compose file:

| Port | Protocol | Service | Purpose |
|------|----------|---------|---------|
| 25 | SMTP | Postfix | Receiving mail from other servers |
| 587 | SMTP (STARTTLS) | Postfix | Sending mail from clients (submission) |
| 465 | SMTPS | Postfix | Sending mail over implicit TLS |
| 143 | IMAP | Dovecot | Reading mail (STARTTLS) |
| 993 | IMAPS | Dovecot | Reading mail over implicit TLS |
| 110 / 995 | POP3 / POP3S | Dovecot | POP3 access |
| 4190 | ManageSieve | Dovecot | Sieve filter management |
| 8083 | HTTP | api | REST API (container port 8080) |
| 11334 | HTTP | Rspamd | Rspamd web interface |
| 8000 | HTTP | docs | This handbook |
| 3000 | HTTP | Grafana | Dashboards |
| 3100 | HTTP | console | Admin console |

The full mapping for every worker service lives in the [Docker Deployment](../deployment/docker-deployment.md) page.

!!! warning "These bindings are for development"
    The base compose file publishes worker ports on all interfaces for single-host development. In production, `docker-compose.prod.yml` rebinds every internal service to `127.0.0.1` and Traefik on 80/443 is the only HTTP way in. Never run the base file alone on an internet-facing host.

### Quick API health check

```bash
curl -s http://localhost:8083/health | python3 -m json.tool
```

You should get back a JSON response with the status of each subsystem. Interactive API docs are at `http://localhost:8083/api-docs` (Swagger) and `http://localhost:8083/api-reference` (Redoc).

## What just happened

When you started the stack, Docker Compose:

1. Ran `secrets-check` to validate your secrets (fail-closed).
2. Started MySQL and Redis first (other services depend on them).
3. Ran the `migrate` one-shot container to bring the schema to the current Alembic head.
4. Started Postfix, Dovecot, and Rspamd -- the core mail services.
5. Started the FastAPI application and the worker services (tracking, webhooks, rate limiter, analytics, queue manager, storage usage, monitoring, and more).
6. Health checks gate each dependent service on its dependencies being ready.

## Stopping and restarting

Stop everything:

```bash
docker compose down
```

Stop everything and delete stored data (database, named volumes):

```bash
docker compose down -v
```

!!! warning "The `-v` flag deletes volumes"
    This destroys the database, Redis data, and the Postfix queue. Mail data itself lives in `./storage/mail_data` (a bind mount) and survives, but only use `-v` if you want a fresh start.

Restart a single service without touching the rest:

```bash
docker compose restart api
```

## Next step

Now that the stack is running, head to [Configuration](configuration.md) to understand the environment variables that control how Mailyte behaves.
