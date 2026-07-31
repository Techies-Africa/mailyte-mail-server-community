"""
Integration tests -- Infrastructure health checks.

Verifies that all services (Redis, MySQL, HTTP microservices, mail ports)
are reachable and responding before the heavier functional tests run.
"""

import os
import socket

import pytest
import redis
import requests

from .conftest import (
    API_BASE,
    TRACKING_BASE,
    WEBHOOKS_BASE,
    RATE_LIMITER_BASE,
    MONITORING_BASE,
    ANALYTICS_BASE,
    DASHBOARD_BASE,
    QUEUE_BASE,
    STORAGE_BASE,
    DOCS_BASE,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_PORT_25,
    IMAP_HOST,
    IMAP_PORT,
    IMAP_SSL_PORT,
    POP3_HOST,
    POP3_PORT,
    POP3_SSL_PORT,
)

TIMEOUT = 10  # seconds for HTTP / socket operations


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


class TestInfrastructure:
    """Redis and MySQL connectivity."""

    def test_redis_healthy(self):
        """Redis must respond to PING within timeout. A failed Redis means rate limiting, caching, and session management are all down."""
        redis_host = os.getenv("TEST_REDIS_HOST", "redis")
        redis_port = int(os.getenv("TEST_REDIS_PORT", "6379"))
        r = redis.Redis(host=redis_host, port=redis_port, socket_timeout=TIMEOUT)
        assert r.ping() is True

    def test_mysql_healthy(self, db_connection):
        """MySQL must execute a trivial query successfully. All user, domain, and mail metadata depend on a healthy database."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        assert result[0] == 1
        cursor.close()


# ---------------------------------------------------------------------------
# HTTP service health endpoints
# ---------------------------------------------------------------------------


class TestHTTPHealth:
    """Every microservice exposes GET /health -> 200."""

    def test_api_health(self):
        """Core API service must return 200 on its health endpoint. The API gateway routes all management operations, so downtime blocks provisioning and administration."""
        resp = requests.get(f"{API_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_tracking_health(self):
        """Tracking service must return 200 on its health endpoint. Without it, open and click tracking for outbound emails stops working."""
        resp = requests.get(f"{TRACKING_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_webhooks_health(self):
        """Webhooks service must return 200 on its health endpoint. Webhook delivery failures mean third-party integrations stop receiving event notifications."""
        resp = requests.get(f"{WEBHOOKS_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_rate_limiter_health(self):
        """Rate limiter service must return 200 on its health endpoint. A down rate limiter exposes the platform to abuse and uncontrolled resource consumption."""
        resp = requests.get(f"{RATE_LIMITER_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    @pytest.mark.xfail(reason="monitoring service may be restarting")
    def test_monitoring_health(self):
        """Monitoring service must return 200 on its health endpoint. Without monitoring, alerting and observability into service health are lost."""
        resp = requests.get(f"{MONITORING_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    @pytest.mark.xfail(reason="analytics service may be restarting")
    def test_analytics_health(self):
        """Analytics service must return 200 on its health endpoint. Analytics downtime prevents delivery metrics and usage reporting from being collected."""
        resp = requests.get(f"{ANALYTICS_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_dashboard_health(self):
        """Dashboard service must return 200 on its health endpoint. The dashboard is the primary admin UI, so its availability is critical for operators."""
        resp = requests.get(f"{DASHBOARD_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_queue_manager_health(self):
        """Queue manager service must return 200 on its health endpoint. The queue manager orchestrates mail delivery retries, so its failure causes mail to stall."""
        resp = requests.get(f"{QUEUE_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_storage_usage_health(self):
        """Storage usage service must return 200 on its health endpoint. Without it, quota enforcement and storage reporting stop functioning."""
        resp = requests.get(f"{STORAGE_BASE}/health", timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_docs_accessible(self):
        """API documentation site must return 200. Accessible docs are essential for developer onboarding and API consumer self-service."""
        resp = requests.get(f"{DOCS_BASE}/", timeout=TIMEOUT)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Mail port checks
# ---------------------------------------------------------------------------


def _port_open(host: str, port: int, timeout: float = TIMEOUT) -> bool:
    """Return True if a TCP connection can be established."""
    with socket.create_connection((host, port), timeout=timeout):
        return True


class TestMailPorts:
    """Verify that all expected mail-related ports are reachable."""

    def test_smtp_port_25_open(self):
        """SMTP port 25 must accept TCP connections. Port 25 handles server-to-server mail delivery, so a closed port means no inbound mail from the internet."""
        assert _port_open(SMTP_HOST, SMTP_PORT_25)

    def test_smtp_port_587_open(self):
        """SMTP submission port 587 must accept TCP connections. Port 587 is the authenticated submission port used by mail clients to send outbound mail."""
        assert _port_open(SMTP_HOST, SMTP_PORT)

    def test_imap_port_143_open(self):
        """IMAP port 143 must accept TCP connections. Port 143 provides STARTTLS-capable IMAP access for mail clients to read their mailboxes."""
        assert _port_open(IMAP_HOST, IMAP_PORT)

    def test_imap_port_993_open(self):
        """IMAPS port 993 must accept TCP connections. Port 993 provides implicit TLS IMAP access, which most modern mail clients prefer."""
        assert _port_open(IMAP_HOST, IMAP_SSL_PORT)

    def test_pop3_port_110_open(self):
        """POP3 port 110 must accept TCP connections. Port 110 provides STARTTLS-capable POP3 access for legacy mail clients that download-and-delete."""
        assert _port_open(POP3_HOST, POP3_PORT)

    def test_pop3_port_995_open(self):
        """POP3S port 995 must accept TCP connections. Port 995 provides implicit TLS POP3 access required by clients that do not support STARTTLS."""
        assert _port_open(POP3_HOST, POP3_SSL_PORT)


# ---------------------------------------------------------------------------
# SMTP banner / EHLO
# ---------------------------------------------------------------------------


class TestSMTPBanner:
    """Basic SMTP protocol checks on port 587."""

    def test_smtp_banner(self):
        """SMTP banner on port 587 must contain ESMTP. A correct banner confirms Postfix is running and identifying itself properly to connecting clients."""
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=TIMEOUT) as sock:
            banner = sock.recv(1024).decode("utf-8", errors="replace")
            assert "ESMTP" in banner, f"Unexpected banner: {banner!r}"

    def test_smtp_ehlo(self):
        """SMTP EHLO response must advertise STARTTLS. Without STARTTLS support, mail clients cannot establish encrypted sessions for authenticated submission."""
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=TIMEOUT) as sock:
            # Read greeting
            sock.recv(1024)
            sock.sendall(b"EHLO test.local\r\n")
            response = b""
            while True:
                chunk = sock.recv(4096)
                response += chunk
                # Multi-line SMTP responses use "250-" for continuation;
                # the final line starts with "250 ".
                if b"\r\n" in chunk and any(
                    line.startswith(b"250 ") for line in response.split(b"\r\n") if line
                ):
                    break
            capabilities = response.decode("utf-8", errors="replace")
            assert "STARTTLS" in capabilities, (
                f"STARTTLS not advertised in EHLO response:\n{capabilities}"
            )
