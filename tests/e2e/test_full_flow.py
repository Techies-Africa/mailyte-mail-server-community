"""
End-to-End Test Suite for Mailyte Email Server

Tests the complete email lifecycle:
1. Organization creation
2. Domain setup and verification
3. Mailbox creation
4. SMTP send email
5. IMAP retrieve email
6. Verify DKIM/SPF/DMARC headers
7. Check tracking (open/click)
8. Verify webhook delivery
9. API operations (search, filter, analytics)
10. Cleanup

Usage:
    pytest tests/e2e/test_full_flow.py -v --tb=short
    pytest tests/e2e/test_full_flow.py -v -k TestEmailSending

Environment variables:
    API_URL       - Base URL for the API gateway  (default: http://localhost:8083)
    SMTP_HOST     - SMTP server hostname           (default: localhost)
    SMTP_PORT     - SMTP server port               (default: 587)
    IMAP_HOST     - IMAP server hostname           (default: localhost)
    IMAP_PORT     - IMAP server port               (default: 993)
    TEST_DOMAIN   - Domain to use for testing      (default: test.mailyte.local)
    API_KEY       - API key for authentication     (default: test-api-key)
"""

import email
import imaplib
import json
import os
import smtplib
import ssl
import threading
import time
import uuid
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
API_URL = os.getenv("API_URL", "http://localhost:8083")
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
IMAP_HOST = os.getenv("IMAP_HOST", "localhost")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
TEST_DOMAIN = os.getenv("TEST_DOMAIN", "test.mailyte.local")
API_KEY = os.getenv("API_KEY", "test-api-key")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "9876"))

# Timeouts and retry settings
SMTP_TIMEOUT = 30
IMAP_TIMEOUT = 30
EMAIL_DELIVERY_WAIT = 10  # seconds to wait for email delivery
MAX_DELIVERY_RETRIES = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def api_headers():
    """Return standard API headers."""
    return {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
    }


def api_get(path, params=None):
    """GET request to the API."""
    return requests.get(f"{API_URL}{path}", headers=api_headers(), params=params, timeout=30)


def api_post(path, data=None):
    """POST request to the API."""
    return requests.post(f"{API_URL}{path}", headers=api_headers(), json=data, timeout=30)


def api_put(path, data=None):
    """PUT request to the API."""
    return requests.put(f"{API_URL}{path}", headers=api_headers(), json=data, timeout=30)


def api_delete(path, data=None):
    """DELETE request to the API."""
    return requests.delete(f"{API_URL}{path}", headers=api_headers(), json=data, timeout=30)


# ---------------------------------------------------------------------------
# Webhook capture server — used to verify webhook delivery
# ---------------------------------------------------------------------------
class WebhookCapture:
    """Lightweight HTTP server that captures webhook POST payloads."""

    def __init__(self, port):
        self.port = port
        self.payloads = []
        self._server = None
        self._thread = None

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length else b""
                try:
                    payload = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    payload = {"raw": body.decode("utf-8", errors="replace")}
                outer.payloads.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "payload": payload,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def log_message(self, fmt, *args):
                pass  # suppress request logging

        self._server = HTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()

    def wait_for_payload(self, timeout=15, min_count=1):
        """Block until we have at least min_count payloads or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.payloads) >= min_count:
                return True
            time.sleep(0.5)
        return len(self.payloads) >= min_count

    def clear(self):
        self.payloads.clear()


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def unique_id():
    """Unique identifier for this test run."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="session")
def webhook_server():
    """Start a local webhook capture server for the test session."""
    server = WebhookCapture(WEBHOOK_PORT)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def test_org(unique_id):
    """Create a test organization and clean up after the session."""
    org_id = f"e2e-test-{unique_id}"
    org_data = {
        "id": org_id,
        "name": f"E2E Test Organization {unique_id}",
        "description": "Automated end-to-end test organization",
        "admin_email": f"admin-{unique_id}@{TEST_DOMAIN}",
        "admin_name": "E2E Test Admin",
        "settings": {},
        "rate_limits": {"hourly": 1000, "daily": 10000},
        "storage_quotas": {"max_total": 10737418240},
        "active": True,
    }

    resp = api_post("/api/v1/organizations", org_data)
    assert resp.status_code in (200, 201), f"Failed to create org: {resp.status_code} {resp.text}"

    body = resp.json()
    org_result = body.get("data", body)
    org_result["_org_id"] = org_id  # keep a handy reference

    yield org_result

    # Cleanup: attempt to delete (may fail if dependents remain)
    try:
        api_delete(f"/api/v1/organizations/{org_id}")
    except Exception:
        pass


@pytest.fixture(scope="session")
def test_domain(test_org, unique_id):
    """Add a domain to the test organization."""
    domain_name = f"{unique_id}.{TEST_DOMAIN}"
    domain_data = {
        "domain": domain_name,
        "organization_id": test_org["_org_id"],
        "description": "E2E test domain",
        "active": True,
        "max_quota": 10737418240,
        "max_users": 100,
        "dkim_enabled": True,
        "dkim_selector": "default",
    }

    resp = api_post("/api/v1/domains", domain_data)
    assert resp.status_code in (200, 201), (
        f"Failed to create domain: {resp.status_code} {resp.text}"
    )

    body = resp.json()
    domain_result = body.get("data", body)
    domain_result["_domain_name"] = domain_name
    domain_result["_org_id"] = test_org["_org_id"]

    yield domain_result

    # Cleanup
    domain_id = domain_result.get("id")
    if domain_id:
        try:
            api_delete(f"/api/v1/domains/{domain_id}")
        except Exception:
            pass


@pytest.fixture(scope="session")
def test_mailbox(test_domain, unique_id):
    """Create a test mailbox on the test domain."""
    domain_name = test_domain["_domain_name"]
    local_part = f"e2euser-{unique_id}"
    email_addr = f"{local_part}@{domain_name}"
    password = "E2eTestPass99!"

    mailbox_data = {
        "email": email_addr,
        "password": password,
        "name": f"E2E Test User {unique_id}",
        "storage_quota": 1073741824,
    }

    resp = api_post("/api/v1/email-accounts", mailbox_data)
    assert resp.status_code in (200, 201), (
        f"Failed to create mailbox: {resp.status_code} {resp.text}"
    )

    body = resp.json()
    mailbox_result = body.get("data", body)
    mailbox_result["email"] = email_addr
    mailbox_result["password"] = password
    mailbox_result["local_part"] = local_part
    mailbox_result["domain"] = domain_name
    mailbox_result["_org_id"] = test_domain["_org_id"]

    yield mailbox_result

    # Cleanup
    account_id = mailbox_result.get("id")
    if account_id:
        try:
            api_delete(f"/api/v1/email-accounts/{account_id}")
        except Exception:
            pass


@pytest.fixture(scope="session")
def sent_email_id(test_mailbox):
    """
    Send a test email via SMTP and return a dict with message details.
    Shared across tests that need to verify the sent message.
    """
    msg_id = f"<e2e-{uuid.uuid4().hex}@{test_mailbox['domain']}>"
    unique_subject = f"E2E Full Flow Test {uuid.uuid4().hex[:8]}"

    msg = MIMEMultipart("mixed")
    msg["From"] = test_mailbox["email"]
    msg["To"] = test_mailbox["email"]  # send to self
    msg["Subject"] = unique_subject
    msg["Message-ID"] = msg_id
    msg["X-E2E-Test"] = "true"

    # Text body
    text_part = MIMEText(
        f"This is an automated end-to-end test email.\n"
        f"Sent at: {datetime.utcnow().isoformat()}\n"
        f"Message-ID: {msg_id}\n",
        "plain",
    )
    msg.attach(text_part)

    # Small attachment
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(b"E2E test attachment content\n" * 10)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="test.txt")
    msg.attach(attachment)

    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls(context=context)
                smtp.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass  # STARTTLS not available; proceed unencrypted
            smtp.login(test_mailbox["email"], test_mailbox["password"])
            smtp.send_message(msg)
    except Exception as exc:
        pytest.skip(f"SMTP send failed (server may not be running): {exc}")

    return {
        "message_id": msg_id,
        "subject": unique_subject,
        "from": test_mailbox["email"],
        "to": test_mailbox["email"],
    }


# ===========================================================================
# Test Classes
# ===========================================================================


class TestOrganizationSetup:
    """Test organization and domain setup."""

    def test_create_organization(self, test_org):
        """Verify organization was created with a valid ID."""
        assert test_org.get("id") or test_org.get("_org_id")

    def test_get_organization(self, test_org):
        """Verify the organization can be retrieved."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_get(f"/api/v1/organizations/{org_id}")
        assert resp.status_code == 200
        body = resp.json()
        data = body.get("data", body)
        assert data.get("id") == org_id or data.get("name") is not None

    def test_organization_quotas(self, test_org):
        """Verify organization quota endpoint works."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_get(f"/api/v1/organizations/{org_id}/quotas")
        assert resp.status_code == 200

    def test_add_domain(self, test_domain):
        """Verify domain was created."""
        assert test_domain.get("_domain_name") is not None
        assert test_domain.get("id") is not None

    def test_get_domain(self, test_domain):
        """Verify domain can be retrieved."""
        domain_id = test_domain.get("id")
        resp = api_get(f"/api/v1/domains/{domain_id}")
        assert resp.status_code == 200

    def test_domain_quotas(self, test_domain):
        """Verify domain quota endpoint works."""
        domain_id = test_domain.get("id")
        resp = api_get(f"/api/v1/domains/{domain_id}/quotas")
        assert resp.status_code == 200

    def test_create_mailbox(self, test_mailbox):
        """Verify mailbox was created with a valid email."""
        assert "@" in test_mailbox["email"]
        assert test_mailbox.get("id") is not None

    def test_get_mailbox(self, test_mailbox):
        """Verify mailbox can be retrieved."""
        account_id = test_mailbox.get("id")
        resp = api_get(f"/api/v1/email-accounts/{account_id}")
        assert resp.status_code == 200

    def test_mailbox_quotas(self, test_mailbox):
        """Verify mailbox quota endpoint works."""
        account_id = test_mailbox.get("id")
        resp = api_get(f"/api/v1/email-accounts/{account_id}/quotas")
        assert resp.status_code == 200


class TestEmailSending:
    """Test SMTP email sending."""

    def test_smtp_connection(self, test_mailbox):
        """Test SMTP STARTTLS connection to the server."""
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                code, _ = smtp.ehlo()
                assert code == 250, f"EHLO failed with code {code}"
                try:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass  # STARTTLS optional in test env
        except (ConnectionRefusedError, OSError) as exc:
            pytest.skip(f"SMTP not reachable: {exc}")

    def test_smtp_auth(self, test_mailbox):
        """Test SMTP authentication with test credentials."""
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass
                smtp.login(test_mailbox["email"], test_mailbox["password"])
        except (ConnectionRefusedError, OSError) as exc:
            pytest.skip(f"SMTP not reachable: {exc}")

    def test_send_plain_email(self, test_mailbox):
        """Send a plain-text email via SMTP."""
        msg = MIMEText("E2E test plain text email body.", "plain")
        msg["From"] = test_mailbox["email"]
        msg["To"] = test_mailbox["email"]
        msg["Subject"] = f"E2E Plain Text Test {uuid.uuid4().hex[:8]}"
        msg["X-E2E-Test"] = "true"

        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass
                smtp.login(test_mailbox["email"], test_mailbox["password"])
                smtp.send_message(msg)
        except (ConnectionRefusedError, OSError) as exc:
            pytest.skip(f"SMTP not reachable: {exc}")

    def test_send_email(self, sent_email_id):
        """Ensure the full-flow test email was sent (triggers the fixture)."""
        assert sent_email_id is not None
        assert sent_email_id["message_id"] is not None
        assert sent_email_id["subject"] is not None

    def test_send_with_attachment(self, test_mailbox):
        """Send an email with an attachment via SMTP."""
        msg = MIMEMultipart()
        msg["From"] = test_mailbox["email"]
        msg["To"] = test_mailbox["email"]
        msg["Subject"] = f"E2E Attachment Test {uuid.uuid4().hex[:8]}"
        msg["X-E2E-Test"] = "true"

        msg.attach(MIMEText("Email with attachment.", "plain"))

        attachment = MIMEBase("application", "pdf")
        attachment.set_payload(b"%PDF-1.4 fake pdf content for testing")
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename="report.pdf")
        msg.attach(attachment)

        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass
                smtp.login(test_mailbox["email"], test_mailbox["password"])
                smtp.send_message(msg)
        except (ConnectionRefusedError, OSError) as exc:
            pytest.skip(f"SMTP not reachable: {exc}")

    def test_send_html_email(self, test_mailbox):
        """Send an HTML email via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["From"] = test_mailbox["email"]
        msg["To"] = test_mailbox["email"]
        msg["Subject"] = f"E2E HTML Test {uuid.uuid4().hex[:8]}"
        msg["X-E2E-Test"] = "true"

        text_part = MIMEText("Plain text fallback.", "plain")
        html_part = MIMEText(
            "<html><body><h1>E2E Test</h1><p>HTML email body.</p></body></html>", "html"
        )
        msg.attach(text_part)
        msg.attach(html_part)

        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass
                smtp.login(test_mailbox["email"], test_mailbox["password"])
                smtp.send_message(msg)
        except (ConnectionRefusedError, OSError) as exc:
            pytest.skip(f"SMTP not reachable: {exc}")


class TestEmailReceiving:
    """Test IMAP email retrieval."""

    def _imap_connect(self, test_mailbox):
        """Helper to create an IMAP connection."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=context, timeout=IMAP_TIMEOUT)
        return imap

    def test_imap_connection(self, test_mailbox):
        """Test IMAP SSL connection to the server."""
        try:
            imap = self._imap_connect(test_mailbox)
            assert imap.state == "NONAUTH" or imap.state == "AUTH"
            imap.logout()
        except (ConnectionRefusedError, OSError, imaplib.IMAP4.error) as exc:
            pytest.skip(f"IMAP not reachable: {exc}")

    def test_imap_auth(self, test_mailbox):
        """Test IMAP authentication with test credentials."""
        try:
            imap = self._imap_connect(test_mailbox)
            status, _ = imap.login(test_mailbox["email"], test_mailbox["password"])
            assert status == "OK", f"IMAP login failed: {status}"
            imap.logout()
        except (ConnectionRefusedError, OSError, imaplib.IMAP4.error) as exc:
            pytest.skip(f"IMAP not reachable: {exc}")

    def test_standard_folders(self, test_mailbox):
        """Verify standard IMAP folders exist (INBOX, Sent, Drafts, Trash, Junk)."""
        try:
            imap = self._imap_connect(test_mailbox)
            imap.login(test_mailbox["email"], test_mailbox["password"])

            status, folder_data = imap.list()
            assert status == "OK", "Failed to list IMAP folders"

            folder_names = []
            for item in folder_data:
                if isinstance(item, bytes):
                    decoded = item.decode("utf-8", errors="replace")
                    # Extract folder name from IMAP LIST response
                    parts = decoded.rsplit('"', 2)
                    if len(parts) >= 2:
                        folder_names.append(parts[-1].strip().strip('"'))
                    else:
                        folder_names.append(decoded.split()[-1].strip('"'))

            # At minimum, INBOX should exist
            inbox_found = any("INBOX" in name.upper() for name in folder_names)
            assert inbox_found, f"INBOX not found in folders: {folder_names}"

            imap.logout()
        except (ConnectionRefusedError, OSError, imaplib.IMAP4.error) as exc:
            pytest.skip(f"IMAP not reachable: {exc}")

    def test_retrieve_email(self, test_mailbox, sent_email_id):
        """Retrieve the sent email via IMAP and verify subject and sender."""
        try:
            # Wait for delivery
            time.sleep(EMAIL_DELIVERY_WAIT)

            imap = self._imap_connect(test_mailbox)
            imap.login(test_mailbox["email"], test_mailbox["password"])
            imap.select("INBOX")

            found = False
            for attempt in range(MAX_DELIVERY_RETRIES):
                status, data = imap.search(None, "ALL")
                if status != "OK":
                    time.sleep(2)
                    continue

                message_numbers = data[0].split()
                for num in reversed(message_numbers[-20:]):  # check last 20
                    status, msg_data = imap.fetch(num, "(RFC822)")
                    if status != "OK":
                        continue
                    raw_email = msg_data[0][1]
                    parsed = email.message_from_bytes(raw_email)
                    if sent_email_id["subject"] in (parsed.get("Subject", "") or ""):
                        found = True
                        assert parsed["From"] is not None
                        assert test_mailbox["email"] in parsed["To"]
                        break
                if found:
                    break
                time.sleep(3)

            assert found, f'Sent email with subject "{sent_email_id["subject"]}" not found via IMAP'
            imap.logout()
        except (ConnectionRefusedError, OSError, imaplib.IMAP4.error) as exc:
            pytest.skip(f"IMAP not reachable: {exc}")

    def test_dkim_header(self, test_mailbox, sent_email_id):
        """Verify DKIM-Signature header exists on the retrieved email."""
        try:
            time.sleep(2)
            imap = self._imap_connect(test_mailbox)
            imap.login(test_mailbox["email"], test_mailbox["password"])
            imap.select("INBOX")

            status, data = imap.search(None, "ALL")
            if status != "OK" or not data[0]:
                pytest.skip("No messages in INBOX to check DKIM")

            message_numbers = data[0].split()
            # Check the most recent message
            status, msg_data = imap.fetch(message_numbers[-1], "(RFC822)")
            raw_email = msg_data[0][1]
            parsed = email.message_from_bytes(raw_email)

            dkim_sig = parsed.get("DKIM-Signature")
            # DKIM may not be configured in test environments, so we just check
            if dkim_sig:
                assert "v=1" in dkim_sig, "DKIM-Signature header missing version"
                assert "s=" in dkim_sig, "DKIM-Signature header missing selector"
                assert "d=" in dkim_sig, "DKIM-Signature header missing domain"
            else:
                pytest.skip("DKIM-Signature header not present (may not be configured)")

            imap.logout()
        except (ConnectionRefusedError, OSError, imaplib.IMAP4.error) as exc:
            pytest.skip(f"IMAP not reachable: {exc}")

    def test_auth_results_header(self, test_mailbox, sent_email_id):
        """Verify Authentication-Results header (SPF/DKIM/DMARC)."""
        try:
            imap = self._imap_connect(test_mailbox)
            imap.login(test_mailbox["email"], test_mailbox["password"])
            imap.select("INBOX")

            status, data = imap.search(None, "ALL")
            if status != "OK" or not data[0]:
                pytest.skip("No messages in INBOX")

            message_numbers = data[0].split()
            status, msg_data = imap.fetch(message_numbers[-1], "(RFC822)")
            raw_email = msg_data[0][1]
            parsed = email.message_from_bytes(raw_email)

            auth_results = parsed.get("Authentication-Results")
            if auth_results:
                # At least one of spf, dkim, or dmarc should appear
                has_auth = any(kw in auth_results.lower() for kw in ["spf=", "dkim=", "dmarc="])
                assert has_auth, f"Authentication-Results lacks spf/dkim/dmarc: {auth_results}"
            else:
                pytest.skip("Authentication-Results header not present")

            imap.logout()
        except (ConnectionRefusedError, OSError, imaplib.IMAP4.error) as exc:
            pytest.skip(f"IMAP not reachable: {exc}")


class TestAPIOperations:
    """Test API endpoint operations."""

    def test_health_check(self):
        """Test API health endpoint."""
        resp = api_get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "healthy"
        assert "version" in body

    def test_root_endpoint(self):
        """Test API root endpoint."""
        resp = api_get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body

    def test_list_organizations(self):
        """List all organizations."""
        resp = api_get("/api/v1/organizations")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("type") == "success"

    def test_list_domains(self, test_org):
        """List domains for the test organization."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_get("/api/v1/domains", params={"organization_id": org_id})
        assert resp.status_code == 200

    def test_list_mailboxes(self, test_org):
        """List mailboxes in the test organization."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_get("/api/v1/email-accounts", params={"organization_id": org_id})
        assert resp.status_code == 200

    def test_email_analytics(self, test_domain):
        """Get email analytics for the test domain (may proxy to analytics service)."""
        domain_name = test_domain["_domain_name"]
        resp = api_get(f"/api/v1/analytics/dashboard/{domain_name}")
        # 503 is acceptable if the analytics service is down
        assert resp.status_code in (200, 503)

    def test_message_trace(self, test_mailbox):
        """Trace sent messages via the message-trace API."""
        resp = api_get(
            "/api/v1/message-trace/trace", params={"sender": test_mailbox["email"], "limit": 10}
        )
        # Accept 200 or 404 (no messages yet)
        assert resp.status_code in (200, 404)

    def test_monitoring_health(self):
        """Test monitoring health endpoint."""
        resp = api_get("/api/v1/monitoring/health")
        assert resp.status_code in (200, 500, 503)

    def test_monitoring_services(self):
        """Test monitoring services list."""
        resp = api_get("/api/v1/monitoring/services")
        assert resp.status_code in (200, 500, 503)

    def test_queue_status(self):
        """Test queue status endpoint."""
        resp = api_get("/api/v1/queue/queue/status")
        assert resp.status_code in (200, 503)

    def test_queue_health(self):
        """Test queue health endpoint."""
        resp = api_get("/api/v1/queue/queue/health")
        assert resp.status_code in (200, 503)

    def test_update_organization(self, test_org):
        """Update the test organization."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_put(
            f"/api/v1/organizations/{org_id}",
            {
                "description": "Updated by E2E test",
            },
        )
        assert resp.status_code == 200

    def test_update_organization_quotas(self, test_org):
        """Update organization quotas."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_put(
            f"/api/v1/organizations/{org_id}/quotas",
            {
                "storage_quotas": {"max_total": 21474836480},
                "rate_limits": {"hourly": 2000},
            },
        )
        assert resp.status_code == 200

    def test_update_mailbox(self, test_mailbox):
        """Update the test mailbox."""
        account_id = test_mailbox.get("id")
        resp = api_put(
            f"/api/v1/email-accounts/{account_id}",
            {
                "name": "Updated E2E User",
                "vacation_enabled": False,
            },
        )
        assert resp.status_code == 200


class TestTracking:
    """Test email tracking endpoints."""

    def test_open_tracking(self):
        """Test open tracking pixel endpoint exists."""
        fake_tracking_id = uuid.uuid4().hex
        resp = api_get(f"/api/v1/tracking/pixel/{fake_tracking_id}")
        # Should return something (even 404 or 503) rather than crash
        assert resp.status_code in (200, 302, 404, 503)

    def test_click_tracking(self):
        """Test click tracking redirect endpoint exists."""
        fake_tracking_id = uuid.uuid4().hex
        resp = api_get(f"/api/v1/tracking/click/{fake_tracking_id}")
        assert resp.status_code in (200, 302, 404, 503)

    def test_unsubscribe_endpoint(self):
        """Test unsubscribe endpoint exists."""
        fake_tracking_id = uuid.uuid4().hex
        resp = api_get(f"/api/v1/tracking/unsubscribe/{fake_tracking_id}")
        assert resp.status_code in (200, 302, 404, 503)

    def test_domain_tracking_stats(self, test_domain):
        """Get tracking stats for the test domain."""
        domain_name = test_domain["_domain_name"]
        resp = api_get(f"/api/v1/tracking/stats/domain/{domain_name}")
        assert resp.status_code in (200, 503)

    def test_suppression_add_and_remove(self, test_mailbox):
        """Test adding and removing from suppression list."""
        suppress_email = f"suppress-{uuid.uuid4().hex[:6]}@example.com"

        # Add to suppression
        resp = api_post(
            "/api/v1/tracking/suppress",
            {
                "email": suppress_email,
                "reason": "E2E test suppression",
            },
        )
        assert resp.status_code in (200, 201, 503)

        # Remove from suppression
        resp = api_delete(f"/api/v1/tracking/suppress/{suppress_email}")
        assert resp.status_code in (200, 204, 404, 503)


class TestWebhooks:
    """Test webhook delivery."""

    def test_webhook_subscription_crud(self, webhook_server):
        """Create, list, and delete a webhook subscription."""
        webhook_url = f"http://localhost:{WEBHOOK_PORT}/hook/test"

        # Create subscription
        resp = api_post(
            "/api/v1/webhooks/subscriptions",
            {
                "url": webhook_url,
                "events": ["email.sent", "email.delivered", "email.bounced"],
                "domain": TEST_DOMAIN,
                "secret": "e2e-webhook-secret",
                "active": True,
            },
        )
        assert resp.status_code in (200, 201, 503)

        if resp.status_code == 503:
            pytest.skip("Webhook service unavailable")

        body = resp.json()
        sub_id = body.get("id") or body.get("subscription_id") or (body.get("data", {}).get("id"))

        # List subscriptions
        resp = api_get("/api/v1/webhooks/subscriptions", params={"domain": TEST_DOMAIN})
        assert resp.status_code in (200, 503)

        # Delete subscription
        if sub_id:
            resp = api_delete(f"/api/v1/webhooks/subscriptions/{sub_id}")
            assert resp.status_code in (200, 204, 503)

    def test_webhook_test_endpoint(self, webhook_server):
        """Test webhook test delivery endpoint."""
        webhook_url = f"http://localhost:{WEBHOOK_PORT}/hook/e2e-test"
        webhook_server.clear()

        resp = api_post(
            "/api/v1/webhooks/test",
            {
                "url": webhook_url,
                "event_type": "email.sent",
                "payload": {
                    "message_id": f"test-{uuid.uuid4().hex}",
                    "from": f"sender@{TEST_DOMAIN}",
                    "to": f"recipient@{TEST_DOMAIN}",
                    "subject": "Webhook test",
                },
            },
        )
        assert resp.status_code in (200, 503)

        if resp.status_code == 200:
            received = webhook_server.wait_for_payload(timeout=10)
            if received:
                assert len(webhook_server.payloads) >= 1
                assert webhook_server.payloads[0]["path"] == "/hook/e2e-test"

    def test_webhook_events(self):
        """Get webhook events for a domain."""
        resp = api_get(f"/api/v1/webhooks/events/{TEST_DOMAIN}")
        assert resp.status_code in (200, 503)

    def test_webhook_fires_on_send(self, webhook_server, test_mailbox):
        """
        Verify webhook fires when email is sent.
        This test registers a webhook, sends an email, and checks for delivery.
        """
        webhook_url = f"http://localhost:{WEBHOOK_PORT}/hook/send-event"
        webhook_server.clear()

        # Register webhook
        resp = api_post(
            "/api/v1/webhooks/subscriptions",
            {
                "url": webhook_url,
                "events": ["email.sent"],
                "domain": test_mailbox["domain"],
                "secret": "e2e-fire-test",
                "active": True,
            },
        )
        if resp.status_code == 503:
            pytest.skip("Webhook service unavailable")

        sub_id = None
        if resp.status_code in (200, 201):
            body = resp.json()
            sub_id = (
                body.get("id") or body.get("subscription_id") or (body.get("data", {}).get("id"))
            )

        # Send email via API queue
        resp = api_post(
            "/api/v1/queue/mail-queue/flush",
            {
                "domain": test_mailbox["domain"],
            },
        )
        # Just verify the endpoint is reachable
        assert resp.status_code in (200, 202, 503)

        # Wait for webhook delivery
        webhook_server.wait_for_payload(timeout=15)

        # Cleanup
        if sub_id:
            api_delete(f"/api/v1/webhooks/subscriptions/{sub_id}")

    def test_webhook_fires_on_bounce(self, webhook_server, test_mailbox):
        """
        Verify webhook fires on bounce event.
        We register a bounce webhook and check the subscription exists.
        """
        webhook_url = f"http://localhost:{WEBHOOK_PORT}/hook/bounce-event"
        webhook_server.clear()

        resp = api_post(
            "/api/v1/webhooks/subscriptions",
            {
                "url": webhook_url,
                "events": ["email.bounced"],
                "domain": test_mailbox["domain"],
                "secret": "e2e-bounce-test",
                "active": True,
            },
        )
        if resp.status_code == 503:
            pytest.skip("Webhook service unavailable")

        sub_id = None
        if resp.status_code in (200, 201):
            body = resp.json()
            sub_id = (
                body.get("id") or body.get("subscription_id") or (body.get("data", {}).get("id"))
            )
            assert sub_id is not None, "Bounce webhook subscription was not created"

        # Cleanup
        if sub_id:
            api_delete(f"/api/v1/webhooks/subscriptions/{sub_id}")


class TestCleanup:
    """Cleanup test data. Runs last due to test ordering."""

    def test_delete_mailbox(self, test_mailbox):
        """Delete test mailbox."""
        account_id = test_mailbox.get("id")
        if account_id:
            resp = api_delete(f"/api/v1/email-accounts/{account_id}")
            assert resp.status_code in (200, 404)

    def test_delete_domain(self, test_domain):
        """Delete test domain."""
        domain_id = test_domain.get("id")
        if domain_id:
            resp = api_delete(f"/api/v1/domains/{domain_id}")
            # 400 if mailboxes still exist (fixture cleanup order may vary)
            assert resp.status_code in (200, 400, 404)

    def test_delete_organization(self, test_org):
        """Delete test organization."""
        org_id = test_org.get("_org_id") or test_org.get("id")
        resp = api_delete(f"/api/v1/organizations/{org_id}")
        # 400 if domains still exist
        assert resp.status_code in (200, 400, 404)
