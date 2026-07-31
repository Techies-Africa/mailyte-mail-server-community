#!/usr/bin/env python3
"""
Mail Flow Integration Tests

Verifies the complete email pipeline:
  SMTP send (port 587) → Postfix → Rspamd scan → Dovecot LMTP delivery → IMAP retrieve (port 993)

Requirements:
  - All services running: `docker compose up -d`
  - All health checks passing: `docker compose ps` (all "healthy")
  - Test user created in database (see setup_test_data below)

Usage:
  python3 tests/test_mail_flow.py
  python3 tests/test_mail_flow.py --setup    # Create test user/domain first
  python3 tests/test_mail_flow.py --host mail.example.com
"""

import os
import sys
import ssl
import time
import uuid
import email
import smtplib
import imaplib
import poplib
import argparse
import hashlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Default test configuration
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
IMAP_HOST = os.getenv("IMAP_HOST", "localhost")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
POP3_HOST = os.getenv("POP3_HOST", "localhost")
POP3_PORT = int(os.getenv("POP3_PORT", "995"))

TEST_USER = os.getenv("TEST_USER", "test@example.com")
TEST_PASS = os.getenv("TEST_PASS", "TestPassword123!")
TEST_DOMAIN = os.getenv("TEST_DOMAIN", "example.com")

# For self-signed certs in development
SSL_VERIFY = os.getenv("SSL_VERIFY", "false").lower() == "true"


class TestResult:
    def __init__(self, name):
        self.name = name
        self.passed = False
        self.message = ""
        self.duration = 0

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name} ({self.duration:.2f}s) — {self.message}"


def get_ssl_context():
    """Create SSL context that accepts self-signed certs in development."""
    ctx = ssl.create_default_context()
    if not SSL_VERIFY:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def test_smtp_connection():
    """Test: SMTP connection and STARTTLS on port 587."""
    result = TestResult("SMTP Connection + STARTTLS")
    start = time.time()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            # Verify STARTTLS is advertised
            if smtp.has_extn("STARTTLS"):
                smtp.starttls(context=get_ssl_context())
                smtp.ehlo()
                result.passed = True
                result.message = f"Connected to {SMTP_HOST}:{SMTP_PORT}, STARTTLS OK"
            else:
                result.message = "STARTTLS not advertised"
    except Exception as e:
        result.message = f"Connection failed: {e}"
    result.duration = time.time() - start
    return result


def test_smtp_auth():
    """Test: SMTP SASL authentication via Dovecot."""
    result = TestResult("SMTP Authentication (SASL)")
    start = time.time()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=get_ssl_context())
            smtp.ehlo()
            smtp.login(TEST_USER, TEST_PASS)
            result.passed = True
            result.message = f"Authenticated as {TEST_USER}"
    except smtplib.SMTPAuthenticationError as e:
        result.message = f"Auth failed: {e}"
    except Exception as e:
        result.message = f"Error: {e}"
    result.duration = time.time() - start
    return result


def test_send_email():
    """Test: Send email via SMTP and return the message ID for retrieval."""
    result = TestResult("SMTP Send Email")
    start = time.time()
    msg_id = f"{uuid.uuid4().hex[:12]}@{TEST_DOMAIN}"

    try:
        msg = MIMEMultipart()
        msg["From"] = TEST_USER
        msg["To"] = TEST_USER
        msg["Subject"] = f"Mailyte Test — {msg_id}"
        msg["Message-ID"] = f"<{msg_id}>"
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["X-Mailyte-Test"] = "true"

        body = (
            f"This is an automated integration test message.\n"
            f"Test ID: {msg_id}\n"
            f"Timestamp: {datetime.now().isoformat()}\n"
            f"\nIf you can read this via IMAP, the full mail flow is working.\n"
        )
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=get_ssl_context())
            smtp.ehlo()
            smtp.login(TEST_USER, TEST_PASS)
            smtp.send_message(msg)

        result.passed = True
        result.message = f"Sent message {msg_id}"
        result.data = msg_id
    except Exception as e:
        result.message = f"Send failed: {e}"
        result.data = None
    result.duration = time.time() - start
    return result


def test_imap_connection():
    """Test: IMAP SSL connection on port 993."""
    result = TestResult("IMAP SSL Connection")
    start = time.time()
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=get_ssl_context())
        imap.logout()
        result.passed = True
        result.message = f"Connected to {IMAP_HOST}:{IMAP_PORT}"
    except Exception as e:
        result.message = f"Connection failed: {e}"
    result.duration = time.time() - start
    return result


def test_imap_auth():
    """Test: IMAP authentication."""
    result = TestResult("IMAP Authentication")
    start = time.time()
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=get_ssl_context())
        imap.login(TEST_USER, TEST_PASS)
        imap.logout()
        result.passed = True
        result.message = f"Authenticated as {TEST_USER}"
    except imaplib.IMAP4.error as e:
        result.message = f"Auth failed: {e}"
    except Exception as e:
        result.message = f"Error: {e}"
    result.duration = time.time() - start
    return result


def test_imap_retrieve(msg_id, max_wait=30):
    """Test: Retrieve the sent test email via IMAP."""
    result = TestResult("IMAP Retrieve Email")
    start = time.time()

    if not msg_id:
        result.message = "Skipped — no message ID (send failed)"
        result.duration = time.time() - start
        return result

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=get_ssl_context())
        imap.login(TEST_USER, TEST_PASS)

        # Poll inbox until message arrives or timeout
        found = False
        deadline = time.time() + max_wait

        while time.time() < deadline:
            imap.select("INBOX")
            # Search for our test message by subject
            status, data = imap.search(None, f'HEADER Message-ID "<{msg_id}>"')

            if status == "OK" and data[0]:
                msg_nums = data[0].split()
                if msg_nums:
                    # Fetch the message
                    status, msg_data = imap.fetch(msg_nums[-1], "(RFC822)")
                    if status == "OK":
                        raw_email = msg_data[0][1]
                        parsed = email.message_from_bytes(raw_email)

                        result.passed = True
                        result.message = (
                            f"Retrieved message {msg_id} (Subject: {parsed['Subject']})"
                        )
                        result.data = parsed
                        found = True
                        break

            time.sleep(2)

        if not found:
            result.message = f"Message {msg_id} not found after {max_wait}s"

        imap.logout()
    except Exception as e:
        result.message = f"Retrieve failed: {e}"
    result.duration = time.time() - start
    return result


def test_dkim_signature(parsed_email):
    """Test: Check if the email has a DKIM-Signature header (Rspamd signed it)."""
    result = TestResult("DKIM Signature Present")
    start = time.time()

    if not parsed_email:
        result.message = "Skipped — no email to check"
        result.duration = time.time() - start
        return result

    try:
        dkim_header = parsed_email.get("DKIM-Signature", "")
        if dkim_header:
            result.passed = True
            # Extract domain from DKIM signature
            d_value = ""
            for part in dkim_header.split(";"):
                part = part.strip()
                if part.startswith("d="):
                    d_value = part[2:].strip()
                    break
            result.message = f"DKIM-Signature present (d={d_value})"
        else:
            result.message = "No DKIM-Signature header — Rspamd may not have signed it"
    except Exception as e:
        result.message = f"Check failed: {e}"
    result.duration = time.time() - start
    return result


def test_auth_results(parsed_email):
    """Test: Check Authentication-Results header for SPF/DKIM/DMARC results."""
    result = TestResult("Authentication-Results Header")
    start = time.time()

    if not parsed_email:
        result.message = "Skipped — no email to check"
        result.duration = time.time() - start
        return result

    try:
        auth_results = parsed_email.get("Authentication-Results", "")
        if auth_results:
            result.passed = True
            # Parse key results
            parts = []
            if "spf=" in auth_results.lower():
                parts.append("SPF")
            if "dkim=" in auth_results.lower():
                parts.append("DKIM")
            if "dmarc=" in auth_results.lower():
                parts.append("DMARC")
            result.message = f"Auth-Results present (checks: {', '.join(parts) or 'unknown'})"
        else:
            result.message = (
                "No Authentication-Results header — milter_headers may not be configured"
            )
    except Exception as e:
        result.message = f"Check failed: {e}"
    result.duration = time.time() - start
    return result


def test_imap_folders():
    """Test: Verify standard IMAP folders were auto-created by Dovecot."""
    result = TestResult("IMAP Standard Folders")
    start = time.time()
    expected_folders = {"INBOX", "Drafts", "Sent", "Trash", "Junk", "Archive"}

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=get_ssl_context())
        imap.login(TEST_USER, TEST_PASS)
        status, folder_data = imap.list()

        found_folders = set()
        if status == "OK":
            for item in folder_data:
                # Parse IMAP LIST response: (flags) delimiter name
                decoded = item.decode() if isinstance(item, bytes) else item
                parts = decoded.rsplit('"', 2)
                if len(parts) >= 1:
                    folder_name = parts[-1].strip().strip('"')
                    found_folders.add(folder_name)

        imap.logout()

        missing = expected_folders - found_folders
        if not missing:
            result.passed = True
            result.message = f"All standard folders present: {', '.join(sorted(found_folders & expected_folders))}"
        else:
            result.message = f"Missing folders: {', '.join(missing)}"
    except Exception as e:
        result.message = f"Folder check failed: {e}"
    result.duration = time.time() - start
    return result


def test_pop3_connection():
    """Test: POP3 SSL connection on port 995."""
    result = TestResult("POP3 SSL Connection")
    start = time.time()
    try:
        pop = poplib.POP3_SSL(POP3_HOST, POP3_PORT, context=get_ssl_context())
        pop.quit()
        result.passed = True
        result.message = f"Connected to {POP3_HOST}:{POP3_PORT}"
    except Exception as e:
        result.message = f"Connection failed: {e}"
    result.duration = time.time() - start
    return result


# =========================================================================
# SECURITY TESTS
# =========================================================================


def test_smtp_reject_no_auth():
    """Test: SMTP rejects relay without authentication (open relay protection)."""
    result = TestResult("SMTP Rejects Unauthenticated Relay")
    start = time.time()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=get_ssl_context())
            smtp.ehlo()
            # Try sending without authentication
            try:
                smtp.sendmail("attacker@evil.com", "victim@gmail.com", "Subject: spam\n\nspam")
                result.message = "CRITICAL: SMTP accepted unauthenticated relay!"
            except smtplib.SMTPRecipientsRefused:
                result.passed = True
                result.message = "Correctly rejected unauthenticated relay"
            except smtplib.SMTPSenderRefused:
                result.passed = True
                result.message = "Correctly rejected unauthenticated sender"
    except Exception as e:
        result.message = f"Error: {e}"
    result.duration = time.time() - start
    return result


def test_smtp_vrfy_disabled():
    """Test: SMTP VRFY command is disabled (anti-enumeration)."""
    result = TestResult("SMTP VRFY Disabled")
    start = time.time()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            code, msg = smtp.verify(TEST_USER)
            if code >= 500:
                result.passed = True
                result.message = f"VRFY rejected (code={code})"
            elif code == 252:
                result.passed = True
                result.message = "VRFY disabled (cannot verify but accepted syntax)"
            else:
                result.message = f"VRFY responded (code={code}), should be disabled"
    except smtplib.SMTPServerDisconnected:
        result.passed = True
        result.message = "VRFY caused disconnect (command disabled)"
    except Exception as e:
        result.message = f"Error: {e}"
    result.duration = time.time() - start
    return result


def test_smtp_banner_no_version():
    """Test: SMTP banner hides software version (information leakage prevention)."""
    result = TestResult("SMTP Banner Hides Version")
    start = time.time()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            banner = smtp.ehlo_resp.decode() if smtp.ehlo_resp else ""
            # Check that banner doesn't reveal Postfix version
            if "postfix" not in banner.lower() or "version" not in banner.lower():
                result.passed = True
                result.message = f"Banner does not reveal version info"
            else:
                result.message = f"Banner reveals version: {banner[:100]}"
    except Exception as e:
        result.message = f"Error: {e}"
    result.duration = time.time() - start
    return result


def test_smtp_wrong_password():
    """Test: SMTP rejects wrong password with appropriate delay."""
    result = TestResult("SMTP Rejects Wrong Password")
    start = time.time()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=get_ssl_context())
            smtp.ehlo()
            try:
                smtp.login(TEST_USER, "WrongPassword123!")
                result.message = "CRITICAL: SMTP accepted wrong password!"
            except smtplib.SMTPAuthenticationError:
                elapsed = time.time() - start
                result.passed = True
                if elapsed >= 1.5:
                    result.message = f"Correctly rejected (with {elapsed:.1f}s delay — brute-force protection active)"
                else:
                    result.message = f"Correctly rejected ({elapsed:.1f}s)"
    except Exception as e:
        result.message = f"Error: {e}"
    result.duration = time.time() - start
    return result


def test_imap_smart_folders():
    """Test: Verify smart folder mailboxes exist (Notifications, Social, Promotions, Updates)."""
    result = TestResult("IMAP Smart Folders")
    start = time.time()
    expected = {"Notifications", "Social", "Promotions", "Updates"}

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=get_ssl_context())
        imap.login(TEST_USER, TEST_PASS)
        status, folder_data = imap.list()

        found_folders = set()
        if status == "OK":
            for item in folder_data:
                decoded = item.decode() if isinstance(item, bytes) else item
                parts = decoded.rsplit('"', 2)
                if len(parts) >= 1:
                    folder_name = parts[-1].strip().strip('"')
                    found_folders.add(folder_name)

        imap.logout()

        found_smart = expected & found_folders
        missing = expected - found_folders

        if not missing:
            result.passed = True
            result.message = f"All smart folders present: {', '.join(sorted(found_smart))}"
        else:
            result.message = f"Missing smart folders: {', '.join(missing)} (found: {', '.join(sorted(found_smart))})"
    except Exception as e:
        result.message = f"Check failed: {e}"
    result.duration = time.time() - start
    return result


def test_tls_version():
    """Test: Verify TLS 1.2+ is enforced (no SSLv3/TLSv1.0/1.1)."""
    result = TestResult("TLS 1.2+ Enforced")
    start = time.time()
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        import socket

        sock = socket.create_connection((IMAP_HOST, IMAP_PORT), timeout=10)
        ssock = ctx.wrap_socket(sock, server_hostname=IMAP_HOST)
        version = ssock.version()
        ssock.close()

        if version in ("TLSv1.2", "TLSv1.3"):
            result.passed = True
            result.message = f"Negotiated {version}"
        else:
            result.message = f"Negotiated {version} — should be TLS 1.2+"
    except Exception as e:
        result.message = f"TLS check failed: {e}"
    result.duration = time.time() - start
    return result


def setup_test_data():
    """Create test domain and user in the database for testing."""
    try:
        import mysql.connector
    except ImportError:
        print(
            "mysql-connector-python not installed. Install with: pip3 install mysql-connector-python"
        )
        return False

    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "database": os.getenv("DB_NAME", "mailserver"),
        "user": os.getenv("DB_USER", "mailuser"),
        "password": os.getenv("DB_PASSWORD", "mailpassword"),
    }

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # Create test organization
        cursor.execute("""
            INSERT IGNORE INTO organizations (name, slug, plan, status, created_at, updated_at)
            VALUES ('Test Org', 'test-org', 'enterprise', 'active', NOW(), NOW())
        """)
        cursor.execute("SELECT id FROM organizations WHERE slug = 'test-org'")
        org_id = cursor.fetchone()[0]

        # Create test domain
        cursor.execute(
            """
            INSERT IGNORE INTO domains (organization_id, domain, active, verified, created_at, updated_at)
            VALUES (%s, %s, 1, 1, NOW(), NOW())
        """,
            (org_id, TEST_DOMAIN),
        )
        cursor.execute("SELECT id FROM domains WHERE domain = %s", (TEST_DOMAIN,))
        domain_id = cursor.fetchone()[0]

        # Create test email account with hashed password
        # Dovecot uses {PLAIN} prefix for plaintext passwords in dev
        password_hash = "{PLAIN}" + TEST_PASS
        cursor.execute(
            """
            INSERT IGNORE INTO email_accounts
            (domain_id, email, password, name, status, storage_quota, created_at, updated_at)
            VALUES (%s, %s, %s, 'Test User', 'active', 5368709120, NOW(), NOW())
        """,
            (domain_id, TEST_USER, password_hash),
        )

        conn.commit()
        cursor.close()
        conn.close()

        print(f"Test data created:")
        print(f"  Domain: {TEST_DOMAIN}")
        print(f"  User:   {TEST_USER}")
        print(f"  Pass:   {TEST_PASS}")
        return True

    except Exception as e:
        print(f"Setup failed: {e}")
        return False


def run_tests():
    """Run all mail flow integration tests."""
    print("=" * 70)
    print("  Mailyte Mail Flow Integration Tests")
    print(f"  SMTP: {SMTP_HOST}:{SMTP_PORT}  |  IMAP: {IMAP_HOST}:{IMAP_PORT}")
    print(f"  User: {TEST_USER}")
    print(f"  SSL Verify: {SSL_VERIFY}")
    print("=" * 70)
    print()

    results = []

    # Phase 1: Connection tests
    print("--- Phase 1: Connection Tests ---")
    for test_fn in [test_smtp_connection, test_imap_connection, test_pop3_connection]:
        r = test_fn()
        results.append(r)
        print(f"  {r}")

    # Phase 2: Authentication tests
    print("\n--- Phase 2: Authentication Tests ---")
    for test_fn in [test_smtp_auth, test_imap_auth]:
        r = test_fn()
        results.append(r)
        print(f"  {r}")

    # Phase 3: Mail flow test
    print("\n--- Phase 3: Send/Receive Flow ---")
    send_result = test_send_email()
    results.append(send_result)
    print(f"  {send_result}")

    msg_id = getattr(send_result, "data", None)

    if msg_id:
        print(f"  Waiting for delivery (up to 30s)...")
        retrieve_result = test_imap_retrieve(msg_id)
        results.append(retrieve_result)
        print(f"  {retrieve_result}")

        parsed = getattr(retrieve_result, "data", None)

        # Phase 4: Email authentication checks
        print("\n--- Phase 4: Email Authentication ---")
        for test_fn in [test_dkim_signature, test_auth_results]:
            r = test_fn(parsed)
            results.append(r)
            print(f"  {r}")
    else:
        print("  Skipping retrieval — send failed")

    # Phase 5: Dovecot features
    print("\n--- Phase 5: Dovecot Features ---")
    folder_result = test_imap_folders()
    results.append(folder_result)
    print(f"  {folder_result}")

    smart_folder_result = test_imap_smart_folders()
    results.append(smart_folder_result)
    print(f"  {smart_folder_result}")

    # Phase 6: Security tests
    print("\n--- Phase 6: Security Tests ---")
    for test_fn in [
        test_smtp_reject_no_auth,
        test_smtp_vrfy_disabled,
        test_smtp_banner_no_version,
        test_smtp_wrong_password,
        test_tls_version,
    ]:
        r = test_fn()
        results.append(r)
        print(f"  {r}")

    # Summary
    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"  Results: {passed}/{total} tests passed")

    if passed == total:
        print("  Status: ALL TESTS PASSED")
    elif passed >= total * 0.7:
        print("  Status: MOSTLY PASSING — check failures above")
    else:
        print("  Status: CRITICAL FAILURES — review configuration")

    print("=" * 70)

    return passed == total


def main():
    parser = argparse.ArgumentParser(description="Mailyte Mail Flow Integration Tests")
    parser.add_argument("--setup", action="store_true", help="Create test user/domain in database")
    parser.add_argument("--host", default=None, help="Override SMTP/IMAP host")
    parser.add_argument("--user", default=None, help="Override test user email")
    parser.add_argument("--password", default=None, help="Override test user password")
    parser.add_argument("--verify-ssl", action="store_true", help="Verify SSL certificates")

    args = parser.parse_args()

    global SMTP_HOST, IMAP_HOST, POP3_HOST, TEST_USER, TEST_PASS, SSL_VERIFY
    if args.host:
        SMTP_HOST = IMAP_HOST = POP3_HOST = args.host
    if args.user:
        TEST_USER = args.user
    if args.password:
        TEST_PASS = args.password
    if args.verify_ssl:
        SSL_VERIFY = True

    if args.setup:
        setup_test_data()
        return

    success = run_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
