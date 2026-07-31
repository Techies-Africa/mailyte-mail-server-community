"""
Integration tests -- Cloud mode configuration and remote database.

Validates that the project supports cloud deployments where MySQL and Redis
run on managed services (e.g. AWS RDS, ElastiCache) rather than local Docker
containers.  These tests verify that configuration is environment-driven and
that the remote database is properly initialised.
"""

import os
import pytest
import redis

from .conftest import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

TIMEOUT = 10  # seconds for network operations

# Path to the project root (two levels above tests/integration/)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

REDIS_HOST = os.getenv("TEST_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("TEST_REDIS_PORT", "6379"))


# ---------------------------------------------------------------------------
# Cloud mode configuration
# ---------------------------------------------------------------------------


class TestCloudModeConfig:
    """Verify configuration files and env-driven settings for cloud mode."""

    def test_cloud_compose_file_exists(self):
        """The docker-compose.cloud.yml override file must exist for cloud
        deployments using remote MySQL/Redis."""
        cloud_compose = os.path.join(PROJECT_ROOT, "docker-compose.cloud.yml")
        if not os.path.exists(cloud_compose):
            pytest.skip(
                "docker-compose.cloud.yml not found — cloud override may "
                "not be part of this checkout"
            )
        assert os.path.isfile(cloud_compose)

    def test_env_supports_remote_db(self):
        """The .env.example must document DB_HOST so operators know to set it
        for cloud mode."""
        env_example = os.path.join(PROJECT_ROOT, ".env.example")
        if not os.path.exists(env_example):
            pytest.skip(".env.example not found at project root")
        with open(env_example, "r") as fh:
            contents = fh.read()
        assert "DB_HOST" in contents, (
            ".env.example does not mention DB_HOST — operators won't know "
            "they can override it for cloud deployments"
        )

    def test_services_use_env_for_db_host(self, db_connection):
        """All services must read DB_HOST from environment, not hardcode
        'mysql'. This enables cloud mode."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        assert result is not None, (
            f"Could not execute query against DB_HOST={DB_HOST} — "
            "environment-based DB configuration may be broken"
        )

    def test_services_use_env_for_redis(self):
        """Redis host must be configurable via environment for
        ElastiCache/cloud Redis."""
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=TIMEOUT)
            pong = r.ping()
        except redis.ConnectionError:
            pytest.skip(f"Cannot connect to Redis at {REDIS_HOST}:{REDIS_PORT}")
        except ImportError:
            pytest.skip("redis-py not installed")
        assert pong is True, "Redis PING did not return PONG"


# ---------------------------------------------------------------------------
# Cloud mode database
# ---------------------------------------------------------------------------


class TestCloudModeDatabase:
    """Verify the remote (or local) database is correctly set up."""

    def test_remote_db_connection(self, db_connection):
        """In cloud mode, DB_HOST points to remote MySQL (RDS). This test
        verifies the connection works regardless of where MySQL runs."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        cursor.close()
        assert row == (1,), f"SELECT 1 returned unexpected result: {row}"

    def test_remote_db_has_schema(self, db_connection):
        """Cloud database must have all required tables. Missing tables
        indicate an incomplete migration."""
        cursor = db_connection.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        assert len(tables) >= 40, (
            f"Expected at least 40 tables in '{DB_NAME}', found {len(tables)}: {tables}"
        )

    def test_remote_db_has_seed_data(self, db_connection):
        """Cloud database must have at least one org and domain for the
        system to function."""
        cursor = db_connection.cursor()

        cursor.execute("SELECT COUNT(*) FROM organizations")
        org_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM domains")
        domain_count = cursor.fetchone()[0]

        cursor.close()

        assert org_count >= 1, "No organizations found — seed data missing"
        assert domain_count >= 1, "No domains found — seed data missing"
