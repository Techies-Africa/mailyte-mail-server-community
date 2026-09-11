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

# Endpoints that are intentionally reachable without an API key. Every other
# endpoint under /api must carry a require_api_key guard (decorator or
# Depends style). Enforced by tests/integration/test_auth_coverage.py --
# that test runs against the live HTTP service from outside the container
# (this repo's established integration-test pattern, see conftest.py), so it
# keeps its own copy of this set rather than importing this module directly.
# Keep the two in sync.
#
# '/health' is the root-level container healthcheck in app.py (what Docker's
# healthcheck.test and uptime probes call) -- it must never require auth or
# the stack cannot report itself healthy. It is NOT the same route as
# GET /api/v1/monitoring/health, which returns internal component status and
# is guarded like every other route in monitoring.py.
#
# '/api/v1/capabilities/' is the CE/Pro capability manifest (phase-02 task
# 2.5) -- mailyte-web fetches it before a user has logged in, so it cannot
# require a key, and it must never expose tenant data. Trailing slash
# matches the path FastAPI actually registers (verify via /openapi.json).
#
# '/api/v1/auth/login' is phase-03's browser login -- credentials go in,
# there is by definition no credential to present yet, so it cannot itself
# require one. It is rate-limited (brute-force tracking below) instead of
# key-gated. Keep this set in sync with tests/integration/test_auth_coverage.py.
PUBLIC_ENDPOINTS = {
    "/health",
    "/api/v1/capabilities/",
    "/api/v1/auth/login",
}

# --- Browser sessions (phase-03) -------------------------------------------
#
# CE talks to this API directly from a browser (ADR-001) and cannot hold a
# long-lived X-API-Key client-side. Sessions are a thin layer over the same
# api_keys-style permission checks below -- not a parallel auth system.

SESSION_COOKIE_NAME = "mailyte_session"
CSRF_COOKIE_NAME = "mailyte_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

SESSION_IDLE_TIMEOUT = timedelta(hours=8)
SESSION_ABSOLUTE_TIMEOUT = timedelta(days=30)

# Requests that mutate state. GETs authenticated by cookie don't need a CSRF
# check -- SameSite=Lax already blocks the cross-site cases that matter for
# a read, and requiring the header on every read buys nothing.
_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# --- Operator sessions (phase-06, ADR-002) ----------------------------------
#
# A deliberately separate cookie and table (operator_sessions, not
# web_sessions) from the tenant machinery above -- ADR-002's entire premise
# is that a tenant credential must never be able to reach platform scope and
# vice versa, so the two session types cannot share a cookie name or a table.
OPERATOR_SESSION_COOKIE_NAME = "mailyte_operator_session"
OPERATOR_CSRF_COOKIE_NAME = "mailyte_operator_csrf"

# ADR-002 SS7: shorter than tenant sessions (8h idle / 30d absolute) --
# platform credentials are higher blast-radius, so they re-authenticate more
# often.
OPERATOR_SESSION_IDLE_TIMEOUT = timedelta(hours=4)
OPERATOR_SESSION_ABSOLUTE_TIMEOUT = timedelta(hours=12)

# Operator login rate limiting (task 6.7) -- tighter than tenant login's
# 5/15min, per the phase doc's explicit instruction to rate-limit harder here.
_OPERATOR_LOGIN_WINDOW = timedelta(minutes=15)
_OPERATOR_LOGIN_HARD_LIMIT = 3
_OPERATOR_LOGIN_BACKOFF_START = 1


class AuthContext(TypedDict):
    """The resolved identity of a request, regardless of which of the three
    credential types (tenant API key, tenant session, operator session)
    produced it. Every route that needs to know who's calling reads this
    instead of re-deriving scope from raw request state."""

    scope: Literal["platform", "organization"]
    organization_id: str | None  # None when scope == 'platform'
    operator_id: str | None  # set only for operator sessions
    role: str | None  # support|operator|admin|owner; None for tenant/raw-key callers
    permission: str  # read|write


# Ordered least to most privileged (ADR-002 SS4). A missing/unrecognised role
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


# Brute-force tracking (task 3.4) reuses the existing failed_auth_attempts
# table (migration 002_security_hardening.sql) rather than inventing a new
# one -- it already has exactly the columns this needs and was otherwise
# unused by any Python code. service='api' is the closest existing enum
# value; there is no 'web' member and adding one is a schema change this
# phase doesn't need.
_BRUTE_FORCE_WINDOW = timedelta(minutes=15)
_BRUTE_FORCE_HARD_LIMIT = 5  # failures within the window -> hard 429 lockout
_BRUTE_FORCE_BACKOFF_START = 3  # failures after which a growing delay kicks in

# API key validation throttling (phase-07 H3) -- utils/auth.py previously had
# no throttling on API key guessing at all. Its own service value
# ('api_key', migration 0004) and its own, looser window: unlike a tenant
# login, there's no email dimension to also track (a bad key isn't
# associated with an identity the way a login attempt is), and a
# legitimate integration mistyping a key a handful of times in a row is
# more common than a handful of wrong passwords, hence the higher limit.
_API_KEY_FAILURE_WINDOW = timedelta(minutes=5)
_API_KEY_FAILURE_HARD_LIMIT = 20
_API_KEY_FAILURE_BACKOFF_START = 5

_dummy_password_hash_cache: str | None = None


def _dummy_password_hash() -> str:
    """A fixed, valid bcrypt hash to check a login attempt against when the
    account doesn't exist -- so a lookup miss costs the same bcrypt work as
    a real password check and doesn't leak user existence via timing."""
    global _dummy_password_hash_cache
    if _dummy_password_hash_cache is None:
        import bcrypt

        _dummy_password_hash_cache = bcrypt.hashpw(
            b"mailyte-constant-time-dummy", bcrypt.gensalt()
        ).decode("utf-8")
    return _dummy_password_hash_cache


def cookie_secure() -> bool:
    """Whether session/CSRF cookies get the Secure flag.

    Defaults true; must be disabled for a CE user testing over plain
    http://localhost during first setup (phase-03 risk table).
    """
    return os.getenv("MAILYTE_COOKIE_SECURE", "true").strip().lower() not in ("false", "0", "no")


def generate_session_token() -> str:
    """128-bit unguessable token for the session cookie. Never stored as-is."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """SHA-256 hex digest -- what's actually stored in web_sessions.token_hash.
    A DB dump alone can't be replayed as a cookie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    window: timedelta = _BRUTE_FORCE_WINDOW,
    hard_limit: int = _BRUTE_FORCE_HARD_LIMIT,
    backoff_start: int = _BRUTE_FORCE_BACKOFF_START,
) -> None:
    """Increment the failure counter for one dimension (ip-only row has
    username=NULL; email-only row has client_ip=''), starting a new row if
    the last failure fell outside the tracking window."""
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
        # id has no DB-side default -- migration 009_ulid_safe.sql converted
        # this table's PK from BIGINT AUTO_INCREMENT (as migration
        # 002_security_hardening.sql originally defined it) to a bare
        # CHAR(26) ULID column, same as every other table it touched.
        # Verified live via DESCRIBE failed_auth_attempts.
        cursor.execute(
            "INSERT INTO failed_auth_attempts "
            "(id, client_ip, username, service, failure_reason, attempt_count, first_attempt_at, last_attempt_at) "
            "VALUES (%s, %s, %s, %s, %s, 1, %s, %s)",
            (generate_ulid(), client_ip, username, service, "invalid_credentials", now, now),
        )


def record_login_failure(cursor, client_ip: str, email: str) -> None:
    """Bump both dimensions -- IP and email -- for a failed login attempt."""
    _bump_failure_row(cursor, client_ip=client_ip, username=None)
    _bump_failure_row(cursor, client_ip="", username=email)


def clear_login_failures(cursor, client_ip: str, email: str) -> None:
    """Reset both dimensions after a successful login."""
    cursor.execute(
        "DELETE FROM failed_auth_attempts WHERE service = 'api' "
        "AND ((client_ip = %s AND username IS NULL) OR (username = %s AND client_ip = ''))",
        (client_ip, email),
    )


def _api_key_attempt_blocked(cursor, client_ip: str) -> bool:
    """True if this IP has hit the API-key failure limit (phase-07 H3).
    IP-only -- an invalid key has no email/identity dimension to also track."""
    return _login_attempt_blocked(cursor, client_ip, "__none__", service="api_key")


def record_api_key_failure(cursor, client_ip: str) -> None:
    _bump_failure_row(
        cursor,
        client_ip=client_ip,
        username=None,
        service="api_key",
        window=_API_KEY_FAILURE_WINDOW,
        hard_limit=_API_KEY_FAILURE_HARD_LIMIT,
        backoff_start=_API_KEY_FAILURE_BACKOFF_START,
    )


def clear_api_key_failures(cursor, client_ip: str) -> None:
    cursor.execute(
        "DELETE FROM failed_auth_attempts WHERE service = 'api_key' AND client_ip = %s AND username IS NULL",
        (client_ip,),
    )


def _operator_login_blocked(cursor, client_ip: str, email: str) -> bool:
    """Same IP+email dual-dimension check as tenant login, under the
    'operator' service value (migration 0007) and a tighter limit
    (task 6.7: 3/15min vs tenant login's 5/15min)."""
    return _login_attempt_blocked(cursor, client_ip, email, service="operator")


def record_operator_login_failure(cursor, client_ip: str, email: str) -> None:
    _bump_failure_row(
        cursor,
        client_ip=client_ip,
        username=None,
        service="operator",
        window=_OPERATOR_LOGIN_WINDOW,
        hard_limit=_OPERATOR_LOGIN_HARD_LIMIT,
        backoff_start=_OPERATOR_LOGIN_BACKOFF_START,
    )
    _bump_failure_row(
        cursor,
        client_ip="",
        username=email,
        service="operator",
        window=_OPERATOR_LOGIN_WINDOW,
        hard_limit=_OPERATOR_LOGIN_HARD_LIMIT,
        backoff_start=_OPERATOR_LOGIN_BACKOFF_START,
    )


def clear_operator_login_failures(cursor, client_ip: str, email: str) -> None:
    cursor.execute(
        "DELETE FROM failed_auth_attempts WHERE service = 'operator' "
        "AND ((client_ip = %s AND username IS NULL) OR (username = %s AND client_ip = ''))",
        (client_ip, email),
    )


_MIN_PASSWORD_LENGTH = 12

# The most common passwords/patterns an attacker's list-based guesser tries
# first -- not an exhaustive top-10k dictionary, but enough to reject the
# obvious ones that easily clear the length+letters+numbers bar below
# (e.g. "password1234" or "qwertyuiop12"). Matched case-insensitively.
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
    """Single source of truth for password policy (phase-07 H7,
    security-model.md H7) -- previously enforced three different ways
    (mailboxes.py's own validate_password() at 8 chars, and two bare
    Pydantic min_length=8 Field constraints in auth.py/bootstrap.py that
    couldn't express the letters/numbers/blocklist checks at all), which
    let the same weak password pass in one place and fail in another
    depending on which endpoint happened to create it. Returns
    (is_valid, message) -- callers already expect this shape from the
    function this replaces."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long"
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain letters"
    if not re.search(r"\d", password):
        return False, "Password must contain numbers"
    if password.lower() in _COMMON_PASSWORDS:
        return False, "Password is too common; choose something less predictable"
    return True, "Valid password"


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


def create_api_response(response_type, message, data=None, error_code=None):
    """Create standardized API response.

    error_code is a stable machine-readable identifier (phase-04 task 4.6)
    for the subset of error responses that need one -- optional and
    backward compatible with the ~200 existing call sites that don't pass it.
    """
    response = {"type": response_type, "msg": message}
    if error_code is not None:
        response["error_code"] = error_code
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

    client_ip = _client_ip(request)

    try:
        cursor = conn.cursor(dictionary=True)

        # Rate limiting (phase-07 H3) -- 20 invalid keys / 5 min per IP,
        # with exponential backoff from the 5th failure. Checked before the
        # lookup so a client already locked out doesn't get a free guess
        # while we're computing the response.
        if _api_key_attempt_blocked(cursor, client_ip):
            raise HTTPException(
                status_code=429,
                detail=create_api_response("error", "Too many failed attempts. Try again later."),
            )

        # Look up by SHA-256 of the presented key. The raw key is never
        # stored: key_id holds only a display prefix, key_hash the digest.
        presented_hash = hashlib.sha256(api_key.encode()).hexdigest()
        cursor.execute(
            """
            SELECT * FROM api_keys
            WHERE key_hash = %s AND active = 1
        """,
            (presented_hash,),
        )
        api_key_data = cursor.fetchone()

        if not api_key_data:
            # Legacy fallback (pre-0019 rows stored the raw key in key_id and
            # auth matched on it). Kept so the hash cutover is deploy-order
            # independent; migration 0019 redacts key_id, after which this
            # branch can never match. Remove once 0019 has run everywhere.
            cursor.execute(
                """
                SELECT * FROM api_keys
                WHERE key_id = %s AND active = 1
            """,
                (api_key,),
            )
            api_key_data = cursor.fetchone()
            if api_key_data:
                # Self-heal: store the digest so the primary lookup works even
                # before 0019 backfills, without waiting for the migration.
                cursor.execute(
                    "UPDATE api_keys SET key_hash = %s WHERE id = %s",
                    (presented_hash, api_key_data["id"]),
                )

        if not api_key_data:
            record_api_key_failure(cursor, client_ip)
            conn.commit()
            raise HTTPException(
                status_code=401, detail=create_api_response("error", "Invalid API key")
            )

        expires_at = api_key_data.get("expires_at")
        if expires_at is not None and expires_at <= datetime.now():
            # A real but stale credential: reject without a brute-force strike.
            conn.commit()
            raise HTTPException(
                status_code=401, detail=create_api_response("error", "API key expired")
            )

        clear_api_key_failures(cursor, client_ip)

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
            "UPDATE api_keys SET last_used = %s WHERE id = %s",
            (datetime.now(), api_key_data["id"]),
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


def _authenticate_session(request: Request, permission_level: str = "read") -> dict:
    """Core auth logic for a browser session -- validates the mailyte_session
    cookie and returns a dict shaped like _authenticate_api_key's, so
    get_org_context() and every downstream consumer stay unchanged (phase-03
    design: "sessions are a thin layer over the existing API-key machinery")."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
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
                   u.id AS user_id, u.organization_id, u.email, u.role, u.is_active
            FROM web_sessions s
            JOIN users u ON u.id = s.user_id
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
                status_code=401, detail=create_api_response("error", "Session expired or invalid")
            )

        # CE ships a single dashboard role with full read/write -- no
        # read_only concept for it. 'admin' here means the platform-scope
        # tier (deep-audit.md §2.1: service restarts, cross-org data,
        # GDPR erasure) which a tenant dashboard session must never reach;
        # that is sudo-console territory (ADR-002), out of scope here.
        if permission_level == "admin":
            raise HTTPException(
                status_code=403, detail=create_api_response("error", "Admin access required")
            )

        # Sliding idle timeout, capped by the absolute expiry -- extend on
        # every authenticated request rather than only at login.
        new_expires_at = min(now + SESSION_IDLE_TIMEOUT, row["absolute_expiry"])
        cursor.execute(
            "UPDATE web_sessions SET expires_at = %s WHERE id = %s",
            (new_expires_at, row["session_id"]),
        )
        conn.commit()

        key_data = {
            "organization_id": row["organization_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "role": row["role"],
        }
        request.state.api_key_data = {**key_data, "read": True, "write": True}
        request.state.api_key = None
        request.state.organization_id = row["organization_id"]
        request.state.session_id = row["session_id"]
        return key_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session validation error: {e}")
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication failed")
        )
    finally:
        cursor.close()
        conn.close()


def _verify_csrf(request: Request, cookie_name: str = CSRF_COOKIE_NAME) -> None:
    """Double-submit CSRF check (task 3.5). Only reached for cookie-
    authenticated state-changing requests -- see _resolve_auth. API-key
    requests never reach here; they carry no ambient browser credential.

    cookie_name is parametrised (phase-06) so the operator session path can
    reuse this same check against OPERATOR_CSRF_COOKIE_NAME instead of the
    tenant session's CSRF_COOKIE_NAME -- the two credential types must never
    share a cookie namespace."""
    csrf_cookie = request.cookies.get(cookie_name)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=403, detail=create_api_response("error", "CSRF token missing or invalid")
        )


def _resolve_auth(request: Request, permission_level: str = "read") -> dict:
    """Accept X-API-Key OR a valid session cookie -- identical downstream
    context either way (get_org_context() doesn't care which). This is the
    single choke point both require_api_key and require_auth funnel through,
    which is also where CSRF enforcement lives so none of the 172 existing
    routes need to call it individually."""
    if request.headers.get("X-API-Key"):
        return _authenticate_api_key(request, permission_level)
    if request.cookies.get(SESSION_COOKIE_NAME):
        result = _authenticate_session(request, permission_level)
        if request.method in _STATE_CHANGING_METHODS:
            _verify_csrf(request)
        return result
    raise HTTPException(
        status_code=401, detail=create_api_response("error", "Authentication required")
    )


def _authenticate_operator_session(request: Request) -> AuthContext:
    """Validate the mailyte_operator_session cookie (task 6.7).

    Structurally parallel to _authenticate_session, but deliberately not
    shared with it: operator_sessions is IP-bound and MFA-gated, neither of
    which applies to tenant sessions, and the two must never be reachable
    through each other's cookie or table (ADR-002's whole premise).
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
            # IP-bound (ADR-002 SS7) -- a cookie replayed from a different
            # network is rejected outright rather than silently rebinding.
            raise HTTPException(
                status_code=401, detail=create_api_response("error", "Operator session IP mismatch")
            )

        if not row["mfa_satisfied"]:
            # A partial session (post-password, pre-MFA) grants nothing at
            # all through this path -- POST /api/v1/platform/auth/mfa
            # re-validates the operator_sessions row directly rather than
            # going through require_scope, which is why it can complete
            # MFA using a session that would be rejected here.
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
        )
    finally:
        cursor.close()
        conn.close()


def get_pending_operator_session(request: Request) -> dict:
    """Validate an operator session cookie WITHOUT requiring mfa_satisfied
    (task 6.7) -- used only by routes/platform_auth.py's mfa/mfa-setup
    endpoints, which exist specifically to let a partial (post-password,
    pre-MFA) session complete or enroll in MFA. Every other route in the
    system must go through require_scope/_authenticate_operator_session,
    which DOES require mfa_satisfied=1; this function is not exported for
    general use.

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
                status_code=401, detail=create_api_response("error", "Operator session IP mismatch")
            )
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pending operator session validation error: {e}")
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Authentication failed")
        )
    finally:
        cursor.close()
        conn.close()


def _resolve_scope_auth(
    request: Request,
    required_scope: str = "organization",
    permission_level: str = "read",
    required_role: str | None = None,
) -> AuthContext:
    """The scope-aware core (task 6.3). Adds a third credential type --
    an operator session -- on top of _resolve_auth's existing tenant
    resolution (API key or tenant session), then enforces the caller's
    resolved scope/role against what the route declares it needs.

    A tenant credential can NEVER satisfy required_scope='platform',
    regardless of its own read/write/admin permission flags (ADR-002 SS8):
    the api_keys.scope column -- not the permissions JSON -- is the only
    thing that can grant platform scope, and only an operator session or a
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
    # middleware (task 6.8) needs to distinguish "a tenant tried this" from
    # "nobody authenticated at all" in the resulting log row.
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


def require_auth(permission_level="read"):
    """FastAPI Depends()-style dual-credential resolver: `auth: dict =
    Depends(require_auth('read'))`. Prefer this for new routes; require_api_key
    (decorator or Depends) keeps working unchanged for the existing 172."""

    def _dependency(request: Request) -> dict:
        return _resolve_auth(request, permission_level)

    return _dependency


def require_api_key(permission_level="read", scope: str = "organization", role: str | None = None):
    """
    Decorator for routes that require auth.

    Despite the name, accepts an X-API-Key header, a tenant session cookie
    (phase-03), or an operator session cookie (phase-06) -- kept under its
    original name and signature so none of the pre-phase-06 call sites need
    to change; scope/role are additive kwargs with backward-compatible
    defaults (task 6.5).

    scope='organization' (the default) preserves today's behaviour exactly:
    a tenant credential in its own org, or a platform credential seeing
    across all orgs (task 6.4's org_filter handles the "which orgs"
    question inside the route body -- this decorator only gates entry).
    scope='platform' rejects every tenant credential outright, regardless of
    its own permission flags -- see _resolve_scope_auth. role, when given,
    additionally requires an operator session at or above that tier
    (ADR-002 SS4); a role-gated route can never be reached by a bare
    platform-scope API key, which has no role at all.

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
            # Set before resolving, not after -- the audit middleware (task
            # 6.8) must see what a request TARGETED even when the resolve
            # call below rejects it. A tenant key denied at a platform route
            # is exactly the signal ADR-002 SS8 cares about most, and it can
            # only be logged if this survives the exception.
            request.state.required_scope = scope
            request.state.required_role = role

            # Scope-aware resolver (task 6.3): accepts X-API-Key, a tenant
            # session, or an operator session, and enforces required
            # scope/role before the handler ever runs.
            _resolve_scope_auth(request, scope, permission_level, role)

            async def _call_handler():
                # Call the original handler — pass request only if it expects it
                if has_request:
                    return await f(*args, request=request, **kwargs)
                return await f(*args, **kwargs)

            # Idempotency-Key replay (phase-04) -- no-ops unless the caller
            # sends the header on a mutating request, so every existing
            # route gets it without being touched. See utils/idempotency.py.
            from .idempotency import enforce_idempotency

            organization_id = getattr(request.state, "organization_id", None)
            return await enforce_idempotency(request, organization_id, _call_handler)

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
    """FastAPI Depends()-style scope resolver (task 6.3):
    `ctx: AuthContext = Depends(require_scope('platform', 'write', role='operator'))`.

    Shares _resolve_scope_auth with require_api_key's decorator -- identical
    enforcement, offered as a dependency for routes that prefer that style
    (this phase's new platform/auth/* endpoints) over the decorator form
    every pre-existing route already uses.
    """

    def _dependency(request: Request) -> AuthContext:
        request.state.required_scope = scope
        request.state.required_role = role
        return _resolve_scope_auth(request, scope, permission, role)

    return _dependency


def org_filter(ctx: AuthContext, requested_org: str | None = None) -> tuple:
    """Return (sql_fragment, params) for a WHERE clause (task 6.4) -- the
    single helper every tenant-scoped list/query route should use instead of
    hand-writing its own org filter.

    organization scope is always forced to ctx's own org: a caller-supplied
    requested_org is IGNORED, never honoured. That parameter is the most
    likely escalation vector in the system (a client passing a *different*
    org's id and hoping the server trusts it) -- silently dropping it here,
    rather than validating and rejecting it, means every call site gets the
    protection automatically instead of needing its own check.

    platform scope is optionally filtered by requested_org, otherwise sees
    every organization -- "sudo sees all" (ADR-002 SS8).
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
    `domain` path/query value (analytics.py, rate_limiter.py, storage.py,
    tracking.py, queue.py) with no ownership check of their own -- the class
    of bug conventions.md SS8 calls "the most likely escalation vector",
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
    """Enforce org ownership on a fetched row -- cross-org access is 404, never 403.

    Per conventions SS8: a 403 confirms the resource exists (just not yours);
    a 404 does not. Use this on every row keyed by a caller-supplied ID
    before returning or mutating it:

        row = fetch_by_id(some_id)
        row = scoped_or_404(row, request.state.auth_context)

    Takes the full AuthContext, not a bare org id. The previous signature was
    `scoped_or_404(row, org_id)` with a docstring recommending
    `get_org_context(request)` -- which returns None for an operator session,
    so every adopter would have reintroduced the platform-scope lockout that
    had to be removed from 49 handlers across seven routers. It had no callers
    at the time, which is the only reason it never caused one.

    Platform scope passes any existing row through: "sudo sees all"
    (ADR-002 SS8). A missing row is still 404 for everyone -- a nonexistent
    resource must not become reachable just because the caller is privileged.
    """
    if row is None:
        raise HTTPException(status_code=404, detail=create_api_response("error", "Not found"))
    if ctx["scope"] == "organization" and row.get("organization_id") != ctx["organization_id"]:
        raise HTTPException(status_code=404, detail=create_api_response("error", "Not found"))
    return row


def validate_update_columns(columns: list, allowed: set) -> list:
    """Whitelist-validate column names for dynamic SQL UPDATE queries."""
    for col in columns:
        if col not in allowed:
            raise ValueError(f"Column '{col}' is not allowed in update operations")
    return columns
