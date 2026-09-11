#!/usr/bin/env python3
"""
Unit tests for worker/api/utils/auth.py
"""

import hashlib
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from utils.auth import (
    ALLOWED_DOMAIN_UPDATE_COLUMNS,
    ALLOWED_MAILBOX_UPDATE_COLUMNS,
    hash_password,
    validate_update_columns,
    verify_password,
)


class TestHashPassword:
    def test_returns_string(self):
        result = hash_password("mypassword123")
        assert isinstance(result, str)

    def test_bcrypt_format(self):
        result = hash_password("mypassword123")
        assert result.startswith("$2b$") or result.startswith("$2a$")

    def test_different_calls_produce_different_hashes(self):
        """bcrypt salts should make each hash unique."""
        hash1 = hash_password("samepassword")
        hash2 = hash_password("samepassword")
        assert hash1 != hash2

    def test_non_empty_result(self):
        result = hash_password("anypassword")
        assert len(result) > 10


class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        password = "correct_password_123"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("correct_password_123")
        assert verify_password("wrong_password", hashed) is False

    def test_sha256_legacy_rejected(self):
        """Legacy SHA-256 hashes should be rejected — users must reset password."""
        password = "legacypassword"
        sha256_hash = "sha256:" + hashlib.sha256(password.encode()).hexdigest()
        assert verify_password(password, sha256_hash) is False

    def test_plain_sha256_rejected(self):
        """Very old format: plain hex without prefix should be rejected."""
        password = "oldformatpass"
        plain_hex = hashlib.sha256(password.encode()).hexdigest()
        assert verify_password(password, plain_hex) is False

    def test_empty_password_vs_hash(self):
        hashed = hash_password("realpassword")
        assert verify_password("", hashed) is False


class TestValidateUpdateColumns:
    def test_valid_columns_pass(self):
        cols = ["active", "description"]
        result = validate_update_columns(cols, ALLOWED_DOMAIN_UPDATE_COLUMNS)
        assert result == cols

    def test_invalid_column_raises_value_error(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_update_columns(["DROP TABLE users"], ALLOWED_DOMAIN_UPDATE_COLUMNS)

    def test_sql_injection_attempt_blocked(self):
        with pytest.raises(ValueError):
            validate_update_columns(
                ["active; DROP TABLE email_accounts; --"],
                ALLOWED_DOMAIN_UPDATE_COLUMNS,
            )

    def test_union_injection_blocked(self):
        with pytest.raises(ValueError):
            validate_update_columns(
                ["1 UNION SELECT password FROM users"],
                ALLOWED_DOMAIN_UPDATE_COLUMNS,
            )

    def test_empty_list_returns_empty(self):
        result = validate_update_columns([], ALLOWED_DOMAIN_UPDATE_COLUMNS)
        assert result == []

    def test_allowed_domain_columns_exist(self):
        assert "active" in ALLOWED_DOMAIN_UPDATE_COLUMNS
        assert "description" in ALLOWED_DOMAIN_UPDATE_COLUMNS

    def test_allowed_mailbox_columns_exist(self):
        assert "name" in ALLOWED_MAILBOX_UPDATE_COLUMNS
        assert "quota" in ALLOWED_MAILBOX_UPDATE_COLUMNS
        assert "active" in ALLOWED_MAILBOX_UPDATE_COLUMNS

    def test_valid_mailbox_columns_pass(self):
        cols = ["name", "quota", "active"]
        result = validate_update_columns(cols, ALLOWED_MAILBOX_UPDATE_COLUMNS)
        assert result == cols

    def test_mixed_valid_and_invalid_raises(self):
        with pytest.raises(ValueError):
            validate_update_columns(
                ["active", "evil_column"],
                ALLOWED_DOMAIN_UPDATE_COLUMNS,
            )


class _FakeCursor:
    """Records executed statements; serves queued fetchone results."""

    def __init__(self, fetch_results):
        self.executed = []
        self._fetch_results = list(fetch_results)

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._fetch_results.pop(0) if self._fetch_results else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self, dictionary=False):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _fake_request(api_key="rawsecretkey_1234567890abcdefABCDEF"):
    import types

    return types.SimpleNamespace(
        headers={"X-API-Key": api_key},
        client=types.SimpleNamespace(host="203.0.113.9"),
        state=types.SimpleNamespace(),
    )


class TestAuthenticateApiKey:
    """The raw key must never be used as the lookup value (only its hash)."""

    def _patch_env(self, monkeypatch, cursor):
        import utils.auth as auth_mod

        monkeypatch.setattr(auth_mod, "get_db_connection", lambda: _FakeConn(cursor))
        monkeypatch.setattr(auth_mod, "_api_key_attempt_blocked", lambda c, ip: False)
        self.failures = []
        monkeypatch.setattr(
            auth_mod, "record_api_key_failure", lambda c, ip: self.failures.append(ip)
        )
        monkeypatch.setattr(auth_mod, "clear_api_key_failures", lambda c, ip: None)
        return auth_mod

    def test_lookup_is_by_hash_not_raw_key(self, monkeypatch):
        raw = "rawsecretkey_1234567890abcdefABCDEF"
        row = {"id": "01ROW", "organization_id": "01ORG", "permissions": {}, "expires_at": None}
        cursor = _FakeCursor([row])
        auth_mod = self._patch_env(monkeypatch, cursor)

        result = auth_mod._authenticate_api_key(_fake_request(raw))

        assert result["id"] == "01ROW"
        select_sql, select_params = cursor.executed[0]
        assert "key_hash = %s" in select_sql
        assert select_params == (hashlib.sha256(raw.encode()).hexdigest(),)
        assert raw not in [
            p for (_, ps) in cursor.executed if ps for p in ps if select_params != ps
        ]

    def test_last_used_updated_by_row_id(self, monkeypatch):
        row = {"id": "01ROW", "organization_id": None, "permissions": {}, "expires_at": None}
        cursor = _FakeCursor([row])
        auth_mod = self._patch_env(monkeypatch, cursor)

        auth_mod._authenticate_api_key(_fake_request())

        update_sql, update_params = cursor.executed[-1]
        assert "SET last_used" in update_sql
        assert update_params[-1] == "01ROW"

    def test_legacy_raw_row_matches_and_self_heals(self, monkeypatch):
        raw = "legacyrawkey_ABCDEF1234567890abcdef"
        row = {"id": "01ROW", "organization_id": "01ORG", "permissions": {}, "expires_at": None}
        cursor = _FakeCursor([None, row])  # hash miss, then legacy key_id hit
        auth_mod = self._patch_env(monkeypatch, cursor)

        result = auth_mod._authenticate_api_key(_fake_request(raw))

        assert result["id"] == "01ROW"
        heal_sql, heal_params = cursor.executed[2]
        assert "SET key_hash = %s" in heal_sql
        assert heal_params == (hashlib.sha256(raw.encode()).hexdigest(), "01ROW")

    def test_unknown_key_records_failure_and_401s(self, monkeypatch):
        from fastapi import HTTPException

        cursor = _FakeCursor([None, None])
        auth_mod = self._patch_env(monkeypatch, cursor)

        with pytest.raises(HTTPException) as exc:
            auth_mod._authenticate_api_key(_fake_request())
        assert exc.value.status_code == 401
        assert self.failures == ["203.0.113.9"]

    def test_expired_key_rejected_without_failure_strike(self, monkeypatch):
        from datetime import datetime, timedelta

        from fastapi import HTTPException

        row = {
            "id": "01ROW",
            "organization_id": "01ORG",
            "permissions": {},
            "expires_at": datetime.now() - timedelta(minutes=1),
        }
        cursor = _FakeCursor([row])
        auth_mod = self._patch_env(monkeypatch, cursor)

        with pytest.raises(HTTPException) as exc:
            auth_mod._authenticate_api_key(_fake_request())
        assert exc.value.status_code == 401
        assert "expired" in str(exc.value.detail).lower()
        assert self.failures == []

    def test_future_expiry_accepted(self, monkeypatch):
        from datetime import datetime, timedelta

        row = {
            "id": "01ROW",
            "organization_id": "01ORG",
            "permissions": {},
            "expires_at": datetime.now() + timedelta(days=30),
        }
        cursor = _FakeCursor([row])
        auth_mod = self._patch_env(monkeypatch, cursor)

        result = auth_mod._authenticate_api_key(_fake_request())
        assert result["id"] == "01ROW"
