#!/usr/bin/env python3
"""
Unit tests for shared/webhook_dispatcher.py
"""

import json
import time
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Set env vars before import
os.environ["WEBHOOK_URL"] = "http://example.com/webhook"
os.environ["WEBHOOK_SECRET"] = "test-secret-key-abc123"
os.environ["WEBHOOK_MAX_RETRIES"] = "2"
os.environ["WEBHOOK_WORKERS"] = "1"

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.webhook_dispatcher import (
    _build_envelope,
    _sign_payload,
    dispatch_event,
    get_dispatcher_stats,
    Events,
    WEBHOOK_MAX_RETRIES,
)


class TestBuildEnvelope:
    def test_basic_envelope(self):
        env = _build_envelope("email.inbound", {"message_id": "abc"})
        assert env["event"] == "email.inbound"
        assert env["data"] == {"message_id": "abc"}
        assert "timestamp" in env
        assert "source" in env

    def test_envelope_with_org(self):
        env = _build_envelope("email.inbound", {}, org_id=42, domain="example.com")
        assert env["org_id"] == 42
        assert env["domain"] == "example.com"

    def test_envelope_timestamp_format(self):
        env = _build_envelope("test.event", {})
        ts = env["timestamp"]
        assert "T" in ts

    def test_envelope_metadata(self):
        env = _build_envelope("test.event", {}, metadata={"key": "value"})
        assert env["metadata"] == {"key": "value"}

    def test_envelope_empty_metadata_default(self):
        env = _build_envelope("test.event", {})
        assert env["metadata"] == {}

    def test_envelope_source_service(self):
        env = _build_envelope("test.event", {}, source_service="my-service")
        assert env["source"] == "my-service"


class TestSignPayload:
    def test_signature_generation(self):
        payload = b'{"event": "test"}'
        sig = _sign_payload(payload)
        assert len(sig) == 64  # SHA256 hex = 64 chars

    def test_signature_consistency(self):
        payload = b"test payload"
        sig1 = _sign_payload(payload)
        sig2 = _sign_payload(payload)
        assert sig1 == sig2

    def test_different_payloads_different_signatures(self):
        sig1 = _sign_payload(b"payload1")
        sig2 = _sign_payload(b"payload2")
        assert sig1 != sig2

    def test_no_secret_returns_empty(self):
        from shared import webhook_dispatcher

        old_secret = webhook_dispatcher.WEBHOOK_SECRET
        webhook_dispatcher.WEBHOOK_SECRET = ""
        result = _sign_payload(b"test")
        webhook_dispatcher.WEBHOOK_SECRET = old_secret
        assert result == ""


class TestDispatchEvent:
    def test_dispatch_with_no_webhook_url(self):
        """dispatch_event should silently skip when no URL configured."""
        from shared import webhook_dispatcher

        orig = webhook_dispatcher.WEBHOOK_URL
        webhook_dispatcher.WEBHOOK_URL = ""
        try:
            dispatch_event("email.inbound", {"test": True})
        finally:
            webhook_dispatcher.WEBHOOK_URL = orig

    def test_dispatch_increments_stats(self):
        from shared import webhook_dispatcher

        orig_url = webhook_dispatcher.WEBHOOK_URL
        webhook_dispatcher.WEBHOOK_URL = "http://example.com/webhook"
        stats_before = webhook_dispatcher._stats["dispatched"]
        try:
            dispatch_event("test.event", {"data": 1})
            time.sleep(0.1)
        finally:
            webhook_dispatcher.WEBHOOK_URL = orig_url
        assert webhook_dispatcher._stats["dispatched"] > stats_before

    def test_events_class_has_email_constants(self):
        assert hasattr(Events, "EMAIL_INBOUND")
        assert hasattr(Events, "EMAIL_OUTBOUND")
        assert hasattr(Events, "EMAIL_BOUNCED")

    def test_events_class_has_auth_constants(self):
        assert hasattr(Events, "AUTH_LOGIN_SUCCESS")
        assert hasattr(Events, "AUTH_LOGIN_FAILURE")

    def test_events_class_has_org_constants(self):
        assert hasattr(Events, "DOMAIN_ADDED")
        assert hasattr(Events, "MAILBOX_CREATED")
        assert hasattr(Events, "WEBHOOK_TEST")

    def test_event_constant_format(self):
        assert "." in Events.EMAIL_INBOUND
        assert "." in Events.AUTH_LOGIN_FAILURE
        assert "." in Events.SECURITY_BRUTE_FORCE


class TestGetDispatcherStats:
    def test_stats_structure(self):
        stats = get_dispatcher_stats()
        assert "dispatched" in stats
        assert "delivered" in stats
        assert "failed" in stats
        assert "dropped" in stats
        assert "queue_size" in stats
        assert "queue_capacity" in stats
        assert "workers" in stats
        assert "webhook_url_configured" in stats

    def test_stats_webhook_url_configured(self):
        from shared import webhook_dispatcher

        orig = webhook_dispatcher.WEBHOOK_URL
        webhook_dispatcher.WEBHOOK_URL = "http://example.com/wh"
        stats = get_dispatcher_stats()
        assert stats["webhook_url_configured"] is True
        webhook_dispatcher.WEBHOOK_URL = orig

    def test_stats_webhook_url_not_configured(self):
        from shared import webhook_dispatcher

        orig = webhook_dispatcher.WEBHOOK_URL
        webhook_dispatcher.WEBHOOK_URL = ""
        stats = get_dispatcher_stats()
        assert stats["webhook_url_configured"] is False
        webhook_dispatcher.WEBHOOK_URL = orig


class TestEventsConstants:
    def test_all_events_have_dot_notation(self):
        for name in dir(Events):
            if not name.startswith("_"):
                val = getattr(Events, name)
                if isinstance(val, str):
                    assert "." in val, f"{name}={val!r} should use dot notation"

    def test_no_duplicate_values(self):
        values = [
            getattr(Events, name)
            for name in dir(Events)
            if not name.startswith("_") and isinstance(getattr(Events, name), str)
        ]
        assert len(values) == len(set(values)), "Duplicate event type values found"

    def test_email_events_values(self):
        assert Events.EMAIL_INBOUND == "email.inbound"
        assert Events.EMAIL_OUTBOUND == "email.outbound"

    def test_org_events_values(self):
        assert Events.ORG_CREATED == "org.created"
        assert Events.DOMAIN_ADDED == "domain.added"
        assert Events.MAILBOX_CREATED == "mailbox.created"
