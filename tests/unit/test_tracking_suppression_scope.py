#!/usr/bin/env python3
"""
Unit tests for the suppression org-scoping fix in worker/api/routes/tracking.py.

POST /suppress and DELETE /suppress/{email} used to proxy to the tracking
service without the caller's organization_id, so the service fell back to org
"default": every tenant's suppression was recorded under the wrong org and
could never be matched or removed. These tests pin the fixed contract:

- a tenant credential always writes/removes in ITS OWN org, and a
  caller-supplied organization_id is overwritten, never honoured;
- a platform credential must name the org explicitly (422 otherwise).

The handlers are exercised via __wrapped__ (the undecorated functions), with
proxy_to_tracking monkeypatched to record what would have gone over the wire
-- auth resolution itself is covered by test_auth.py and the integration
suite.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

import routes.tracking as tracking  # noqa: E402  (needs the sys.path setup above)

TENANT_CTX = {
    "scope": "organization",
    "organization_id": "org_tenant_a",
    "operator_id": None,
    "role": None,
    "permission": "write",
}

PLATFORM_CTX = {
    "scope": "platform",
    "organization_id": None,
    "operator_id": "op_1",
    "role": "operator",
    "permission": "write",
}


class FakeRequest:
    def __init__(self, ctx, body=None, query_params=None):
        self.state = SimpleNamespace(auth_context=ctx)
        self._body = body
        self.query_params = query_params or {}
        self.headers = {}

    async def json(self):
        return self._body


@pytest.fixture
def proxy_recorder(monkeypatch):
    """Replace proxy_to_tracking with a recorder that captures the call."""
    calls = []

    async def fake_proxy(request, endpoint, method="GET", data=None, params=None):
        calls.append({"endpoint": endpoint, "method": method, "data": data, "params": params})
        return {"status": "success"}, 200

    monkeypatch.setattr(tracking, "proxy_to_tracking", fake_proxy)
    return calls


def _body_of(response):
    return json.loads(response.body)


class TestAddSuppressionOrgScope:
    def test_tenant_org_injected_when_absent(self, proxy_recorder):
        req = FakeRequest(TENANT_CTX, body={"email": "gone@example.com", "reason": "bounce"})
        resp = asyncio.run(tracking.add_suppression.__wrapped__(request=req))
        assert resp.status_code == 200
        assert len(proxy_recorder) == 1
        assert proxy_recorder[0]["endpoint"] == "/tracking/suppress"
        assert proxy_recorder[0]["data"]["organization_id"] == "org_tenant_a"
        assert proxy_recorder[0]["data"]["email"] == "gone@example.com"

    def test_tenant_cannot_spoof_another_org(self, proxy_recorder):
        """A caller-supplied organization_id is overwritten, never honoured."""
        req = FakeRequest(
            TENANT_CTX,
            body={"email": "gone@example.com", "organization_id": "org_victim"},
        )
        resp = asyncio.run(tracking.add_suppression.__wrapped__(request=req))
        assert resp.status_code == 200
        assert proxy_recorder[0]["data"]["organization_id"] == "org_tenant_a"

    def test_platform_without_org_rejected(self, proxy_recorder):
        req = FakeRequest(PLATFORM_CTX, body={"email": "gone@example.com"})
        resp = asyncio.run(tracking.add_suppression.__wrapped__(request=req))
        assert resp.status_code == 422
        assert "organization_id" in _body_of(resp)["msg"]
        assert proxy_recorder == []  # nothing proxied

    def test_platform_with_org_forwarded(self, proxy_recorder):
        req = FakeRequest(
            PLATFORM_CTX,
            body={"email": "gone@example.com", "organization_id": "org_named"},
        )
        resp = asyncio.run(tracking.add_suppression.__wrapped__(request=req))
        assert resp.status_code == 200
        assert proxy_recorder[0]["data"]["organization_id"] == "org_named"

    def test_non_object_body_rejected(self, proxy_recorder):
        req = FakeRequest(TENANT_CTX, body=["not", "a", "dict"])
        resp = asyncio.run(tracking.add_suppression.__wrapped__(request=req))
        assert resp.status_code == 422
        assert proxy_recorder == []


class TestRemoveSuppressionOrgScope:
    def test_tenant_org_sent_as_query_param(self, proxy_recorder):
        req = FakeRequest(TENANT_CTX)
        resp = asyncio.run(
            tracking.remove_suppression.__wrapped__(email="gone@example.com", request=req)
        )
        assert resp.status_code == 200
        assert proxy_recorder[0]["endpoint"] == "/tracking/suppress/gone@example.com"
        assert proxy_recorder[0]["method"] == "DELETE"
        assert proxy_recorder[0]["params"] == {"organization_id": "org_tenant_a"}

    def test_tenant_query_org_ignored(self, proxy_recorder):
        """A tenant naming another org in the query string is still pinned
        to its own org."""
        req = FakeRequest(TENANT_CTX, query_params={"organization_id": "org_victim"})
        asyncio.run(tracking.remove_suppression.__wrapped__(email="gone@example.com", request=req))
        assert proxy_recorder[0]["params"] == {"organization_id": "org_tenant_a"}

    def test_platform_without_org_rejected(self, proxy_recorder):
        req = FakeRequest(PLATFORM_CTX)
        resp = asyncio.run(
            tracking.remove_suppression.__wrapped__(email="gone@example.com", request=req)
        )
        assert resp.status_code == 422
        assert proxy_recorder == []

    def test_platform_with_query_org_forwarded(self, proxy_recorder):
        req = FakeRequest(PLATFORM_CTX, query_params={"organization_id": "org_named"})
        resp = asyncio.run(
            tracking.remove_suppression.__wrapped__(email="gone@example.com", request=req)
        )
        assert resp.status_code == 200
        assert proxy_recorder[0]["params"] == {"organization_id": "org_named"}
