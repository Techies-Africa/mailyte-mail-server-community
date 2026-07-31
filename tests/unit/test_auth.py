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
