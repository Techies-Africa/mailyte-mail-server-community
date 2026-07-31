"""
Unit tests for shared/webhook_dispatcher.py

Tests the new features added in the latest revision:
- Envelope structure (id, signature block, tags, user_variables)
- Both HMAC verification methods
- Retry schedule (_RETRY_SCHEDULE)
- HTTP 406 permanent no-retry
- Events class constants (tracking.* names, no email.opened/clicked aliases)
- dispatch_event / dispatch_event_sync signatures
"""

import json
import hmac
import hashlib
import time
import threading
from unittest.mock import patch, MagicMock, call
import sys
import os

# Make shared/ importable from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

# Reload to clear cached env-based module state
import shared.webhook_dispatcher as wd

importlib.reload(wd)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SECRET = "test-secret-key"
_orig_secret = wd.WEBHOOK_SECRET


def _set_secret(s):
    wd.WEBHOOK_SECRET = s


def _reset_secret():
    wd.WEBHOOK_SECRET = _orig_secret


# ---------------------------------------------------------------------------
# Envelope structure
# ---------------------------------------------------------------------------


class TestBuildEnvelope:
    def test_required_top_level_fields(self):
        env = wd._build_envelope("email.delivered", {"msg": "1"})
        for field in (
            "id",
            "event",
            "timestamp",
            "source",
            "org_id",
            "domain",
            "tags",
            "user_variables",
            "data",
            "metadata",
            "signature",
        ):
            assert field in env, f"Missing field: {field}"

    def test_event_name_preserved(self):
        env = wd._build_envelope("tracking.open", {"x": 1})
        assert env["event"] == "tracking.open"

    def test_id_is_uuid4(self):
        import re

        env = wd._build_envelope("email.inbound", {})
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        assert uuid_pattern.match(env["id"]), f"id is not a UUID4: {env['id']}"

    def test_each_envelope_gets_unique_id(self):
        ids = {wd._build_envelope("email.delivered", {})["id"] for _ in range(50)}
        assert len(ids) == 50, "Duplicate envelope IDs generated"

    def test_tags_passthrough(self):
        env = wd._build_envelope("email.bounced", {}, tags=["campaign:q1", "transactional"])
        assert env["tags"] == ["campaign:q1", "transactional"]

    def test_user_variables_passthrough(self):
        env = wd._build_envelope("tracking.click", {}, user_variables={"order_id": "123"})
        assert env["user_variables"] == {"order_id": "123"}

    def test_tags_defaults_to_empty_list(self):
        env = wd._build_envelope("email.delivered", {})
        assert env["tags"] == []

    def test_user_variables_defaults_to_empty_dict(self):
        env = wd._build_envelope("email.delivered", {})
        assert env["user_variables"] == {}

    def test_org_id_and_domain_passed_through(self):
        env = wd._build_envelope("email.stored", {}, org_id=42, domain="acme.com")
        assert env["org_id"] == 42
        assert env["domain"] == "acme.com"

    def test_data_preserved(self):
        data = {"message_id": "<abc@test.com>", "recipient": "u@e.com"}
        env = wd._build_envelope("email.delivered", data)
        assert env["data"] == data


# ---------------------------------------------------------------------------
# Signature block
# ---------------------------------------------------------------------------


class TestSignatureBlock:
    def setup_method(self):
        _set_secret(SECRET)

    def teardown_method(self):
        _reset_secret()

    def test_signature_block_has_required_fields(self):
        block = wd._build_signature_block(1743158400, "abc123token")
        assert "timestamp" in block
        assert "token" in block
        assert "signature" in block

    def test_signature_block_is_verifiable(self):
        ts = int(time.time())
        token = "randomtoken42"
        block = wd._build_signature_block(ts, token)
        expected = hmac.new(
            SECRET.encode(),
            (str(ts) + token).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert block["signature"] == expected

    def test_signature_block_empty_when_no_secret(self):
        _set_secret("")
        block = wd._build_signature_block(123, "tok")
        assert block["signature"] == ""
        _set_secret(SECRET)

    def test_envelope_signature_block_verifiable(self):
        env = wd._build_envelope("email.delivered", {})
        sig = env["signature"]
        ts = sig["timestamp"]
        token = sig["token"]
        expected = hmac.new(
            SECRET.encode(),
            (str(ts) + token).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert sig["signature"] == expected

    def test_replay_protection_timestamp_is_recent(self):
        env = wd._build_envelope("email.inbound", {})
        ts = env["signature"]["timestamp"]
        assert abs(time.time() - ts) < 5, "Envelope timestamp is too old"


# ---------------------------------------------------------------------------
# Full-body HMAC signing
# ---------------------------------------------------------------------------


class TestSignPayload:
    def setup_method(self):
        _set_secret(SECRET)

    def teardown_method(self):
        _reset_secret()

    def test_sign_payload_produces_hex(self):
        sig = wd._sign_payload(b'{"event":"test"}')
        assert all(c in "0123456789abcdef" for c in sig)

    def test_sign_payload_is_deterministic(self):
        body = b'{"event":"email.delivered"}'
        assert wd._sign_payload(body) == wd._sign_payload(body)

    def test_sign_payload_empty_without_secret(self):
        _set_secret("")
        assert wd._sign_payload(b"anything") == ""
        _set_secret(SECRET)

    def test_sign_payload_matches_manual_hmac(self):
        body = b'{"event":"tracking.open"}'
        expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert wd._sign_payload(body) == expected


# ---------------------------------------------------------------------------
# Delivery — _deliver return values and 406 handling
# ---------------------------------------------------------------------------


class TestDeliver:
    def _make_response(self, status_code, text="OK"):
        r = MagicMock()
        r.status_code = status_code
        r.text = text
        return r

    def test_2xx_returns_true(self):
        env = wd._build_envelope("email.delivered", {})
        with patch("requests.post", return_value=self._make_response(200)):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    result = wd._deliver(env, attempt=1)
        assert result is True

    def test_406_returns_none(self):
        env = wd._build_envelope("email.delivered", {})
        with patch("requests.post", return_value=self._make_response(406)):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    result = wd._deliver(env, attempt=1)
        assert result is None, "HTTP 406 must return None (no-retry signal)"

    def test_5xx_returns_false(self):
        env = wd._build_envelope("email.bounced", {})
        with patch("requests.post", return_value=self._make_response(500)):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    result = wd._deliver(env, attempt=1)
        assert result is False

    def test_4xx_non_406_returns_false(self):
        env = wd._build_envelope("email.bounced", {})
        with patch("requests.post", return_value=self._make_response(429)):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    result = wd._deliver(env, attempt=1)
        assert result is False

    def test_timeout_returns_false(self):
        import requests as req

        env = wd._build_envelope("email.delivered", {})
        with patch("requests.post", side_effect=req.Timeout()):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    result = wd._deliver(env, attempt=1)
        assert result is False

    def test_no_url_returns_false(self):
        env = wd._build_envelope("email.delivered", {})
        orig = wd.WEBHOOK_URL
        wd.WEBHOOK_URL = ""
        result = wd._deliver(env)
        wd.WEBHOOK_URL = orig
        assert result is False

    def test_x_webhook_id_header_is_sent(self):
        env = wd._build_envelope("email.delivered", {})
        captured_headers = {}

        def fake_post(url, data, headers, timeout):
            captured_headers.update(headers)
            return self._make_response(200)

        with patch("requests.post", side_effect=fake_post):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    wd._deliver(env, attempt=1)

        assert "X-Webhook-Id" in captured_headers
        assert captured_headers["X-Webhook-Id"] == env["id"]

    def test_signature_header_is_sent(self):
        env = wd._build_envelope("email.delivered", {})
        captured_headers = {}

        def fake_post(url, data, headers, timeout):
            captured_headers.update(headers)
            return self._make_response(200)

        with patch("requests.post", side_effect=fake_post):
            with patch.object(wd, "_log_delivery"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    wd._deliver(env, attempt=1)

        assert "X-Webhook-Signature" in captured_headers
        assert captured_headers["X-Webhook-Signature"].startswith("sha256=")


# ---------------------------------------------------------------------------
# Retry schedule
# ---------------------------------------------------------------------------


class TestRetrySchedule:
    def test_schedule_has_7_entries(self):
        assert len(wd._RETRY_SCHEDULE) == 7

    def test_schedule_values(self):
        assert wd._RETRY_SCHEDULE == [600, 600, 900, 1800, 3600, 7200, 14400]

    def test_default_max_retries_is_7(self):
        # The env default — actual module value depends on env, but default string is 7
        # We reload with controlled env to verify
        import importlib

        orig = os.environ.get("WEBHOOK_MAX_RETRIES")
        os.environ.pop("WEBHOOK_MAX_RETRIES", None)
        import shared.webhook_dispatcher as fresh

        importlib.reload(fresh)
        assert fresh.WEBHOOK_MAX_RETRIES == 7
        if orig is not None:
            os.environ["WEBHOOK_MAX_RETRIES"] = orig

    def test_406_stops_retries_immediately(self):
        """_deliver_with_retries must stop after a 406 without sleeping."""
        env = wd._build_envelope("email.delivered", {})
        deliver_calls = []

        def mock_deliver(e, attempt=1):
            deliver_calls.append(attempt)
            return None  # Simulate 406

        with patch.object(wd, "_deliver", side_effect=mock_deliver):
            with patch.object(wd, "_write_to_dlq") as mock_dlq:
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    wd._deliver_with_retries(env)

        assert len(deliver_calls) == 1, (
            f"Should stop after first 406, but _deliver called {len(deliver_calls)} times"
        )
        mock_dlq.assert_not_called()  # 406 does not go to DLQ

    def test_success_on_first_attempt_no_retry(self):
        env = wd._build_envelope("email.delivered", {})
        deliver_calls = []

        def mock_deliver(e, attempt=1):
            deliver_calls.append(attempt)
            return True

        with patch.object(wd, "_deliver", side_effect=mock_deliver):
            with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                wd._deliver_with_retries(env)

        assert deliver_calls == [1]

    def test_all_failures_writes_to_dlq(self):
        orig_max = wd.WEBHOOK_MAX_RETRIES
        wd.WEBHOOK_MAX_RETRIES = 3  # Keep test fast
        env = wd._build_envelope("email.delivered", {})

        with patch.object(wd, "_deliver", return_value=False):
            with patch.object(wd, "_write_to_dlq") as mock_dlq:
                with patch.object(wd, "time") as mock_time:
                    mock_time.sleep = MagicMock()
                    with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                        wd._deliver_with_retries(env)

        mock_dlq.assert_called_once()
        wd.WEBHOOK_MAX_RETRIES = orig_max

    def test_retry_uses_schedule_delays(self):
        orig_max = wd.WEBHOOK_MAX_RETRIES
        wd.WEBHOOK_MAX_RETRIES = 3
        env = wd._build_envelope("email.delivered", {})
        sleep_calls = []

        def fake_deliver(e, attempt=1):
            return False  # Always fail

        with patch.object(wd, "_deliver", side_effect=fake_deliver):
            with patch.object(wd, "_write_to_dlq"):
                with patch.object(wd, "WEBHOOK_URL", "http://example.com/hook"):
                    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                        wd._deliver_with_retries(env)

        # With max_retries=3, sleep happens between attempt 1→2 and 2→3
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == wd._RETRY_SCHEDULE[0]  # 600
        assert sleep_calls[1] == wd._RETRY_SCHEDULE[1]  # 600
        wd.WEBHOOK_MAX_RETRIES = orig_max


# ---------------------------------------------------------------------------
# Events class — our naming convention, no email.opened/clicked aliases
# ---------------------------------------------------------------------------


class TestEventsClass:
    def test_tracking_open_exists(self):
        assert wd.Events.TRACKING_OPEN == "tracking.open"

    def test_tracking_click_exists(self):
        assert wd.Events.TRACKING_CLICK == "tracking.click"

    def test_tracking_unsubscribe_exists(self):
        assert wd.Events.TRACKING_UNSUBSCRIBE == "tracking.unsubscribe"

    def test_no_email_opened_alias(self):
        assert not hasattr(wd.Events, "EMAIL_OPENED"), (
            "EMAIL_OPENED should not exist — use tracking.open"
        )

    def test_no_email_clicked_alias(self):
        assert not hasattr(wd.Events, "EMAIL_CLICKED"), (
            "EMAIL_CLICKED should not exist — use tracking.click"
        )

    def test_no_email_unsubscribed_alias(self):
        assert not hasattr(wd.Events, "EMAIL_UNSUBSCRIBED"), (
            "EMAIL_UNSUBSCRIBED should not exist — use tracking.unsubscribe"
        )

    def test_core_delivery_events(self):
        assert wd.Events.EMAIL_ACCEPTED == "email.accepted"
        assert wd.Events.EMAIL_INBOUND == "email.inbound"
        assert wd.Events.EMAIL_DELIVERED == "email.delivered"
        assert wd.Events.EMAIL_BOUNCED == "email.bounced"
        assert wd.Events.EMAIL_DEFERRED == "email.deferred"
        assert wd.Events.EMAIL_REJECTED == "email.rejected"
        assert wd.Events.EMAIL_DROPPED == "email.dropped"

    def test_storage_events(self):
        assert wd.Events.STORAGE_QUOTA_WARNING == "storage.quota.warning"
        assert wd.Events.STORAGE_QUOTA_EXCEEDED == "storage.quota.exceeded"

    def test_all_event_values_are_dotted_strings(self):
        import inspect

        for name, value in inspect.getmembers(wd.Events):
            if name.startswith("_") or callable(value):
                continue
            assert isinstance(value, str), f"Events.{name} is not a string"
            assert "." in value, f"Events.{name}='{value}' has no dot separator"


# ---------------------------------------------------------------------------
# dispatch_event public API
# ---------------------------------------------------------------------------


class TestDispatchEvent:
    def setup_method(self):
        wd.WEBHOOK_URL = "http://example.com/hook"

    def teardown_method(self):
        wd.WEBHOOK_URL = ""

    def test_accepts_tags_and_user_variables(self):
        """dispatch_event must accept tags and user_variables without error."""
        with patch.object(wd, "_ensure_workers"):
            with patch.object(wd, "_enqueue") as mock_enqueue:
                wd.dispatch_event(
                    "email.delivered",
                    {"msg": "1"},
                    tags=["tag:a"],
                    user_variables={"k": "v"},
                )
        mock_enqueue.assert_called_once()
        envelope = mock_enqueue.call_args[0][0]
        assert envelope["tags"] == ["tag:a"]
        assert envelope["user_variables"] == {"k": "v"}

    def test_silently_skips_when_no_url(self):
        wd.WEBHOOK_URL = ""
        with patch.object(wd, "_enqueue") as mock_enqueue:
            wd.dispatch_event("email.delivered", {})
        mock_enqueue.assert_not_called()
        wd.WEBHOOK_URL = "http://example.com/hook"

    def test_envelope_id_in_queued_event(self):
        with patch.object(wd, "_ensure_workers"):
            with patch.object(wd, "_enqueue") as mock_enqueue:
                wd.dispatch_event("tracking.open", {"msg": "x"})
        envelope = mock_enqueue.call_args[0][0]
        assert "id" in envelope
        assert envelope["event"] == "tracking.open"

    def test_dispatch_event_sync_returns_true(self):
        with patch.object(wd, "_build_envelope", return_value={"id": "x", "event": "e"}):
            with patch.object(wd, "_deliver_with_retries"):
                result = wd.dispatch_event_sync("email.delivered", {})
        assert result is True

    def test_dispatch_event_sync_returns_false_when_no_url(self):
        wd.WEBHOOK_URL = ""
        result = wd.dispatch_event_sync("email.delivered", {})
        assert result is False
        wd.WEBHOOK_URL = "http://example.com/hook"
