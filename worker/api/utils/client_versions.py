"""Client version negotiation for native and web clients.

Clients send, on every request:

    X-Client-Platform: ios | android | web | macos | windows | linux
    X-Client-Version:  1.4.2
    X-Client-Build:    142

The API answers, on every response, from per-platform environment:

    X-Min-Client-Version:    MIN_CLIENT_VERSION_<PLATFORM>
    X-Latest-Client-Version: LATEST_CLIENT_VERSION_<PLATFORM>   (optional)
    X-Update-Required:       true | false
    X-Update-Url:            UPDATE_URL_<PLATFORM>               (optional)

Per platform because the clients ship on different cadences -- an iOS build
can sit in review for days after Android is live.

Three properties are load-bearing and pinned by tests:

* An unset platform is unrestricted. Deploying this must never lock every
  client out of a deployment that has configured nothing.
* An unparseable version never forces an update. Losing access to your mail
  over a malformed version string is worse than an old build running on.
* ``FORCE_UPDATE_<PLATFORM>`` makes ``X-Update-Required: true`` regardless of
  version, so a bad release can be blocked without shipping a new minimum --
  and clearing it pauses a rollout without a version bump.

The server only *advises*: nothing here rejects a request. Enforcement is
the client's job (it gates its UI on ``X-Update-Required``), which keeps a
misconfigured minimum from turning into an outage.
"""

from __future__ import annotations

import os
import re

PLATFORMS = ("ios", "android", "web", "macos", "windows", "linux")

_TRUTHY = {"1", "true", "yes", "on"}
_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(raw: str | None) -> tuple[int, int, int] | None:
    """``"1.4.2"`` -> ``(1, 4, 2)``; tolerates ``v1.4``, ``1.4.2+142``,
    ``1.4.2-beta``; returns None for anything without a leading number."""
    if not raw:
        return None
    match = _VERSION_RE.match(raw)
    if not match:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor or 0), int(patch or 0))


def normalize_platform(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    return value if value in PLATFORMS else None


def _env(name: str, platform: str) -> str:
    return os.getenv(f"{name}_{platform.upper()}", "").strip()


def evaluate(platform_header: str | None, version_header: str | None) -> dict[str, str]:
    """Response headers for one request. Empty dict when nothing applies."""
    platform = normalize_platform(platform_header)
    if platform is None:
        return {}

    minimum = _env("MIN_CLIENT_VERSION", platform)
    latest = _env("LATEST_CLIENT_VERSION", platform)
    update_url = _env("UPDATE_URL", platform)
    forced = _env("FORCE_UPDATE", platform).lower() in _TRUTHY

    headers: dict[str, str] = {}
    if minimum:
        headers["X-Min-Client-Version"] = minimum
    if latest:
        headers["X-Latest-Client-Version"] = latest
    if update_url:
        headers["X-Update-Url"] = update_url

    required = False
    if forced:
        required = True
    elif minimum:
        current = parse_version(version_header)
        floor = parse_version(minimum)
        # Unparseable on either side never forces: a malformed minimum in the
        # environment is an operator mistake, not a reason to strand clients.
        if current is not None and floor is not None and current < floor:
            required = True

    headers["X-Update-Required"] = "true" if required else "false"
    return headers


async def client_version_middleware(request, call_next):
    """Starlette HTTP middleware: stamp the version-negotiation headers on
    every response. Never blocks or alters the request."""
    response = await call_next(request)
    for key, value in evaluate(
        request.headers.get("X-Client-Platform"),
        request.headers.get("X-Client-Version"),
    ).items():
        response.headers[key] = value
    return response
