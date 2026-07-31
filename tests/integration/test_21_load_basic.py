"""
Integration tests -- Basic load and concurrency.

Lightweight concurrency tests that verify the server can handle a small
number of simultaneous connections without errors.  These are NOT full
load/stress tests — they exercise the happy path under mild parallelism.
"""

import imaplib
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests

from .conftest import (
    API_BASE,
    API_KEY,
    DB_HOST,
    DB_NAME,
    DB_PASS,
    DB_PORT,
    DB_USER,
    IMAP_HOST,
    IMAP_PORT,
    SMTP_HOST,
    SMTP_PORT,
    TEST_PASS,
    TEST_USER,
)

TIMEOUT = 15  # seconds for individual operations


# ---------------------------------------------------------------------------
# SMTP concurrency
# ---------------------------------------------------------------------------


class TestSMTPConcurrency:
    """Verify SMTP can handle several connections at the same time."""

    def test_multiple_smtp_connections(self):
        """The server must handle 5 concurrent SMTP connections without
        refusing any. Production handles hundreds."""
        results = []
        errors = []

        def _connect():
            try:
                s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
                code, _ = s.ehlo("concurrency-test.local")
                results.append(code)
                s.quit()
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_connect) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=TIMEOUT + 5)

        if not results and errors:
            pytest.skip(f"Could not connect to SMTP at {SMTP_HOST}:{SMTP_PORT}: {errors[0]}")

        assert len(errors) == 0, f"{len(errors)}/5 SMTP connections failed: {errors}"
        assert all(code == 250 for code in results), f"Not all EHLO responses were 250: {results}"

    def test_rapid_ehlo_commands(self):
        """Sending 10 EHLO commands in quick succession tests connection
        recycling and rate limiting."""
        try:
            s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        except (smtplib.SMTPConnectError, OSError):
            pytest.skip(f"SMTP unreachable at {SMTP_HOST}:{SMTP_PORT}")

        try:
            codes = []
            for i in range(10):
                code, _ = s.ehlo(f"rapid-{i}.test.local")
                codes.append(code)
            s.quit()
        except smtplib.SMTPServerDisconnected:
            pytest.fail("Server disconnected during rapid EHLO sequence")

        assert all(c == 250 for c in codes), f"Expected all 250 responses, got: {codes}"


# ---------------------------------------------------------------------------
# IMAP concurrency
# ---------------------------------------------------------------------------


class TestIMAPConcurrency:
    """Verify IMAP handles several simultaneous authenticated sessions."""

    def test_multiple_imap_logins(self):
        """Multiple IMAP clients connecting simultaneously is normal
        (phone + desktop + web). Must all succeed."""
        results = []
        errors = []

        def _login():
            try:
                m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
                m.starttls()
                m.login(TEST_USER, TEST_PASS)
                results.append("OK")
                m.logout()
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_login) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=TIMEOUT + 5)

        if not results and errors:
            pytest.skip(f"Could not connect to IMAP at {IMAP_HOST}:{IMAP_PORT}: {errors[0]}")

        assert len(errors) == 0, f"{len(errors)}/3 IMAP logins failed: {errors}"
        assert len(results) == 3, f"Expected 3 successful logins, got {len(results)}"

    def test_imap_rapid_folder_operations(self):
        """Rapid LIST/SELECT operations test Dovecot's index
        performance."""
        try:
            m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
            m.starttls()
            m.login(TEST_USER, TEST_PASS)
        except Exception:
            pytest.skip(f"IMAP unreachable at {IMAP_HOST}:{IMAP_PORT}")

        try:
            for _ in range(5):
                status, data = m.list()
                assert status == "OK", f"LIST failed: {data}"

                status, data = m.select("INBOX")
                assert status == "OK", f"SELECT INBOX failed: {data}"
        finally:
            try:
                m.logout()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# API concurrency
# ---------------------------------------------------------------------------


class TestAPIConcurrency:
    """Verify the REST API handles parallel requests."""

    def test_api_parallel_requests(self):
        """API must handle 10 concurrent requests without errors. Load
        balancers send parallel traffic."""

        def _health_check(_):
            return requests.get(f"{API_BASE}/health", timeout=TIMEOUT)

        try:
            requests.get(f"{API_BASE}/health", timeout=TIMEOUT)
        except requests.ConnectionError:
            pytest.skip("API unreachable")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_health_check, i) for i in range(10)]
            responses = [f.result() for f in as_completed(futures)]

        status_codes = [r.status_code for r in responses]
        assert all(code == 200 for code in status_codes), (
            f"Not all parallel /health requests returned 200: {status_codes}"
        )

    def test_api_rapid_auth_checks(self):
        """Rapid API key validation tests the auth cache and DB connection
        pool."""
        headers = {
            "X-API-Key": API_KEY,
            "Content-Type": "application/json",
        }

        try:
            requests.get(
                f"{API_BASE}/api/v1/domains",
                headers=headers,
                timeout=TIMEOUT,
            )
        except requests.ConnectionError:
            pytest.skip("API unreachable")

        status_codes = []
        for _ in range(10):
            resp = requests.get(
                f"{API_BASE}/api/v1/domains",
                headers=headers,
                timeout=TIMEOUT,
            )
            status_codes.append(resp.status_code)

        success = sum(1 for code in status_codes if code in (200, 422))
        assert success >= 8, f"Too many failures in 10 rapid auth checks: {status_codes}"


# ---------------------------------------------------------------------------
# Database concurrency
# ---------------------------------------------------------------------------


class TestDatabaseConcurrency:
    """Verify the database connection pool handles parallel queries."""

    def test_db_connection_pool(self):
        """Multiple simultaneous DB queries must not deadlock. Tests
        connection pool sizing."""
        try:
            import mysql.connector
        except ImportError:
            pytest.skip("mysql-connector-python not installed")

        results = []
        errors = []

        def _query():
            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME,
                    user=DB_USER,
                    password=DB_PASS,
                    connect_timeout=TIMEOUT,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
                results.append(row)
                cursor.close()
                conn.close()
            except Exception as exc:
                errors.append(str(exc))

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_query) for _ in range(5)]
            for f in as_completed(futures):
                pass  # results collected inside _query

        if not results and errors:
            pytest.skip(f"Cannot connect to MySQL: {errors[0]}")

        assert len(errors) == 0, f"{len(errors)}/5 DB queries failed: {errors}"
        assert all(row == (1,) for row in results), (
            f"Not all SELECT 1 queries returned (1,): {results}"
        )
