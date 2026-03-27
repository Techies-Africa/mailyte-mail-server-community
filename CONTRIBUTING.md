# Contributing to Mailyte Email Server

Thanks for your interest in contributing to Mailyte. This guide covers the process for contributing to the Community Edition.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/mailyte-mail-server.git`
3. Create a branch: `git checkout -b feature/your-feature`
4. Make your changes
5. Run tests (see below)
6. Push and open a pull request

## Development Setup

```bash
cp .env.example .env
# Edit .env with your settings

# Start in development mode (hot-reload)
./start.sh
# Choose option 4 (development mode)

# Or directly:
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

The API will be at `http://localhost:8083/api-docs`.

## Running Tests

```bash
# Unit tests (no Docker required)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim \
  bash -c "pip install -q pytest bcrypt fastapi && python -m pytest tests/unit/ -v"

# Integration tests (requires running services)
./start.sh test
```

## Code Style

- Python: We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- Shell scripts: Validated with [ShellCheck](https://www.shellcheck.net/)
- SQL: Standard MySQL 8.0 syntax

Run the linter before submitting:

```bash
pip install ruff
ruff check .
ruff format --check .
```

## Project Structure

```
mailer/          # Core mail services (Postfix, Dovecot, Rspamd)
worker/          # Python microservices (API, tracking, webhooks, etc.)
database/        # SQLAlchemy models and Alembic migrations
shared/          # Shared utilities (config, logging, webhook dispatcher)
scripts/         # CLI tools and management scripts
tests/           # Unit and integration tests
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

The following features are maintained in the private Enterprise Edition and should not be added to the Community Edition:

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

Open a [GitHub issue](https://github.com/Techies-Africa/mailyte-mail-server-community/issues) with:

1. Steps to reproduce
2. Expected behavior
3. Actual behavior
4. Docker/OS version
5. Relevant logs (`docker compose logs <service>`)

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
