"""
Operator audit logging (ADR-002 §6).

Written by middleware (app.py's operator_audit_middleware), never by
individual route handlers -- a handler that forgets to log is a security
hole, and middleware cannot forget. Logs every request that either targeted a
platform-scope route or was made by a platform-scope credential, regardless
of outcome: a tenant credential denied at a platform-only endpoint is exactly
the compromise signal ADR-002 §8 cares about, and it must be captured even
though that caller has no operator identity at all (hence
operator_id/operator_email being nullable -- see migration
0005_operator_identity's docstring for why this deviates from ADR-002's own
draft schema).
"""

import json
import logging
from typing import Any

from fastapi import Request, Response

from .database import get_db_connection

logger = logging.getLogger(__name__)

# Recursively stripped from request_body before storage, case-insensitively
# matched against JSON key names. ADR-002 §6: "request_body JSON -- redacted
# of secrets". The audit trail records what was attempted, never the
# credential that attempted it.
_REDACTED_KEYS = {
    "password",
    "token",
    "secret",
    "api_key",
    "authorization",
    "admin_password",
    "admin_token",
}


def redact(raw_body: bytes) -> dict | None:
    """Parse a raw request body as JSON and strip secret-shaped fields.
    Returns None for an empty or non-JSON body -- not every audited request
    has one (GETs, proxy pass-throughs with no body)."""
    if not raw_body:
        return None
    try:
        data = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return None
    return _redact_value(data)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***redacted***" if k.lower() in _REDACTED_KEYS else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def derive_action(request: Request) -> str:
    """A greppable action label. Prefers the matched route's name (FastAPI
    defaults this to the endpoint function's __name__, e.g. "restart_service"
    -- already the "system.action"-shaped identifier ADR-002's example
    envisions, with zero per-route registry to maintain) and falls back to
    "METHOD path" for anything that didn't match a route at all (e.g. a 404
    on a nonexistent path)."""
    route = request.scope.get("route")
    name = getattr(route, "name", None)
    return name or f"{request.method} {request.url.path}"


def _target_from_path(request: Request) -> tuple:
    """Best-effort target_type/target_id from the first path parameter, if
    any (e.g. {'service_name': 'postfix'} -> ('service_name', 'postfix'))."""
    params = request.path_params
    if not params:
        return None, None
    key = next(iter(params))
    return key, str(params[key])


def _affected_org(request: Request) -> str | None:
    if "organization_id" in request.path_params:
        return request.path_params["organization_id"]
    return request.path_params.get("org_id") or getattr(request.state, "organization_id", None)


def write_operator_audit(
    *,
    operator_id: str | None,
    operator_email: str | None,
    caller_scope: str | None,
    action: str,
    target_type: str | None,
    target_id: str | None,
    organization_id: str | None,
    request_body: dict | None,
    result: str,
    ip_address: str,
    correlation_id: str | None,
) -> None:
    conn = get_db_connection()
    if not conn:
        # Fail open on the audit write itself -- refusing to serve a request
        # because the audit table is unreachable would turn a logging outage
        # into an availability outage, which is worse.
        logger.error("operator_audit write skipped: database unavailable")
        return
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO operator_audit
                (operator_id, operator_email, caller_scope, action, target_type, target_id,
                 organization_id, request_body, result, ip_address, correlation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                operator_id,
                operator_email,
                caller_scope,
                action,
                target_type,
                target_id,
                organization_id,
                json.dumps(request_body) if request_body is not None else None,
                result,
                ip_address,
                correlation_id,
            ),
        )
        conn.commit()
        cursor.close()
    except Exception as exc:
        logger.error(f"operator_audit write failed: {exc}")
    finally:
        conn.close()


async def maybe_audit(request: Request, response: Response, raw_body: bytes) -> None:
    """Called from app.py's operator_audit_middleware after the response is
    known. Logs when either the ROUTE required platform scope
    (require_api_key(..., scope='platform', ...) / require_scope('platform'))
    or the CALLER actually resolved to platform scope -- the first covers a
    tenant credential denied outright (ctx never got set to platform), the
    second covers a platform credential's cross-org reads of tenant-scoped
    resources (ADR-002 §8: "sudo sees all" is itself a privileged action
    worth logging even though the route's own required_scope is
    'organization').
    """
    required_scope = getattr(request.state, "required_scope", None)
    ctx = getattr(request.state, "auth_context", None)
    caller_scope = ctx.get("scope") if ctx else None

    if required_scope != "platform" and caller_scope != "platform":
        return

    target_type, target_id = _target_from_path(request)
    result = (
        "success"
        if response.status_code < 400
        else ("denied" if response.status_code in (401, 403) else "failure")
    )

    write_operator_audit(
        operator_id=(ctx or {}).get("operator_id"),
        operator_email=getattr(request.state, "operator_email", None),
        caller_scope=caller_scope,
        action=derive_action(request),
        target_type=target_type,
        target_id=target_id,
        organization_id=_affected_org(request),
        request_body=redact(raw_body),
        result=result,
        ip_address=(request.client.host if request.client else "unknown"),
        # CE's app.py has no correlation-id middleware, so this is normally
        # None. Kept as a header read rather than dropped: a reverse proxy in
        # front of the API can set it, and the column is nullable either way.
        correlation_id=response.headers.get("X-Correlation-Id"),
    )
