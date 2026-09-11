"""Send-credit enforcement logic.

The rules under test are the ones that decide whether a customer's mail goes
out, so each is asserted directly rather than inferred from a happy path:
included allowance first, then credits, then refusal — and every failure mode
failing OPEN, because a database blip must never become a mail outage.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Imported by file path rather than through `services.`: that package's
# __init__ pulls in the whole rate-limiter service graph (redis, mysql), and
# this module deliberately depends on none of it.
_MODULE = (
    Path(__file__).resolve().parents[2]
    / "worker"
    / "rate_limiter"
    / "services"
    / "send_credit_service.py"
)
_spec = importlib.util.spec_from_file_location("send_credit_service", _MODULE)
_send_credit_service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_send_credit_service)
SendCreditService = _send_credit_service.SendCreditService


class FakeRedis:
    """Enough Redis to exercise the service, plus a switch to make it fail."""

    def __init__(self, values=None, broken=False):
        self.values = dict(values or {})
        self.broken = broken

    def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.values.get(key)

    def set(self, key, value):
        if self.broken:
            raise ConnectionError("redis down")
        self.values[key] = value

    def incr(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def decr(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        self.values[key] = int(self.values.get(key, 0)) - 1
        return self.values[key]

    def expire(self, key, seconds):
        return True


ORG = "01ORG0000000000000000000AB"


def service(monkeypatch, values=None, broken=False, enabled=True):
    monkeypatch.setenv("SEND_CREDIT_ENFORCEMENT", "true" if enabled else "false")
    return SendCreditService(redis_client=FakeRedis(values, broken), config_service=None)


def month_key(svc):
    return svc._pool_key(ORG)


# -- the switch ------------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    """Shipping the code must not change behaviour until someone enables it."""
    monkeypatch.delenv("SEND_CREDIT_ENFORCEMENT", raising=False)
    svc = SendCreditService(redis_client=FakeRedis(), config_service=None)

    allowed, reason = svc.check(ORG)
    assert allowed
    assert "disabled" in reason


def test_disabled_still_counts(monkeypatch):
    """The switch gates refusals, not metering. This test used to assert the
    opposite ("turning it on later starts from a clean month") -- and the
    consequence was that with the flag off every credential send went through
    uncounted: no usage to bill, nothing to reconcile, and no real-traffic
    behaviour to watch before ever flipping the flag on."""
    svc = service(monkeypatch, enabled=False)
    svc.record(ORG)
    assert svc.redis.values[month_key(svc)] == 1


# -- the allowance ---------------------------------------------------------


def test_sending_within_the_included_allowance_is_free(monkeypatch):
    svc = service(monkeypatch, {f"app_send_allowance:{ORG}": 5000})

    allowed, reason = svc.check(ORG)
    assert allowed
    assert "included allowance" in reason


def test_allowance_is_consumed_before_credits(monkeypatch):
    svc = service(
        monkeypatch,
        {f"app_send_allowance:{ORG}": 2, f"send_credits:{ORG}": 10},
    )

    # Two messages inside the allowance leave the credit balance untouched.
    svc.record(ORG)
    svc.record(ORG)
    assert svc.balance(ORG) == 10

    # The third is past the allowance and costs a credit.
    svc.record(ORG)
    assert svc.balance(ORG) == 9


# -- credits ---------------------------------------------------------------


def test_credits_cover_sending_past_the_allowance(monkeypatch):
    svc = service(
        monkeypatch,
        {f"app_send_allowance:{ORG}": 0, f"send_credits:{ORG}": 3, month_key_for(ORG): 10},
    )

    allowed, reason = svc.check(ORG)
    assert allowed
    assert "credits" in reason


def test_no_allowance_and_no_credits_is_refused(monkeypatch):
    svc = service(
        monkeypatch,
        {f"app_send_allowance:{ORG}": 0, f"send_credits:{ORG}": 0},
    )

    allowed, reason = svc.check(ORG)
    assert not allowed
    assert "no sending credits" in reason


def test_balance_never_goes_negative(monkeypatch):
    svc = service(monkeypatch, {f"app_send_allowance:{ORG}": 0, f"send_credits:{ORG}": 1})

    svc.record(ORG)
    svc.record(ORG)  # already at zero; must not go below

    assert svc.balance(ORG) == 0


# -- the shared-pool ceiling ----------------------------------------------


def test_shared_pool_ceiling_refuses_a_large_sender_without_their_own_ip(monkeypatch):
    """Credits buy sending; they do not buy other customers' deliverability."""
    monkeypatch.setenv("BROADCAST_SHARED_CEILING", "100")
    svc = service(
        monkeypatch,
        {
            f"app_send_allowance:{ORG}": 1000000,
            f"send_credits:{ORG}": 1000000,
            month_key_for(ORG): 100,
            f"dedicated_ip:{ORG}": "0",
        },
    )

    allowed, reason = svc.check(ORG)
    assert not allowed
    assert "dedicated IP is required" in reason


def test_a_dedicated_ip_lifts_the_shared_ceiling(monkeypatch):
    monkeypatch.setenv("BROADCAST_SHARED_CEILING", "100")
    svc = service(
        monkeypatch,
        {
            f"app_send_allowance:{ORG}": 1000000,
            month_key_for(ORG): 5000,
            f"dedicated_ip:{ORG}": "1",
        },
    )

    assert svc.check(ORG)[0]


def test_below_the_ceiling_no_dedicated_ip_is_needed(monkeypatch):
    monkeypatch.setenv("BROADCAST_SHARED_CEILING", "100")
    svc = service(
        monkeypatch,
        {f"app_send_allowance:{ORG}": 1000, month_key_for(ORG): 99, f"dedicated_ip:{ORG}": "0"},
    )

    assert svc.check(ORG)[0]


# -- failing open ----------------------------------------------------------


def test_a_balance_we_were_never_told_allows_sending(monkeypatch):
    """None means "no opinion", not "zero". An organization whose balance has
    not been pushed yet must not be cut off by our own missing data."""
    svc = service(monkeypatch, {f"app_send_allowance:{ORG}": 0})

    allowed, reason = svc.check(ORG)
    assert allowed
    assert "no balance pushed" in reason


def test_redis_failure_fails_open(monkeypatch):
    svc = service(monkeypatch, broken=True)

    allowed, _ = svc.check(ORG)
    assert allowed


def test_unresolved_organization_fails_open(monkeypatch):
    svc = service(monkeypatch, {})

    assert svc.check("")[0]
    assert svc.check("unknown")[0]


def test_record_never_raises_even_when_redis_is_down(monkeypatch):
    svc = service(monkeypatch, broken=True)

    # An accounting failure must not block a message already judged allowed.
    svc.record(ORG)


# -- pushes from Laravel ---------------------------------------------------


def test_balance_and_allowance_are_pushable(monkeypatch):
    svc = service(monkeypatch, {})

    svc.set_balance(ORG, 50000)
    svc.set_pool_allowance(ORG, 20000)

    assert svc.balance(ORG) == 50000
    assert svc.pool_allowance(ORG) == 20000


def test_a_negative_push_is_clamped_to_zero(monkeypatch):
    svc = service(monkeypatch, {})

    svc.set_balance(ORG, -5)

    assert svc.balance(ORG) == 0


def test_snapshot_reports_what_enforcement_believes(monkeypatch):
    svc = service(monkeypatch, {f"app_send_allowance:{ORG}": 5000, f"send_credits:{ORG}": 120})

    snapshot = svc.snapshot(ORG)

    assert snapshot["organization_id"] == ORG
    assert snapshot["enforcement_enabled"] is True
    assert snapshot["app_send_allowance"] == 5000
    assert snapshot["credit_balance"] == 120


def month_key_for(organization_id: str) -> str:
    from datetime import datetime, timezone

    return f"app_send:{organization_id}:{datetime.now(timezone.utc).strftime('%Y%m')}"
