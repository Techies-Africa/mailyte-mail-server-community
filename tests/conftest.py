#!/usr/bin/env python3
"""
Pytest configuration and shared fixtures for Mailyte tests.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set test environment variables before importing anything
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "test_mailserver")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("WEBHOOK_URL", "")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret-key")
os.environ.setdefault("SERVICE_NAME", "test")
os.environ.setdefault("ADMIN_TOKEN_SECRET", "test-admin-token")


@pytest.fixture
def mock_db_connection():
    """Mock database connection for unit tests."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    try:
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    except ImportError:
        return MagicMock()


@pytest.fixture
def sample_org():
    """Sample organization data."""
    return {
        "id": "test-org-001",
        "name": "Test Organization",
        "admin_email": "admin@testorg.com",
        "active": True,
    }


@pytest.fixture
def sample_domain(sample_org):
    """Sample domain data."""
    return {
        "id": 1,
        "domain": "testorg.com",
        "organization_id": sample_org["id"],
        "active": True,
    }


@pytest.fixture
def sample_api_key():
    """Sample API key data."""
    return {
        "id": 1,
        "api_key": "test-api-key-12345",
        "organization_id": "test-org-001",
        "active": 1,
        "admin_access": 0,
        "read_only": 0,
    }
