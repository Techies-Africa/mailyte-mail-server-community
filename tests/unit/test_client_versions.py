"""Unit tests for worker/api/utils/client_versions.py -- the three
load-bearing properties from the mobile team's spec (v1 SS5)."""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from utils.client_versions import (  # noqa: E402
    evaluate,
    normalize_platform,
    parse_version,
)


class TestParseVersion:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.4.2", (1, 4, 2)),
            ("v1.4.2", (1, 4, 2)),
            ("1.4", (1, 4, 0)),
            ("2", (2, 0, 0)),
            ("1.4.2+142", (1, 4, 2)),
            ("1.4.2-beta.1", (1, 4, 2)),
            (" 1.10.0 ", (1, 10, 0)),
        ],
    )
    def test_parses(self, raw, expected):
        assert parse_version(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "latest", "one.two", "-1", "..."])
    def test_unparseable_is_none(self, raw):
        assert parse_version(raw) is None

    def test_numeric_not_lexicographic(self):
        assert parse_version("1.10.0") > parse_version("1.9.9")


class TestNormalizePlatform:
    def test_known_platforms_case_insensitive(self):
        assert normalize_platform("iOS") == "ios"
        assert normalize_platform(" ANDROID ") == "android"

    def test_unknown_is_none(self):
        assert normalize_platform("blackberry") is None
        assert normalize_platform(None) is None


class TestEvaluate:
    def test_unset_platform_is_unrestricted(self, monkeypatch):
        """Nothing configured -> no minimum, never required."""
        for var in ("MIN_CLIENT_VERSION_IOS", "FORCE_UPDATE_IOS", "UPDATE_URL_IOS"):
            monkeypatch.delenv(var, raising=False)
        headers = evaluate("ios", "0.0.1")
        assert "X-Min-Client-Version" not in headers
        assert headers["X-Update-Required"] == "false"

    def test_unknown_platform_gets_no_headers(self, monkeypatch):
        monkeypatch.setenv("MIN_CLIENT_VERSION_IOS", "9.9.9")
        assert evaluate("unknown-os", "0.0.1") == {}
        assert evaluate(None, None) == {}

    def test_below_minimum_requires_update(self, monkeypatch):
        monkeypatch.setenv("MIN_CLIENT_VERSION_ANDROID", "1.4.0")
        headers = evaluate("android", "1.3.9")
        assert headers["X-Min-Client-Version"] == "1.4.0"
        assert headers["X-Update-Required"] == "true"

    def test_at_or_above_minimum_does_not(self, monkeypatch):
        monkeypatch.setenv("MIN_CLIENT_VERSION_ANDROID", "1.4.0")
        assert evaluate("android", "1.4.0")["X-Update-Required"] == "false"
        assert evaluate("android", "1.4.2")["X-Update-Required"] == "false"
        assert evaluate("android", "2.0.0")["X-Update-Required"] == "false"

    def test_unparseable_client_version_never_forces(self, monkeypatch):
        monkeypatch.setenv("MIN_CLIENT_VERSION_IOS", "1.4.0")
        assert evaluate("ios", "garbage")["X-Update-Required"] == "false"
        assert evaluate("ios", None)["X-Update-Required"] == "false"

    def test_unparseable_minimum_never_forces(self, monkeypatch):
        monkeypatch.setenv("MIN_CLIENT_VERSION_IOS", "not-a-version")
        assert evaluate("ios", "0.0.1")["X-Update-Required"] == "false"

    def test_force_flag_is_authoritative(self, monkeypatch):
        monkeypatch.delenv("MIN_CLIENT_VERSION_ANDROID", raising=False)
        monkeypatch.setenv("FORCE_UPDATE_ANDROID", "1")
        assert evaluate("android", "99.0.0")["X-Update-Required"] == "true"
        # ...and even when the version is unparseable: force means force.
        assert evaluate("android", "???")["X-Update-Required"] == "true"

    def test_force_flag_false_values(self, monkeypatch):
        monkeypatch.setenv("FORCE_UPDATE_ANDROID", "0")
        assert evaluate("android", "1.0.0")["X-Update-Required"] == "false"

    def test_platforms_are_independent(self, monkeypatch):
        monkeypatch.setenv("MIN_CLIENT_VERSION_IOS", "1.5.0")
        monkeypatch.delenv("MIN_CLIENT_VERSION_ANDROID", raising=False)
        monkeypatch.delenv("FORCE_UPDATE_ANDROID", raising=False)
        assert evaluate("ios", "1.4.0")["X-Update-Required"] == "true"
        assert evaluate("android", "1.4.0")["X-Update-Required"] == "false"

    def test_latest_and_url_passthrough(self, monkeypatch):
        monkeypatch.setenv("LATEST_CLIENT_VERSION_IOS", "1.6.0")
        monkeypatch.setenv("UPDATE_URL_IOS", "https://apps.apple.com/app/id1")
        headers = evaluate("ios", "1.6.0")
        assert headers["X-Latest-Client-Version"] == "1.6.0"
        assert headers["X-Update-Url"] == "https://apps.apple.com/app/id1"


class TestMiddleware:
    def test_stamps_headers_on_response(self, monkeypatch):
        import asyncio
        import types

        from utils.client_versions import client_version_middleware

        monkeypatch.setenv("MIN_CLIENT_VERSION_IOS", "1.4.0")
        request = types.SimpleNamespace(
            headers={"X-Client-Platform": "ios", "X-Client-Version": "1.2.0"}
        )
        response = types.SimpleNamespace(headers={})

        async def call_next(_req):
            return response

        result = asyncio.run(client_version_middleware(request, call_next))
        assert result is response
        assert response.headers["X-Update-Required"] == "true"
        assert response.headers["X-Min-Client-Version"] == "1.4.0"
