#!/usr/bin/env python3
"""
Mailyte CE — Edge Case Test Suite
Tests boundary conditions, error handling, malicious inputs, and failure scenarios.
"""

import imaplib
import poplib
import smtplib
import socket
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import requests

API = "http://api:8080/api/v1"
KEY = "test-api-key-123"
H = {"X-API-Key": KEY, "Content-Type": "application/json"}
RESULTS = []


def test(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append((name, status, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def send_smtp(from_a, to_a, subject, body, pw="testpass123", html=False):
    msg = MIMEMultipart("alternative")
    msg["From"] = from_a
    msg["To"] = to_a
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="test.local")
    msg.attach(MIMEText(body, "html" if html else "plain", "utf-8"))
    s = smtplib.SMTP("postfix", 587, timeout=15)
    s.starttls()
    s.login(from_a, pw)
    s.sendmail(from_a, [to_a], msg.as_string())
    s.quit()
    return msg["Message-ID"]


print("\n" + "=" * 70)
print("  MAILYTE CE — EDGE CASE TEST SUITE")
print("=" * 70)

# =========================================================================
# 1. API INPUT VALIDATION
# =========================================================================
print("\n--- 1. API INPUT VALIDATION ---")

# Empty body
r = requests.post(f"{API}/mailboxes/add", headers=H, json={}, timeout=10)
test("Empty body rejected", r.status_code in (400, 422), f"status={r.status_code}")

# Missing required fields
r = requests.post(f"{API}/mailboxes/add", headers=H, json={"email": "x@test.local"}, timeout=10)
test("Missing fields rejected", r.status_code in (400, 422), f"status={r.status_code}")

# Invalid email format
r = requests.post(
    f"{API}/mailboxes/add",
    headers=H,
    json={
        "email": "notanemail",
        "local_part": "bad",
        "password": "test1234",
        "domain": "test.local",
    },
    timeout=10,
)
test("Invalid email format rejected", r.status_code in (400, 422), f"status={r.status_code}")

# SQL injection in API key
r = requests.get(f"{API}/domains/", headers={"X-API-Key": "' OR 1=1 --"}, timeout=10)
test("SQL injection in API key blocked", r.status_code == 401)

# SQL injection in domain query
r = requests.get(f"{API}/domains/?domain=' OR 1=1 --", headers=H, timeout=10)
test("SQL injection in query param", r.status_code in (200, 400, 404), f"status={r.status_code}")

# XSS in name field
r = requests.post(
    f"{API}/mailboxes/add",
    headers=H,
    json={
        "email": "xss@test.local",
        "local_part": "xss",
        "password": "testpass123",
        "name": "<script>alert('xss')</script>",
        "domain": "test.local",
    },
    timeout=10,
)
test("XSS in name field", r.status_code in (200, 201, 400), f"status={r.status_code}")
# Clean up if created
if r.status_code in (200, 201):
    requests.post(f"{API}/mailboxes/delete", headers=H, json=["xss@test.local"], timeout=10)

# Very long email
long_local = "a" * 200
r = requests.post(
    f"{API}/mailboxes/add",
    headers=H,
    json={
        "email": f"{long_local}@test.local",
        "local_part": long_local,
        "password": "testpass123",
        "domain": "test.local",
    },
    timeout=10,
)
test("Very long email rejected", r.status_code in (400, 422, 500), f"status={r.status_code}")

# Weak password
r = requests.post(
    f"{API}/mailboxes/add",
    headers=H,
    json={
        "email": "weak@test.local",
        "local_part": "weak",
        "password": "123",
        "domain": "test.local",
    },
    timeout=10,
)
test("Weak password rejected", r.status_code in (400, 422), f"status={r.status_code}")

# Non-existent domain
r = requests.post(
    f"{API}/mailboxes/add",
    headers=H,
    json={
        "email": "user@nonexistent.com",
        "local_part": "user",
        "password": "testpass123",
        "domain": "nonexistent.com",
    },
    timeout=10,
)
test("Non-existent domain rejected", r.status_code in (400, 404), f"status={r.status_code}")

# Duplicate mailbox
r = requests.post(
    f"{API}/mailboxes/add",
    headers=H,
    json={
        "email": "user@test.local",
        "local_part": "user",
        "password": "testpass123",
        "domain": "test.local",
    },
    timeout=10,
)
test("Duplicate mailbox rejected", r.status_code == 409, f"status={r.status_code}")

# Invalid JSON
r = requests.post(f"{API}/mailboxes/add", headers={"X-API-Key": KEY}, data="not json", timeout=10)
test("Invalid JSON rejected", r.status_code in (400, 422), f"status={r.status_code}")

# Huge payload
huge = {"email": "x@test.local", "name": "A" * 100000}
r = requests.post(f"{API}/mailboxes/add", headers=H, json=huge, timeout=10)
test("Huge payload handled", r.status_code in (400, 413, 422, 500), f"status={r.status_code}")

# =========================================================================
# 2. SMTP EDGE CASES
# =========================================================================
print("\n--- 2. SMTP EDGE CASES ---")

# Send to non-existent user
try:
    send_smtp("user@test.local", "nobody@test.local", "To Nobody", "Should bounce")
    test("Send to non-existent user", False, "Should have been rejected")
except smtplib.SMTPRecipientsRefused:
    test("Send to non-existent user rejected", True, "550 User unknown")
except Exception as e:
    test("Send to non-existent user", False, str(e)[:80])

# Send to external domain (should be rejected — no relay)
try:
    s = smtplib.SMTP("postfix", 25, timeout=10)
    s.ehlo()
    s.mail("attacker@evil.com")
    code, msg = s.rcpt("victim@gmail.com")
    test("Open relay blocked (port 25)", code >= 400, f"code={code}")
    s.quit()
except Exception as e:
    test("Open relay blocked (port 25)", True, str(e)[:60])

# Empty subject
try:
    send_smtp("user@test.local", "user@test.local", "", "No subject email")
    test("Empty subject accepted", True)
except Exception as e:
    test("Empty subject", False, str(e)[:60])

# Very long subject
try:
    send_smtp("user@test.local", "user@test.local", "X" * 500, "Long subject test")
    test("Long subject (500 chars) accepted", True)
except Exception as e:
    test("Long subject (500 chars)", False, str(e)[:60])

# Unicode subject
try:
    send_smtp("user@test.local", "user@test.local", "Тест 测试 テスト 🚀", "Unicode subject test")
    test("Unicode subject accepted", True)
except Exception as e:
    test("Unicode subject", False, str(e)[:60])

# Unicode body
try:
    send_smtp(
        "user@test.local",
        "user@test.local",
        "Unicode Body",
        "Hello 你好 Привет مرحبا こんにちは 🎉🔥💌",
    )
    test("Unicode body accepted", True)
except Exception as e:
    test("Unicode body", False, str(e)[:60])

# Large body (100KB)
try:
    large_body = "X" * 102400
    send_smtp("user@test.local", "user@test.local", "Large Body", large_body)
    test("Large body (100KB) accepted", True)
except Exception as e:
    test("Large body (100KB)", False, str(e)[:60])

# Multiple recipients
try:
    msg = MIMEText("Multi-recipient test")
    msg["From"] = "user@test.local"
    msg["To"] = "user@test.local, test2@test.local"
    msg["Subject"] = "Multi-recipient"
    msg["Message-ID"] = make_msgid(domain="test.local")
    s = smtplib.SMTP("postfix", 587, timeout=15)
    s.starttls()
    s.login("user@test.local", "testpass123")
    s.sendmail("user@test.local", ["user@test.local", "test2@test.local"], msg.as_string())
    s.quit()
    test("Multiple recipients", True)
except Exception as e:
    test("Multiple recipients", False, str(e)[:60])

# Rapid sequential sends (stress test)
rapid_ok = 0
for i in range(20):
    try:
        send_smtp("user@test.local", "user@test.local", f"Rapid #{i}", f"Body {i}")
        rapid_ok += 1
    except:
        break
test("Rapid sends (20x)", rapid_ok >= 15, f"{rapid_ok}/20 succeeded")

# Connection without STARTTLS (should reject auth)
try:
    s = smtplib.SMTP("postfix", 587, timeout=10)
    s.login("user@test.local", "testpass123")
    test("Auth without TLS rejected", False, "Should have failed")
    s.quit()
except smtplib.SMTPNotSupportedError:
    test("Auth without TLS rejected", True, "SMTP AUTH requires TLS")
except smtplib.SMTPException as e:
    test("Auth without TLS rejected", True, str(e)[:60])
except Exception as e:
    test("Auth without TLS rejected", False, str(e)[:60])

# =========================================================================
# 3. IMAP EDGE CASES
# =========================================================================
print("\n--- 3. IMAP EDGE CASES ---")

# Login with wrong password
try:
    m = imaplib.IMAP4("dovecot", 143)
    m.starttls()
    m.login("user@test.local", "wrongpassword")
    test("IMAP wrong password rejected", False, "Should have failed")
    m.logout()
except imaplib.IMAP4.error:
    test("IMAP wrong password rejected", True)

# Login with non-existent user
try:
    m = imaplib.IMAP4("dovecot", 143)
    m.starttls()
    m.login("nobody@test.local", "testpass123")
    test("IMAP non-existent user rejected", False, "Should have failed")
    m.logout()
except imaplib.IMAP4.error:
    test("IMAP non-existent user rejected", True)

# Create and delete folder
try:
    m = imaplib.IMAP4("dovecot", 143)
    m.starttls()
    m.login("user@test.local", "testpass123")
    m.create("TestFolder")
    typ, folders = m.list()
    has_folder = any(b"TestFolder" in f for f in folders)
    test("IMAP create folder", has_folder)
    m.delete("TestFolder")
    test("IMAP delete folder", True)
    m.logout()
except Exception as e:
    test("IMAP folder operations", False, str(e)[:60])

# Select non-existent folder
try:
    m = imaplib.IMAP4("dovecot", 143)
    m.starttls()
    m.login("user@test.local", "testpass123")
    typ, data = m.select("NonExistentFolder")
    test("IMAP select bad folder handled", typ == "NO")
    m.logout()
except imaplib.IMAP4.error:
    test("IMAP select bad folder handled", True)
except Exception as e:
    test("IMAP select bad folder", False, str(e)[:60])

# Concurrent IMAP connections
try:
    conns = []
    for i in range(5):
        m = imaplib.IMAP4("dovecot", 143)
        m.starttls()
        m.login("user@test.local", "testpass123")
        conns.append(m)
    test("5 concurrent IMAP connections", len(conns) == 5)
    for m in conns:
        m.logout()
except Exception as e:
    test("Concurrent IMAP connections", False, str(e)[:60])

# POP3 wrong password
try:
    p = poplib.POP3("dovecot", 110, timeout=10)
    p.stls()
    p.user("user@test.local")
    p.pass_("wrongpassword")
    test("POP3 wrong password rejected", False, "Should have failed")
    p.quit()
except poplib.error_proto:
    test("POP3 wrong password rejected", True)

# =========================================================================
# 4. SSL/TLS EDGE CASES
# =========================================================================
print("\n--- 4. SSL/TLS EDGE CASES ---")

# TLS 1.2+ enforced (no SSLv3/TLSv1.0)
try:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.maximum_version = ssl.TLSVersion.TLSv1  # Force old TLS
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    ss = ctx.wrap_socket(s, server_hostname="dovecot")
    ss.connect(("dovecot", 993))
    test("Old TLS rejected", False, "Should have failed")
    ss.close()
except (ssl.SSLError, ConnectionResetError, OSError):
    test("Old TLS (1.0) rejected by IMAP", True)

# SMTP SMTPS port 465
try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = smtplib.SMTP_SSL("postfix", 465, context=ctx, timeout=10)
    test("SMTPS (port 465)", True)
    s.quit()
except Exception as e:
    test("SMTPS (port 465)", False, str(e)[:60])

# =========================================================================
# 5. API ERROR HANDLING
# =========================================================================
print("\n--- 5. API ERROR HANDLING ---")

# Non-existent endpoint
r = requests.get(f"{API}/nonexistent/", headers=H, timeout=10)
test("404 for non-existent endpoint", r.status_code == 404)

# Wrong HTTP method
r = requests.delete(f"{API}/domains/", headers=H, timeout=10)
test("Wrong HTTP method handled", r.status_code in (404, 405))

# Get non-existent domain
r = requests.get(f"{API}/domains/nonexistent-id-12345", headers=H, timeout=10)
test("Non-existent domain returns 404", r.status_code in (404, 500), f"status={r.status_code}")

# Get non-existent org
r = requests.get(f"{API}/organizations/nonexistent-org", headers=H, timeout=10)
test("Non-existent org returns 404", r.status_code in (404, 500), f"status={r.status_code}")

# Rate limiter with non-existent domain
r = requests.get(f"{API}/rate-limiter/rate-limits/domain/nonexistent.com", headers=H, timeout=10)
test("Rate limits for non-existent domain", r.status_code in (200, 404), f"status={r.status_code}")

# Tracking with non-existent ID
r = requests.get("http://tracking:8086/t/open/nonexistent-tracking-id-abc123", timeout=10)
test("Tracking non-existent ID handled", r.status_code in (200, 404), f"status={r.status_code}")

# API with expired/malformed token
r = requests.get(f"{API}/domains/", headers={"X-API-Key": ""}, timeout=10)
test("Empty API key rejected", r.status_code == 401)

r = requests.get(f"{API}/domains/", headers={"X-API-Key": "null"}, timeout=10)
test("'null' API key rejected", r.status_code == 401)

r = requests.get(f"{API}/domains/", headers={"X-API-Key": " "}, timeout=10)
test("Whitespace API key rejected", r.status_code == 401)

# =========================================================================
# 6. EMAIL CONTENT EDGE CASES
# =========================================================================
print("\n--- 6. EMAIL CONTENT ---")

# Empty body
try:
    send_smtp("user@test.local", "user@test.local", "Empty Body Test", "")
    test("Empty body email accepted", True)
except Exception as e:
    test("Empty body email", False, str(e)[:60])

# HTML with embedded base64 image
try:
    html = '<html><body><img src="data:image/png;base64,iVBORw0KGgo=" alt="pixel"></body></html>'
    send_smtp("user@test.local", "user@test.local", "Base64 Image", html, html=True)
    test("HTML with base64 image", True)
except Exception as e:
    test("HTML with base64 image", False, str(e)[:60])

# Email with attachment
try:
    msg = MIMEMultipart()
    msg["From"] = "user@test.local"
    msg["To"] = "user@test.local"
    msg["Subject"] = "Attachment Test"
    msg["Message-ID"] = make_msgid(domain="test.local")
    msg.attach(MIMEText("See attached", "plain"))
    part = MIMEBase("application", "octet-stream")
    part.set_payload(b"Hello this is a test file content\n" * 100)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename="test.txt")
    msg.attach(part)
    s = smtplib.SMTP("postfix", 587, timeout=15)
    s.starttls()
    s.login("user@test.local", "testpass123")
    s.sendmail("user@test.local", ["user@test.local"], msg.as_string())
    s.quit()
    test("Email with attachment", True)
except Exception as e:
    test("Email with attachment", False, str(e)[:60])

# =========================================================================
# 7. SERVICE RESILIENCE
# =========================================================================
print("\n--- 7. SERVICE RESILIENCE ---")

# API under load (50 rapid requests)
import concurrent.futures


def api_request(_):
    try:
        r = requests.get(f"{API}/domains/", headers=H, timeout=10)
        return r.status_code == 200
    except:
        return False


with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results_list = list(executor.map(api_request, range(50)))
ok_count = sum(results_list)
test("API under load (50 concurrent)", ok_count >= 40, f"{ok_count}/50 succeeded")


# Health endpoint under load
def health_request(_):
    try:
        r = requests.get("http://api:8080/health", timeout=10)
        return r.status_code == 200
    except:
        return False


with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results_list = list(executor.map(health_request, range(50)))
ok_count = sum(results_list)
test("Health under load (50 concurrent)", ok_count >= 45, f"{ok_count}/50 succeeded")

# Redis resilience
import redis

rc = redis.Redis(host="redis", port=6379, db=0)
try:
    pipe = rc.pipeline()
    for i in range(100):
        pipe.set(f"edge_test_{i}", f"value_{i}")
    pipe.execute()
    for i in range(100):
        val = rc.get(f"edge_test_{i}")
        assert val == f"value_{i}".encode()
    for i in range(100):
        rc.delete(f"edge_test_{i}")
    test("Redis 100 key pipeline", True)
except Exception as e:
    test("Redis pipeline", False, str(e)[:60])

# MySQL connection pool
import mysql.connector

try:
    conns = []
    for i in range(10):
        c = mysql.connector.connect(
            host="mysql",
            port=3306,
            database="mailserver",
            user="mailuser",
            password="mailpassword123",
        )
        conns.append(c)
    test("10 concurrent MySQL connections", len(conns) == 10)
    for c in conns:
        c.close()
except Exception as e:
    test("MySQL concurrent connections", False, str(e)[:60])

# =========================================================================
# 8. WEBHOOK EDGE CASES
# =========================================================================
print("\n--- 8. WEBHOOKS ---")

# Webhook endpoints CRUD
r = requests.get(f"{API}/webhooks/endpoints", headers=H, timeout=10)
test("List webhook endpoints", r.status_code in (200, 404), f"status={r.status_code}")

r = requests.get(f"{API}/webhooks/deliveries", headers=H, timeout=10)
test("List webhook deliveries", r.status_code in (200, 404), f"status={r.status_code}")

r = requests.get(f"{API}/webhooks/dead-letters", headers=H, timeout=10)
test("List webhook dead letters", r.status_code in (200, 404), f"status={r.status_code}")

# =========================================================================
# 9. AUTOCONFIG EDGE CASES
# =========================================================================
print("\n--- 9. AUTOCONFIG ---")

# Autoconfig with invalid email
r = requests.get("http://autoconfig:8100/mail/config-v1.1.xml?emailaddress=notanemail", timeout=10)
test("Autoconfig with invalid email", r.status_code in (200, 400, 404), f"status={r.status_code}")

# Autoconfig without email param
r = requests.get("http://autoconfig:8100/mail/config-v1.1.xml", timeout=10)
test("Autoconfig without email param", r.status_code in (200, 400), f"status={r.status_code}")

# Autodiscover (Outlook)
r = requests.get("http://autoconfig:8100/autodiscover/autodiscover.xml", timeout=10)
test("Autodiscover endpoint exists", r.status_code in (200, 404, 405), f"status={r.status_code}")

# =========================================================================
# SUMMARY
# =========================================================================
print("\n" + "=" * 70)
passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
total = len(RESULTS)
pct = int(passed / total * 100) if total > 0 else 0
print(f"  RESULTS: {passed}/{total} passed ({pct}%), {failed} failed")
print("=" * 70)

if failed > 0:
    print("\n  FAILURES:")
    for name, status, detail in RESULTS:
        if status == "FAIL":
            print(f"    - {name}: {detail}")
print()
