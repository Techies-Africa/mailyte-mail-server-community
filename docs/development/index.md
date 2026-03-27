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

    Python style, naming conventions, file organization, and import ordering.

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

    Extend Mailyte with plugins — hooks, event handlers, and custom integrations.

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
  alembic/              # Database migration scripts
  config/               # Service configuration files
    mailer/
      postfix/          # Postfix overrides
      dovecot/          # Dovecot overrides
      rspamd/           # Rspamd overrides
  database/
    migrations/sql/     # SQL bootstrap files
  deployment/           # Kubernetes, production configs
  docs/                 # This handbook (MkDocs)
  logs/                 # Service logs (git-ignored)
  mailer/               # Mail service Dockerfiles and configs
    postfix/
    dovecot/
    rspamd/
    cert_manager/
  monitoring/           # Prometheus, Grafana configs
  scripts/              # Utility scripts
  security/             # Security-related configs
  shared/               # Shared Python modules across workers
  storage/              # Mail data, certs, DKIM keys (git-ignored)
  tests/                # Test suite
  worker/               # Worker service modules
    api/                # REST API (FastAPI)
    tracking/           # Email tracking
    webhooks/           # Webhook delivery
    analytics/          # Analytics aggregation
    rate_limiter/       # Rate limiting
    queue_manager/      # Mail queue management
    storage_usage/      # Storage monitoring
    rag/                # AI-powered search
    monitoring/         # Health checks and metrics
    dashboard/          # Admin dashboard
    templates/          # Email templates
    encryption/         # Email encryption
    archiver/           # Email archiving
    activesync/         # ActiveSync protocol
    cloud_sync/         # Cloud backup sync
    delivery_optimizer/ # Delivery optimization
  docker-compose.yml    # Main compose file
  main.py               # Application entry point
  manage.py             # Management CLI
  pyproject.toml        # Python project config
  requirements.txt      # Python dependencies
```

!!! info "Key directories"
    Most of your time will be spent in `worker/` (business logic), `shared/` (common utilities), `tests/` (test suite), and `alembic/` (database migrations). The `mailer/` directory is for mail service configuration and rarely needs changes.

---

## Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| **Language** | Python | 3.11+ |
| **API framework** | FastAPI | Latest |
| **Database** | MySQL | 8.0 |
| **Cache** | Redis | 7 |
| **Vector DB** | Qdrant | Latest |
| **SMTP** | Postfix | 3.x |
| **IMAP/POP3** | Dovecot | 2.3+ |
| **Spam filter** | Rspamd | Latest |
| **Containers** | Docker + Docker Compose | v2+ |
| **Migrations** | Alembic | Latest |
| **Testing** | pytest | Latest |
| **Linting** | Black, isort, flake8 | Latest |
| **Docs** | MkDocs Material | Latest |

---

## Development workflow

Here's the typical flow for making a change:

```mermaid
flowchart LR
    A["Fork & branch"] --> B["Write code"]
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
    git checkout -b feature/my-new-feature
    ```

    Branch naming: `feature/`, `fix/`, `docs/`, `refactor/`, `test/`

=== "2. Code"

    Make your changes in the relevant `worker/` module or `shared/` library. If you need a database change, create an Alembic migration.

=== "3. Test"

    ```bash
    # Run all tests
    pytest tests/

    # Run tests for a specific module
    pytest tests/test_tracking.py

    # Run with coverage
    pytest --cov=worker tests/
    ```

=== "4. Lint"

    ```bash
    # Format code
    black .
    isort .

    # Check for issues
    flake8 .
    ```

=== "5. PR"

    Push your branch and open a pull request. Fill in the PR template with what changed, why, and how to test it.

---

## Common development tasks

| I want to... | How |
|---|---|
| Run the full stack locally | `docker compose up` -- see [Dev Setup](getting-started.md) |
| Add a new API endpoint | Add a route in `worker/api/`, see [Adding Features](adding-features.md) |
| Create a database migration | `alembic revision --autogenerate -m "description"` |
| Build a new worker | Copy the worker template, see [Custom Workers](custom-workers.md) |
| Add Prometheus metrics | See [Metrics Implementation](metrics-implementation.md) |
| Write a plugin | See [Plugin Development](plugin-development.md) |
| Run the docs locally | `mkdocs serve` from the repo root |
| Ship a release | Follow the [Release Process](release-process.md) |

---

## Quick Links

| Guide | What it covers |
|-------|---------------|
| [Getting Started](getting-started.md) | Dev environment setup, running locally |
| [Adding Features](adding-features.md) | How to add a new feature end to end |
| [Contributing](contributing.md) | PR process, branch naming, code review |
| [Coding Standards](coding-standards.md) | Python style, naming, file organization |
| [Testing](testing.md) | Running and writing tests |
| [Custom Workers](custom-workers.md) | Building a new worker module |
| [Plugin Development](plugin-development.md) | Extending with plugins |
| [Monitoring Integration](monitoring-integration.md) | Adding Prometheus metrics |
| [Metrics Implementation](metrics-implementation.md) | Counters, gauges, histograms |
| [Release Process](release-process.md) | Versioning, changelog, deployment |

---

## Related sections

- [Getting Started](../getting-started/index.md) -- install and run Mailyte (not dev-specific)
- [Architecture](../architecture/index.md) -- understand the system you're building on
- [API Reference](../api/index.md) -- the API you'll be extending
- [Features](../features/index.md) -- understand existing features before adding new ones
- [Security](../security/index.md) -- security practices to follow in your code
