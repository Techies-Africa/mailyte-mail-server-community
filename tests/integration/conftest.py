"""
Shared fixtures for integration tests.

These tests run against LIVE Docker services — they are NOT unit tests.
Ensure the dev environment is running: ./start.sh dev
"""

import imaplib
import os
import smtplib
import time

import pytest

# ---------------------------------------------------------------------------
# Configuration — read from env or use defaults matching docker-compose
# ---------------------------------------------------------------------------
SMTP_HOST = os.getenv("TEST_SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("TEST_SMTP_PORT", "587"))
SMTP_PORT_25 = int(os.getenv("TEST_SMTP_PORT_25", "25"))
IMAP_HOST = os.getenv("TEST_IMAP_HOST", "localhost")
IMAP_PORT = int(os.getenv("TEST_IMAP_PORT", "143"))
IMAP_SSL_PORT = int(os.getenv("TEST_IMAP_SSL_PORT", "993"))
POP3_HOST = os.getenv("TEST_POP3_HOST", "localhost")
POP3_PORT = int(os.getenv("TEST_POP3_PORT", "110"))
POP3_SSL_PORT = int(os.getenv("TEST_POP3_SSL_PORT", "995"))

API_BASE = os.getenv("TEST_API_BASE", "http://localhost:8083")
API_KEY = os.getenv("TEST_API_KEY", "test-api-key-123")
ADMIN_TOKEN = os.getenv("TEST_ADMIN_TOKEN", "tokensecret")

TRACKING_BASE = os.getenv("TEST_TRACKING_BASE", "http://localhost:8086")
WEBHOOKS_BASE = os.getenv("TEST_WEBHOOKS_BASE", "http://localhost:8081")
RATE_LIMITER_BASE = os.getenv("TEST_RATE_LIMITER_BASE", "http://localhost:8082")
MONITORING_BASE = os.getenv("TEST_MONITORING_BASE", "http://localhost:8085")
ANALYTICS_BASE = os.getenv("TEST_ANALYTICS_BASE", "http://localhost:8087")
DASHBOARD_BASE = os.getenv("TEST_DASHBOARD_BASE", "http://localhost:8088")
QUEUE_BASE = os.getenv("TEST_QUEUE_BASE", "http://localhost:8090")
STORAGE_BASE = os.getenv("TEST_STORAGE_BASE", "http://localhost:8092")
DOCS_BASE = os.getenv("TEST_DOCS_BASE", "http://localhost:8000")

TEST_USER = os.getenv("TEST_USER", "user@test.local")
TEST_PASS = os.getenv("TEST_PASS", "testpass123")
TEST_DOMAIN = os.getenv("TEST_DOMAIN", "test.local")
TEST_ORG = os.getenv("TEST_ORG", "test-org")

# MySQL connection (for direct DB checks)
DB_HOST = os.getenv("TEST_DB_HOST", "localhost")
DB_PORT = int(os.getenv("TEST_DB_PORT", "3307"))
DB_NAME = os.getenv("TEST_DB_NAME", "mailserver")
DB_USER = os.getenv("TEST_DB_USER", "mailuser")
DB_PASS = os.getenv("TEST_DB_PASS", "mailpassword123")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def api_headers():
    """API headers with valid test key."""
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def admin_headers():
    """API headers with admin token."""
    return {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def api_url():
    """Base API URL."""
    return API_BASE


@pytest.fixture(scope="session")
def db_connection():
    """Direct MySQL connection for verification queries."""
    try:
        import mysql.connector

        conn = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASS
        )
        yield conn
        conn.close()
    except ImportError:
        pytest.skip("mysql-connector-python not installed")
    except Exception as e:
        pytest.skip(f"Cannot connect to MySQL: {e}")


@pytest.fixture
def smtp_connection():
    """Authenticated SMTP connection on port 587."""
    s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    s.starttls()
    s.login(TEST_USER, TEST_PASS)
    yield s
    try:
        s.quit()
    except Exception:
        pass


@pytest.fixture
def imap_connection():
    """Authenticated IMAP connection on port 143."""
    m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    m.starttls()
    m.login(TEST_USER, TEST_PASS)
    yield m
    try:
        m.logout()
    except Exception:
        pass


def send_test_email(subject="Test", body="Test body", html=False, to=None):
    """Helper to send an email and return the message ID."""
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid

    content_type = "html" if html else "plain"
    msg = MIMEText(body, content_type, "utf-8")
    msg["Subject"] = subject
    msg["From"] = TEST_USER
    msg["To"] = to or TEST_USER
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=TEST_DOMAIN)

    s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    s.starttls()
    s.login(TEST_USER, TEST_PASS)
    s.sendmail(TEST_USER, [to or TEST_USER], msg.as_string())
    s.quit()
    return msg["Message-ID"]


def wait_for_delivery(timeout=20):
    """Wait for mail to be delivered through the pipeline."""
    time.sleep(timeout)


def get_inbox_messages():
    """Get all messages from test user's inbox."""
    m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    m.starttls()
    m.login(TEST_USER, TEST_PASS)
    m.select("INBOX")
    typ, data = m.search(None, "ALL")
    msgs = []
    if data[0]:
        for num in data[0].split():
            typ, msg_data = m.fetch(num, "(RFC822)")
            msgs.append(msg_data[0][1])
    m.logout()
    return msgs


def clear_inbox():
    """Delete all messages from test user's inbox."""
    m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    m.starttls()
    m.login(TEST_USER, TEST_PASS)
    m.select("INBOX")
    typ, data = m.search(None, "ALL")
    if data[0]:
        for num in data[0].split():
            m.store(num, "+FLAGS", "\\Deleted")
        m.expunge()
    m.logout()
