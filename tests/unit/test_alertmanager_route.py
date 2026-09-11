#!/usr/bin/env python3
"""
Unit tests for the webhooks service's POST /alertmanager route.

Both Alertmanager receivers (monitoring/alertmanager/alertmanager.yml) post to
http://webhooks:8081/alertmanager; these tests cover the payload -> event
translation and the route handler itself. Outbound delivery is mocked — the
handler forwards through shared.webhook_dispatcher.dispatch_event, which is
monkeypatched so no HTTP request ever leaves the test.

The handler is invoked directly with a stub request (it only awaits
request.json()) because httpx, which FastAPI's TestClient requires, is not
installed in this environment.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
# worker/webhooks/app.py resolves `services.*` against its own directory
# (mirroring /app inside the container), so that directory has to be on the
# path before the module can be imported.
sys.path.insert(0, str(project_root / "worker" / "webhooks"))

# Loaded under a unique module name, NOT `import app`: the migration unit
# tests (test_migration_*.py) import worker/migration/app.py as `app`, and
# whichever test module is collected first would poison sys.modules["app"]
# for the other.
_spec = importlib.util.spec_from_file_location(
    "webhooks_service_app", str(project_root / "worker" / "webhooks" / "app.py")
)
webhooks_app = importlib.util.module_from_spec(_spec)
sys.modules["webhooks_service_app"] = webhooks_app
_spec.loader.exec_module(webhooks_app)


def _firing_payload():
    """A minimal but faithful Alertmanager v4 webhook payload."""
    return {
        "version": "4",
        "groupKey": '{}:{alertname="ServiceDown"}',
        "status": "firing",
        "receiver": "critical-alerts",
        "groupLabels": {"alertname": "ServiceDown"},
        "commonLabels": {"alertname": "ServiceDown", "severity": "critical"},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "ServiceDown", "severity": "critical", "job": "api"},
                "annotations": {
                    "summary": "Service api is down",
                    "description": "api has been down for more than 2 minutes.",
                },
                "startsAt": "2026-08-30T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "abc123",
            },
            {
                "status": "resolved",
                "labels": {"alertname": "ServiceDown", "severity": "critical", "job": "rag"},
                "annotations": {"summary": "Service rag is down"},
                "startsAt": "2026-08-30T09:00:00Z",
                "endsAt": "2026-08-30T09:30:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "def456",
            },
        ],
    }


class _StubRequest:
    """Duck-typed stand-in for fastapi.Request; the handler only calls .json()."""

    def __init__(self, payload=None, raise_json=False):
        self._payload = payload
        self._raise = raise_json

    async def json(self):
        if self._raise:
            raise ValueError("malformed body")
        return self._payload


class TestBuildAlertEvents:
    def test_firing_alert_maps_to_firing_event(self):
        events = webhooks_app.build_alert_events(_firing_payload())
        assert events[0]["event_type"] == webhooks_app.ALERT_EVENT_FIRING
        assert events[0]["data"]["alertname"] == "ServiceDown"
        assert events[0]["data"]["severity"] == "critical"
        assert events[0]["data"]["summary"] == "Service api is down"
        assert events[0]["data"]["labels"]["job"] == "api"

    def test_alert_status_wins_over_group_status(self):
        # Second alert is resolved inside a firing group (send_resolved: true).
        events = webhooks_app.build_alert_events(_firing_payload())
        assert events[1]["event_type"] == webhooks_app.ALERT_EVENT_RESOLVED
        assert events[1]["data"]["status"] == "resolved"

    def test_group_context_carried_on_each_event(self):
        events = webhooks_app.build_alert_events(_firing_payload())
        for event in events:
            assert event["data"]["receiver"] == "critical-alerts"
            assert event["data"]["group_key"] == '{}:{alertname="ServiceDown"}'
            assert event["data"]["external_url"] == "http://alertmanager:9093"

    def test_empty_alerts_yields_no_events(self):
        assert webhooks_app.build_alert_events({"status": "firing", "alerts": []}) == []
        assert webhooks_app.build_alert_events({}) == []

    def test_missing_fields_get_safe_defaults(self):
        events = webhooks_app.build_alert_events({"alerts": [{}]})
        assert len(events) == 1
        assert events[0]["event_type"] == webhooks_app.ALERT_EVENT_FIRING
        assert events[0]["data"]["alertname"] == "unknown"

    def test_non_dict_alert_entries_skipped(self):
        events = webhooks_app.build_alert_events({"alerts": ["garbage", None, {}]})
        assert len(events) == 1


class TestAlertmanagerRoute:
    def test_forwards_each_alert_via_dispatcher(self, monkeypatch):
        dispatched = []
        monkeypatch.setattr(
            webhooks_app,
            "dispatch_event",
            lambda event_type, data, **kwargs: dispatched.append(
                {"event_type": event_type, "data": data, **kwargs}
            ),
        )

        result = asyncio.run(
            webhooks_app.handle_alertmanager_notification(_StubRequest(_firing_payload()))
        )

        assert result == {"status": "success", "alerts_forwarded": 2}
        assert len(dispatched) == 2
        assert dispatched[0]["event_type"] == webhooks_app.ALERT_EVENT_FIRING
        assert dispatched[1]["event_type"] == webhooks_app.ALERT_EVENT_RESOLVED
        assert all(d["source_service"] == "alertmanager" for d in dispatched)

    def test_empty_group_returns_success_without_dispatch(self, monkeypatch):
        dispatched = []
        monkeypatch.setattr(
            webhooks_app, "dispatch_event", lambda *a, **k: dispatched.append((a, k))
        )

        result = asyncio.run(
            webhooks_app.handle_alertmanager_notification(
                _StubRequest({"version": "4", "status": "resolved", "alerts": []})
            )
        )

        assert result == {"status": "success", "alerts_forwarded": 0}
        assert dispatched == []

    def test_invalid_json_returns_400(self, monkeypatch):
        monkeypatch.setattr(
            webhooks_app,
            "dispatch_event",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        )
        response = asyncio.run(
            webhooks_app.handle_alertmanager_notification(_StubRequest(raise_json=True))
        )
        assert response.status_code == 400

    def test_non_dict_payload_returns_400(self, monkeypatch):
        monkeypatch.setattr(
            webhooks_app,
            "dispatch_event",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        )
        response = asyncio.run(
            webhooks_app.handle_alertmanager_notification(_StubRequest(["not", "a", "dict"]))
        )
        assert response.status_code == 400

    def test_route_registered_on_app(self):
        paths = {route.path: getattr(route, "methods", set()) for route in webhooks_app.app.routes}
        assert "/alertmanager" in paths
        assert "POST" in paths["/alertmanager"]
