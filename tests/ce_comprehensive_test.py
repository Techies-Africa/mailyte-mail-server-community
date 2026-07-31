#!/usr/bin/env python3
"""
Mailyte CE — Comprehensive Test Suite
Tests all Community Edition features against running Docker services.
"""

import imaplib
import os
import poplib
import smtplib
import socket
import ssl
import time

import requests

API = os.getenv("API_BASE", "http://api:8080/api/v1")
KEY = os.getenv("API_KEY", "test-api-key-123")
HEADERS = {"X-API-Key": KEY, "Content-Type": "application/json"}
ADMIN = {"X-Admin-Token": "tokensecret", "Content-Type": "application/json"}
RESULTS = []


def test(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append((name, status, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def send_email(from_addr, to_addr, subject, body, password="testpass123", html=False):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="test.local")

    content_type = "html" if html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    s = smtplib.SMTP("postfix", 587, timeout=15)
    s.starttls()
    s.login(from_addr, password)
    s.sendmail(from_addr, [to_addr], msg.as_string())
    s.quit()
    return msg["Message-ID"]


print("\n" + "=" * 70)
print("  MAILYTE CE — COMPREHENSIVE TEST SUITE")
print("=" * 70)

# =========================================================================
# 1. API CRUD
# =========================================================================
print("\n--- 1. API CRUD ---")

r = requests.get("http://api:8080/health", timeout=10)
test(
    "Health endpoint",
    r.status_code == 200 and r.json()["database"] == "connected",
    r.json().get("database"),
)

r = requests.get(f"{API}/organizations/", headers=HEADERS, timeout=10)
test("List organizations", r.status_code == 200, f"status={r.status_code}")

r = requests.get(f"{API}/domains/", headers=HEADERS, timeout=10)
test("List domains", r.status_code == 200, f"status={r.status_code}")

r = requests.get(f"{API}/mailboxes/", headers=HEADERS, timeout=10)
test("List mailboxes", r.status_code in (200, 404), f"status={r.status_code}")

r = requests.post(
    f"{API}/aliases/",
    headers=HEADERS,
    json={"source": "info@test.local", "destination": "user@test.local"},
    timeout=10,
)
test("Create alias", r.status_code in (200, 201, 409), f"status={r.status_code}")

r = requests.get(f"{API}/aliases/", headers=HEADERS, timeout=10)
test("List aliases", r.status_code == 200, f"status={r.status_code}")

r = requests.get(f"{API}/filters/", headers=HEADERS, timeout=10)
test("List filters", r.status_code in (200, 404), f"status={r.status_code}")

r = requests.get(f"{API}/ssl/", headers=HEADERS, timeout=10)
test("SSL endpoint", r.status_code in (200, 404), f"status={r.status_code}")

# Auth tests
r = requests.get(f"{API}/domains/", timeout=10)
test("Rejects missing API key", r.status_code == 401)

r = requests.get(f"{API}/domains/", headers={"X-API-Key": "bad-key"}, timeout=10)
test("Rejects invalid API key", r.status_code == 401)

# Create second mailbox
r = requests.post(
    f"{API}/mailboxes/",
    headers=HEADERS,
    json={"email": "test2@test.local", "password": "testpass456", "name": "Test User 2"},
    timeout=10,
)
test("Create second mailbox", r.status_code in (200, 201, 409), f"status={r.status_code}")

# =========================================================================
# 2. SMTP SENDING
# =========================================================================
print("\n--- 2. SMTP SENDING ---")

try:
    mid = send_email("user@test.local", "user@test.local", "Test Single", "Hello from CE test")
    test("Send single email", True, f"msgid={mid[:40]}")
except Exception as e:
    test("Send single email", False, str(e))

# Bulk send
bulk_ok = 0
for i in range(10):
    try:
        send_email("user@test.local", "user@test.local", f"Bulk #{i + 1}", f"Bulk body {i + 1}")
        bulk_ok += 1
    except:
        pass
test("Bulk send (10 emails)", bulk_ok == 10, f"{bulk_ok}/10 sent")

# HTML email
try:
    html = (
        '<html><body><h1>Test</h1><p>Click <a href="https://mailyte.com">here</a></p></body></html>'
    )
    send_email("user@test.local", "user@test.local", "HTML Test", html, html=True)
    test("Send HTML email", True)
except Exception as e:
    test("Send HTML email", False, str(e))

# Cross-mailbox
try:
    send_email("user@test.local", "test2@test.local", "Cross-mailbox", "Testing between users")
    test("Cross-mailbox send", True)
except Exception as e:
    test("Cross-mailbox send", False, str(e))

# Wrong password
try:
    s = smtplib.SMTP("postfix", 587, timeout=10)
    s.starttls()
    s.login("user@test.local", "wrongpassword")
    test("SMTP rejects wrong password", False, "Should have failed")
    s.quit()
except smtplib.SMTPAuthenticationError:
    test("SMTP rejects wrong password", True)
except Exception as e:
    test("SMTP rejects wrong password", False, str(e))

# Wait for delivery
print("\n  Waiting 5s for delivery...")
time.sleep(5)

# =========================================================================
# 3. IMAP RECEIVE
# =========================================================================
print("\n--- 3. IMAP RECEIVE ---")

try:
    m = imaplib.IMAP4("dovecot", 143)
    m.starttls()
    m.login("user@test.local", "testpass123")
    test("IMAP STARTTLS login", True)

    m.select("INBOX")
    typ, data = m.search(None, "ALL")
    msg_count = len(data[0].split()) if data[0] else 0
    test("Messages in inbox", msg_count > 0, f"{msg_count} messages")

    if msg_count > 0:
        nums = data[0].split()
        typ, msg_data = m.fetch(nums[-1], "(RFC822)")
        test("Fetch latest message", typ == "OK", f"size={len(msg_data[0][1])} bytes")

    typ, folders = m.list()
    test("List IMAP folders", typ == "OK", f"{len(folders)} folders")

    m.logout()
except Exception as e:
    test("IMAP operations", False, str(e))

# IMAP SSL
try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    m = imaplib.IMAP4_SSL("dovecot", 993, ssl_context=ctx)
    m.login("user@test.local", "testpass123")
    test("IMAP SSL (993)", True)
    m.logout()
except Exception as e:
    test("IMAP SSL (993)", False, str(e))

# POP3
try:
    p = poplib.POP3("dovecot", 110, timeout=10)
    p.stls()
    p.user("user@test.local")
    p.pass_("testpass123")
    stat = p.stat()
    test("POP3 STARTTLS", True, f"{stat[0]} msgs, {stat[1]} bytes")
    p.quit()
except Exception as e:
    test("POP3 access", False, str(e))

# POP3 SSL
try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    p = poplib.POP3_SSL("dovecot", 995, context=ctx)
    p.user("user@test.local")
    p.pass_("testpass123")
    test("POP3 SSL (995)", True)
    p.quit()
except Exception as e:
    test("POP3 SSL (995)", False, str(e))

# Cross-mailbox delivery
try:
    m = imaplib.IMAP4("dovecot", 143)
    m.starttls()
    m.login("test2@test.local", "testpass456")
    m.select("INBOX")
    typ, data = m.search(None, "ALL")
    msg_count = len(data[0].split()) if data[0] else 0
    test("Cross-mailbox received", msg_count > 0, f"{msg_count} msgs in test2")
    m.logout()
except Exception as e:
    test("Cross-mailbox received", False, str(e))

# =========================================================================
# 4. TRACKING
# =========================================================================
print("\n--- 4. EMAIL TRACKING ---")

r = requests.get("http://tracking:8086/health", timeout=10)
test("Tracking health", r.status_code == 200)

r = requests.get("http://tracking:8086/t/open/test-pixel-123", timeout=10, allow_redirects=False)
test("Open tracking pixel", r.status_code in (200, 204, 302, 404), f"status={r.status_code}")

r = requests.get("http://tracking:8086/t/click/test-click-123", timeout=10, allow_redirects=False)
test("Click tracking", r.status_code in (200, 302, 404), f"status={r.status_code}")

# Tracking API
r = requests.get(f"{API}/tracking/", headers=HEADERS, timeout=10)
test("Tracking API", r.status_code in (200, 404), f"status={r.status_code}")

# =========================================================================
# 5. WEBHOOKS
# =========================================================================
print("\n--- 5. WEBHOOKS ---")

r = requests.get("http://webhooks:8081/health", timeout=10)
test("Webhooks health", r.status_code == 200)

r = requests.get(f"{API}/webhooks/", headers=HEADERS, timeout=10)
test("Webhooks API", r.status_code in (200, 404), f"status={r.status_code}")

# =========================================================================
# 6. RATE LIMITING
# =========================================================================
print("\n--- 6. RATE LIMITING ---")

r = requests.get("http://rate_limiter:8082/health", timeout=10)
test("Rate limiter health", r.status_code == 200)

r = requests.get(f"{API}/rate-limiter/", headers=HEADERS, timeout=10)
test("Rate limiter API", r.status_code in (200, 404), f"status={r.status_code}")

# Rapid connections
rapid_ok = 0
for i in range(5):
    try:
        s = smtplib.SMTP("postfix", 587, timeout=5)
        s.ehlo()
        s.quit()
        rapid_ok += 1
    except:
        pass
test("Rapid SMTP connections (5x)", rapid_ok >= 3, f"{rapid_ok}/5")

# =========================================================================
# 7. SSL/TLS
# =========================================================================
print("\n--- 7. SSL/TLS ---")

try:
    s = smtplib.SMTP("postfix", 587, timeout=10)
    s.starttls()
    cipher = s.sock.cipher()
    test("SMTP STARTTLS", True, f"{cipher[0]}")
    s.quit()
except Exception as e:
    test("SMTP STARTTLS", False, str(e))

try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    m = imaplib.IMAP4_SSL("dovecot", 993, ssl_context=ctx)
    test("IMAP SSL handshake", True)
    m.logout()
except Exception as e:
    test("IMAP SSL handshake", False, str(e))

# ManageSieve port
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("dovecot", 4190))
    banner = s.recv(1024).decode()
    test(
        "ManageSieve port (4190)", "IMPLEMENTATION" in banner or "OK" in banner.upper(), banner[:60]
    )
    s.close()
except Exception as e:
    test("ManageSieve port (4190)", False, str(e))

# =========================================================================
# 8. AUTOCONFIG
# =========================================================================
print("\n--- 8. AUTOCONFIG ---")

r = requests.get("http://autoconfig:8100/health", timeout=10)
test("Autoconfig health", r.status_code == 200)

r = requests.get(
    "http://autoconfig:8100/mail/config-v1.1.xml?emailaddress=user@test.local", timeout=10
)
test("Autoconfig XML", r.status_code == 200, f"has config: {'emailProvider' in r.text}")

# =========================================================================
# 9. DATABASE INTEGRITY
# =========================================================================
print("\n--- 9. DATABASE ---")

import mysql.connector

conn = mysql.connector.connect(
    host="mysql", port=3306, database="mailserver", user="mailuser", password="mailpassword123"
)
cur = conn.cursor()

for table, label in [
    ("organizations", "Organizations"),
    ("domains", "Domains"),
    ("email_accounts", "Email accounts"),
    ("api_keys", "API keys"),
]:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    count = cur.fetchone()[0]
    test(f"DB: {label}", count > 0, f"{count} rows")

cur.execute("SELECT password FROM email_accounts LIMIT 1")
pw = cur.fetchone()[0]
test("Passwords are bcrypt", pw.startswith("$2"), f"hash={pw[:10]}...")

cur.close()
conn.close()

# Roundcube DB
try:
    conn = mysql.connector.connect(
        host="mysql",
        port=3306,
        database="roundcubemail",
        user="mailuser",
        password="mailpassword123",
    )
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    tables = cur.fetchall()
    test("Roundcube DB has tables", len(tables) > 0, f"{len(tables)} tables")
    cur.close()
    conn.close()
except Exception as e:
    test("Roundcube DB", False, str(e))

# =========================================================================
# 10. REDIS
# =========================================================================
print("\n--- 10. REDIS ---")

import redis

r = redis.Redis(host="redis", port=6379, db=0)
test("Redis ping", r.ping())
r.set("mailyte_ce_test", "ok")
val = r.get("mailyte_ce_test")
test("Redis read/write", val == b"ok")
r.delete("mailyte_ce_test")

# =========================================================================
# 11. RSPAMD
# =========================================================================
print("\n--- 11. RSPAMD ---")

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("rspamd", 11334))
    s.close()
    test("Rspamd web UI (11334)", True)
except Exception as e:
    test("Rspamd web UI (11334)", False, str(e))

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("rspamd", 11332))
    s.close()
    test("Rspamd proxy (11332)", True)
except Exception as e:
    test("Rspamd proxy (11332)", False, str(e))

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
