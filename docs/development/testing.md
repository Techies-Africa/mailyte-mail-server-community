---
title: Testing
description: Running tests, writing tests, setting up the test database, and mocking external services.
---

# Testing

We use pytest for everything. Tests live in the `tests/` directory and follow the same structure as the code they test.

## Running Tests

### Full Suite

```bash
# Run all tests
pytest

# Verbose output
pytest -v

# Stop on first failure
pytest -x

# Show print output
pytest -s
```

### Specific Tests

```bash
# Single file
pytest tests/test_domains.py

# Single test class
pytest tests/test_domains.py::TestDomainCreation

# Single test
pytest tests/test_domains.py::TestDomainCreation::test_create_domain -v

# Tests matching a keyword
pytest -k "domain and not delete"
```

### Coverage

```bash
# Run with coverage report
pytest --cov=worker --cov-report=html

# View the HTML report
open htmlcov/index.html
```

## Test Database

Tests run against a separate MySQL database to avoid messing with development data.

### Setup

The test config in `pytest.ini` (or `pyproject.toml`) sets up the test database:

```ini
# pytest.ini
[pytest]
testpaths = tests
env =
    DB_NAME=mailserver_test
    DB_HOST=localhost
    DB_PORT=3306
    DB_USER=mailuser
    DB_PASSWORD=devpassword
    REDIS_HOST=localhost
    REDIS_PORT=6379
```

### Fixtures

Common fixtures are in `tests/conftest.py`:

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def db():
    """Create and tear down the test database."""
    # Create test database
    setup_test_database()
    yield get_db_connection()
    # Drop test database
    teardown_test_database()


@pytest.fixture
def client(db):
    """FastAPI test client."""
    from worker.api.main import app

    return TestClient(app)


@pytest.fixture
def api_key_header(db):
    """Valid API key header for authenticated requests."""
    key = create_test_api_key(db)
    return {"X-API-Key": key}


@pytest.fixture
def sample_org(db):
    """Create a sample organization for testing."""
    org_id = "test-org"
    db.execute(
        "INSERT IGNORE INTO organizations (id, name, active) VALUES (%s, %s, %s)",
        (org_id, "Test Organization", True),
    )
    return org_id


@pytest.fixture
def sample_domain(db, sample_org):
    """Create a sample domain for testing."""
    db.execute(
        "INSERT IGNORE INTO domains (domain, organization_id, active) VALUES (%s, %s, %s)",
        ("test.example.com", sample_org, True),
    )
    return "test.example.com"
```

### Auto-Cleanup

Use a fixture that wraps each test in a transaction rollback:

```python
@pytest.fixture(autouse=True)
def cleanup_db(db):
    """Roll back any changes after each test."""
    yield
    db.rollback()
```

## Writing Tests

### Test Structure

Follow the Arrange-Act-Assert pattern:

```python
def test_create_domain(client, api_key_header, sample_org):
    # Arrange
    domain_data = {
        "domain": "newtest.com",
        "organization_id": sample_org,
        "mailboxes": 50,
    }

    # Act
    response = client.post(
        "/api/v1/add/domain",
        headers=api_key_header,
        json=domain_data,
    )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "success"
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

Always test the unhappy path:

```python
def test_create_domain_duplicate_returns_409(client, api_key_header, sample_domain):
    response = client.post(
        "/api/v1/add/domain",
        headers=api_key_header,
        json={"domain": sample_domain, "organization_id": "test-org"},
    )
    assert response.status_code == 409


def test_create_domain_invalid_name(client, api_key_header, sample_org):
    response = client.post(
        "/api/v1/add/domain",
        headers=api_key_header,
        json={"domain": "not a valid domain!!", "organization_id": sample_org},
    )
    assert response.status_code == 422


def test_create_domain_without_auth(client):
    response = client.post(
        "/api/v1/add/domain",
        json={"domain": "test.com"},
    )
    assert response.status_code == 401
```

## Mocking External Services

### Mocking Redis

```python
from unittest.mock import patch, MagicMock


@patch("worker.api.services.redis_client.Redis")
def test_rate_limit_check(mock_redis, client, api_key_header):
    mock_redis_instance = MagicMock()
    mock_redis.return_value = mock_redis_instance
    mock_redis_instance.get.return_value = b"50"  # 50 requests so far

    response = client.post("/api/v1/add/domain", headers=api_key_header, json={...})
    assert response.status_code == 200
```

### Mocking SMTP

```python
@patch("smtplib.SMTP")
def test_send_email(mock_smtp, client, api_key_header):
    mock_instance = MagicMock()
    mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_instance)

    response = client.post(
        "/api/v1/send/email",
        headers=api_key_header,
        json={
            "from": "sender@test.com",
            "to": "recipient@test.com",
            "subject": "Test",
            "text": "Hello",
        },
    )

    assert response.status_code == 200
    mock_instance.sendmail.assert_called_once()
```

### Mocking Webhook Delivery

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

### Mocking Time

```python
from freezegun import freeze_time


@freeze_time("2025-03-25 14:00:00")
def test_cert_expiry_check():
    # All datetime.now() calls return 2025-03-25 14:00:00
    cert = {"valid_until": "2025-04-01 00:00:00"}
    assert days_until_expiry(cert) == 7
```

## Integration Tests

Integration tests hit the real database and services. They're slower but catch more bugs.

```python
@pytest.mark.integration
class TestFullMailFlow:
    def test_domain_to_mailbox_lifecycle(self, client, api_key_header):
        # Create org
        client.post(
            "/api/v1/add/organization",
            headers=api_key_header,
            json={"id": "lifecycle-test", "name": "Lifecycle Test"},
        )

        # Create domain
        client.post(
            "/api/v1/add/domain",
            headers=api_key_header,
            json={"domain": "lifecycle.test", "organization_id": "lifecycle-test"},
        )

        # Create mailbox
        resp = client.post(
            "/api/v1/add/mailbox",
            headers=api_key_header,
            json={
                "local_part": "user",
                "domain": "lifecycle.test",
                "password": "testpass123",
                "name": "Test User",
            },
        )
        assert resp.status_code == 200

        # Verify mailbox exists
        resp = client.get("/api/v1/get/mailbox/user@lifecycle.test", headers=api_key_header)
        assert resp.status_code == 200
```

Run integration tests separately:

```bash
pytest -m integration
```

## Test Categories

```python
# Mark tests
@pytest.mark.unit          # Fast, no external deps
@pytest.mark.integration   # Needs database
@pytest.mark.slow          # Takes > 5 seconds
@pytest.mark.smoke         # Critical path only
```

Run by category:

```bash
pytest -m unit             # Only unit tests
pytest -m "not slow"       # Skip slow tests
pytest -m smoke            # Quick smoke test
```
