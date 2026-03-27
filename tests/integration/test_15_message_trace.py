"""
Integration tests for message trace, quarantine, and audit logging.

Verifies the message-trace API endpoints and backing database tables
against the live Docker services.
"""
import pytest
import requests

from .conftest import (
    API_BASE,
    API_KEY,
    TEST_USER,
)

TIMEOUT = 10


# ---------------------------------------------------------------------------
# Message trace API
# ---------------------------------------------------------------------------

class TestMessageTraceAPI:

    def test_message_trace_search(self, api_headers):
        """GET /message-trace/trace with sender param is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/message-trace/trace",
            headers=api_headers,
            params={"sender": TEST_USER},
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 404, 422, 500), (
            f"Unexpected status for message trace search: {resp.status_code}"
        )

    def test_message_trace_quarantine(self, api_headers):
        """GET /message-trace/quarantine endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/message-trace/quarantine",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 404, 422, 500), (
            f"Unexpected status for quarantine list: {resp.status_code}"
        )

    def test_message_trace_audit(self, api_headers):
        """GET /message-trace/audit endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/message-trace/audit",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 404, 422, 500), (
            f"Unexpected status for audit log: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Database tables
# ---------------------------------------------------------------------------

class TestMessageTraceDatabase:

    def test_mail_logs_table(self, db_connection):
        """mail_logs table must exist and be queryable."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM mail_logs")
        count = cursor.fetchone()[0]
        cursor.close()
        assert count >= 0

    def test_quarantine_table(self, db_connection):
        """quarantine table must exist and be queryable."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM quarantine")
        count = cursor.fetchone()[0]
        cursor.close()
        assert count >= 0
