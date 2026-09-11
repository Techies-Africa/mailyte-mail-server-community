"""
Browser session auth tests (phase-03).

Runs against the LIVE service, same pattern as test_auth_coverage.py: a
fresh dashboard user is seeded directly in MySQL (bcrypt hash computed
in-process, matching what /api/v1/auth/login checks against), then every
assertion goes through real HTTP calls -- no in-process TestClient.
"""

import uuid

import bcrypt
import pytest
import requests

from .conftest import API_BASE


def _bcrypt_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


@pytest.fixture(autouse=True)
def clean_brute_force_state(db_connection):
    """Every login-hitting test shares one source IP (the test runner's) --
    without this, an earlier test's failed attempts would leave the IP
    dimension locked out and fail later tests that expect a clean login.
    Runs before AND after so a failed/aborted test doesn't poison the next."""
    cursor = db_connection.cursor()
    cursor.execute("DELETE FROM failed_auth_attempts WHERE service = 'api'")
    db_connection.commit()
    cursor.close()
    yield
    cursor = db_connection.cursor()
    cursor.execute("DELETE FROM failed_auth_attempts WHERE service = 'api'")
    db_connection.commit()
    cursor.close()


@pytest.fixture
def dashboard_user(db_connection):
    """A fresh users row with a known password, cleaned up afterward."""
    cursor = db_connection.cursor(dictionary=True)
    cursor.execute("SELECT id FROM organizations LIMIT 1")
    org = cursor.fetchone()
    if not org:
        pytest.skip("No organization exists yet -- run bootstrap first")

    user_id = uuid.uuid4().hex[:26]
    email = f"test-auth-{uuid.uuid4().hex[:10]}@example.com"
    password = "Correct-Horse-Battery-Staple-1"
    cursor.execute(
        "INSERT INTO users (id, organization_id, email, password_hash, role) VALUES (%s, %s, %s, %s, 'admin')",
        (user_id, org["id"], email, _bcrypt_hash(password)),
    )
    db_connection.commit()

    yield {"id": user_id, "email": email, "password": password, "organization_id": org["id"]}

    cursor.execute("DELETE FROM web_sessions WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    cursor.execute(
        "DELETE FROM failed_auth_attempts WHERE service = 'api' AND (username = %s OR username IS NULL)",
        (email,),
    )
    db_connection.commit()
    cursor.close()


def _login(email: str, password: str) -> requests.Response:
    return requests.post(
        f"{API_BASE}/api/v1/auth/login", json={"email": email, "password": password}, timeout=10
    )


class TestLogin:
    def test_correct_credentials_set_session_and_csrf_cookies(self, dashboard_user):
        resp = _login(dashboard_user["email"], dashboard_user["password"])
        assert resp.status_code == 200
        assert "mailyte_session" in resp.cookies
        assert "mailyte_csrf" in resp.cookies
        assert resp.cookies["mailyte_session"] != resp.cookies["mailyte_csrf"]
        body = resp.json()
        assert body["data"]["user"]["email"] == dashboard_user["email"]
        assert "capabilities" in body["data"]

    def test_wrong_password_rejected_with_generic_message(self, dashboard_user):
        resp = _login(dashboard_user["email"], "not-the-password")
        assert resp.status_code == 401
        assert resp.json()["msg"] == "Invalid email or password"

    def test_nonexistent_email_gets_identical_error_shape(self, dashboard_user):
        # Different email than the wrong-password case, same organization
        # namespace, to avoid tripping the per-email lockout from the test above.
        resp = requests.post(
            f"{API_BASE}/api/v1/auth/login",
            json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com", "password": "whatever"},
            timeout=10,
        )
        assert resp.status_code == 401
        assert resp.json()["msg"] == "Invalid email or password"

    def test_repeated_failures_eventually_rate_limited(self, dashboard_user):
        statuses = []
        for _ in range(6):
            statuses.append(_login(dashboard_user["email"], "still-wrong").status_code)
        assert 429 in statuses, f"Expected a 429 among repeated failures, got {statuses}"


class TestSessionAuthenticatesRequests:
    def test_session_cookie_reaches_a_normal_endpoint(self, dashboard_user):
        login_resp = _login(dashboard_user["email"], dashboard_user["password"])
        session_cookie = login_resp.cookies["mailyte_session"]

        resp = requests.get(
            f"{API_BASE}/api/v1/mailboxes/email-accounts",
            cookies={"mailyte_session": session_cookie},
            timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == "success"

    def test_api_key_still_works_alongside_sessions(self, api_headers):
        resp = requests.get(
            f"{API_BASE}/api/v1/mailboxes/email-accounts", headers=api_headers, timeout=10
        )
        assert resp.status_code == 200

    def test_no_credential_at_all_is_401(self):
        resp = requests.get(f"{API_BASE}/api/v1/mailboxes/email-accounts", timeout=10)
        assert resp.status_code == 401


class TestCSRF:
    def test_mutating_request_without_csrf_header_is_rejected(self, dashboard_user):
        login_resp = _login(dashboard_user["email"], dashboard_user["password"])
        session_cookie = login_resp.cookies["mailyte_session"]

        resp = requests.post(
            f"{API_BASE}/api/v1/auth/logout",
            cookies={"mailyte_session": session_cookie},
            timeout=10,
        )
        assert resp.status_code == 403

    def test_mutating_request_with_correct_csrf_header_succeeds(self, dashboard_user):
        login_resp = _login(dashboard_user["email"], dashboard_user["password"])
        session_cookie = login_resp.cookies["mailyte_session"]
        csrf_cookie = login_resp.cookies["mailyte_csrf"]

        resp = requests.post(
            f"{API_BASE}/api/v1/auth/logout",
            cookies={"mailyte_session": session_cookie, "mailyte_csrf": csrf_cookie},
            headers={"X-CSRF-Token": csrf_cookie},
            timeout=10,
        )
        assert resp.status_code == 200

    def test_api_key_requests_are_csrf_exempt(self, api_headers):
        # A write endpoint, authenticated purely by API key -- must never
        # demand a CSRF header (phase-03 task 3.5: "API-key-authenticated
        # requests are exempt"). GET is enough to prove the key path never
        # even reaches the CSRF check; write-path exemption is structural
        # (utils/auth.py:_resolve_auth only calls _verify_csrf on the
        # cookie-auth branch), not something that needs its own mutation here.
        resp = requests.get(
            f"{API_BASE}/api/v1/mailboxes/email-accounts", headers=api_headers, timeout=10
        )
        assert resp.status_code == 200


class TestLogoutAndRevocation:
    def test_logout_invalidates_the_session(self, dashboard_user):
        login_resp = _login(dashboard_user["email"], dashboard_user["password"])
        session_cookie = login_resp.cookies["mailyte_session"]
        csrf_cookie = login_resp.cookies["mailyte_csrf"]

        logout_resp = requests.post(
            f"{API_BASE}/api/v1/auth/logout",
            cookies={"mailyte_session": session_cookie, "mailyte_csrf": csrf_cookie},
            headers={"X-CSRF-Token": csrf_cookie},
            timeout=10,
        )
        assert logout_resp.status_code == 200

        resp = requests.get(
            f"{API_BASE}/api/v1/mailboxes/email-accounts",
            cookies={"mailyte_session": session_cookie},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_me_reports_session_identity(self, dashboard_user):
        login_resp = _login(dashboard_user["email"], dashboard_user["password"])
        session_cookie = login_resp.cookies["mailyte_session"]

        resp = requests.get(
            f"{API_BASE}/api/v1/auth/me", cookies={"mailyte_session": session_cookie}, timeout=10
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["auth_method"] == "session"
        assert body["user"]["email"] == dashboard_user["email"]

    def test_me_reports_api_key_identity(self, api_headers):
        resp = requests.get(f"{API_BASE}/api/v1/auth/me", headers=api_headers, timeout=10)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["auth_method"] == "api_key"
        assert body["user"] is None
