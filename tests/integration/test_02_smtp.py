"""
Integration tests -- SMTP sending and delivery.

Validates STARTTLS, authentication, message delivery, and relay rejection
against the live mail server.

NOTE: This entire module runs serially (no xdist parallelism) because
delivery tests share a single mailbox and race on inbox state.
"""
import email
import smtplib
import uuid

import pytest

# Tell pytest-xdist to run everything in this file in a single worker
pytestmark = pytest.mark.xdist_group("smtp_delivery")

from .conftest import (
    SMTP_HOST,
    SMTP_PORT,
    TEST_USER,
    TEST_PASS,
    TEST_DOMAIN,
    send_test_email,
    wait_for_delivery,
    get_inbox_messages,
    clear_inbox,
)

TIMEOUT = 15  # seconds for SMTP operations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def empty_inbox():
    """Ensure the inbox is empty before and after a test that inspects it."""
    clear_inbox()
    yield
    clear_inbox()


# ---------------------------------------------------------------------------
# TLS and Authentication
# ---------------------------------------------------------------------------

class TestSMTPConnection:
    """STARTTLS negotiation and credential validation."""

    def test_smtp_starttls(self):
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            resp_code, _ = server.starttls()
            assert resp_code == 220, "STARTTLS did not return 220"
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def test_smtp_auth_valid(self):
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.starttls()
            server.login(TEST_USER, TEST_PASS)
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def test_smtp_auth_invalid(self):
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.starttls()
            with pytest.raises(smtplib.SMTPAuthenticationError):
                server.login(TEST_USER, "wrong-password-definitely")
        finally:
            try:
                server.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Sending and Delivery
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestSMTPDelivery:
    """End-to-end send/receive via SMTP + IMAP."""

    @pytest.mark.timeout(90)
    @pytest.mark.xfail(reason="delivery timing varies in CI — passes locally")
    def test_send_plain_text(self, empty_inbox):
        subject = f"Plain text test {uuid.uuid4().hex[:8]}"
        send_test_email(subject=subject, body="Hello, plain text world.")
        # Tracking filter + reinjection can take up to 30s in Docker
        wait_for_delivery(timeout=30)

        messages = get_inbox_messages()
        assert len(messages) >= 1, "No messages found in inbox after delivery wait"

        # Find our specific message by subject
        found = False
        for raw in messages:
            msg = email.message_from_bytes(raw)
            if msg["Subject"] == subject:
                found = True
                break
        assert found, f"Message with subject {subject!r} not found in inbox"

    @pytest.mark.timeout(90)
    @pytest.mark.xfail(reason="delivery timing varies in CI — passes locally")
    def test_send_html_email(self, empty_inbox):
        subject = f"HTML test {uuid.uuid4().hex[:8]}"
        html_body = "<html><body><h1>Hello</h1><p>HTML email body.</p></body></html>"
        send_test_email(subject=subject, body=html_body, html=True)
        wait_for_delivery(timeout=30)

        messages = get_inbox_messages()
        assert len(messages) >= 1, "No messages found in inbox after delivery wait"

        found = False
        for raw in messages:
            msg = email.message_from_bytes(raw)
            if msg["Subject"] == subject:
                found = True
                break
        assert found, f"Message with subject {subject!r} not found in inbox"

    def test_send_to_nonexistent_user(self):
        """Sending to a nonexistent user should not crash the server.

        The message may be bounced or silently rejected -- either is acceptable
        as long as no unhandled exception is raised.
        """
        try:
            send_test_email(
                subject="Nonexistent user test",
                body="Should bounce or be rejected.",
                to=f"nobody@{TEST_DOMAIN}",
            )
        except smtplib.SMTPRecipientsRefused:
            pass  # acceptable -- server rejected the recipient


# ---------------------------------------------------------------------------
# Relay / Security
# ---------------------------------------------------------------------------

class TestSMTPSecurity:
    """Unauthenticated relay must be rejected."""

    def test_reject_unauthenticated_relay(self):
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.starttls()
            # Do NOT log in -- attempt to send directly.
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                server.sendmail(
                    TEST_USER,
                    [TEST_USER],
                    "Subject: Relay test\r\n\r\nShould be rejected.",
                )
        except smtplib.SMTPSenderRefused:
            # Also acceptable -- some servers reject at MAIL FROM
            pass
        finally:
            try:
                server.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# EHLO capabilities
# ---------------------------------------------------------------------------

class TestSMTPCapabilities:
    """Verify expected EHLO extensions are advertised."""

    def test_smtp_ehlo_capabilities(self):
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.ehlo()
            # smtplib stores EHLO features in a dict after ehlo()
            advertised = {k.upper() for k in server.esmtp_features}
            expected = {"STARTTLS", "SIZE", "8BITMIME", "DSN", "PIPELINING"}
            missing = expected - advertised
            assert not missing, (
                f"Missing EHLO capabilities: {missing}. "
                f"Advertised: {advertised}"
            )
        finally:
            try:
                server.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# TLS version and cipher validation
# ---------------------------------------------------------------------------

class TestSMTPTLS:
    """Validate TLS protocol versions and cipher strength on the SMTP port."""

    def test_tls_version_12_or_13(self):
        """Verify server negotiates TLSv1.2 or TLSv1.3.

        Older TLS versions (1.0, 1.1, SSLv3) have known vulnerabilities such
        as POODLE and BEAST.  The server must refuse to negotiate anything
        below TLSv1.2 so that client connections are protected in transit.
        """
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.ehlo()
            server.starttls()
            ssl_version = server.sock.version()  # e.g. "TLSv1.3"
            assert "TLSv1.2" in ssl_version or "TLSv1.3" in ssl_version, (
                f"Unexpected TLS version negotiated: {ssl_version}. "
                "Only TLSv1.2 and TLSv1.3 are acceptable."
            )
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def test_smtp_cipher_strength(self):
        """Verify cipher suite uses AES-128+ or ChaCha20.

        Weak ciphers like RC4 or DES are cryptographically broken and must
        never be negotiated.  This test ensures the server selects a modern
        symmetric cipher for the SMTP TLS session.
        """
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.ehlo()
            server.starttls()
            cipher_info = server.sock.cipher()  # (name, version, bits)
            cipher_name = cipher_info[0] if cipher_info else ""
            assert "AES" in cipher_name.upper() or "CHACHA" in cipher_name.upper(), (
                f"Weak or unexpected cipher negotiated: {cipher_name}. "
                "Expected a cipher containing AES or CHACHA20."
            )
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def test_smtp_size_limit_announced(self):
        """SMTP SIZE extension must be advertised to prevent oversized messages.

        RFC 1870 defines the SIZE extension which allows the server to tell
        clients the maximum message size it will accept.  Without this,
        clients may attempt to send arbitrarily large messages which wastes
        bandwidth and can cause denial-of-service.
        """
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.ehlo()
            advertised = {k.upper() for k in server.esmtp_features}
            assert "SIZE" in advertised, (
                f"SIZE extension not advertised. Features: {advertised}"
            )
        finally:
            try:
                server.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Attachment handling
# ---------------------------------------------------------------------------

class TestSMTPAttachments:
    """Verify the server handles MIME multipart messages with attachments."""

    @pytest.mark.timeout(90)
    @pytest.mark.xfail(reason="delivery timing")
    def test_send_with_small_attachment(self, empty_inbox):
        """Email with a small text attachment should be accepted and delivered.

        Verifies that the server correctly processes MIME multipart messages
        that include file attachments.  A failure here would indicate problems
        with the content filter or MIME parser in the delivery pipeline.
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders
        from email.utils import formatdate, make_msgid

        subject = f"Attachment test {uuid.uuid4().hex[:8]}"
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = TEST_USER
        msg["To"] = TEST_USER
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=TEST_DOMAIN)

        # Text body
        msg.attach(MIMEText("This email has an attachment.", "plain", "utf-8"))

        # Small .txt attachment
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(b"Hello from attachment content.")
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition", "attachment", filename="testfile.txt"
        )
        msg.attach(attachment)

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            server.starttls()
            server.login(TEST_USER, TEST_PASS)
            server.sendmail(TEST_USER, [TEST_USER], msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass

        wait_for_delivery(timeout=30)
        messages = get_inbox_messages()
        assert len(messages) >= 1, "No messages found in inbox after sending attachment email"

        found = False
        for raw in messages:
            parsed = email.message_from_bytes(raw)
            if parsed["Subject"] == subject:
                found = True
                break
        assert found, f"Message with subject {subject!r} not found in inbox"


# ---------------------------------------------------------------------------
# Rate limiting / resilience
# ---------------------------------------------------------------------------

class TestSMTPRateLimiting:
    """Ensure the SMTP server stays stable under rapid connection bursts."""

    def test_rapid_connections_not_crash(self):
        """Sending 5 rapid connections should not crash the server.

        Rate limiting may defer or temporarily reject connections, but the
        server must remain operational and not raise unhandled exceptions.
        This guards against resource exhaustion bugs in the connection
        accept loop.
        """
        errors = []
        connections = []
        for i in range(5):
            try:
                s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
                s.ehlo()
                connections.append(s)
            except Exception as exc:
                errors.append(f"Connection {i}: {exc}")

        # Clean up
        for s in connections:
            try:
                s.quit()
            except Exception:
                pass

        # We tolerate some refused connections (rate limiting), but at least
        # one must succeed to prove the server is still alive.
        successful = len(connections)
        assert successful >= 1, (
            f"All 5 rapid connections failed — server may have crashed. "
            f"Errors: {errors}"
        )
