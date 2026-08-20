#!/usr/bin/env python3
"""
Authentication utilities for API Gateway — FastAPI version

Uses FastAPI Depends() for clean dependency injection.
Route handlers that need auth should add: `auth: dict = Depends(require_api_key('read'))`
The legacy decorator syntax `@require_api_key('read')` also works via the wrapper.
"""

import hashlib
import inspect
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps
from typing import Literal, TypedDict

from fastapi import HTTPException, Request

# conventions §6: import shared modules explicitly by package, never a bare
# `from ulid_utils import ...` -- that resolves to a local stub because /app
# precedes the shared path. The compose file mounts ./shared at /app/shared,
# so this is importable without touching sys.path.
from shared.ulid_utils import generate_ulid

from .database import get_db_connection

logger = logging.getLogger(__name__)

# Allowed column names for dynamic UPDATE queries (SQL injection prevention)
ALLOWED_DOMAIN_UPDATE_COLUMNS = {
    "domain",
    "active",
    "description",
    "catch_all",
    "dkim_enabled",
    "dmarc_policy",
    "spf_record",
    "custom_mx",
    "settings",
}

ALLOWED_MAILBOX_UPDATE_COLUMNS = {
    "name",
    "quota",
    "active",
    "status",
    "settings",
    "storage_quota",
    "send_quota",
    "receive_quota",
    "auto_reply_enabled",
    "auto_reply_message",
}

# --- Operator sessions (ADR-002, ported from mailyte-email-server) ----------
#
# The Mailyte Console (ADR-004) is CE's admin panel, and a CE self-hoster is
# the platform operator of their own instance. Everything below implements
# the privilege boundary that makes that safe: a platform-scope credential
# is a real identity with a role, a session, and an audit trail -- never the
# static ADMIN_TOKEN_SECRET shared password that require_admin() below still
# guards the legacy admin routes with.
#
# Deliberately its own cookie and its own table. ADR-002's whole premise is
# that a tenant credential must never be able to reach platform scope and
# vice versa, so the two credential types cannot share a namespace.
OPERATOR_SESSION_COOKIE_NAME = "mailyte_operator_session"
OPERATOR_CSRF_COOKIE_NAME = "mailyte_operator_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

# ADR-002 §7: platform credentials are higher blast-radius than tenant ones,
# so they re-authenticate more often. Idle slides forward on activity;
# absolute never does.
OPERATOR_SESSION_IDLE_TIMEOUT = timedelta(hours=4)
OPERATOR_SESSION_ABSOLUTE_TIMEOUT = timedelta(hours=12)

# Requests that mutate state. A GET authenticated by cookie doesn't need a
# CSRF check -- SameSite=Lax already blocks the cross-site cases that matter
# for a read.
_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Operator login rate limiting -- tighter than a mailbox or tenant login,
# because one operator account is the whole platform.
_OPERATOR_LOGIN_WINDOW = timedelta(minutes=15)
_OPERATOR_LOGIN_HARD_LIMIT = 3
_OPERATOR_LOGIN_BACKOFF_START = 1


class AuthContext(TypedDict):
    """The resolved identity of a request, regardless of which credential
    type (tenant API key or operator session) produced it. Every route that
    needs to know who's calling reads this instead of re-deriving scope from
    raw request state."""

    scope: Literal["platform", "organization"]
    organization_id: str | None  # None when scope == 'platform'
    operator_id: str | None  # set only for operator sessions
    role: str | None  # support|operator|admin|owner; None for tenant callers
    permission: str  # read|write


# Ordered least to most privileged (ADR-002 §4). A missing/unrecognised role
# ranks below every real tier, so an unknown value fails closed rather than
# silently passing a role gate.
_ROLE_RANK = {"support": 1, "operator": 2, "admin": 3, "owner": 4}


def _role_satisfies(actual_role: str | None, required_role: str | None) -> bool:
    """True if actual_role's tier is at or above required_role's.

    required_role=None means the route has no role gate beyond scope itself
    -- always satisfied. A missing actual_role (a raw platform-scope API key
    with no operator identity attached) satisfies nothing that IS gated:
    administrative actions require a real operator, not a bare credential.
    """
    if required_role is None:
        return True
    if actual_role is None:
        return False
    return _ROLE_RANK.get(actual_role, 0) >= _ROLE_RANK.get(required_role, 999)


def hash_password(password: str) -> str:
    """Hash password using bcrypt. Raises an error if bcrypt is not available."""
    try:
        import bcrypt
    except ImportError:
        raise RuntimeError(
            "bcrypt package is required for password hashing. Install it with: pip install bcrypt"
        )
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash. Legacy SHA-256 hashes are rejected."""
    try:
        import bcrypt
    except ImportError:
        raise RuntimeError(
            "bcrypt package is required for password verification. "
            "Install it with: pip install bcrypt"
        )

    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

    # Reject legacy SHA-256 hashes — users must reset their password
    logger.warning(
        "Rejected login attempt using legacy SHA-256 password hash. User must reset password."
    )
    return False


_dummy_password_hash_cache: str | None = None


def _dummy_password_hash() -> str:
    """A fixed, valid bcrypt hash to check a login attempt against when the
    account doesn't exist -- so a lookup miss costs the same bcrypt work as a
    real password check and doesn't leak account existence via timing."""
    global _dummy_password_hash_cache
    if _dummy_password_hash_cache is None:
        import bcrypt

        _dummy_password_hash_cache = bcrypt.hashpw(
            b"mailyte-constant-time-dummy", bcrypt.gensalt()
        ).decode("utf-8")
    return _dummy_password_hash_cache


_MIN_PASSWORD_LENGTH = 12

# The most common passwords an attacker's list-based guesser tries first --
# not an exhaustive dictionary, but enough to reject the obvious ones that
# easily clear the length+letters+numbers bar below (e.g. "password1234").
# Matched case-insensitively.
_COMMON_PASSWORDS = frozenset(
    {
        "password1234",
        "password123",
        "password12",
        "passw0rd123",
        "letmein12345",
        "qwertyuiop12",
        "qwerty123456",
        "123456789012",
        "admin1234567",
        "welcome12345",
        "iloveyou1234",
        "changeme12345",
        "football1234",
        "baseball1234",
        "trustno1trustno1",
        "abc123456789",
        "sunshine1234",
        "princess1234",
        "dragon123456",
        "monkey1234567",
        "superman1234",
        "master123456",
        "shadow123456",
        "michael123456",
        "jennifer1234",
        "starwars1234",
        "freedom12345",
        "whatever1234",
    }
)


def validate_password_strength(password: str) -> tuple:
    """Password policy for operator credentials. Returns (is_valid, message).

    An operator password is the credential to the whole platform, so it does
    not get to be weaker than a mailbox password. One function, one policy,
    so a credential minted by one endpoint cannot be weaker than one minted
    by another.
    """
    if len(password) < _MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long"
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain letters"
    if not re.search(r"\d", password):
        return False, "Password must contain numbers"
    if password.lower() in _COMMON_PASSWORDS:
        return False, "Password is too common; choose something less predictable"
    return True, "Valid password"


def cookie_secure() -> bool:
    """Whether operator session/CSRF cookies get the Secure flag.

    Defaults true. A self-hoster bringing the console up over plain
    http://localhost for the first time must set MAILYTE_COOKIE_SECURE=false,
    or the browser silently drops the cookie and login appears to succeed and
    then bounce straight back to the login screen.
    """
    return os.getenv("MAILYTE_COOKIE_SECURE", "true").strip().lower() not in ("false", "0", "no")


def generate_session_token() -> str:
    """256-bit unguessable token for the session cookie. Never stored as-is."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """SHA-256 hex digest -- what's actually stored in
    operator_sessions.token_hash. A DB dump alone can't be replayed as a
    cookie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Failed-login tracking --------------------------------------------------
#
# Reuses the existing failed_auth_attempts table (0001_baseline) rather than
# inventing a new one; migration 0006_operator_rate_limit_service added the
# 'operator' member to its `service` enum so operator lockouts never share a
# bucket with SMTP/IMAP/API failures.


def _login_attempt_blocked(cursor, client_ip: str, email: str, *, service: str = "api") -> bool:
    """True if either the IP or the email dimension is currently locked out."""
    cursor.execute(
        "SELECT 1 FROM failed_auth_attempts "
        "WHERE service = %s AND blocked_until > %s "
        "AND ((client_ip = %s AND username IS NULL) OR (username = %s AND client_ip = '')) "
        "LIMIT 1",
        (service, datetime.now(), client_ip, email),
    )
    return cursor.fetchone() is not None


def _compute_backoff(
    attempt_count: int, now: datetime, *, window: timedelta, hard_limit: int, backoff_start: int
) -> datetime | None:
    if attempt_count >= hard_limit:
        return now + window
    if attempt_count >= backoff_start:
        return now + timedelta(seconds=15 * (2 ** (attempt_count - backoff_start)))
    return None


def _bump_failure_row(
    cursor,
    *,
    client_ip: str,
    username: str | None,
    service: str = "api",
    window: timedelta = _OPERATOR_LOGIN_WINDOW,
    hard_limit: int = _OPERATOR_LOGIN_HARD_LIMIT,
    backoff_start: int = _OPERATOR_LOGIN_BACKOFF_START,
) -> None:
    """Increment the failure counter for one dimension (the ip-only row has
    username=NULL; the email-only row has client_ip=''), starting a new row
    if the last failure fell outside the tracking window."""
    now = datetime.now()
    window_start = now - window
    if username is None:
        cursor.execute(
            "SELECT id, attempt_count FROM failed_auth_attempts "
            "WHERE service = %s AND client_ip = %s AND username IS NULL AND last_attempt_at > %s "
            "ORDER BY last_attempt_at DESC LIMIT 1",
            (service, client_ip, window_start),
        )
    else:
        cursor.execute(
            "SELECT id, attempt_count FROM failed_auth_attempts "
            "WHERE service = %s AND username = %s AND client_ip = '' AND last_attempt_at > %s "
            "ORDER BY last_attempt_at DESC LIMIT 1",
            (service, username, window_start),
        )
    row = cursor.fetchone()

    if row:
        new_count = row["attempt_count"] + 1
        cursor.execute(
            "UPDATE failed_auth_attempts SET attempt_count = %s, last_attempt_at = %s, "
            "blocked_until = %s, failure_reason = %s WHERE id = %s",
            (
                new_count,
                now,
                _compute_backoff(
                    new_count,
                    now,
                    window=window,
                    hard_limit=hard_limit,
                    backoff_start=backoff_start,
                ),
                "invalid_credentials",
                row["id"],
            ),
        )
    else:
        # failed_auth_attempts.id is a bare CHAR(26) ULID column with no
        # DB-side default (0001_baseline's frozen mysqldump), so the id is
        # generated here rather than left to AUTO_INCREMENT.
        cursor.execute(
            "INSERT INTO failed_auth_attempts "
            "(id, client_ip, username, service, failure_reason, attempt_count, "
            "first_attempt_at, last_attempt_at) "
            "VALUES (%s, %s, %s, %s, %s, 1, %s, %s)",
            (generate_ulid(), client_ip, username, service, "invalid_credentials", now, now),
        )


def _operator_login_blocked(cursor, client_ip: str, email: str) -> bool:
    """IP + email dual-dimension lockout check under the 'operator' service
    value (migration 0006): 3 failures in 15 minutes."""
    return _login_attempt_blocked(cursor, client_ip, email, service="operator")


def record_operator_login_failure(cursor, client_ip: str, email: str) -> None:
    """Bump both dimensions -- IP and email -- for a failed operator login."""
    _bump_failure_row(cursor, client_ip=client_ip, username=None, service="operator")
    _bump_failure_row(cursor, client_ip="", username=email, service="operator")


def clear_operator_login_failures(cursor, client_ip: str, email: str) -> None:
    """Reset both dimensions after a successful operator login."""
    cursor.execute(
        "DELETE FROM failed_auth_attempts WHERE service = 'operator' "
        "AND ((client_ip = %s AND username IS NULL) OR (username = %s AND client_ip = ''))",
        (client_ip, email),
    )


def create_api_response(response_type, message, data=None):
    """Create standardized API response"""
    response = {"type": response_type, "msg": message}
    if data is not None:
        response["data"] = data
    return response


def get_org_context(request: Request) -> str | None:
    """Return the organization_id bound to the current API key, if any."""
    api_key_data = getattr(request.state, "api_key_data", None)
    if api_key_data:
        return api_key_data.get("organization_id")
    return None


def _client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _authenticate_api_key(request: Request, permission_level: str = "read") -> dict:
    """Core auth logic — validates API key and returns key data."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "API key required")
        )

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )

    try:
        cursor = conn.cursor(dictionary=True)
        # Try key_id column (current schema)
        cursor.execute(
            """
            SELECT * FROM api_keys
            WHERE key_id = %s AND active = 1
        """,
            (api_key,),
        )

        api_key_data = cursor.fetchone()
        if not api_key_data:
            raise HTTPException(
                status_code=401, detail=create_api_response("error", "Invalid API key")
            )

        # Check permissions from JSON field
        permissions = api_key_data.get("permissions") or {}
        if isinstance(permissions, str):
            import json

            permissions = json.loads(permissions)

        if permission_level == "admin" and not permissions.get("admin_access"):
            raise HTTPException(
                status_code=403, detail=create_api_response("error", "Admin access required")
            )

        if permission_level == "write" and permissions.get("read_only"):
            raise HTTPException(
                status_code=403, detail=create_api_response("error", "Write access required")
            )

        # Update last used
        cursor.execute(
            "UPDATE api_keys SET last_used = %s WHERE key_id = %s", (datetime.now(), api_key)
        )
        conn.commit()

        # Store on request state for downstream access
        request.state.api_key_data = {**api_key_data, **permissions}
        request.state.api_key = api_key
        request.state.organization_id = api_key_data.get("organization_id")

        return api_key_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API key validation error: {e}")
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication failed")
        )
    finally:
        cursor.close()
        conn.close()


def _verify_csrf(request: Request, cookie_name: str = OPERATOR_CSRF_COOKIE_NAME) -> None:
    """Double-submit CSRF check. Only reached for cookie-authenticated
    state-changing requests -- an X-API-Key request carries no ambient
    browser credential and never reaches here."""
    csrf_cookie = request.cookies.get(cookie_name)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=403, detail=create_api_response("error", "CSRF token missing or invalid")
        )


def _authenticate_operator_session(request: Request) -> AuthContext:
    """Validate the mailyte_operator_session cookie.

    Deliberately not shared with the API-key path: an operator session is
    IP-bound and MFA-gated, neither of which applies to an API key, and the
    two must never be reachable through each other's credential (ADR-002's
    whole premise).
    """
    token = request.cookies.get(OPERATOR_SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication required")
        )
    token_hash = hash_session_token(token)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT s.id AS session_id, s.expires_at, s.absolute_expiry, s.revoked_at,
                   s.ip_address AS session_ip, s.mfa_satisfied,
                   o.id AS operator_id, o.email, o.role, o.is_active
            FROM operator_sessions s
            JOIN platform_operators o ON o.id = s.operator_id
            WHERE s.token_hash = %s
            """,
            (token_hash,),
        )
        row = cursor.fetchone()

        now = datetime.now()
        if (
            not row
            or row["revoked_at"] is not None
            or not row["is_active"]
            or row["expires_at"] < now
            or row["absolute_expiry"] < now
        ):
            raise HTTPException(
                status_code=401,
                detail=create_api_response("error", "Operator session expired or invalid"),
            )

        if row["session_ip"] != _client_ip(request):
            # IP-bound (ADR-002 §7) -- a cookie replayed from a different
            # network is rejected outright rather than silently rebinding.
            # Behind a reverse proxy this requires X-Forwarded-For to be set
            # correctly, or every session binds to the proxy's own address
            # and the control is worthless.
            raise HTTPException(
                status_code=401,
                detail=create_api_response("error", "Operator session IP mismatch"),
            )

        if not row["mfa_satisfied"]:
            # A partial session (post-password, pre-MFA) grants nothing at
            # all through this path -- POST /api/v1/platform/auth/mfa
            # re-validates the operator_sessions row directly via
            # get_pending_operator_session, which is why it can complete MFA
            # using a session that would be rejected here.
            raise HTTPException(
                status_code=403, detail=create_api_response("error", "MFA required")
            )

        new_expires_at = min(now + OPERATOR_SESSION_IDLE_TIMEOUT, row["absolute_expiry"])
        cursor.execute(
            "UPDATE operator_sessions SET expires_at = %s WHERE id = %s",
            (new_expires_at, row["session_id"]),
        )
        conn.commit()

        if request.method in _STATE_CHANGING_METHODS:
            _verify_csrf(request, OPERATOR_CSRF_COOKIE_NAME)

        request.state.operator_id = row["operator_id"]
        request.state.operator_email = row["email"]
        request.state.organization_id = None
        request.state.api_key_data = {"organization_id": None, "read": True, "write": True}
        request.state.api_key = None

        return AuthContext(
            scope="platform",
            organization_id=None,
            operator_id=row["operator_id"],
            role=row["role"],
            permission="write",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Operator session validation error: {e}")
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication failed")
        ) from e
    finally:
        cursor.close()
        conn.close()


def get_pending_operator_session(request: Request) -> dict:
    """Validate an operator session cookie WITHOUT requiring mfa_satisfied.

    Used only by routes/platform_auth.py's mfa/mfa-setup endpoints, which
    exist specifically to let a partial (post-password, pre-MFA) session
    complete or enroll in MFA. Every other route goes through
    require_scope/_authenticate_operator_session, which DOES require
    mfa_satisfied=1.

    Still IP-bound and still checks expiry/revocation -- the only thing
    relaxed here is the MFA gate itself.
    """
    token = request.cookies.get(OPERATOR_SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication required")
        )
    token_hash = hash_session_token(token)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT s.id AS session_id, s.expires_at, s.absolute_expiry, s.revoked_at,
                   s.ip_address AS session_ip, s.mfa_satisfied,
                   o.id AS operator_id, o.email, o.role, o.is_active
            FROM operator_sessions s
            JOIN platform_operators o ON o.id = s.operator_id
            WHERE s.token_hash = %s
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        now = datetime.now()
        if (
            not row
            or row["revoked_at"] is not None
            or not row["is_active"]
            or row["expires_at"] < now
            or row["absolute_expiry"] < now
        ):
            raise HTTPException(
                status_code=401,
                detail=create_api_response("error", "Operator session expired or invalid"),
            )
        if row["session_ip"] != _client_ip(request):
            raise HTTPException(
                status_code=401,
                detail=create_api_response("error", "Operator session IP mismatch"),
            )
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pending operator session validation error: {e}")
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication failed")
        ) from e
    finally:
        cursor.close()
        conn.close()


def _resolve_auth(request: Request, permission_level: str = "read") -> dict:
    """Tenant credential resolution.

    CE has exactly one tenant credential type -- the X-API-Key header. (EE
    additionally accepts a browser session cookie backed by its `users` and
    `web_sessions` tables; CE's schema has neither, so there is nothing to
    resolve against and no second branch here.) Kept as its own function so
    _resolve_scope_auth below reads the same in both editions.
    """
    return _authenticate_api_key(request, permission_level)


def _resolve_scope_auth(
    request: Request,
    required_scope: str = "organization",
    permission_level: str = "read",
    required_role: str | None = None,
) -> AuthContext:
    """The scope-aware core. Adds a second credential type -- an operator
    session -- on top of _resolve_auth's tenant resolution, then enforces the
    caller's resolved scope/role against what the route declares it needs.

    A tenant credential can NEVER satisfy required_scope='platform',
    regardless of its own read/write/admin permission flags (ADR-002 §8): the
    api_keys.scope column -- not the permissions JSON -- is the only thing
    that can grant platform scope, and only an operator session or a
    platform-scope key carries it.
    """
    if request.cookies.get(OPERATOR_SESSION_COOKIE_NAME):
        ctx = _authenticate_operator_session(request)
    else:
        key_data = _resolve_auth(request, permission_level)
        ctx = AuthContext(
            scope=key_data.get("scope", "organization"),
            organization_id=key_data.get("organization_id"),
            operator_id=None,
            role=None,
            permission=permission_level,
        )

    # Stored before enforcement, not after -- a DENIED request's ctx (e.g. a
    # tenant credential correctly resolved to scope='organization', then
    # rejected for not being 'platform') is exactly what the audit
    # middleware needs to distinguish "a tenant tried this" from "nobody
    # authenticated at all" in the resulting log row.
    request.state.auth_context = ctx

    if required_scope == "platform" and ctx["scope"] != "platform":
        raise HTTPException(
            status_code=403, detail=create_api_response("error", "Platform scope required")
        )

    if not _role_satisfies(ctx["role"], required_role):
        raise HTTPException(
            status_code=403, detail=create_api_response("error", "Insufficient operator role")
        )

    return ctx


def require_api_key(permission_level="read", scope: str = "organization", role: str | None = None):
    """
    Decorator for routes that require auth.

    Despite the name, accepts an X-API-Key header or an operator session
    cookie -- kept under its original name and signature so none of the
    pre-existing call sites need to change; scope/role are additive kwargs
    with backward-compatible defaults.

    scope='organization' (the default) preserves the previous behaviour
    exactly: a tenant credential in its own org, or a platform credential
    seeing across all orgs (org_filter below handles the "which orgs"
    question inside the route body -- this decorator only gates entry).
    scope='platform' rejects every tenant credential outright, regardless of
    its own permission flags. role, when given, additionally requires an
    operator session at or above that tier (ADR-002 §4); a role-gated route
    can never be reached by a bare platform-scope API key, which has no role
    at all.

    Works by injecting a `request: Request` parameter into the wrapped function
    so FastAPI automatically populates it, then runs auth before the handler.
    """

    def decorator(f):
        # Get the original function's signature
        original_sig = inspect.signature(f)
        original_params = list(original_sig.parameters.values())

        # Check if 'request' is already a parameter
        has_request = any(p.name == "request" for p in original_params)

        @wraps(f)
        async def decorated_function(*args, request: Request, **kwargs):
            # Set before resolving, not after -- the audit middleware must
            # see what a request TARGETED even when the resolve call below
            # rejects it. A tenant key denied at a platform route is exactly
            # the signal ADR-002 §8 cares about most, and it can only be
            # logged if this survives the exception.
            request.state.required_scope = scope
            request.state.required_role = role

            # Run authentication
            _resolve_scope_auth(request, scope, permission_level, role)
            # Call the original handler — pass request only if it expects it
            if has_request:
                return await f(*args, request=request, **kwargs)
            return await f(*args, **kwargs)

        # Rebuild the signature to include `request: Request` if not already there
        if not has_request:
            request_param = inspect.Parameter(
                "request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request
            )
            new_params = [request_param] + original_params
            decorated_function.__signature__ = original_sig.replace(parameters=new_params)

        return decorated_function

    return decorator


def require_scope(scope: str = "organization", permission: str = "read", role: str | None = None):
    """FastAPI Depends()-style scope resolver:
    `ctx: AuthContext = Depends(require_scope('platform', 'write', role='operator'))`.

    Shares _resolve_scope_auth with require_api_key's decorator -- identical
    enforcement, offered as a dependency for routes that prefer that style
    (the platform/* routers) over the decorator form every pre-existing route
    already uses.
    """

    def _dependency(request: Request) -> AuthContext:
        request.state.required_scope = scope
        request.state.required_role = role
        return _resolve_scope_auth(request, scope, permission, role)

    return _dependency


def org_filter(ctx: AuthContext, requested_org: str | None = None) -> tuple:
    """Return (sql_fragment, params) for a WHERE clause -- the single helper
    every tenant-scoped list/query route should use instead of hand-writing
    its own org filter.

    organization scope is always forced to ctx's own org: a caller-supplied
    requested_org is IGNORED, never honoured. That parameter is the most
    likely escalation vector in the system (a client passing a *different*
    org's id and hoping the server trusts it) -- silently dropping it here,
    rather than validating and rejecting it, means every call site gets the
    protection automatically instead of needing its own check.

    platform scope is optionally filtered by requested_org, otherwise sees
    every organization -- "sudo sees all" (ADR-002 §8).
    """
    if ctx["scope"] == "organization":
        return ("organization_id = %s", [ctx["organization_id"]])
    if requested_org:
        return ("organization_id = %s", [requested_org])
    return ("1=1", [])


def verify_domain_scope(cursor, domain: str, ctx: AuthContext) -> None:
    """404 if `domain` doesn't belong to ctx's own organization. Platform
    scope always passes -- it operates across every organization by design.

    For routes that proxy straight to a downstream service keyed by a raw
    `domain` path/query value with no ownership check of their own -- the
    class of bug conventions §8 calls "the most likely escalation vector",
    just reached via a path segment instead of a query parameter.
    """
    if ctx["scope"] == "platform":
        return
    cursor.execute("SELECT organization_id FROM domains WHERE domain = %s", (domain,))
    row = cursor.fetchone()
    if not row or row["organization_id"] != ctx["organization_id"]:
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Domain not found")
        )


def verify_mailbox_scope(cursor, email: str, ctx: AuthContext) -> None:
    """Same as verify_domain_scope, keyed by mailbox email address."""
    if ctx["scope"] == "platform":
        return
    cursor.execute("SELECT organization_id FROM email_accounts WHERE email = %s", (email,))
    row = cursor.fetchone()
    if not row or row["organization_id"] != ctx["organization_id"]:
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Mailbox not found")
        )


def scoped_or_404(row, ctx: AuthContext):
    """Enforce org ownership on a fetched row -- cross-org access is 404,
    never 403.

    Per conventions §8: a 403 confirms the resource exists (just not yours);
    a 404 does not. Platform scope passes any existing row through: "sudo
    sees all" (ADR-002 §8). A missing row is still 404 for everyone -- a
    nonexistent resource must not become reachable just because the caller is
    privileged.
    """
    if row is None:
        raise HTTPException(status_code=404, detail=create_api_response("error", "Not found"))
    if ctx["scope"] == "organization" and row.get("organization_id") != ctx["organization_id"]:
        raise HTTPException(status_code=404, detail=create_api_response("error", "Not found"))
    return row


def require_admin():
    """Decorator for routes that require admin auth via X-Admin-Token header."""

    def decorator(f):
        original_sig = inspect.signature(f)
        original_params = list(original_sig.parameters.values())
        has_request = any(p.name == "request" for p in original_params)

        @wraps(f)
        async def decorated_function(*args, request: Request, **kwargs):
            token = request.headers.get("X-Admin-Token") or request.headers.get("X-Admin-Password")
            expected = os.getenv("ADMIN_TOKEN_SECRET") or os.getenv("ADMIN_PASSWORD")
            if not token or token != expected:
                raise HTTPException(
                    status_code=401,
                    detail=create_api_response("error", "Admin authentication required"),
                )
            if has_request:
                return await f(*args, request=request, **kwargs)
            return await f(*args, **kwargs)

        if not has_request:
            request_param = inspect.Parameter(
                "request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request
            )
            new_params = [request_param] + original_params
            decorated_function.__signature__ = original_sig.replace(parameters=new_params)

        return decorated_function

    return decorator


def validate_update_columns(columns: list, allowed: set) -> list:
    """Whitelist-validate column names for dynamic SQL UPDATE queries."""
    for col in columns:
        if col not in allowed:
            raise ValueError(f"Column '{col}' is not allowed in update operations")
    return columns
