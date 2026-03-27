---
title: Getting Started (Development)
description: Set up your local development environment, run the server, and get hot reload working.
---

# Getting Started (Development)

This gets you from a fresh clone to a running dev server with hot reload. Should take about 10 minutes.

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- **Python 3.11+** (for running tests and linting locally)
- **Git**

## Clone the Repo

```bash
git clone https://github.com/TechiesAfrica/mailyte-email-server.git
cd mailyte-email-server
```

## Set Up Environment

Copy the example env file:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
HOSTNAME=mail.localhost
DOMAIN=localhost
DB_HOST=mysql
DB_USER=mailuser
DB_PASSWORD=devpassword
DB_ROOT_PASSWORD=devrootpassword
ADMIN_PASSWORD=devadminpass
ADMIN_TOKEN_SECRET=dev-secret-change-in-prod
ACME_STAGING=true
FLASK_ENV=development
FLASK_DEBUG=1
```

## Start the Dev Stack

Use the dev compose file for volume mounts and hot reload:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Or if you're using the main file with dev overrides:

```bash
docker compose up -d
```

This starts all services: MySQL, Redis, Postfix, Dovecot, Rspamd, cert_manager, API, and workers.

### First Run Takes a While

The first `docker compose up` builds all images and initializes the database. Expect 3-5 minutes. Subsequent starts are much faster.

### Check Everything is Running

```bash
docker compose ps
```

All services should show `Up` or `healthy`.

## Accessing Services

| Service | URL/Port |
|---------|----------|
| API | `http://localhost:8083` |
| API Health | `http://localhost:8083/health` |
| Webhooks | `http://localhost:8081` |
| Rspamd UI | `http://localhost:11334` |
| MySQL | `localhost:3306` |
| Redis | `localhost:6379` |
| Grafana | `http://localhost:3000` |
| Prometheus | `http://localhost:9090` |

## Hot Reload

The dev setup mounts your local code into the containers, so changes are picked up automatically.

### API (FastAPI)

The API worker mounts `./worker/api:/app` and runs with `--reload`:

```yaml
# docker-compose.dev.yml
api:
  volumes:
    - ./worker/api:/app
    - ./shared:/app/shared
    - ./database:/app/database
  environment:
    - FLASK_DEBUG=1
```

Edit any file in `worker/api/` and the API restarts automatically.

### Other Workers

Workers also mount their source directories. Most use watchdog or similar to detect changes. If not, restart the specific container:

```bash
docker compose restart tracking
```

### Mailer Services

Postfix, Dovecot, and Rspamd don't hot-reload code changes (they're not Python). After changing their configs:

```bash
# Reload config without full restart
docker exec -it postfix postfix reload
docker exec -it dovecot doveadm reload
docker exec -it rspamd rspamc reload
```

## Install Python Dependencies Locally

For running tests and linters outside Docker:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-test.txt
```

## Run Tests

```bash
# Run the full test suite
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_api.py

# Run a specific test
pytest tests/test_api.py::test_create_domain -v
```

See [Testing](testing.md) for more details.

## Run Linters

```bash
# Format code
black worker/ shared/ tests/

# Sort imports
isort worker/ shared/ tests/

# Check style
flake8 worker/ shared/ tests/
```

## Database Migrations

```bash
# Create a new migration
python manage.py migrate:create "description of change"

# Run pending migrations
python manage.py migrate:run

# Check current version
python manage.py migrate:current
```

Alembic migration files live in `alembic/versions/`.

## Useful Dev Commands

```bash
# View API logs
docker compose logs -f api

# Open a MySQL shell
docker exec -it mysql mysql -u root -pdevrootpassword mailserver

# Open a Redis shell
docker exec -it redis redis-cli

# Rebuild a specific service
docker compose build api && docker compose up -d api

# Reset everything (nuclear option)
docker compose down -v && docker compose up -d
```

!!! warning "docker compose down -v"
    The `-v` flag deletes volumes, which means your database and all stored email are gone. Only do this when you want a clean slate.

## IDE Setup

### VS Code

Recommended extensions:

- Python (ms-python.python)
- Docker (ms-azuretools.vscode-docker)
- YAML (redhat.vscode-yaml)

Settings for the project:

```json
{
  "python.formatting.provider": "black",
  "python.sortImports.args": ["--profile", "black"],
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "ms-python.python"
  }
}
```

### PyCharm

1. Set the Python interpreter to your venv
2. Enable Black as the formatter
3. Configure Docker Compose as a run configuration
