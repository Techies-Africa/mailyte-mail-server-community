---
title: Getting Started (Development)
description: Set up your local development environment, run the server, and get hot reload working.
---

# Getting Started (Development)

This gets you from a fresh clone to a running dev server with hot reload. Should take about 10 minutes.

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- **Python 3.11+** (for running tests and linting locally — `pyproject.toml` sets `requires-python = ">=3.11"` and CI runs 3.11)
- **Git**

## Clone the Repo

```bash
git clone https://github.com/Techies-Africa/mailyte-email-server.git
cd mailyte-email-server
```

The default working branch is `develop`.

## Set Up Environment

Generate a `.env` with strong random secrets:

```bash
bash scripts/generate-secrets.sh
```

This copies `.env.example` to `.env` (if missing), fills in the eight required secrets (`DB_ROOT_PASSWORD`, `DB_PASSWORD`, `WEBHOOK_SECRET`, `OAUTH_TOKEN_SECRET`, `URL_HMAC_SECRET`, `ADMIN_PASSWORD`, `ADMIN_TOKEN_SECRET`, `GRAFANA_ADMIN_PASSWORD`), and mirrors the DB root password into `secrets/db_root_password` for the MySQL container.

!!! warning "Secrets are fail-closed"
    The stack refuses to start if any of those secrets is missing, shorter than 16 characters, or a known-weak value — the `secrets-check` container (running `worker/api/startup_checks.py`) aborts the whole `docker compose up`. A hand-written `.env` with placeholder passwords will not boot.

Then edit `.env` and set at minimum:

```bash
HOSTNAME=mail.localhost
DOMAIN=localhost
ACME_STAGING=true
```

For DKIM/PGP key encryption also run `bash scripts/generate_dkim_kek.sh` once (it writes a mounted key file, not an env var).

## Start the Dev Stack

`docker-compose.dev.yml` is an **override**, not a standalone file — it only contains dev-mode command overrides (uvicorn `--reload`), so it must be combined with the base file:

```bash
./start.sh dev
# or interactively: ./start.sh → option 4 (Development mode)

# Which is equivalent to:
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

`./start.sh` also creates the required `storage/` and `logs/` directories and a self-signed dev TLS certificate on first run.

!!! tip "Machine-specific overrides"
    If a host port is already taken on your machine (e.g. something else owns port 80, which `cert_manager` binds), copy `docker-compose.override.yml.example` to `docker-compose.override.yml` (git-ignored) and adjust it there. Compose loads that file automatically for a plain `docker compose up`, but not when compose files are named explicitly — so production is unaffected.

### First Run Takes a While

The first `up` builds all images, then the `migrate` container brings the schema to Alembic head before anything that touches the database starts. Expect several minutes. Subsequent starts are much faster.

### Check Everything is Running

```bash
docker compose ps
```

All services should show `Up` or `healthy`. (`migrate` and `secrets-check` are one-shot containers — `Exited (0)` is their healthy state.)

## Accessing Services

Host-mapped ports (left side of the mapping — some differ from the container port):

| Service | URL/Port |
|---------|----------|
| API | `http://localhost:8083` (container port 8080) |
| API Swagger UI | `http://localhost:8083/api-docs` |
| API Health | `http://localhost:8083/health` |
| Webhooks | `http://localhost:8081` |
| Rate limiter | `http://localhost:8082` |
| Monitoring | `http://localhost:8085` |
| Tracking | `http://localhost:8086` |
| Analytics | `http://localhost:8087` (container port 8085) |
| Dashboard | `http://localhost:8088` |
| Archiver | `http://localhost:8089` (container port 8083) |
| Encryption | `http://localhost:8093` (container port 8084) |
| Rspamd UI | `http://localhost:11334` |
| Docs (this handbook) | `http://localhost:8000` |
| Grafana | `http://localhost:3000` |
| Prometheus | `http://localhost:9090` |

!!! info "MySQL and Redis are not exposed to the host"
    Neither service publishes a port — they are reachable only on the Docker network. Use `docker exec` (see [Useful Dev Commands](#useful-dev-commands)) instead of connecting to `localhost:3306`/`localhost:6379`.

## Hot Reload

The **base** compose file bind-mounts worker source into the containers (e.g. `./worker/api:/app`, plus `./shared` and `./database`); the **dev** override switches the Python workers to `uvicorn --reload`:

```yaml
# docker-compose.dev.yml
api:
  command: ["python", "-m", "uvicorn", "app:app", "--host=0.0.0.0", "--port=8080", "--reload"]
```

Edit any file in `worker/api/` and the API restarts automatically. The same applies to `tracking`, `webhooks`, `monitoring`, `analytics`, `archiver`, `rate_limiter`, `queue_manager`, `storage_usage`, and `dashboard`. For workers without a `--reload` override, restart the container:

```bash
docker compose restart <service>
```

### Mailer Services

Postfix, Dovecot, and Rspamd don't hot-reload (they're not Python). After changing their configs:

```bash
docker exec -it postfix postfix reload
docker exec -it dovecot doveadm reload
docker compose restart rspamd
```

## Install Python Dependencies Locally

For running tests and linters outside Docker, use a `.venv` at the repo root — `manage.py` automatically prefers `.venv/bin/alembic` when it exists:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt -r worker/api/requirements.txt
pip install ruff mypy mypy-baseline
```

There is no repo-root `requirements.txt` — each service pins its own dependencies in `worker/<service>/requirements.txt` (and `shared/requirements.txt`). `worker/api/requirements.txt` is needed locally because the unit tests import from `worker/api/`.

## Run Tests

```bash
# Fast unit tests (no Docker, no database)
pytest tests/unit/ -v

# A specific test file / test
pytest tests/unit/test_auth.py -v
pytest tests/unit/test_webhook_dispatcher.py::TestSignPayload -v

# Integration tests — need the dev stack running
./start.sh test
```

`pytest.ini` adds `--timeout=30` to every run, so `pytest-timeout` (in `requirements-test.txt`) must be installed or pytest exits with "unrecognized arguments". See [Testing](testing.md) for the full picture, including the CI coverage ratchet.

## Run Linters

Ruff (formatting + linting) and mypy, both gated in CI against committed baselines:

```bash
# Format check / auto-format
ruff format --check .
ruff format .

# Lint — CI compares the violation count against ruff-baseline.txt
ruff check .

# Type check — CI filters known errors through mypy-baseline.txt
bash scripts/run_mypy.sh | mypy-baseline filter
```

See [Coding Standards](coding-standards.md) for how the baseline ratchets work and when to update the baseline files.

## Database Migrations

Migrations are Alembic, wrapped in a Laravel-style CLI:

```bash
# Create a new migration (autogenerate against the models)
python manage.py migrate:create "description of change"

# Run all pending migrations
python manage.py migrate

# Roll back the last migration (or --steps=N)
python manage.py migrate:rollback

# Show current revision / full history
python manage.py migrate:current
python manage.py migrate:status
```

Migration files live in `alembic/versions/` (sequentially numbered, `0001_baseline` onward).

In Docker, schema is owned by the dedicated `migrate` service: it runs `scripts/run_migrations.py` once per `docker compose up`, before any service that touches the database starts.

!!! warning "Rebuild the migrate image after pulling new migrations"
    The `migrate` container bakes `alembic/` into its image at build time. After pulling new migrations, a stale image silently no-ops — it reports success while your database stays behind. Rebuild it first:

    ```bash
    docker compose build migrate
    docker compose up -d
    ```

    If your local API starts throwing "unknown column" errors, this is the first thing to check.

## Useful Dev Commands

```bash
# View API logs
docker compose logs -f api

# Open a MySQL shell (root password lives in secrets/db_root_password)
docker exec -it mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)" mailserver'

# Open a Redis shell
docker exec -it redis redis-cli

# Rebuild a specific service
docker compose build api && docker compose up -d api

# Reset everything (nuclear option)
docker compose down -v && ./start.sh dev
```

!!! warning "docker compose down -v"
    The `-v` flag deletes volumes, which means your database and all stored email are gone. Only do this when you want a clean slate.

## IDE Setup

### VS Code

Recommended extensions:

- Python (ms-python.python)
- Ruff (charliermarsh.ruff)
- Docker (ms-azuretools.vscode-docker)
- YAML (redhat.vscode-yaml)

Settings for the project:

```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true
  },
  "python.defaultInterpreterPath": ".venv/bin/python"
}
```

### PyCharm

1. Set the Python interpreter to `.venv`
2. Enable Ruff as the formatter (via the Ruff plugin)
3. Configure Docker Compose as a run configuration
