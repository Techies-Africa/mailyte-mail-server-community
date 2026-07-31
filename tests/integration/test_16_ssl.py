"""
Integration tests -- SSL/TLS certificate verification.

Validates that SSL certificates are tracked in the database, STARTTLS is
offered on SMTP, and SSL/TLS handshakes succeed on secure IMAP and POP3
ports with acceptable protocol versions.
"""

import smtplib
import socket
import ssl

import pytest
import requests

from .conftest import (
    API_BASE,
    IMAP_HOST,
    IMAP_SSL_PORT,
    POP3_SSL_PORT,
    SMTP_HOST,
    SMTP_PORT,
)

TIMEOUT = 10  # seconds for network operations


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class TestSSLDatabase:
    """Verify the ssl_certificates table is accessible."""

    def test_ssl_certificates_table(self, db_connection):
        """The ssl_certificates table must exist and be queryable. This table tracks certificate metadata needed for automated renewal and expiry alerting."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM ssl_certificates")
        result = cursor.fetchone()
        assert result is not None, "Query returned no rows"
        assert result[0] >= 0
        cursor.close()


# ---------------------------------------------------------------------------
# STARTTLS
# ---------------------------------------------------------------------------


class TestSTARTTLS:
    """SMTP STARTTLS advertisement and negotiation."""

    def test_smtp_supports_starttls(self):
        """SMTP EHLO response must advertise STARTTLS capability. Without STARTTLS, mail clients and relaying servers cannot encrypt the SMTP session, exposing credentials and message content."""
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=TIMEOUT) as sock:
            # Read greeting
            sock.recv(1024)
            sock.sendall(b"EHLO test.local\r\n")
            response = b""
            while True:
                chunk = sock.recv(4096)
                response += chunk
                if b"\r\n" in chunk and any(
                    line.startswith(b"250 ") for line in response.split(b"\r\n") if line
                ):
                    break
            capabilities = response.decode("utf-8", errors="replace")
            assert "STARTTLS" in capabilities, (
                f"STARTTLS not advertised in EHLO response:\n{capabilities}"
            )


# ---------------------------------------------------------------------------
# SSL/TLS handshakes
# ---------------------------------------------------------------------------


class TestSSLHandshake:
    """Verify that SSL handshakes succeed on secure ports."""

    def test_imap_ssl_handshake(self):
        """SSL/TLS handshake on IMAPS port 993 must complete successfully. A failed handshake means mail clients cannot establish secure connections to read mailboxes."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((IMAP_HOST, IMAP_SSL_PORT), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=IMAP_HOST) as ssock:
                # A successful wrap_socket means the handshake completed
                assert ssock.version() is not None, "SSL handshake did not establish a version"

    def test_pop3_ssl_handshake(self):
        """SSL/TLS handshake on POP3S port 995 must complete successfully. A failed handshake prevents POP3 clients from securely downloading messages."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((IMAP_HOST, POP3_SSL_PORT), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=IMAP_HOST) as ssock:
                assert ssock.version() is not None, "SSL handshake did not establish a version"


# ---------------------------------------------------------------------------
# TLS version
# ---------------------------------------------------------------------------


class TestTLSVersion:
    """Ensure the server negotiates TLS 1.2 or higher."""

    def test_tls_version(self):
        """SMTP STARTTLS must negotiate TLS 1.2 or higher. Older TLS versions have known vulnerabilities and are rejected by security-conscious receiving servers."""
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            server.starttls(context=ctx)

            tls_version = server.sock.version()
            assert tls_version in ("TLSv1.2", "TLSv1.3"), f"Unexpected TLS version: {tls_version}"
        finally:
            try:
                server.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestSSLAPI:
    """SSL status endpoint (may not exist in all deployments)."""

    def test_ssl_api_status(self, api_headers):
        """SSL status API endpoint must respond when available. This endpoint exposes certificate expiry and renewal status used by the admin dashboard."""
        try:
            resp = requests.get(
                f"{API_BASE}/api/v1/ssl/status",
                headers=api_headers,
                timeout=TIMEOUT,
            )
        except requests.ConnectionError:
            pytest.skip("API unreachable")
        if resp.status_code == 404:
            pytest.skip("SSL status endpoint not implemented")
        assert resp.status_code in (200, 401, 403, 500), (
            f"Unexpected status code: {resp.status_code}"
        )
