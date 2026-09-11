# Contributing to Mailyte Email Server

Thanks for your interest in contributing to Mailyte. This guide covers the basics; the full contributor handbook lives in [docs/development/](docs/development/index.md).

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/mailyte-email-server.git`
3. Create a branch off `develop`: `git checkout -b feature/your-feature`
4. Make your changes
5. Run tests and linters (see below)
6. Push and open a pull request

## Development Setup

```bash
# Generate .env with strong random secrets (required — the stack
# refuses to start with missing or known-weak secrets)
bash scripts/generate-secrets.sh
# Then edit .env (HOSTNAME, DOMAIN, ...)

# Start in development mode (hot-reload)
./start.sh dev
# Or interactively: ./start.sh → option 4 (Development mode)

# Which is equivalent to:
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

The API will be at `http://localhost:8083/api-docs`.

## Running Tests

Python 3.11+ is required.

```bash
# Unit tests (no Docker required)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt -r worker/api/requirements.txt
pytest tests/unit/

# Integration tests (requires the running dev stack)
./start.sh test
```

## Code Style

- Python: [Ruff](https://docs.astral.sh/ruff/) for linting and formatting, [mypy](https://mypy-lang.org/) for type checking — both configured in `pyproject.toml`
- Shell scripts: Validated with [ShellCheck](https://www.shellcheck.net/)
- SQL: Standard MySQL 8.0 syntax

Run before submitting:

```bash
pip install ruff mypy mypy-baseline
ruff format --check .
ruff check .
bash scripts/run_mypy.sh | mypy-baseline filter
```

CI gates lint, type errors, coverage, and the OpenAPI contract against baseline files at the repo root (`ruff-baseline.txt`, `mypy-baseline.txt`, `coverage-baseline.txt`, `openapi-untyped-baseline.txt`). The counts may only shrink: new violations fail the build, and when your PR removes some, update the baseline in the same PR. See [docs/development/coding-standards.md](docs/development/coding-standards.md).

## Project Structure

```
mailer/          # Core mail services (Postfix, Dovecot, Rspamd, cert_manager)
worker/          # Python microservices (API, tracking, webhooks, etc.)
database/        # SQLAlchemy models and frozen SQL bootstrap files
alembic/         # Alembic migrations (run via manage.py or the migrate service)
shared/          # Shared utilities (config, metrics, webhook dispatcher)
scripts/         # CLI tools and management scripts
tests/           # Unit, integration, e2e, and load tests
```

## Pull Request Guidelines

- Keep PRs focused on a single change
- Include tests for new functionality
- Update documentation if behavior changes
- Ensure all existing tests pass
- Follow the existing code style

## What to Contribute

- Bug fixes
- Documentation improvements
- Test coverage
- Performance improvements
- New mail filters or Sieve templates
- Improved error messages
- Docker/deployment improvements

## What Belongs in Enterprise Edition

The following features are maintained in the Enterprise Edition and should not be added to the Community Edition (`mailyte-mail-server-community`):

- Analytics and reporting dashboards
- AI/ML features (RAG, semantic search)
- GDPR compliance tools
- Multi-tenancy improvements beyond single-org
- ActiveSync, JMAP, CalDAV, OAuth
- Reseller and white-label features
- Advanced queue management
- Prometheus/Grafana monitoring integration

If you're unsure whether a feature belongs in CE or EE, open an issue to discuss.

## Reporting Bugs

Open a [GitHub issue](https://github.com/Techies-Africa/mailyte-email-server/issues) with:

1. Steps to reproduce
2. Expected behavior
3. Actual behavior
4. Docker/OS version
5. Relevant logs (`docker compose logs <service>`)

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
