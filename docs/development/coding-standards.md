---
title: Coding Standards
description: Python style guide for Mailyte — Ruff, mypy, the CI baseline ratchets, naming conventions, and file organization.
---

# Coding Standards

Consistency matters more than any individual style choice. Here's what we've agreed on.

## Formatting and Linting

Two tools, both configured in `pyproject.toml` and both gated in CI (`.github/workflows/ci.yml`): **Ruff** for formatting and linting, **mypy** for type checking. Run them before every commit.

```bash
pip install ruff mypy mypy-baseline
```

### Ruff (Formatting)

```bash
# Auto-format
ruff format .

# What CI runs (must pass cleanly)
ruff format --check .
```

Config in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
exclude = ["database/migrations/archive", "alembic/versions"]

[tool.ruff.format]
quote-style = "double"
```

### Ruff (Linting)

```bash
ruff check .
```

Enabled rule families:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM"]
```

`I` is import sorting — there is no separate isort; Ruff handles it (`ruff check --fix .` applies it).

CI does **not** require zero violations. It counts them and compares against `ruff-baseline.txt` (a single number at the repo root). The count may only shrink:

- If your change adds a violation, CI fails — fix it, or offset it by fixing an existing one.
- If your change removes violations, CI prints a notice — update `ruff-baseline.txt` to the new count in the same PR so the ratchet doesn't drift back up.

### mypy (Type Checking)

mypy can't run once across the whole tree — `worker/*` and `mailer/*` are independently deployed services with no `__init__.py` between them, so module names collide (`worker/api/app.py` and `worker/analytics/app.py` are both module `app`). `scripts/run_mypy.sh` type-checks each directory in its own invocation and concatenates the output.

Known pre-existing errors live in `mypy-baseline.txt` and are filtered out by the [mypy-baseline](https://pypi.org/project/mypy-baseline/) tool; only **new** errors fail CI:

```bash
# What CI runs
bash scripts/run_mypy.sh | mypy-baseline filter

# After FIXING existing errors, shrink the baseline
bash scripts/run_mypy.sh | mypy-baseline sync
```

The baseline may only shrink, never grow — don't `sync` to absorb new errors you introduced.

mypy config in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
exclude = ["database/migrations/archive/", "alembic/versions/"]
```

### Shell Scripts

`scripts/*.sh` are linted with [ShellCheck](https://www.shellcheck.net/) in CI (advisory).

## Naming Conventions

### Files and Directories

- **snake_case** for everything: `email_tracking.py`, `queue_manager/`
- Descriptive names that say what's inside
- One module per concern

### Variables and Functions

```python
# Functions: snake_case, verb phrases
def get_mailbox(email: str) -> dict: ...


def calculate_storage_usage(domain_id: int) -> int: ...


def send_webhook_notification(event: dict) -> bool: ...


# Variables: snake_case, descriptive
total_storage_bytes = 0
is_authenticated = True
retry_count = 3
```

### Classes

```python
# PascalCase
class EmailTrackingService: ...


class WebhookDeliveryWorker: ...


class MailboxExportRequest(BaseModel): ...
```

### Constants

```python
# UPPER_SNAKE_CASE
MAX_RETRY_ATTEMPTS = 3
DEFAULT_QUOTA_BYTES = 1073741824  # 1 GB
WEBHOOK_TIMEOUT_SECONDS = 30
```

### Database Columns

```python
# snake_case in the database
# Match Python variable names to column names
organization_id = row["organization_id"]
created_at = row["created_at"]
```

## File Organization

### Worker Module Structure

Each worker is a standalone FastAPI service. The common shape (see `worker/storage_usage/` for a compact example, `worker/api/` for the largest):

```
worker/
  storage_usage/
    app.py             # FastAPI app: routes, /health, /metrics, middleware
    config.py          # Configuration from env vars
    services/          # Core business logic (larger workers)
    Dockerfile         # Container build (copies shared/ and database/ in)
    requirements.txt   # Pinned dependencies for this service only
```

The API gateway (`worker/api/`) additionally splits into `routes/`, `schemas/`, and `utils/`.

### Shared Code

Code used by multiple workers goes in `shared/` (each service's Dockerfile copies it into the image; in dev it's bind-mounted):

```
shared/
  config.py              # Configuration helpers
  db_pool.py             # Database connection pooling
  envelope_encryption.py # KEK/DEK envelope encryption
  imap_mail.py           # IMAP access helpers
  kafka_client.py        # Kafka producer/consumer helpers
  logging_config.py      # Logging setup
  metrics.py             # Prometheus metrics helper (get_metrics)
  object_storage.py      # S3/object storage
  ulid_utils.py          # ULID primary key helpers
  webhook_dispatcher.py  # Global webhook event dispatcher
```

## Type Hints

Use type hints everywhere. They make the code self-documenting and catch bugs early — and new untyped code shows up in the mypy gate.

```python
def get_domain(domain_name: str) -> dict | None:
    """Fetch a domain from the database. Returns None if not found."""
    ...


def send_email(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    headers: dict | None = None,
) -> bool: ...
```

Prefer modern syntax (`dict | None` over `Optional[dict]`) — the Ruff `UP` rules flag the legacy forms.

## Docstrings

Use docstrings on public functions and classes. Keep them short.

```python
def calculate_bounce_rate(org_id: str, hours: int = 24) -> float:
    """Calculate the bounce rate for an organization over the given time window.

    Returns a float between 0.0 and 1.0.
    """
    ...
```

Don't write docstrings for obvious things:

```python
# Bad — the function name already says this
def get_user_by_email(email: str) -> dict:
    """Get a user by their email address."""
    ...


# Better — add useful context
def get_user_by_email(email: str) -> dict:
    """Looks up the email_accounts table. Returns the full row including quota data."""
    ...
```

## Error Handling

### API Routes

Use HTTPException with meaningful error messages:

```python
from fastapi import HTTPException

if not domain:
    raise HTTPException(status_code=404, detail="domain_not_found")

if not user_is_authorized:
    raise HTTPException(status_code=403, detail="insufficient_permissions")
```

Give every new endpoint a `response_model` — CI's OpenAPI gate counts untyped 200-responses against `openapi-untyped-baseline.txt`, and that count may only shrink (see [Adding Features](adding-features.md)).

### Business Logic

Raise specific exceptions, don't catch everything:

```python
# Good
try:
    result = db.execute(query)
except IntegrityError:
    raise DuplicateEntryError(f"Domain {domain} already exists")

# Bad
try:
    result = db.execute(query)
except Exception:
    pass  # Never do this
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

# Levels:
logger.debug("Processing message %s", message_id)  # Noisy, off in prod
logger.info("Domain %s created for org %s", domain, org_id)  # Normal operations
logger.warning("Rate limit at 80%% for org %s", org_id)  # Needs attention soon
logger.error("Webhook delivery failed: %s", error)  # Something broke
logger.critical("Database connection lost")  # Service-level failure
```

Use `%s` formatting (not f-strings) in log calls — the formatting is deferred and skipped if the log level is disabled.

## Database Queries

### Use Parameterized Queries

```python
# Good
db.execute("SELECT * FROM domains WHERE domain = %s", (domain_name,))

# Bad — SQL injection risk
db.execute(f"SELECT * FROM domains WHERE domain = '{domain_name}'")
```

### Batch Operations

```python
# Good — one query
db.executemany(
    "INSERT INTO aliases (source, destination, domain_id) VALUES (%s, %s, %s)",
    [(a.source, a.destination, a.domain_id) for a in aliases],
)

# Bad — N queries in a loop
for alias in aliases:
    db.execute("INSERT INTO aliases ...")
```

## Dependencies

- Add new runtime dependencies to the **service's own** `worker/<service>/requirements.txt` (there is no repo-root `requirements.txt`); shared-module deps go in `shared/requirements.txt`
- Test-only dependencies go in `requirements-test.txt`
- Pin versions: `requests==2.32.5`, not `requests>=2.0`
- CI runs `pip-audit` against the pinned files, so keeping pins current matters
