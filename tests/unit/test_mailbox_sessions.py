#!/usr/bin/env python3
"""
Unit tests for mailbox-holder session lifetimes and the require_mailbox
dependency family (worker/api/utils/mailbox_auth.py, routes/mailbox_auth.py,
and the /security region of routes/mailbox.py).

Pins the 2026-08-31 mobile-team items:

* §4  -- X-Client-Platform selects native (30d/180d) vs browser (8h/7d)
         lifetimes; the idle window is persisted on the session row; the
         per-request slide uses the STORED window, not a module constant.
* §2b -- must_change_password is refused with 403 password_change_required
         by require_mailbox, and admitted only by the password-change and
         logout variants. The MFA gate still comes first.
* §12 -- two_factor_confirmed_at is null and recovery_codes_remaining is 0
         until /2fa/confirm succeeds; /confirm records the timestamp,
         /disable clears it.

And one thing found on the way: FastAPI reads keyword-only parameters of a
dependency as QUERY parameters, so the old `require_mailbox(request, *,
allow_pending_mfa=False)` accepted `?allow_pending_mfa=true` on every
/api/v1/mailbox/* route and let a pre-TOTP session through. The dependency
family must expose no query parameters at all.

DB and IMAP are mocked; nothing here touches a network.
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response
from fastapi.dependencies.utils import get_dependant

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from routes import mailbox as mailbox_routes  # noqa: E402
from routes import mailbox_auth as auth_routes  # noqa: E402
from utils import mailbox_auth as ma  # noqa: E402

ACCOUNT_ID = "01MBOXULID00000000000000AA"
SESSION_ID = "01SESSULID00000000000000AA"
ORG_ID = "01ORGULID0000000000000000A"
EMAIL = "holder@example.com"

WEB_IDLE = timedelta(hours=8)
WEB_ABSOLUTE = timedelta(days=7)
TOLERANCE = timedelta(seconds=10)


class _Headers(dict):
    """Case-insensitive like starlette's Headers, which is what the code
    under test reads (`Authorization`, `X-Client-Platform`, `x-forwarded-for`)."""

    def __init__(self, items=None):
        super().__init__({k.lower(): v for k, v in (items or {}).items()})

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeRequest:
    def __init__(self, headers=None, method="GET", cookies=None):
        self.headers = _Headers(headers)
        self.method = method
        self.cookies = cookies or {}
        self.client = SimpleNamespace(host="203.0.113.5")
        self.state = SimpleNamespace()


def _bearer_request(**kwargs):
    return FakeRequest(headers={"Authorization": "Bearer tok"}, **kwargs)


def _session_row(**overrides):
    now = datetime.now()
    row = {
        "session_id": SESSION_ID,
        "expires_at": now + timedelta(hours=1),
        "absolute_expiry": now + timedelta(days=100),
        "revoked_at": None,
        "mfa_satisfied": 1,
        "idle_timeout_seconds": int(WEB_IDLE.total_seconds()),
        "email_account_id": ACCOUNT_ID,
        "email": EMAIL,
        "organization_id": ORG_ID,
        "status": "active",
        "must_change_password": 0,
        "password_change_reason": None,
    }
    row.update(overrides)
    return row


def _conn(fetchone=None, fetchall=None, rowcount=1):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    cursor.rowcount = rowcount
    return conn, cursor


def _executed(cursor):
    return [
        (str(c.args[0]), c.args[1] if len(c.args) > 1 else None)
        for c in cursor.execute.call_args_list
    ]


def _mailbox_ctx(**overrides):
    ctx = {
        "email_account_id": ACCOUNT_ID,
        "email": EMAIL,
        "organization_id": ORG_ID,
        "session_id": SESSION_ID,
        "must_change_password": False,
        "password_change_reason": None,
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# §4 -- platform-aware lifetimes
# ---------------------------------------------------------------------------


class TestLifetimes:
    def test_web_and_unknown_get_browser_lifetimes(self):
        assert ma.session_lifetimes(None) == (WEB_IDLE, WEB_ABSOLUTE)
        assert ma.session_lifetimes("web") == (WEB_IDLE, WEB_ABSOLUTE)

    @pytest.mark.parametrize("platform", sorted(ma.NATIVE_CLIENT_PLATFORMS))
    def test_native_platforms_get_native_lifetimes(self, platform):
        assert ma.session_lifetimes(platform) == (
            ma.MAILBOX_NATIVE_SESSION_IDLE_TIMEOUT,
            ma.MAILBOX_NATIVE_SESSION_ABSOLUTE_TIMEOUT,
        )

    def test_native_defaults_are_30_and_180_days(self):
        if os.getenv("MAILBOX_NATIVE_SESSION_IDLE_DAYS") or os.getenv(
            "MAILBOX_NATIVE_SESSION_ABSOLUTE_DAYS"
        ):
            pytest.skip("native lifetimes overridden in this environment")
        assert timedelta(days=30) == ma.MAILBOX_NATIVE_SESSION_IDLE_TIMEOUT
        assert timedelta(days=180) == ma.MAILBOX_NATIVE_SESSION_ABSOLUTE_TIMEOUT

    def test_native_lifetimes_are_longer_than_browser(self):
        assert ma.MAILBOX_NATIVE_SESSION_IDLE_TIMEOUT > WEB_IDLE
        assert ma.MAILBOX_NATIVE_SESSION_ABSOLUTE_TIMEOUT > WEB_ABSOLUTE

    @pytest.mark.parametrize(
        "raw, expected_days",
        [
            (None, 30),
            ("", 30),
            ("45", 45),
            ("  7 ", 7),
            ("abc", 30),
            ("0", 30),
            ("-3", 30),
        ],
    )
    def test_env_days_override_and_fallback(self, raw, expected_days):
        with patch.dict(os.environ):
            os.environ.pop("MAILBOX_NATIVE_SESSION_IDLE_DAYS", None)
            if raw is not None:
                os.environ["MAILBOX_NATIVE_SESSION_IDLE_DAYS"] = raw
            assert ma._env_days("MAILBOX_NATIVE_SESSION_IDLE_DAYS", 30) == timedelta(
                days=expected_days
            )

    @pytest.mark.parametrize(
        "header, expected",
        [
            ("ios", "ios"),
            (" iOS ", "ios"),
            ("ANDROID", "android"),
            ("macos", "macos"),
            ("web", "web"),
            ("WEB", "web"),
            ("electron", None),
            ("", None),
            (None, None),
        ],
    )
    def test_client_platform_header_is_normalised_and_allowlisted(self, header, expected):
        headers = {} if header is None else {"X-Client-Platform": header}
        assert ma.client_platform_from_request(FakeRequest(headers=headers)) == expected


class TestCreateSessionPersistsWindow:
    def _params(self, client_platform):
        _, cursor = _conn()
        token = ma.create_mailbox_session(
            cursor,
            email_account_id=ACCOUNT_ID,
            organization_id=ORG_ID,
            client_ip="203.0.113.5",
            user_agent="Mailyte/1.0",
            client_platform=client_platform,
        )
        assert isinstance(token, str) and token
        sql, params = _executed(cursor)[0]
        assert "client_platform" in sql and "idle_timeout_seconds" in sql
        return params

    def test_native_session_row_carries_platform_and_native_windows(self):
        params = self._params("ios")
        now = datetime.now()
        idle, absolute = ma.session_lifetimes("ios")
        assert params[9] == "ios"
        assert params[10] == int(idle.total_seconds())
        assert abs((params[6] - now) - idle) < TOLERANCE
        assert abs((params[7] - now) - absolute) < TOLERANCE

    def test_web_session_row_carries_browser_windows(self):
        params = self._params(None)
        now = datetime.now()
        assert params[9] is None
        assert params[10] == int(WEB_IDLE.total_seconds())
        assert abs((params[6] - now) - WEB_IDLE) < TOLERANCE
        assert abs((params[7] - now) - WEB_ABSOLUTE) < TOLERANCE


# ---------------------------------------------------------------------------
# require_mailbox family
# ---------------------------------------------------------------------------


class TestRequireMailboxSlide:
    def _slide(self, row, dependency=None):
        conn, cursor = _conn(fetchone=row)
        with patch.object(ma, "get_db_connection", return_value=conn):
            ctx = (dependency or ma.require_mailbox)(_bearer_request())
        updates = [(s, p) for s, p in _executed(cursor) if s.lstrip().upper().startswith("UPDATE")]
        assert len(updates) == 1, "exactly one sliding-expiry update expected"
        return ctx, updates[0][1][0]

    def test_slides_using_the_stored_idle_window_not_the_constant(self):
        native_idle = timedelta(days=30)
        _, new_expiry = self._slide(
            _session_row(idle_timeout_seconds=int(native_idle.total_seconds()))
        )
        assert abs((new_expiry - datetime.now()) - native_idle) < TOLERANCE

    def test_browser_row_still_slides_eight_hours(self):
        _, new_expiry = self._slide(_session_row())
        assert abs((new_expiry - datetime.now()) - WEB_IDLE) < TOLERANCE

    def test_missing_window_falls_back_to_browser_default(self):
        _, new_expiry = self._slide(_session_row(idle_timeout_seconds=None))
        assert abs((new_expiry - datetime.now()) - WEB_IDLE) < TOLERANCE

    def test_slide_is_capped_by_absolute_expiry(self):
        absolute = datetime.now() + timedelta(hours=1)
        _, new_expiry = self._slide(
            _session_row(idle_timeout_seconds=30 * 86400, absolute_expiry=absolute)
        )
        assert new_expiry == absolute

    def test_context_carries_password_change_state(self):
        ctx, _ = self._slide(_session_row())
        assert ctx["must_change_password"] is False
        assert ctx["password_change_reason"] is None
        assert ctx["email_account_id"] == ACCOUNT_ID


class TestPasswordChangeGate:
    def _resolve(self, dependency, row):
        conn, cursor = _conn(fetchone=row)
        with patch.object(ma, "get_db_connection", return_value=conn):
            return dependency(_bearer_request()), cursor

    def test_require_mailbox_refuses_a_forced_change_account(self):
        row = _session_row(must_change_password=1, password_change_reason="temporary")
        with pytest.raises(HTTPException) as exc:
            self._resolve(ma.require_mailbox, row)
        assert exc.value.status_code == 403
        assert exc.value.detail["error_code"] == "password_change_required"
        assert exc.value.detail["msg"] == "Set a new password to continue"
        assert exc.value.detail["type"] == "error"

    def test_refusal_does_not_slide_the_session(self):
        row = _session_row(must_change_password=1, password_change_reason="temporary")
        conn, cursor = _conn(fetchone=row)
        with (
            patch.object(ma, "get_db_connection", return_value=conn),
            pytest.raises(HTTPException),
        ):
            ma.require_mailbox(_bearer_request())
        assert not any(s.lstrip().upper().startswith("UPDATE") for s, _ in _executed(cursor))

    def test_password_change_variant_admits_it_and_reports_reason(self):
        row = _session_row(must_change_password=1, password_change_reason="admin_reset")
        ctx, _ = self._resolve(ma.require_mailbox_for_password_change, row)
        assert ctx["must_change_password"] is True
        assert ctx["password_change_reason"] == "admin_reset"

    def test_logout_variant_admits_pending_mfa_and_forced_change_together(self):
        row = _session_row(mfa_satisfied=0, must_change_password=1)
        ctx, _ = self._resolve(ma.require_mailbox_for_logout, row)
        assert ctx["session_id"] == SESSION_ID

    def test_mfa_gate_comes_before_the_password_gate(self):
        """A temporary password must not become a way around the second factor."""
        row = _session_row(mfa_satisfied=0, must_change_password=1)
        for dependency in (ma.require_mailbox, ma.require_mailbox_for_password_change):
            with pytest.raises(HTTPException) as exc:
                self._resolve(dependency, row)
            assert exc.value.status_code == 403
            assert exc.value.detail["error_code"] == "mfa_required"

    def test_ordinary_account_passes_every_variant(self):
        for dependency in (
            ma.require_mailbox,
            ma.require_mailbox_for_password_change,
            ma.require_mailbox_for_logout,
        ):
            ctx, _ = self._resolve(dependency, _session_row())
            assert ctx["email"] == EMAIL


class TestDependencySignatureLeak:
    """The bug: keyword-only dependency parameters become query parameters."""

    @pytest.mark.parametrize(
        "name",
        ["require_mailbox", "require_mailbox_for_password_change", "require_mailbox_for_logout"],
    )
    def test_dependency_exposes_no_query_parameters(self, name):
        dependant = get_dependant(path="/x", call=getattr(ma, name))
        assert [p.name for p in dependant.query_params] == []
        assert [p.name for p in dependant.header_params] == []
        assert [p.name for p in dependant.cookie_params] == []
        assert dependant.request_param_name == "request"

    def test_factory_flags_are_not_reachable_from_a_request(self):
        """Even a request that tries to pass the flag by query string gets
        the variant's fixed behaviour."""
        row = _session_row(mfa_satisfied=0)
        conn, _ = _conn(fetchone=row)
        request = FakeRequest(headers={"Authorization": "Bearer tok"})
        request.query_params = {"allow_pending_mfa": "true"}
        with (
            patch.object(ma, "get_db_connection", return_value=conn),
            pytest.raises(HTTPException) as exc,
        ):
            ma.require_mailbox(request)
        assert exc.value.detail["error_code"] == "mfa_required"


# ---------------------------------------------------------------------------
# POST /mailbox-auth/login -- flags + platform lifetimes in the response
# ---------------------------------------------------------------------------


class TestLoginResponse:
    def _login(self, headers, account_overrides=None):
        account = {
            "id": ACCOUNT_ID,
            "email": EMAIL,
            "name": "Holder",
            "organization_id": ORG_ID,
            "status": "active",
            "must_change_password": 0,
            "password_change_reason": None,
        }
        account.update(account_overrides or {})
        conn, cursor = _conn(fetchone=account)
        request = FakeRequest(headers=headers, method="POST")
        response = Response()
        body = auth_routes.MailboxLoginRequest(email_address=EMAIL, password="pw")
        with (
            patch.object(auth_routes, "get_db_connection", return_value=conn),
            patch.object(auth_routes, "mailbox_login_blocked", return_value=False),
            patch.object(auth_routes, "verify_mailbox_credentials", return_value=True),
            patch.object(auth_routes, "mailbox_two_factor_enabled", return_value=False),
            patch.object(auth_routes, "record_mailbox_login_failure"),
            patch.object(auth_routes, "clear_mailbox_login_failures"),
        ):
            result = auth_routes.login(body, request, response, x_forwarded_for=None)
        return result, cursor, response

    def test_temporary_password_login_issues_session_and_reports_flags(self):
        result, cursor, _ = self._login(
            {"X-Client-Platform": "android", "User-Agent": "Mailyte/1.0"},
            {"must_change_password": 1, "password_change_reason": "temporary"},
        )
        data = result["data"]
        assert result["type"] == "success"
        assert data["token"]
        assert data["must_change_password"] is True
        assert data["password_change_reason"] == "temporary"
        assert data["client_platform"] == "android"
        idle, absolute = ma.session_lifetimes("android")
        assert data["idle_timeout_seconds"] == int(idle.total_seconds())
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert expires_at.tzinfo is not None
        assert abs((expires_at - datetime.now(UTC)) - absolute) < TOLERANCE
        insert_params = next(p for s, p in _executed(cursor) if "INSERT INTO mailbox_sessions" in s)
        assert insert_params[9] == "android"
        assert insert_params[10] == int(idle.total_seconds())

    def test_login_without_platform_header_keeps_browser_lifetimes(self):
        result, cursor, response = self._login({"User-Agent": "Mozilla/5.0"})
        data = result["data"]
        assert data["must_change_password"] is False
        assert data["password_change_reason"] is None
        assert data["client_platform"] is None
        assert data["idle_timeout_seconds"] == int(WEB_IDLE.total_seconds())
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert abs((expires_at - datetime.now(UTC)) - WEB_ABSOLUTE) < TOLERANCE
        insert_params = next(p for s, p in _executed(cursor) if "INSERT INTO mailbox_sessions" in s)
        assert insert_params[9] is None
        # Cookie lifetime tracks the platform's absolute window.
        set_cookie = " ".join(v.decode() for k, v in response.raw_headers if k == b"set-cookie")
        assert f"Max-Age={int(WEB_ABSOLUTE.total_seconds())}" in set_cookie

    def test_native_login_cookie_lifetime_tracks_native_absolute(self):
        _, _, response = self._login({"X-Client-Platform": "ios"})
        _, absolute = ma.session_lifetimes("ios")
        set_cookie = " ".join(v.decode() for k, v in response.raw_headers if k == b"set-cookie")
        assert f"Max-Age={int(absolute.total_seconds())}" in set_cookie

    def test_existing_contract_fields_are_untouched(self):
        result, _, _ = self._login({})
        data = result["data"]
        for key in ("token", "csrf_token", "expires_at", "email_address", "email_account"):
            assert key in data
        assert data["email_account"] == {
            "id": ACCOUNT_ID,
            "email_address": EMAIL,
            "name": "Holder",
        }


# ---------------------------------------------------------------------------
# §12 -- GET /security, /2fa/confirm, /2fa/disable, /security/sessions
# ---------------------------------------------------------------------------


def _totp(status, body):
    return {"status": status, "body": body, "error": None}


class TestSecurityShow:
    def test_pending_enrolment_reports_nothing_confirmed(self):
        conn, _ = _conn()
        pending = _totp(
            200,
            {
                "enrolled": True,
                "enabled": False,
                "verified": False,
                "backup_codes_remaining": 8,
                "created_at": "2026-08-31T10:00:00",
            },
        )
        with (
            patch.object(mailbox_routes, "_totp_call", return_value=pending),
            patch.object(mailbox_routes, "get_db_connection", return_value=conn) as db,
        ):
            result = mailbox_routes.security_show(mailbox=_mailbox_ctx())
        data = result["data"]
        assert data["two_factor_enabled"] is False
        assert data["two_factor_confirmed_at"] is None
        assert data["two_factor_pending"] is True
        assert data["recovery_codes_remaining"] == 0
        assert data["protects"] == mailbox_routes.TWO_FACTOR_SCOPE
        db.assert_not_called()

    def test_enabled_enrolment_reports_the_recorded_confirmation(self):
        confirmed = datetime(2026, 8, 31, 12, 30, 0)
        conn, _ = _conn(fetchone={"two_factor_confirmed_at": confirmed})
        enabled = _totp(
            200,
            {
                "enrolled": True,
                "enabled": True,
                "verified": True,
                "backup_codes_remaining": 6,
                "created_at": "2026-08-31T10:00:00",
            },
        )
        with (
            patch.object(mailbox_routes, "_totp_call", return_value=enabled),
            patch.object(mailbox_routes, "get_db_connection", return_value=conn),
        ):
            result = mailbox_routes.security_show(mailbox=_mailbox_ctx())
        data = result["data"]
        assert data["two_factor_enabled"] is True
        assert data["two_factor_confirmed_at"] == confirmed.isoformat()
        assert data["two_factor_confirmed_at"] != "2026-08-31T10:00:00"
        assert data["two_factor_pending"] is False
        assert data["recovery_codes_remaining"] == 6

    def test_never_enrolled(self):
        with patch.object(
            mailbox_routes,
            "_totp_call",
            return_value=_totp(200, {"enrolled": False, "enabled": False}),
        ):
            result = mailbox_routes.security_show(mailbox=_mailbox_ctx())
        data = result["data"]
        assert data == {
            "two_factor_enabled": False,
            "two_factor_confirmed_at": None,
            "two_factor_pending": False,
            "recovery_codes_remaining": 0,
            "protects": mailbox_routes.TWO_FACTOR_SCOPE,
        }


class TestTwoFactorConfirmAndDisable:
    def test_confirm_records_confirmed_at_and_returns_enabled(self):
        conn, cursor = _conn()
        with (
            patch.object(mailbox_routes, "_totp_call", return_value=_totp(200, {})),
            patch.object(mailbox_routes, "get_db_connection", return_value=conn),
        ):
            result = mailbox_routes.two_factor_confirm(
                mailbox_routes.TwoFactorCodeRequest(code="123456"), mailbox=_mailbox_ctx()
            )
        sql, params = _executed(cursor)[0]
        assert "two_factor_confirmed_at" in sql and sql.lstrip().startswith("UPDATE email_accounts")
        assert isinstance(params[0], datetime)
        assert params[1] == ACCOUNT_ID
        assert result["data"]["two_factor_enabled"] is True
        assert result["data"]["two_factor_confirmed_at"] == params[0].isoformat()

    def test_failed_confirm_records_nothing(self):
        conn, _ = _conn()
        failed = {"status": 401, "body": {}, "error": "Invalid TOTP token"}
        with (
            patch.object(mailbox_routes, "_totp_call", return_value=failed),
            patch.object(mailbox_routes, "get_db_connection", return_value=conn) as db,
            pytest.raises(HTTPException) as exc,
        ):
            mailbox_routes.two_factor_confirm(
                mailbox_routes.TwoFactorCodeRequest(code="000000"), mailbox=_mailbox_ctx()
            )
        assert exc.value.status_code == 422
        db.assert_not_called()

    def test_disable_clears_confirmed_at(self):
        conn, cursor = _conn()
        with (
            patch.object(mailbox_routes, "_totp_call", return_value=_totp(200, {})),
            patch.object(mailbox_routes, "get_db_connection", return_value=conn),
        ):
            result = mailbox_routes.two_factor_disable(
                mailbox_routes.TwoFactorCodeRequest(code="123456"), mailbox=_mailbox_ctx()
            )
        sql, params = _executed(cursor)[0]
        assert "two_factor_confirmed_at" in sql
        assert params == (None, ACCOUNT_ID)
        assert result["data"]["two_factor_enabled"] is False

    def test_begin_writes_nothing_locally(self):
        """/begin is the totp service's business; the confirmation stamp is
        exclusively /confirm's."""
        conn, _ = _conn()
        started = _totp(200, {"secret": "S", "otpauth_uri": "otpauth://x", "backup_codes": ["a"]})
        with (
            patch.object(mailbox_routes, "_totp_call", return_value=started),
            patch.object(mailbox_routes, "get_db_connection", return_value=conn) as db,
        ):
            result = mailbox_routes.two_factor_begin(mailbox=_mailbox_ctx())
        db.assert_not_called()
        # The webmail's ApiTwoFactorEnrolment contract.
        assert set(result["data"]) == {"secret", "qr_code_svg", "recovery_codes"}


class TestSecuritySessions:
    def test_rows_expose_client_platform_and_idle_window(self):
        now = datetime.now()
        rows = [
            {
                "id": SESSION_ID,
                "created_at": now,
                "expires_at": now + timedelta(days=29),
                "revoked_at": None,
                "ip_address": "203.0.113.5",
                "user_agent": "Mailyte iOS/1.0",
                "client_platform": "ios",
                "idle_timeout_seconds": 30 * 86400,
            },
            {
                "id": "01OTHERSESSION0000000000AA",
                "created_at": now - timedelta(days=1),
                "expires_at": now + timedelta(hours=3),
                "revoked_at": None,
                "ip_address": "198.51.100.9",
                "user_agent": "Mozilla/5.0",
                "client_platform": None,
                "idle_timeout_seconds": 28800,
            },
        ]
        conn, cursor = _conn(fetchall=rows)
        with patch.object(mailbox_routes, "get_db_connection", return_value=conn):
            result = mailbox_routes.security_sessions(mailbox=_mailbox_ctx())
        sql, _ = _executed(cursor)[0]
        assert "client_platform" in sql and "idle_timeout_seconds" in sql
        first, second = result["data"]
        assert first["client_platform"] == "ios"
        assert first["idle_timeout_seconds"] == 30 * 86400
        assert first["current"] is True and first["active"] is True
        assert second["client_platform"] is None
        assert second["idle_timeout_seconds"] == 28800
        assert second["current"] is False
