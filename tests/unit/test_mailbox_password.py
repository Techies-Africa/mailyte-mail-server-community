#!/usr/bin/env python3
"""
Unit tests for mailbox password change (mobile team 2026-08-31 §2, P0):

* POST /api/v1/mailbox/security/password (worker/api/routes/mailbox_password.py)
  -- the holder's own change: policy reuse, Dovecot re-verification of the
  current password, bcrypt storage, flag clearing, revocation of every OTHER
  session, auth-cache flush, and the error_code contract
  (weak_password / wrong_password / password_reused).
* POST /api/v1/mailboxes/email-accounts/{id}/reset-password
  (worker/api/routes/mailboxes.py) -- the admin reset, org-scoped like its
  neighbours, that sets must_change_password when temporary=true.

DB, IMAP, doveadm and the webhook dispatcher are mocked. The undecorated
admin handler is exercised via __wrapped__ with auth_context supplied
directly, the same way test_legacy_route_fixes.py does.
"""

import asyncio
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from routes import mailbox_password as pw_routes  # noqa: E402
from routes import mailboxes as mailboxes_routes  # noqa: E402
from utils import mailbox_auth as ma  # noqa: E402
from utils.auth import validate_password_strength, verify_password  # noqa: E402

from shared.webhook_dispatcher import Events  # noqa: E402

ACCOUNT_ID = "01MBOXULID00000000000000AA"
SESSION_ID = "01SESSULID00000000000000AA"
ORG_ID = "01ORGULID0000000000000000A"
OTHER_ORG_ID = "01ORGULID0000000000000000B"
EMAIL = "holder@example.com"

CURRENT = "Current-Passw0rd-2026"
NEW = "Brand-New-Passw0rd-2026"


class _Headers(dict):
    def __init__(self, items=None):
        super().__init__({k.lower(): v for k, v in (items or {}).items()})

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = _Headers(headers)
        self.method = "POST"
        self.cookies = {}
        self.client = SimpleNamespace(host="203.0.113.5")
        self.state = SimpleNamespace()


def _ctx(**overrides):
    ctx = {
        "email_account_id": ACCOUNT_ID,
        "email": EMAIL,
        "organization_id": ORG_ID,
        "session_id": SESSION_ID,
        "must_change_password": True,
        "password_change_reason": "temporary",
    }
    ctx.update(overrides)
    return ctx


def _conn(rowcount=0):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.rowcount = rowcount
    return conn, cursor


def _executed(cursor):
    return [
        (str(c.args[0]), c.args[1] if len(c.args) > 1 else None)
        for c in cursor.execute.call_args_list
    ]


def _body(current=CURRENT, new=NEW):
    return pw_routes.PasswordChangeRequest(current_password=current, new_password=new)


class _Patched:
    """Everything change_password reaches for, mocked in one place."""

    def __init__(self, conn, *, password_ok=True, blocked=False, flushed=True):
        self._patches = [
            patch.object(pw_routes, "get_db_connection", return_value=conn),
            patch.object(pw_routes, "mailbox_login_blocked", return_value=blocked),
            patch.object(pw_routes, "verify_mailbox_credentials", return_value=password_ok),
            patch.object(pw_routes, "record_mailbox_login_failure"),
            patch.object(pw_routes, "clear_mailbox_login_failures"),
            patch.object(pw_routes, "flush_auth_cache", return_value=flushed),
            patch.object(pw_routes, "dispatch_event"),
        ]
        self.mocks = {}

    def __enter__(self):
        for p in self._patches:
            mock = p.start()
            self.mocks[p.attribute] = mock
        return self.mocks

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


# ---------------------------------------------------------------------------
# POST /api/v1/mailbox/security/password
# ---------------------------------------------------------------------------


class TestRouteWiring:
    def test_router_exposes_the_password_route(self):
        routes = [(r.path, sorted(r.methods)) for r in pw_routes.router.routes]
        assert ("/security/password", ["POST"]) in routes

    def test_route_uses_the_password_change_dependency_variant(self):
        param = inspect.signature(pw_routes.change_password).parameters["mailbox"]
        assert param.default.dependency is ma.require_mailbox_for_password_change
        assert param.default.dependency is not ma.require_mailbox

    def test_handler_is_sync_so_it_runs_in_the_threadpool(self):
        assert not inspect.iscoroutinefunction(pw_routes.change_password)


class TestPolicy:
    @pytest.mark.parametrize(
        "weak", ["short1", "nodigitsatallhere", "123456789012", "password1234"]
    )
    def test_weak_password_is_422_with_the_failing_rule(self, weak):
        conn, _ = _conn()
        with _Patched(conn) as mocks, pytest.raises(HTTPException) as exc:
            pw_routes.change_password(_body(new=weak), FakeRequest(), mailbox=_ctx())
        assert exc.value.status_code == 422
        assert exc.value.detail["error_code"] == "weak_password"
        assert exc.value.detail["msg"] == validate_password_strength(weak)[1]
        # Rejected before any DB or IMAP work.
        mocks["get_db_connection"].assert_not_called()
        mocks["verify_mailbox_credentials"].assert_not_called()

    def test_policy_is_the_shared_twelve_char_policy(self):
        ok, _ = validate_password_strength("Eleven-ch4r")
        assert ok is False, "an 11-character password must fail the shared policy"
        ok, _ = validate_password_strength(NEW)
        assert ok is True


class TestVerification:
    def test_wrong_current_password_is_401_and_counts_as_a_failure(self):
        conn, cursor = _conn()
        with _Patched(conn, password_ok=False) as mocks, pytest.raises(HTTPException) as exc:
            pw_routes.change_password(_body(), FakeRequest(), mailbox=_ctx())
        assert exc.value.status_code == 401
        assert exc.value.detail["error_code"] == "wrong_password"
        mocks["verify_mailbox_credentials"].assert_called_once_with(EMAIL, CURRENT)
        mocks["record_mailbox_login_failure"].assert_called_once()
        assert mocks["record_mailbox_login_failure"].call_args.args[1:] == ("203.0.113.5", EMAIL)
        assert not any(s.lstrip().upper().startswith("UPDATE") for s, _ in _executed(cursor))
        mocks["flush_auth_cache"].assert_not_called()

    def test_forwarded_for_is_the_rate_limit_ip(self):
        conn, _ = _conn()
        request = FakeRequest({"X-Forwarded-For": "198.51.100.7, 10.0.0.1"})
        with _Patched(conn, password_ok=False) as mocks, pytest.raises(HTTPException):
            pw_routes.change_password(_body(), request, mailbox=_ctx())
        assert mocks["record_mailbox_login_failure"].call_args.args[1] == "198.51.100.7"

    def test_locked_out_is_429_before_dovecot_is_asked(self):
        conn, _ = _conn()
        with _Patched(conn, blocked=True) as mocks, pytest.raises(HTTPException) as exc:
            pw_routes.change_password(_body(), FakeRequest(), mailbox=_ctx())
        assert exc.value.status_code == 429
        mocks["verify_mailbox_credentials"].assert_not_called()

    def test_same_password_is_409_password_reused(self):
        conn, cursor = _conn()
        with _Patched(conn) as mocks, pytest.raises(HTTPException) as exc:
            pw_routes.change_password(_body(current=NEW, new=NEW), FakeRequest(), mailbox=_ctx())
        assert exc.value.status_code == 409
        assert exc.value.detail["error_code"] == "password_reused"
        # Only answered once the current password was proven, never before.
        mocks["verify_mailbox_credentials"].assert_called_once()
        assert not any(s.lstrip().upper().startswith("UPDATE") for s, _ in _executed(cursor))

    def test_dovecot_outage_propagates_as_502_not_wrong_password(self):
        conn, _ = _conn()
        outage = HTTPException(status_code=502, detail={"type": "error", "msg": "down"})
        with _Patched(conn) as mocks:
            mocks["verify_mailbox_credentials"].side_effect = outage
            with pytest.raises(HTTPException) as exc:
                pw_routes.change_password(_body(), FakeRequest(), mailbox=_ctx())
        assert exc.value.status_code == 502
        mocks["record_mailbox_login_failure"].assert_not_called()


class TestSuccessfulChange:
    def _change(self, rowcount=3, flushed=True):
        conn, cursor = _conn(rowcount=rowcount)
        with _Patched(conn, flushed=flushed) as mocks:
            result = pw_routes.change_password(_body(), FakeRequest(), mailbox=_ctx())
        return result, cursor, mocks

    def test_response_envelope(self):
        result, _, _ = self._change(rowcount=3)
        assert result == {
            "type": "success",
            "msg": "Password changed",
            "data": {"sessions_revoked": 3, "cache_flushed": True},
        }

    def test_stores_a_bcrypt_hash_of_the_new_password_and_clears_the_flags(self):
        _, cursor, _ = self._change()
        account_updates = [(s, p) for s, p in _executed(cursor) if "UPDATE email_accounts" in s]
        assert len(account_updates) == 1
        sql, params = account_updates[0]
        assert "must_change_password = 0" in sql
        assert "password_change_reason = NULL" in sql
        assert "password_changed_at = NOW()" in sql
        assert params[1] == ACCOUNT_ID
        assert params[0] != NEW and params[0].startswith(("$2b$", "$2a$"))
        assert verify_password(NEW, params[0]) is True
        assert verify_password(CURRENT, params[0]) is False

    def test_revokes_every_other_session_but_not_the_callers(self):
        _, cursor, _ = self._change()
        session_updates = [(s, p) for s, p in _executed(cursor) if "UPDATE mailbox_sessions" in s]
        assert len(session_updates) == 1
        sql, params = session_updates[0]
        assert "revoked_at = NOW()" in sql
        assert "id <> %s" in sql
        assert "revoked_at IS NULL" in sql
        assert params == (ACCOUNT_ID, SESSION_ID)

    def test_flushes_dovecot_auth_cache_for_the_mailbox(self):
        _, _, mocks = self._change()
        mocks["flush_auth_cache"].assert_called_once_with(EMAIL)

    def test_flush_failure_is_reported_not_fatal(self):
        result, _, _ = self._change(flushed=False)
        assert result["type"] == "success"
        assert result["data"]["cache_flushed"] is False

    def test_clears_the_failure_counter_and_emits_the_event(self):
        _, _, mocks = self._change()
        mocks["clear_mailbox_login_failures"].assert_called_once()
        event_call = mocks["dispatch_event"].call_args
        assert event_call.args[0] is Events.MAILBOX_PASSWORD_CHANGED
        assert event_call.kwargs["org_id"] == ORG_ID
        assert event_call.kwargs["data"]["changed_by"] == "mailbox_holder"

    def test_works_for_an_account_with_no_forced_change_pending(self):
        conn, cursor = _conn(rowcount=0)
        with _Patched(conn):
            result = pw_routes.change_password(
                _body(), FakeRequest(), mailbox=_ctx(must_change_password=False)
            )
        assert result["data"]["sessions_revoked"] == 0


# ---------------------------------------------------------------------------
# POST /api/v1/mailboxes/email-accounts/{id}/reset-password (admin)
# ---------------------------------------------------------------------------


class FakeAdminRequest:
    def __init__(self, scope="organization", org_id=ORG_ID):
        self.state = SimpleNamespace(auth_context={"scope": scope, "organization_id": org_id})


def _account(**overrides):
    account = SimpleNamespace(
        id=ACCOUNT_ID,
        email=EMAIL,
        organization_id=ORG_ID,
        password="$2b$12$old",
        must_change_password=False,
        password_change_reason=None,
        password_changed_at=None,
        updated_at=None,
    )
    for k, v in overrides.items():
        setattr(account, k, v)
    return account


def _fake_session(account, revoked=2):
    session = MagicMock()
    account_query = MagicMock()
    account_query.filter_by.return_value.first.return_value = account
    session_query = MagicMock()
    session_query.filter.return_value.update.return_value = revoked

    def query(model):
        if model is mailboxes_routes.EmailAccount:
            return account_query
        if model is mailboxes_routes.MailboxSession:
            return session_query
        raise AssertionError(f"unexpected query on {model}")

    session.query.side_effect = query
    return session, session_query


def _reset(account, body, request, revoked=2, flushed=True):
    session, session_query = _fake_session(account, revoked=revoked)
    handler = mailboxes_routes.reset_email_account_password.__wrapped__
    with (
        patch.object(mailboxes_routes, "get_db_session", return_value=session),
        patch.object(mailboxes_routes, "flush_auth_cache", return_value=flushed) as flush,
        patch.object(mailboxes_routes, "dispatch_event") as dispatch,
    ):
        result = asyncio.run(handler(account.id if account else ACCOUNT_ID, body, request))
    return result, session, session_query, flush, dispatch


def _json(response):
    return json.loads(response.body)


class TestAdminResetWiring:
    def test_route_is_registered_under_email_accounts(self):
        routes = [(r.path, sorted(r.methods)) for r in mailboxes_routes.router.routes]
        assert ("/email-accounts/{account_id}/reset-password", ["POST"]) in routes


class TestAdminReset:
    def test_temporary_reset_sets_the_forced_change_flags(self):
        account = _account()
        body = mailboxes_routes.MailboxPasswordReset(new_password=NEW, temporary=True)
        result, session, session_query, flush, dispatch = _reset(account, body, FakeAdminRequest())
        assert result["type"] == "success"
        assert result["msg"] == "Password reset"
        assert account.must_change_password is True
        assert account.password_change_reason == "temporary"
        assert isinstance(account.password_changed_at, datetime)
        assert verify_password(NEW, account.password) is True
        session.commit.assert_called_once()
        data = result["data"]
        assert data["must_change_password"] is True
        assert data["password_change_reason"] == "temporary"
        assert data["sessions_revoked"] == 2
        assert data["cache_flushed"] is True
        assert "password" not in data and NEW not in json.dumps(data)

    def test_reset_revokes_every_session_and_flushes_the_cache(self):
        account = _account()
        body = mailboxes_routes.MailboxPasswordReset(new_password=NEW, temporary=False)
        _, _, session_query, flush, dispatch = _reset(account, body, FakeAdminRequest())
        session_query.filter.return_value.update.assert_called_once()
        flush.assert_called_once_with(EMAIL)
        assert dispatch.call_args.args[0] is Events.MAILBOX_PASSWORD_CHANGED
        assert dispatch.call_args.kwargs["data"]["changed_by"] == "admin"

    def test_permanent_reset_clears_any_pending_forced_change(self):
        account = _account(must_change_password=True, password_change_reason="temporary")
        body = mailboxes_routes.MailboxPasswordReset(new_password=NEW, temporary=False)
        result, *_ = _reset(account, body, FakeAdminRequest())
        assert account.must_change_password is False
        assert account.password_change_reason is None
        assert result["data"]["must_change_password"] is False
        assert result["data"]["password_change_reason"] is None

    def test_admin_reset_reason_is_recorded(self):
        account = _account()
        body = mailboxes_routes.MailboxPasswordReset(
            new_password=NEW, temporary=True, reason="admin_reset"
        )
        _reset(account, body, FakeAdminRequest())
        assert account.password_change_reason == "admin_reset"

    def test_unknown_reason_is_400(self):
        account = _account()
        body = mailboxes_routes.MailboxPasswordReset(
            new_password=NEW, temporary=True, reason="because"
        )
        result, session, *_ = _reset(account, body, FakeAdminRequest())
        assert result.status_code == 400
        session.commit.assert_not_called()

    def test_weak_password_is_400_weak_password_with_the_rule(self):
        account = _account()
        body = mailboxes_routes.MailboxPasswordReset(new_password="short1", temporary=True)
        result, session, *_ = _reset(account, body, FakeAdminRequest())
        assert result.status_code == 400
        payload = _json(result)
        assert payload["error_code"] == "weak_password"
        assert payload["msg"] == validate_password_strength("short1")[1]
        session.commit.assert_not_called()
        assert account.password == "$2b$12$old"

    def test_cross_org_is_404_and_changes_nothing(self):
        account = _account(organization_id=OTHER_ORG_ID)
        body = mailboxes_routes.MailboxPasswordReset(new_password=NEW, temporary=True)
        result, session, _, flush, _ = _reset(account, body, FakeAdminRequest(org_id=ORG_ID))
        assert result.status_code == 404
        assert _json(result)["msg"] == "Email account not found"
        session.commit.assert_not_called()
        flush.assert_not_called()
        assert account.password == "$2b$12$old"

    def test_platform_scope_may_reset_any_org(self):
        account = _account(organization_id=OTHER_ORG_ID)
        body = mailboxes_routes.MailboxPasswordReset(new_password=NEW, temporary=True)
        result, *_ = _reset(account, body, FakeAdminRequest(scope="platform", org_id=None))
        assert result["type"] == "success"

    def test_missing_account_is_404(self):
        body = mailboxes_routes.MailboxPasswordReset(new_password=NEW, temporary=True)
        session, _ = _fake_session(None)
        handler = mailboxes_routes.reset_email_account_password.__wrapped__
        with (
            patch.object(mailboxes_routes, "get_db_session", return_value=session),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            result = asyncio.run(handler(ACCOUNT_ID, body, FakeAdminRequest()))
        assert result.status_code == 404
        flush.assert_not_called()
