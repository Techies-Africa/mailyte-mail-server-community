---
title: Development
description: Everything you need to contribute code — repo structure, dev environment, coding standards, testing, and the release process.
---

# Development

Everything you need to build features, fix bugs, or extend Mailyte. Whether you're adding a new worker, writing tests, or shipping a release — start here.

---

## In this section

<div class="grid cards" markdown>

-   :material-play-circle:{ .lg .middle } **Getting Started (Dev)**

    ---

    Set up your local dev environment, run the stack, and make your first change.

    [:octicons-arrow-right-24: Dev setup](getting-started.md)

-   :material-puzzle-plus:{ .lg .middle } **Adding Features**

    ---

    How to add a new feature end to end — from database migration to API endpoint to worker.

    [:octicons-arrow-right-24: Adding features](adding-features.md)

-   :material-source-pull:{ .lg .middle } **Contributing**

    ---

    PR process, branch naming, commit conventions, and code review guidelines.

    [:octicons-arrow-right-24: Contributing](contributing.md)

-   :material-format-paint:{ .lg .middle } **Coding Standards**

    ---

    Ruff, mypy, the baseline ratchets, naming conventions, and file organization.

    [:octicons-arrow-right-24: Coding standards](coding-standards.md)

-   :material-test-tube:{ .lg .middle } **Testing**

    ---

    Running and writing tests — unit, integration, and end-to-end.

    [:octicons-arrow-right-24: Testing](testing.md)

-   :material-cog-box:{ .lg .middle } **Custom Workers**

    ---

    Build a new background worker module with the standard worker template.

    [:octicons-arrow-right-24: Custom workers](custom-workers.md)

-   :material-puzzle:{ .lg .middle } **Plugin Development**

    ---

    The supported extension points — webhooks, custom workers, and mail-pipeline hooks.

    [:octicons-arrow-right-24: Plugin development](plugin-development.md)

-   :material-chart-line:{ .lg .middle } **Monitoring Integration**

    ---

    Add Prometheus metrics, Grafana dashboards, and health check endpoints.

    [:octicons-arrow-right-24: Monitoring integration](monitoring-integration.md)

-   :material-tag:{ .lg .middle } **Release Process**

    ---

    Versioning, changelog management, building images, and deploying.

    [:octicons-arrow-right-24: Release process](release-process.md)

</div>

---

## Repo Structure

```
mailyte-email-server/
  alembic/              # Alembic migration scripts (alembic/versions/)
  config/               # Service configuration files
  database/
    migrations/sql/     # Frozen SQL bootstrap files (historical; CE first-boot)
  deployment/           # Production deployment configs
  docs/                 # This handbook (MkDocs Material; mkdocs.yml lives here)
  logs/                 # Service logs (git-ignored)
  mailer/               # Mail service Dockerfiles and configs
    postfix/
    dovecot/
    rspamd/
    cert_manager/
    log_ingestor/       # Parses Postfix logs into mail_logs/delivery_events
  monitoring/           # Prometheus, Grafana, Alertmanager configs
  scripts/              # Utility + CI scripts (run_mypy.sh, run_migrations.py, ...)
  secrets/              # Generated secret files (git-ignored)
  security/             # Security services (DLP, TOTP, geo-blocking, ...)
  shared/               # Shared Python modules across workers
  storage/              # Mail data, certs, DKIM keys (git-ignored)
  tests/                # Test suite (unit/, integration/, e2e/, load/)
  worker/               # Worker service modules (one directory per service)
    api/                # REST API gateway (FastAPI)
    activesync/  analytics/  archiver/  autoconfig/  caldav/
    dashboard/  delivery_optimizer/  encryption/  jmap/  migration/
    monitoring/  oauth/  queue_manager/  rag/  rate_limiter/
    storage_usage/  templates/  tracking/  url_protection/  webhooks/
  docker-compose.yml    # Base compose file
  docker-compose.dev.yml    # Dev override (hot reload)
  docker-compose.prod.yml   # Production override
  main.py               # Application entry point
  manage.py             # Migration CLI (Laravel-style wrapper around Alembic)
  pyproject.toml        # Python project config (ruff + mypy config)
  pytest.ini            # Pytest config
  requirements-test.txt # Test dependencies
  ruff-baseline.txt / mypy-baseline.txt / coverage-baseline.txt /
  openapi-untyped-baseline.txt   # CI ratchet baselines
```

!!! info "Key directories"
    Most of your time will be spent in `worker/` (business logic), `shared/` (common utilities), `tests/` (test suite), and `alembic/` (database migrations). The `mailer/` directory is for mail service configuration and rarely needs changes. There is no repo-root `requirements.txt` — each service pins its own in `worker/<service>/requirements.txt`.

---

## Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| **Language** | Python | 3.11 (CI and containers; `requires-python >= 3.11`) |
| **API framework** | FastAPI | Pinned per service (`worker/api/requirements.txt`) |
| **Database** | MySQL | 8.0 |
| **Cache** | Redis | 7 |
| **Vector DB** | Qdrant | Latest |
| **SMTP** | Postfix | 3.x |
| **IMAP/POP3** | Dovecot | 2.3+ |
| **Spam filter** | Rspamd | Latest |
| **Containers** | Docker + Docker Compose | v2+ |
| **Migrations** | Alembic | via `manage.py` / `migrate` service |
| **Testing** | pytest | 8.x (`requirements-test.txt`) |
| **Lint/format** | Ruff | baseline-gated in CI |
| **Type check** | mypy + mypy-baseline | baseline-gated in CI |
| **Docs** | MkDocs Material | `docs/requirements.txt` |

---

## Development workflow

Here's the typical flow for making a change:

```mermaid
flowchart LR
    A["Branch"] --> B["Write code"]
    B --> C["Write tests"]
    C --> D["Run linters"]
    D --> E["Run tests"]
    E --> F["Open PR"]
    F --> G["Code review"]
    G --> H["Merge"]
    style A fill:#4051b5,color:#fff
    style H fill:#2e7d32,color:#fff
```

=== "1. Branch"

    ```bash
    git checkout develop
    git checkout -b feature/my-new-feature
    ```

    Branch naming: `feature/`, `fix/`, `docs/`, `refactor/`, `test/`

=== "2. Code"

    Make your changes in the relevant `worker/` module or `shared/` library. If you need a database change, create an Alembic migration.

=== "3. Test"

    ```bash
    # Fast unit tests
    pytest tests/unit/

    # A specific test file
    pytest tests/unit/test_auth.py

    # With coverage (CI ratchets the repo total — see Testing)
    pytest tests/unit/ --cov=shared --cov=worker
    ```

=== "4. Lint"

    ```bash
    # Format
    ruff format .

    # Lint (CI compares the count against ruff-baseline.txt)
    ruff check .

    # Type check (CI filters through mypy-baseline.txt)
    bash scripts/run_mypy.sh | mypy-baseline filter
    ```

=== "5. PR"

    Push your branch and open a pull request. Fill in the PR template with what changed, why, and how to test it.

---

## Common development tasks

| I want to... | How |
|---|---|
| Run the full stack locally | `./start.sh dev` — see [Dev Setup](getting-started.md) |
| Add a new API endpoint | Add a route in `worker/api/routes/`, see [Adding Features](adding-features.md) |
| Create a database migration | `python manage.py migrate:create "description"` |
| Build a new worker | Copy an existing worker's layout, see [Custom Workers](custom-workers.md) |
| Add Prometheus metrics | See [Metrics Implementation](metrics-implementation.md) |
| Extend Mailyte without forking | See [Plugin Development](plugin-development.md) |
| Run the docs locally | `./start.sh dev` serves this handbook with live reload at `http://localhost:8000` (the `docs` service — `mkdocs.yml` lives in `docs/`, so a bare `mkdocs serve` from the repo root does not work) |
| Ship a release | Follow the [Release Process](release-process.md) |

---

## Quick Links

| Guide | What it covers |
|-------|---------------|
| [Getting Started](getting-started.md) | Dev environment setup, running locally |
| [Adding Features](adding-features.md) | How to add a new feature end to end |
| [Contributing](contributing.md) | PR process, branch naming, code review |
| [Coding Standards](coding-standards.md) | Ruff, mypy, baseline ratchets, naming |
| [Testing](testing.md) | Running and writing tests |
| [Custom Workers](custom-workers.md) | Building a new worker module |
| [Plugin Development](plugin-development.md) | Extension points — webhooks, workers, pipeline hooks |
| [Monitoring Integration](monitoring-integration.md) | Health checks and the shared metrics helper |
| [Metrics Implementation](metrics-implementation.md) | Counters, gauges, histograms |
| [Release Process](release-process.md) | Versioning, changelog, deployment |

---

## Related sections

- [Getting Started](../getting-started/index.md) -- install and run Mailyte (not dev-specific)
- [Architecture](../architecture/index.md) -- understand the system you're building on
- [API Reference](../api/index.md) -- the API you'll be extending
- [Features](../features/index.md) -- understand existing features before adding new ones
- [Security](../security/index.md) -- security practices to follow in your code
