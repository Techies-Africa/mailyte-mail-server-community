---
title: Coding Standards
description: Python style guide for Mailyte — Black, isort, flake8, naming conventions, and file organization.
---

# Coding Standards

Consistency matters more than any individual style choice. Here's what we've agreed on.

## Formatting

We use three tools. Run them before every commit.

### Black (Code Formatting)

Black formats your code so you don't have to argue about style.

```bash
black worker/ shared/ tests/
```

Config in `pyproject.toml`:

```toml
[tool.black]
line-length = 100
target-version = ['py311']
```

### isort (Import Sorting)

isort organizes your imports into groups: stdlib, third-party, local.

```bash
isort worker/ shared/ tests/
```

Config in `pyproject.toml`:

```toml
[tool.isort]
profile = "black"
line_length = 100
```

### flake8 (Linting)

flake8 catches common mistakes.

```bash
flake8 worker/ shared/ tests/
```

Config in `pyproject.toml` or `.flake8`:

```ini
[flake8]
max-line-length = 100
extend-ignore = E203, W503
exclude = .git, __pycache__, .venv, alembic
```

### Run All Three

```bash
black worker/ shared/ tests/ && isort worker/ shared/ tests/ && flake8 worker/ shared/ tests/
```

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

Each worker follows the same pattern:

```
worker/
  tracking/
    __init__.py
    main.py           # Entry point, starts the service
    routes.py          # HTTP endpoints (health, metrics)
    service.py         # Core business logic
    models.py          # Data models / Pydantic schemas
    config.py          # Configuration loading
    Dockerfile         # Container build
    requirements.txt   # Dependencies
```

### Shared Code

Code used by multiple workers goes in `shared/`:

```
shared/
  database.py          # Database connection helpers
  redis_client.py      # Redis connection
  metrics.py           # Prometheus metric helpers
  auth.py              # Authentication utilities
  models/              # Shared data models
```

## Type Hints

Use type hints everywhere. They make the code self-documenting and catch bugs early.

```python
from typing import Optional


def get_domain(domain_name: str) -> Optional[dict]:
    """Fetch a domain from the database. Returns None if not found."""
    ...


def send_email(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    headers: Optional[dict] = None,
) -> bool: ...
```

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

- Add new Python dependencies to `requirements.txt` (or the worker's own `requirements.txt`)
- Pin versions: `requests==2.31.0`, not `requests>=2.0`
- Run `pip freeze` to get the exact version
