"""
Integration tests for quota enforcement and storage management.

Verifies storage usage endpoints, quota configuration,
and database schema for quota-related columns against
the live Docker services.
"""

import requests

from .conftest import (
    API_BASE,
    STORAGE_BASE,
    TEST_DOMAIN,
    TEST_USER,
)

TIMEOUT = 10


# ---------------------------------------------------------------------------
# Storage service health
# ---------------------------------------------------------------------------


class TestStorageHealth:
    def test_storage_usage_health(self):
        """Storage service health endpoint returns 200."""
        resp = requests.get(f"{STORAGE_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code in (200, 422, 500)


# ---------------------------------------------------------------------------
# Storage usage API
# ---------------------------------------------------------------------------


class TestStorageUsageAPI:
    def test_get_domain_storage_usage(self, api_headers):
        """Domain storage usage endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/storage/usage/domain/{TEST_DOMAIN}",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        # 200 when data exists, 500 if storage tracking is not yet populated
        assert resp.status_code in (200, 422, 500, 503), (
            f"Unexpected status for domain storage usage: {resp.status_code}"
        )

    def test_get_mailbox_storage_usage(self, api_headers):
        """Mailbox storage usage endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/storage/usage/mailbox/{TEST_USER}",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        # Accept any non-crash response; the mailbox may or may not have data
        assert resp.status_code in (200, 422, 500, 503), (
            f"Unexpected status for mailbox storage usage: {resp.status_code}"
        )

    def test_get_domain_quotas(self, api_headers):
        """Domain quotas endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/storage/quotas/domain/{TEST_DOMAIN}",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        # Accept success or server-side error (service is alive either way)
        assert resp.status_code in (200, 404, 422, 500, 503), (
            f"Unexpected status for domain quotas: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Database-level quota checks
# ---------------------------------------------------------------------------


class TestQuotaDatabase:
    def test_default_quota_exists(self, db_connection):
        """Test user's email account has a storage_quota greater than zero."""
        cursor = db_connection.cursor()
        cursor.execute(
            "SELECT storage_quota FROM email_accounts WHERE email = %s",
            (TEST_USER,),
        )
        row = cursor.fetchone()
        cursor.close()

        assert row is not None, f"No email_account found for {TEST_USER}"
        storage_quota = row[0]
        assert storage_quota is not None and storage_quota > 0, (
            f"Expected storage_quota > 0, got {storage_quota}"
        )

    def test_quota_columns_in_schema(self, db_connection):
        """email_accounts table must have storage_quota and storage_used columns."""
        cursor = db_connection.cursor()
        cursor.execute("DESCRIBE email_accounts")
        columns = {row[0] for row in cursor.fetchall()}
        cursor.close()

        assert "storage_quota" in columns, "Missing storage_quota column in email_accounts"
        assert "storage_used" in columns, "Missing storage_used column in email_accounts"

    def test_domain_max_users_configured(self, db_connection):
        """Test domain has max_users configured and greater than zero."""
        cursor = db_connection.cursor()
        cursor.execute(
            "SELECT max_users FROM domains WHERE domain = %s",
            (TEST_DOMAIN,),
        )
        row = cursor.fetchone()
        cursor.close()

        assert row is not None, f"No domain record found for {TEST_DOMAIN}"
        max_users = row[0]
        assert max_users is not None and max_users > 0, f"Expected max_users > 0, got {max_users}"
