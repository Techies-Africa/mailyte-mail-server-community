"""
Auth coverage regression guard (phase-02 task 2.3).

Enumerates every registered route from the live service's OpenAPI schema and
asserts that calling it with no API key -- and separately, with an obviously
invalid one -- never succeeds (2xx). This is the regression test for the
whole class of bug phase-02 found and fixed: routes that either had no guard
at all, or accepted any non-empty header value without validating it against
the api_keys table.

Runs against the live service (this repo's established integration-test
pattern -- see conftest.py's docstring), not an in-process TestClient, so it
needs no new test dependency and matches how every other test in this
directory already works.
"""

import pytest
import requests

from .conftest import API_BASE

# Keep in sync with worker/api/utils/auth.py:PUBLIC_ENDPOINTS. That module
# can't be imported directly here without dragging in mysql.connector and a
# live DB connection just to read a constant.
#
# '/api/v1/auth/login' (phase-03) is credentials-in, so it cannot itself
# require a credential -- it's rate-limited instead (see test_auth_sessions.py).
PUBLIC_ENDPOINTS = {
    "/health",
    "/api/v1/capabilities/",
    "/api/v1/auth/login",
}

# Endpoints that are unauthenticated *by design* and documented as such --
# see deep-audit.md §2.3: hit by mail recipients with no account at all.
PUBLIC_PATH_PREFIXES = (
    "/api/v1/tracking/pixel/",
    "/api/v1/tracking/click/",
    "/api/v1/tracking/unsubscribe/",
)

# FastAPI's own docs/schema routes are not part of the API surface this test
# is guarding.
NON_API_PREFIXES = ("/static", "/docs", "/redoc", "/openapi.json", "/api-docs", "/api-reference")


def _is_public(path: str) -> bool:
    if path in PUBLIC_ENDPOINTS:
        return True
    if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
        return True
    if any(path.startswith(p) for p in NON_API_PREFIXES):
        return True
    if not path.startswith("/api/"):
        return True
    return False


@pytest.fixture(scope="module")
def openapi_routes():
    """Fetch every (method, path) pair the live service has registered."""
    resp = requests.get(f"{API_BASE}/openapi.json", timeout=15)
    assert resp.status_code == 200, "openapi.json must be reachable to enumerate routes"
    spec = resp.json()
    routes = []
    for path, operations in spec["paths"].items():
        for method in operations:
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            routes.append((method.upper(), path))
    assert len(routes) >= 150, (
        f"Expected at least 150 routes, found {len(routes)} -- did the schema fail to load?"
    )
    return routes


class TestAuthCoverage:
    """Every non-public route must reject requests with no credentials."""

    def test_every_route_rejects_no_api_key(self, openapi_routes):
        unguarded = []
        for method, path in openapi_routes:
            if _is_public(path):
                continue
            try:
                resp = requests.request(method, f"{API_BASE}{path}", timeout=10)
            except requests.RequestException as exc:
                pytest.fail(
                    f"{method} {path} raised {exc} -- route should reject cleanly, not error"
                )
            if resp.status_code < 300:
                unguarded.append(f"{method} {path} -> {resp.status_code}")

        assert not unguarded, (
            f"{len(unguarded)} route(s) returned a 2xx with NO API key at all:\n"
            + "\n".join(unguarded)
        )

    def test_every_route_rejects_garbage_api_key(self, openapi_routes):
        """Regression guard for the exact bug class phase-02 found:
        routes that required *some* header to be present but never validated
        its value against the api_keys table (migration.py's X-Org-Id, and
        compliance.py's locally-redefined require_admin with an unset
        ADMIN_API_KEY env var both fell into this trap).
        """
        bypassed = []
        headers = {
            "X-API-Key": "not-a-real-key-vD9x2",
            "X-Org-Id": "some-other-org-spoofed",
            "X-Admin-Token": "not-a-real-token",
            "Content-Type": "application/json",
        }
        for method, path in openapi_routes:
            if _is_public(path):
                continue
            try:
                resp = requests.request(
                    method, f"{API_BASE}{path}", headers=headers, json={}, timeout=10
                )
            except requests.RequestException as exc:
                pytest.fail(
                    f"{method} {path} raised {exc} -- route should reject cleanly, not error"
                )
            if resp.status_code < 300:
                bypassed.append(f"{method} {path} -> {resp.status_code}")

        assert not bypassed, (
            f"{len(bypassed)} route(s) returned a 2xx with a GARBAGE API key:\n"
            + "\n".join(bypassed)
        )
