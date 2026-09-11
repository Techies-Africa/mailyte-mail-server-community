"""
Privilege boundary regression guard (phase-06 task 6.5, ADR-002 §8).

Automates ADR-002's non-negotiable test -- the three properties that must
hold for every one of the 172 (now 176 with phase-05/06 additions) endpoints:

  1. A valid TENANT credential (scope='organization') must never reach a
     platform-only endpoint -- 403 or 404, never 200.
  2. A TENANT credential must never be able to override which org it's
     scoped to via a client-supplied query/path parameter.
  3. A TENANT credential requesting another org's resource by ID gets 404,
     never 403 (403 would confirm the resource exists).

Runs against the live service (this repo's established integration-test
pattern -- see conftest.py's docstring), not an in-process TestClient, same
as test_auth_coverage.py. Creates its own two isolated orgs/keys via direct
SQL (mirroring conftest.py's existing db_connection fixture) rather than
reusing the dev stack's bootstrapped org -- POST /api/v1/bootstrap is
single-use and already consumed on any stack this runs against.
"""

import hashlib
import secrets

import pytest
import requests

from .conftest import API_BASE, DB_HOST, DB_NAME, DB_PASS, DB_PORT, DB_USER

# Mirrors plans/01-mailyte-email-server/endpoint-scope-matrix.md's
# "Platform-only" table exactly -- the two must be kept in sync. A path
# segment written {like_this} is a literal placeholder substituted below;
# any non-empty string is fine since these all must 403/404 before ever
# touching real data.
PLATFORM_ONLY = [
    ("GET", "/api/v1/monitoring/health"),
    ("GET", "/api/v1/monitoring/services"),
    ("GET", "/api/v1/monitoring/services/postfix"),
    ("GET", "/api/v1/monitoring/metrics"),
    ("GET", "/api/v1/monitoring/stats"),
    ("POST", "/api/v1/monitoring/services/postfix/restart"),
    ("POST", "/api/v1/monitoring/auto-heal"),
    ("POST", "/api/v1/monitoring/webhooks/test"),
    ("GET", "/api/v1/organizations/"),
    ("POST", "/api/v1/organizations/"),
    ("DELETE", "/api/v1/organizations/does-not-matter"),
    ("PUT", "/api/v1/organizations/does-not-matter/quotas"),
    ("POST", "/api/v1/reseller/sub-organizations"),
    ("GET", "/api/v1/reseller/sub-organizations"),
    ("GET", "/api/v1/reseller/sub-organizations/does-not-matter"),
    ("PUT", "/api/v1/reseller/sub-organizations/does-not-matter/plan"),
    ("DELETE", "/api/v1/reseller/sub-organizations/does-not-matter"),
    ("GET", "/api/v1/reseller/billing/summary"),
    ("GET", "/api/v1/ssl/status"),
    ("GET", "/api/v1/ssl/certificates"),
    ("GET", "/api/v1/ssl/certificates/example.test"),
    ("GET", "/api/v1/ssl/accounts"),
    ("GET", "/api/v1/queue/queue/status"),
    ("GET", "/api/v1/queue/queue/domain/example.test"),
    ("GET", "/api/v1/queue/mail-queue/deferred"),
    ("POST", "/api/v1/queue/mail-queue/flush"),
    ("GET", "/api/v1/queue/queue/jobs/example.test"),
    ("GET", "/api/v1/queue/queue/health"),
    ("GET", "/api/v1/whitelabel/config/does-not-matter"),
    ("PUT", "/api/v1/whitelabel/config/does-not-matter"),
    ("POST", "/api/v1/whitelabel/config/does-not-matter/verify-domain"),
    ("GET", "/api/v1/whitelabel/config/does-not-matter/preview"),
    ("DELETE", "/api/v1/whitelabel/config/does-not-matter"),
    ("POST", "/api/v1/compliance/data-export/nobody@example.test"),
    ("GET", "/api/v1/compliance/data-export/nobody@example.test/status"),
    ("POST", "/api/v1/compliance/data-erasure/nobody@example.test"),
    ("GET", "/api/v1/compliance/consent/nobody@example.test"),
    ("POST", "/api/v1/compliance/consent/nobody@example.test"),
    ("GET", "/api/v1/compliance/audit-log"),
    ("GET", "/api/v1/message-trace/trace"),
    ("GET", "/api/v1/message-trace/trace/does-not-matter"),
    ("GET", "/api/v1/message-trace/quarantine"),
    ("POST", "/api/v1/message-trace/quarantine/does-not-matter/release"),
    ("POST", "/api/v1/message-trace/quarantine/does-not-matter/block"),
    ("GET", "/api/v1/message-trace/audit"),
    ("POST", "/api/v1/rate-limiter/rate-limits/domain/example.test"),
    ("POST", "/api/v1/rate-limiter/rate-limits/mailbox/nobody@example.test"),
    ("POST", "/api/v1/rate-limiter/rate-limits/reset"),
    ("POST", "/api/v1/rate-limiter/quotas/domain/example.test"),
    ("POST", "/api/v1/storage/quotas/domain/example.test"),
    ("POST", "/api/v1/storage/quotas/mailbox/nobody@example.test"),
    ("POST", "/api/v1/storage/cleanup"),
    ("GET", "/api/v1/storage/usage/summary"),
    # New platform-only endpoints added this phase.
    ("GET", "/api/v1/platform/auth/me"),
    ("POST", "/api/v1/platform/auth/logout"),
]


def _random_ulid_like() -> str:
    """26-char id, matching the fallback pattern already used in this repo
    (mailboxes.py: `str(uuid.uuid4()).replace('-', '')[:26]`) -- good enough
    for a throwaway test row's primary key, not a real ULID."""
    return secrets.token_hex(16)[:26]


@pytest.fixture(scope="module")
def db_conn():
    import mysql.connector

    conn = mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASS
    )
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def two_orgs(db_conn):
    """Two isolated organizations, each with one tenant-scope API key
    (read+write, no admin_access). Org B additionally gets one domain and
    one mailbox, for the cross-org-404 test. Cleaned up in reverse FK order
    after the module's tests finish."""
    cur = db_conn.cursor(dictionary=True)

    org_a_id, org_b_id = _random_ulid_like(), _random_ulid_like()
    key_a, key_b = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    domain_b_id, mailbox_b_id = _random_ulid_like(), _random_ulid_like()
    domain_b_name = f"privtest-{secrets.token_hex(4)}.test"
    mailbox_b_email = f"user@{domain_b_name}"

    cur.execute(
        "INSERT INTO organizations (id, name, active) VALUES (%s, %s, 1)",
        (org_a_id, "privtest-org-a"),
    )
    cur.execute(
        "INSERT INTO organizations (id, name, active) VALUES (%s, %s, 1)",
        (org_b_id, "privtest-org-b"),
    )
    for key, org_id in ((key_a, org_a_id), (key_b, org_b_id)):
        cur.execute(
            "INSERT INTO api_keys (id, key_id, key_hash, name, permissions, organization_id, scope, active) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'organization', 1)",
            (
                _random_ulid_like(),
                key,
                hashlib.sha256(key.encode()).hexdigest(),
                "privtest-key",
                '{"read": true, "write": true}',
                org_id,
            ),
        )
    cur.execute(
        "INSERT INTO domains (id, domain, organization_id, active) VALUES (%s, %s, %s, 1)",
        (domain_b_id, domain_b_name, org_b_id),
    )
    cur.execute(
        "INSERT INTO email_accounts (id, email, local_part, domain_id, organization_id, password, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'active')",
        (mailbox_b_id, mailbox_b_email, "user", domain_b_id, org_b_id, "not-a-real-bcrypt-hash"),
    )
    db_conn.commit()

    yield {
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "key_a": key_a,
        "key_b": key_b,
        "mailbox_b_id": mailbox_b_id,
        "mailbox_b_email": mailbox_b_email,
    }

    cur.execute("DELETE FROM email_accounts WHERE id = %s", (mailbox_b_id,))
    cur.execute("DELETE FROM domains WHERE id = %s", (domain_b_id,))
    cur.execute("DELETE FROM api_keys WHERE key_id IN (%s, %s)", (key_a, key_b))
    cur.execute("DELETE FROM organizations WHERE id IN (%s, %s)", (org_a_id, org_b_id))
    db_conn.commit()
    cur.close()


def test_tenant_key_cannot_reach_platform_endpoints(two_orgs):
    """ADR-002 §8, property 1. Every platform-only endpoint must reject a
    tenant credential outright, regardless of its own read/write/admin
    permission flags."""
    headers = {"X-API-Key": two_orgs["key_a"], "Content-Type": "application/json"}
    leaks = []
    for method, path in PLATFORM_ONLY:
        r = requests.request(
            method,
            f"{API_BASE}{path}",
            headers=headers,
            json={} if method in ("POST", "PUT") else None,
            timeout=15,
        )
        if r.status_code < 400:
            leaks.append(f"{method} {path} -> {r.status_code}")
    assert not leaks, (
        f"PRIVILEGE ESCALATION -- tenant key reached platform-only endpoint(s): {leaks}"
    )


def test_tenant_cannot_override_org_via_query_param(two_orgs):
    """ADR-002 §8, property 2. A tenant credential for org A supplying org
    B's id via a query parameter must still only see org A's data -- the
    parameter is silently ignored, not honoured (task 6.4's org_filter)."""
    headers = {"X-API-Key": two_orgs["key_a"]}
    r = requests.get(
        f"{API_BASE}/api/v1/mailboxes/email-accounts",
        params={"organization_id": two_orgs["org_b_id"]},
        headers=headers,
        timeout=15,
    )
    assert r.status_code == 200
    for row in r.json().get("data", {}).get("items", []):
        assert row["organization_id"] != two_orgs["org_b_id"], (
            "org_id query param was honoured instead of ignored -- cross-org data leak"
        )


def test_cross_org_resource_returns_404_not_403(two_orgs):
    """ADR-002 §8, property 3. Org A's key requesting org B's mailbox by ID
    gets 404 -- never 403, which would confirm the resource exists in
    someone else's org (conventions.md §8)."""
    headers = {"X-API-Key": two_orgs["key_a"]}
    r = requests.get(
        f"{API_BASE}/api/v1/mailboxes/email-accounts/{two_orgs['mailbox_b_id']}",
        headers=headers,
        timeout=15,
    )
    assert r.status_code == 404, f"expected 404 for cross-org resource access, got {r.status_code}"


def test_platform_credential_sees_across_all_orgs(two_orgs, db_conn):
    """ADR-002 §8's positive case: a platform credential querying a
    tenant-scoped resource WITHOUT specifying an org sees data across all
    organizations -- this is intentional, not a leak. Creates a throwaway
    platform-scope API key directly (bypassing operator login/MFA, which
    is exercised end-to-end by test_platform_auth.py instead) since this
    test only needs to prove org_filter's platform branch, not the login
    flow."""
    cur = db_conn.cursor(dictionary=True)
    platform_key = secrets.token_urlsafe(24)
    key_row_id = _random_ulid_like()
    cur.execute(
        "INSERT INTO api_keys (id, key_id, key_hash, name, permissions, organization_id, scope, active) "
        "VALUES (%s, %s, %s, %s, %s, NULL, 'platform', 1)",
        (
            key_row_id,
            platform_key,
            hashlib.sha256(platform_key.encode()).hexdigest(),
            "privtest-platform-key",
            '{"read": true, "write": true}',
        ),
    )
    db_conn.commit()
    try:
        r = requests.get(
            f"{API_BASE}/api/v1/mailboxes/email-accounts",
            headers={"X-API-Key": platform_key},
            timeout=15,
        )
        assert r.status_code == 200
        org_ids_seen = {row["organization_id"] for row in r.json().get("data", {}).get("items", [])}
        assert two_orgs["org_b_id"] in org_ids_seen, (
            "platform credential did not see org B's mailbox in an unfiltered list -- "
            "org_filter's platform branch is not returning all orgs as intended"
        )
    finally:
        cur.execute("DELETE FROM api_keys WHERE key_id = %s", (platform_key,))
        db_conn.commit()
        cur.close()
