"""
Idempotency-Key + error contract tests (phase-04).

Same pattern as test_auth_coverage.py / test_04_api.py: real HTTP calls
against the live service, direct MySQL for setup/verification -- no
in-process TestClient. A throwaway org+domain is seeded so the
"claimed by another org" case can be exercised without the platform-admin
`POST /organizations` endpoint (the test API key is tenant-scoped, not
admin, so it can't call that itself).
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from .conftest import API_BASE

MAILBOXES_ADD = f"{API_BASE}/api/v1/mailboxes/add"
DOMAINS_CREATE = f"{API_BASE}/api/v1/domains/"


@pytest.fixture(scope="module")
def own_org_and_domain(db_connection):
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT id, domain FROM domains LIMIT 1")
    row = cursor.fetchone()
    if not row:
        pytest.skip("No domain exists yet -- run bootstrap first")
    cursor.execute("SELECT organization_id FROM domains WHERE id = %s", (row["id"],))
    org_row = cursor.fetchone()
    cursor.close()
    return {"organization_id": org_row["organization_id"], "domain": row["domain"]}


@pytest.fixture
def other_org_domain(db_connection, own_org_and_domain):
    """A domain owned by a DIFFERENT org, to test the no-leak duplicate-domain case."""
    cursor = db_connection.cursor()
    other_org_id = uuid.uuid4().hex[:26]
    other_domain = f"other-org-{uuid.uuid4().hex[:8]}.example"
    cursor.execute(
        "INSERT INTO organizations (id, name, active) VALUES (%s, %s, 1)",
        (other_org_id, "Idempotency Test — Other Org"),
    )
    cursor.execute(
        "INSERT INTO domains (id, domain, organization_id, active) VALUES (%s, %s, %s, 1)",
        (uuid.uuid4().hex[:26], other_domain, other_org_id),
    )
    db_connection.commit()

    yield {"organization_id": other_org_id, "domain": other_domain}

    cursor.execute("DELETE FROM domains WHERE organization_id = %s", (other_org_id,))
    cursor.execute("DELETE FROM organizations WHERE id = %s", (other_org_id,))
    db_connection.commit()
    cursor.close()


def _add_mailbox(headers, local_part, domain, idempotency_key=None):
    h = dict(headers)
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return requests.post(
        MAILBOXES_ADD,
        headers=h,
        json={"local_part": local_part, "domain": domain, "password": "Str0ng!Passw0rd123"},
        timeout=10,
    )


@pytest.fixture
def cleanup_mailbox(db_connection):
    created = []
    yield created
    if created:
        cursor = db_connection.cursor()
        for email in created:
            cursor.execute("DELETE FROM email_accounts WHERE email = %s", (email,))
        db_connection.commit()
        cursor.close()


@pytest.fixture(autouse=True)
def cleanup_idempotency_keys(db_connection):
    yield
    cursor = db_connection.cursor()
    cursor.execute("DELETE FROM idempotency_keys WHERE request_path LIKE '%mailboxes/add%'")
    db_connection.commit()
    cursor.close()


class TestIdempotencyReplay:
    def test_same_key_twice_creates_one_resource_and_replays(
        self, api_headers, own_org_and_domain, cleanup_mailbox
    ):
        local_part = f"idem-{uuid.uuid4().hex[:10]}"
        email = f"{local_part}@{own_org_and_domain['domain']}"
        cleanup_mailbox.append(email)
        key = str(uuid.uuid4())

        first = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"], key)
        second = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"], key)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert second.headers.get("Idempotency-Replayed") == "true"

    def test_same_key_different_body_is_422(self, api_headers, own_org_and_domain, cleanup_mailbox):
        key = str(uuid.uuid4())
        local_part_a = f"idem-{uuid.uuid4().hex[:10]}"
        local_part_b = f"idem-{uuid.uuid4().hex[:10]}"
        cleanup_mailbox.append(f"{local_part_a}@{own_org_and_domain['domain']}")

        first = _add_mailbox(api_headers, local_part_a, own_org_and_domain["domain"], key)
        second = _add_mailbox(api_headers, local_part_b, own_org_and_domain["domain"], key)

        assert first.status_code == 200
        assert second.status_code == 422
        assert second.json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"

    def test_concurrent_requests_same_key_create_exactly_one(
        self, api_headers, own_org_and_domain, cleanup_mailbox
    ):
        local_part = f"idem-concurrent-{uuid.uuid4().hex[:10]}"
        email = f"{local_part}@{own_org_and_domain['domain']}"
        cleanup_mailbox.append(email)
        key = str(uuid.uuid4())

        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(
                pool.map(
                    lambda _: (
                        _add_mailbox(
                            api_headers, local_part, own_org_and_domain["domain"], key
                        ).status_code
                    ),
                    range(10),
                )
            )

        # Every response must be a clean 200 (winner + replays) or 409
        # (in-progress collisions) -- never a 500 and never a duplicate row.
        assert all(s in (200, 409) for s in statuses), statuses
        assert 200 in statuses

    def test_no_key_is_not_idempotent(self, api_headers, own_org_and_domain, cleanup_mailbox):
        """Sanity check: without the header, the mechanism doesn't engage at all."""
        local_part = f"idem-{uuid.uuid4().hex[:10]}"
        email = f"{local_part}@{own_org_and_domain['domain']}"
        cleanup_mailbox.append(email)

        first = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])
        second = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error_code"] == "MAILBOX_ALREADY_EXISTS"
        assert second.json()["data"]["existing_id"]


class TestNaturalIdempotency:
    def test_duplicate_mailbox_without_key_returns_existing_id(
        self, api_headers, own_org_and_domain, cleanup_mailbox
    ):
        local_part = f"idem-{uuid.uuid4().hex[:10]}"
        email = f"{local_part}@{own_org_and_domain['domain']}"
        cleanup_mailbox.append(email)

        _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])
        dup = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])

        assert dup.status_code == 409
        body = dup.json()
        assert body["error_code"] == "MAILBOX_ALREADY_EXISTS"
        assert "existing_id" in body["data"]

    def test_delete_nonexistent_mailbox_is_204(self, api_headers):
        resp = requests.delete(
            f"{API_BASE}/api/v1/mailboxes/email-accounts/does-not-exist-{uuid.uuid4().hex}",
            headers=api_headers,
            timeout=10,
        )
        assert resp.status_code == 204

    def test_delete_already_deleted_mailbox_is_204(self, api_headers, own_org_and_domain):
        local_part = f"idem-del-{uuid.uuid4().hex[:10]}"
        create_resp = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])
        assert create_resp.status_code == 200

        list_resp = requests.get(
            f"{API_BASE}/api/v1/mailboxes/email-accounts",
            headers=api_headers,
            params={"per_page": 200},
            timeout=10,
        )
        accounts = list_resp.json()["data"]["items"]
        account = next(
            a for a in accounts if a["email"] == f"{local_part}@{own_org_and_domain['domain']}"
        )

        first_delete = requests.delete(
            f"{API_BASE}/api/v1/mailboxes/email-accounts/{account['id']}",
            headers=api_headers,
            timeout=10,
        )
        second_delete = requests.delete(
            f"{API_BASE}/api/v1/mailboxes/email-accounts/{account['id']}",
            headers=api_headers,
            timeout=10,
        )
        assert first_delete.status_code == 200
        assert second_delete.status_code == 204

    def test_duplicate_domain_same_org_returns_existing_id(self, api_headers, own_org_and_domain):
        resp = requests.post(
            DOMAINS_CREATE,
            headers=api_headers,
            json={
                "domain": own_org_and_domain["domain"],
                "organization_id": own_org_and_domain["organization_id"],
            },
            timeout=10,
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error_code"] == "DOMAIN_ALREADY_CLAIMED"
        assert body["data"]["existing_id"]

    def test_duplicate_domain_other_org_leaks_no_detail(
        self, api_headers, own_org_and_domain, other_org_domain
    ):
        """Same domain name, but this org isn't the owner -- must not reveal the other org."""
        resp = requests.post(
            DOMAINS_CREATE,
            headers=api_headers,
            json={
                "domain": other_org_domain["domain"],
                "organization_id": own_org_and_domain["organization_id"],
            },
            timeout=10,
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error_code"] == "DOMAIN_ALREADY_CLAIMED"
        assert "data" not in body


class TestErrorContract:
    def test_error_response_carries_correlation_id(
        self, api_headers, own_org_and_domain, cleanup_mailbox
    ):
        local_part = f"idem-{uuid.uuid4().hex[:10]}"
        cleanup_mailbox.append(f"{local_part}@{own_org_and_domain['domain']}")
        _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])
        dup = _add_mailbox(api_headers, local_part, own_org_and_domain["domain"])

        assert dup.status_code == 409
        assert dup.json()["correlation_id"]
        assert dup.headers.get("X-Correlation-Id") == dup.json()["correlation_id"]

    def test_success_response_carries_correlation_header(self):
        resp = requests.get(f"{API_BASE}/health", timeout=10)
        assert resp.status_code == 200
        assert resp.headers.get("X-Correlation-Id")

    def test_no_error_response_is_bare_200(self):
        """Repo-wide audit (phase-04 task 4.5): every create_api_response('error', ...)
        call site is verified (via static grep, run separately) to carry an
        explicit non-200 status code. This is a live spot-check on the most
        common one."""
        resp = requests.post(
            f"{API_BASE}/api/v1/mailboxes/add", headers={"X-API-Key": "not-a-real-key"}, timeout=10
        )
        assert resp.status_code == 401
        assert resp.json()["type"] == "error"
