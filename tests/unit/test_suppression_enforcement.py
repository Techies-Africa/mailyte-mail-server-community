#!/usr/bin/env python3
"""
Unit tests for outbound suppression-list enforcement.

Covers the two pure pieces of the enforcement path:

- worker/rate_limiter/services/suppression_check_service.py -- the DB lookup
  behind the policy consultation, whose contract is FAIL OPEN on every error
  (the suppression list must never be the reason mail stops flowing) with a
  short TTL cache in front of the query.

- mailer/postfix/scripts/rate_limit_policy.py -- the Postfix policy bridge,
  which must forward recipient/recipient_count to the rate limiter and map a
  `suppressed` response to a permanent REJECT (never a defer, never a silent
  drop), while every transport failure stays a fail-open DUNNO.

Both modules live outside importable packages (the services package's
__init__ pulls redis/mysql; the bridge sits in mailer/postfix/scripts), so
they are loaded directly by file path.
"""

import importlib.util
import sys
from pathlib import Path
from urllib.error import URLError

project_root = Path(__file__).parent.parent.parent


def _load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, project_root / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


suppression_check = _load_module(
    "suppression_check_service",
    "worker/rate_limiter/services/suppression_check_service.py",
)
bridge = _load_module(
    "rate_limit_policy_bridge",
    "mailer/postfix/scripts/rate_limit_policy.py",
)


# ---------------------------------------------------------------------------
# SuppressionCheckService
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchone(self):
        return self._row

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class FakePool:
    def __init__(self, cursor=None, raise_on_connect=None):
        self._cursor = cursor
        self._raise = raise_on_connect
        self.connections = 0

    def get_connection(self):
        if self._raise:
            raise self._raise
        self.connections += 1
        return FakeConnection(self._cursor)


class FakeDatabaseService:
    def __init__(self, pool):
        self.db_pool = pool


def _service(row=None, pool=None):
    if pool is None:
        pool = FakePool(cursor=FakeCursor(row))
    return suppression_check.SuppressionCheckService(FakeDatabaseService(pool)), pool


class TestSuppressionCheckService:
    def test_suppressed_row_found(self):
        service, pool = _service(row=("BOUNCE",))
        assert service.is_suppressed("org_a", "gone@example.com") == (True, "BOUNCE")

    def test_not_suppressed(self):
        service, _ = _service(row=None)
        assert service.is_suppressed("org_a", "fine@example.com") == (False, "")

    def test_query_scoped_to_org_and_recipient(self):
        cursor = FakeCursor(None)
        service, _ = _service(pool=FakePool(cursor=cursor))
        service.is_suppressed("org_a", "gone@example.com")
        query, params = cursor.executed[0]
        assert "organization_id = %s" in query
        assert "active = 1" in query
        assert params[0] == "gone@example.com"
        assert params[1] == "org_a"

    def test_missing_pool_fails_open(self):
        service = suppression_check.SuppressionCheckService(FakeDatabaseService(None))
        assert service.is_suppressed("org_a", "gone@example.com") == (False, "")

    def test_db_error_fails_open(self):
        service, _ = _service(pool=FakePool(raise_on_connect=RuntimeError("db down")))
        assert service.is_suppressed("org_a", "gone@example.com") == (False, "")

    def test_empty_org_or_recipient_short_circuits(self):
        service, pool = _service(row=("BOUNCE",))
        assert service.is_suppressed("", "gone@example.com") == (False, "")
        assert service.is_suppressed("org_a", "") == (False, "")
        assert pool.connections == 0  # never touched the database

    def test_result_cached_within_ttl(self):
        service, pool = _service(row=("MANUAL",))
        assert service.is_suppressed("org_a", "gone@example.com") == (True, "MANUAL")
        assert service.is_suppressed("org_a", "gone@example.com") == (True, "MANUAL")
        # Case-insensitive cache key: same address, one query total.
        assert service.is_suppressed("org_a", "GONE@Example.com") == (True, "MANUAL")
        assert pool.connections == 1

    def test_cache_expires(self, monkeypatch):
        service, pool = _service(row=("MANUAL",))
        service.is_suppressed("org_a", "gone@example.com")
        # Jump past the TTL.
        real_monotonic = suppression_check.time.monotonic
        monkeypatch.setattr(
            suppression_check.time,
            "monotonic",
            lambda: real_monotonic() + service.CACHE_TTL + 1,
        )
        service.is_suppressed("org_a", "gone@example.com")
        assert pool.connections == 2


# ---------------------------------------------------------------------------
# Postfix policy bridge (check_via_json_api)
# ---------------------------------------------------------------------------

OUTBOUND_ATTRS = {
    "sender": "sender@tenant.example",
    "sasl_username": "sender@tenant.example",
    "recipient": "gone@example.com",
    "recipient_count": "1",
}


class TestBridgeSuppressionMapping:
    def test_suppressed_maps_to_reject(self, monkeypatch):
        monkeypatch.setattr(
            bridge,
            "_http_post_json",
            lambda url, payload: {
                "allowed": False,
                "suppressed": True,
                "message": "5.7.1 Recipient address <gone@example.com> is on your "
                "organization's suppression list (BOUNCE).",
            },
        )
        action = bridge.check_via_json_api(dict(OUTBOUND_ATTRS))
        assert action.startswith("REJECT 5.7.1")
        assert "suppression list" in action
        assert "\n" not in action  # policy action must be a single line

    def test_recipient_forwarded_in_payload(self, monkeypatch):
        captured = {}

        def record(url, payload):
            captured.update(payload)
            return {"allowed": True}

        monkeypatch.setattr(bridge, "_http_post_json", record)
        assert bridge.check_via_json_api(dict(OUTBOUND_ATTRS)) == "DUNNO"
        assert captured["recipient"] == "gone@example.com"
        assert captured["recipient_count"] == 1
        assert captured["record"] is True

    def test_missing_recipient_count_defaults_to_zero(self, monkeypatch):
        captured = {}

        def record(url, payload):
            captured.update(payload)
            return {"allowed": True}

        monkeypatch.setattr(bridge, "_http_post_json", record)
        attrs = dict(OUTBOUND_ATTRS)
        del attrs["recipient_count"]
        bridge.check_via_json_api(attrs)
        assert captured["recipient_count"] == 0

    def test_rate_limited_still_defers(self, monkeypatch):
        monkeypatch.setattr(
            bridge,
            "_http_post_json",
            lambda url, payload: {"allowed": False, "message": "over quota"},
        )
        action = bridge.check_via_json_api(dict(OUTBOUND_ATTRS))
        assert action.startswith("DEFER_IF_PERMIT 4.7.1")

    def test_transport_error_fails_open(self, monkeypatch):
        def boom(url, payload):
            raise URLError("rate limiter down")

        monkeypatch.setattr(bridge, "_http_post_json", boom)
        assert bridge.check_via_json_api(dict(OUTBOUND_ATTRS)) == "DUNNO"

    def test_suppressed_without_message_uses_fallback_text(self, monkeypatch):
        monkeypatch.setattr(
            bridge,
            "_http_post_json",
            lambda url, payload: {"allowed": False, "suppressed": True},
        )
        action = bridge.check_via_json_api(dict(OUTBOUND_ATTRS))
        assert action.startswith("REJECT 5.7.1")
