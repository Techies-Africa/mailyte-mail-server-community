---
title: Testing
description: Running tests, the test suite layout, the CI coverage ratchet, and mocking external services.
---

# Testing

We use pytest for everything. Tests live in the `tests/` directory.

## Suite Layout

```
tests/
  conftest.py            # Shared fixtures + test env vars
  unit/                  # Pure unit tests — no Docker, no database
  integration/           # Run against the LIVE Docker stack (marked integration)
  e2e/                   # End-to-end API/flow tests against a running stack
  load/                  # k6 load-test scripts (JavaScript, not pytest)
  test_dashboard.py      # \
  test_phase2_services.py #  } top-level suites, run in CI alongside unit/
  test_security_regressions.py  # /
```

Pytest config lives in `pytest.ini` at the repo root (and a second `tests/pytest.ini` used when running from inside `tests/`). The registered markers are `integration`, `slow`, `security`, and `smoke` — there is no `unit` marker; unit tests are just the `tests/unit/` directory.

!!! note "`--timeout=30` is always on"
    Root `pytest.ini` sets `addopts = -v --tb=short --no-header --timeout=30`, so `pytest-timeout` must be installed (it's in `requirements-test.txt`) or every invocation fails with "unrecognized arguments".

## Installing Test Dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt -r worker/api/requirements.txt
```

`worker/api/requirements.txt` is required because `tests/unit/test_auth.py` and `tests/test_security_regressions.py` import from `worker/api/utils/`, which needs FastAPI installed to import at all.

## Running Tests

### Unit Tests (fast, no services)

```bash
pytest tests/unit/

# Single file
pytest tests/unit/test_webhook_dispatcher.py

# Single test, by keyword
pytest tests/unit/ -k "auth and not session"

# Stop on first failure / show print output
pytest tests/unit/ -x -s
```

### What CI Runs

CI (`.github/workflows/ci.yml`) runs everything except the live-stack suites, with MySQL 8 and Redis 7 service containers and `PYTHONPATH=.`:

```bash
pytest tests/ \
  --ignore=tests/e2e \
  --ignore=tests/integration \
  --ignore=tests/load \
  --ignore=tests/integration_test.py \
  --ignore=tests/test_mail_flow.py \
  --ignore=tests/test_dashboard.py \
  --cov=shared --cov=worker --cov-report=xml:coverage.xml
```

Test failures fail CI. (Until 2026-08-30 this step ended in `|| true`, so a red suite still produced a green job — do not reintroduce that.) `tests/test_dashboard.py` is ignored because it is Flask-era code against what is now a FastAPI dashboard service; all six of its tests fail unconditionally. Fix or delete that file, then drop the ignore from `ci.yml` and this list.

### Integration Tests (live Docker stack)

Integration tests in `tests/integration/` speak real SMTP/IMAP/HTTP to the running containers. Start the stack first, then:

```bash
./start.sh dev     # stack must be up
./start.sh test    # runs tests/integration/ in a container on the Docker network
```

`./start.sh test` runs pytest inside a `python:3.11-slim` container attached to `mailserver_network`, with 4 parallel workers (`pytest-xdist -n 4 --dist loadgroup` — tests that must run serially share an `xdist_group` marker) and all `TEST_*` connection env vars pointed at the container names (`postfix`, `dovecot`, `api:8080`, ...).

To run them from the host instead (services published on localhost):

```bash
pytest tests/integration/
```

Select by directory, not by `-m integration` — most files in `tests/integration/` don't carry the marker, so a marker filter deselects nearly everything.

`tests/integration/conftest.py` reads `TEST_SMTP_HOST`, `TEST_API_BASE`, `TEST_DB_*` etc. from the environment, defaulting to `localhost` and the host-mapped ports (API at `http://localhost:8083`).

### Coverage

```bash
pytest tests/unit/ --cov=shared --cov=worker --cov-report=html
open htmlcov/index.html
```

CI enforces two coverage gates:

1. **Repo total is a ratchet** — total line coverage must not fall below the number in `coverage-baseline.txt`. If your PR raises it, update the baseline so it can't drift back down.
2. **Changed code ≥ 60%** — `diff-cover` compares `coverage.xml` against `origin/develop` and fails if the lines you touched are under 60% covered.

## Fixtures

The real shared fixtures are in `tests/conftest.py`. They are **mocks and sample data**, not a live database — unit tests never touch MySQL:

```python
# tests/conftest.py (actual fixtures)


@pytest.fixture
def mock_db_connection():
    """Returns (mock_conn, mock_cursor) MagicMocks."""


@pytest.fixture
def mock_redis():
    """fakeredis.FakeRedis(decode_responses=True), or a MagicMock fallback."""


@pytest.fixture
def sample_org():
    """Dict: {'id': 'test-org-001', 'name': 'Test Organization', ...}"""


@pytest.fixture
def sample_domain(sample_org):
    """Dict: {'domain': 'testorg.com', 'organization_id': ..., ...}"""


@pytest.fixture
def sample_api_key():
    """Dict: {'api_key': 'test-api-key-12345', 'organization_id': ..., ...}"""
```

`tests/conftest.py` also sets the `DB_*`/`REDIS_*`/`ADMIN_TOKEN_SECRET` env vars (via `os.environ.setdefault`) before anything imports the code under test, so modules that read config at import time get safe test values.

## Writing Tests

### Test Structure

Follow the Arrange-Act-Assert pattern:

```python
def test_lookup_returns_none_for_unknown_domain(mock_db_connection):
    # Arrange
    mock_conn, mock_cursor = mock_db_connection
    mock_cursor.fetchone.return_value = None

    # Act
    result = get_domain(mock_conn, "missing.example.com")

    # Assert
    assert result is None
```

### Test Naming

Name tests descriptively. When a test fails, the name should tell you what's broken:

```python
# Good
def test_create_domain_with_valid_data():
def test_create_domain_rejects_duplicate():
def test_create_domain_requires_organization():
def test_delete_domain_cascades_to_mailboxes():

# Bad
def test_domain():
def test_1():
def test_domain_works():
```

### Testing Error Cases

Always test the unhappy path — invalid input, missing auth, duplicates. The e2e suite (`tests/e2e/test_api_endpoints.py`) covers every registered API route this way; new endpoints should get the same treatment.

## Mocking External Services

All of these libraries are pinned in `requirements-test.txt`.

### Mocking Redis (fakeredis)

Use the `mock_redis` fixture — it's a real in-memory Redis implementation, so expiry, counters, and data structures behave correctly:

```python
def test_rate_limit_counter(mock_redis):
    mock_redis.incr("rl:org-1:minute")
    mock_redis.expire("rl:org-1:minute", 60)
    assert int(mock_redis.get("rl:org-1:minute")) == 1
```

### Mocking SMTP

```python
from unittest.mock import patch, MagicMock


@patch("smtplib.SMTP")
def test_send_email(mock_smtp):
    mock_instance = MagicMock()
    mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_instance)

    send_message("sender@test.com", "recipient@test.com", "Test", "Hello")

    mock_instance.sendmail.assert_called_once()
```

### Mocking HTTP (responses)

```python
import responses


@responses.activate
def test_webhook_delivery():
    responses.add(
        responses.POST,
        "https://app.example.com/webhook",
        json={"status": "ok"},
        status=200,
    )

    deliver_webhook("https://app.example.com/webhook", {"event": "test"})

    assert len(responses.calls) == 1
```

`tests/unit/test_webhook_dispatcher.py` is a full worked example of this pattern against `shared/webhook_dispatcher.py`.

### Mocking Time (freezegun)

```python
from freezegun import freeze_time


@freeze_time("2026-08-30 14:00:00")
def test_cert_expiry_check():
    cert = {"valid_until": "2026-09-06 00:00:00"}
    assert days_until_expiry(cert) == 7
```

## Markers

```python
@pytest.mark.integration   # Requires running Docker services
@pytest.mark.slow          # Takes > 10 seconds (email delivery)
@pytest.mark.security      # Security-related tests
@pytest.mark.smoke         # Quick sanity checks (run first)
```

Run by marker:

```bash
pytest -m "not slow"       # Skip slow tests
pytest -m security         # Security regression tests
pytest -m smoke            # Quick smoke test
```

(Live-stack tests are selected by directory — `pytest tests/integration/` — since most of them don't carry the `integration` marker.)

## Load Tests

`tests/load/` contains [k6](https://k6.io/) scripts (`api_load.js`, `smtp_load.js`, `imap_load.js`) — they are JavaScript, run with the `k6` CLI against a running stack, and are not collected by pytest.
