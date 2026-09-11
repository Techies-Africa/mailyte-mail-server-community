"""
Mailbox-holder authentication -- the third credential tier.

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 1. Everything the
webmail needs to authenticate a mailbox holder, so that it no longer needs
mailyte-api to do it.

Three tiers now exist and deliberately share nothing (ADR-002):

    users        -> web_sessions       -> mailyte_session          (dashboard)
    operators    -> operator sessions  -> mailyte_operator_session (console)
    email_accounts -> mailbox_sessions -> mailyte_mailbox_session  (webmail)

**Authentication is IMAP LOGIN against Dovecot, never a stored password.**
Laravel's MailboxAuthService decrypted `email_accounts.password` and compared
it in PHP. That is worse in two ways: it requires the password be reversibly
encrypted rather than hashed, and it authenticates against a *copy* that can
drift from the credential Dovecot actually honours -- which is exactly the
bug that broke webmail sign-in after the mailcow migration rewrote 106 Dovecot
hashes while Laravel kept checking its own stale copy. Asking Dovecot is the
only check that cannot disagree with reality.

A consequence worth stating plainly: this module cannot tell a wrong password
from a disabled Dovecot account, because both are "Dovecot said no". That is
the correct amount of information to have.
"""

import contextlib
import imaplib
import logging
import os
import secrets
from datetime import datetime, timedelta

import requests
from fastapi import HTTPException, Request, Response

from shared.ulid_utils import generate_ulid

from .auth import (
    CSRF_HEADER_NAME,
    _bump_failure_row,
    _login_attempt_blocked,
    cookie_secure,
    create_api_response,
    generate_session_token,
    hash_session_token,
)
from .database import get_db_connection

logger = logging.getLogger(__name__)

# Own cookie namespace. Never reuse the dashboard's -- a mailbox holder
# landing on the dashboard must not arrive holding something it recognises.
MAILBOX_SESSION_COOKIE_NAME = "mailyte_mailbox_session"
MAILBOX_CSRF_COOKIE_NAME = "mailyte_mailbox_csrf"

# Shorter than the dashboard's 8h/30d. A webmail session is a mail client
# left open on a machine that is more often shared than a dashboard is, and
# re-authenticating costs a mailbox holder one password entry.
#
# These are the BROWSER lifetimes. A native mail app is a different animal:
# the device is personal, the credential store is the OS keychain, and a
# mail client that signs itself out after eight quiet hours is one nobody
# keeps installed. Native sessions get the longer windows below, selected by
# the X-Client-Platform header at sign-in (mobile team, 2026-08-31 §4).
MAILBOX_SESSION_IDLE_TIMEOUT = timedelta(hours=8)
MAILBOX_SESSION_ABSOLUTE_TIMEOUT = timedelta(days=7)

_MAILBOX_COOKIE_MAX_AGE = int(MAILBOX_SESSION_ABSOLUTE_TIMEOUT.total_seconds())


def _env_days(name: str, default_days: int) -> timedelta:
    """A positive whole number of days from the environment, else the default.

    Falls back rather than raising: a typo in a deploy env must not take
    the API down at import, and the default is the documented behaviour."""
    raw = os.getenv(name, "").strip()
    if raw:
        try:
            days = int(raw)
            if days >= 1:
                return timedelta(days=days)
            logger.warning("%s=%r is not a positive day count; using %d", name, raw, default_days)
        except ValueError:
            logger.warning("%s=%r is not an integer; using %d", name, raw, default_days)
    return timedelta(days=default_days)


MAILBOX_NATIVE_SESSION_IDLE_TIMEOUT = _env_days("MAILBOX_NATIVE_SESSION_IDLE_DAYS", 30)
MAILBOX_NATIVE_SESSION_ABSOLUTE_TIMEOUT = _env_days("MAILBOX_NATIVE_SESSION_ABSOLUTE_DAYS", 180)

CLIENT_PLATFORM_HEADER = "X-Client-Platform"
NATIVE_CLIENT_PLATFORMS = frozenset({"ios", "android", "macos", "windows", "linux"})
# Everything we are willing to store. Anything else is recorded as NULL --
# a free-text header is not something to write into a column that
# GET /security/sessions shows back to the holder.
KNOWN_CLIENT_PLATFORMS = NATIVE_CLIENT_PLATFORMS | {"web"}


def client_platform_from_request(request: Request) -> str | None:
    """The normalised X-Client-Platform, or None if absent/unrecognised.

    None is deliberately indistinguishable from "web" for lifetime purposes:
    a client that says nothing gets the conservative browser window, and
    only a client that positively identifies as native gets the long one.
    """
    raw = (request.headers.get(CLIENT_PLATFORM_HEADER) or "").strip().lower()
    return raw if raw in KNOWN_CLIENT_PLATFORMS else None


def session_lifetimes(client_platform: str | None) -> tuple[timedelta, timedelta]:
    """(idle, absolute) for a session opened from this platform.

    The single place the platform-to-lifetime decision is made: sign-in
    calls it to stamp the row and to tell the client when the session ends,
    and nothing else re-derives it -- the per-request slide reads the idle
    window back off the row (idle_timeout_seconds), never this function."""
    if client_platform in NATIVE_CLIENT_PLATFORMS:
        return MAILBOX_NATIVE_SESSION_IDLE_TIMEOUT, MAILBOX_NATIVE_SESSION_ABSOLUTE_TIMEOUT
    return MAILBOX_SESSION_IDLE_TIMEOUT, MAILBOX_SESSION_ABSOLUTE_TIMEOUT


IMAP_HOST = os.getenv("IMAP_HOST", "dovecot")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
# Bounded so a wedged Dovecot cannot pin a threadpool worker indefinitely.
IMAP_AUTH_TIMEOUT = int(os.getenv("IMAP_AUTH_TIMEOUT", "10"))


def verify_mailbox_credentials(email_address: str, password: str) -> bool:
    """Ask Dovecot whether this password is good for this mailbox.

    Returns True/False; raises only when Dovecot itself is unreachable, which
    is a 502 rather than a failed login -- telling someone their password is
    wrong when in fact the mail server is down sends them to reset a password
    that was never the problem.
    """
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_AUTH_TIMEOUT)
    except Exception as exc:
        logger.error(f"IMAP unreachable during mailbox auth: {exc}")
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "Mail server unavailable"),
        ) from None

    try:
        conn.login(email_address, password)
        return True
    except imaplib.IMAP4.error:
        # Wrong password, disabled account, or Dovecot refusing for any other
        # reason. Indistinguishable here, and deliberately so.
        return False
    except Exception as exc:
        logger.error(f"IMAP error during mailbox auth: {exc}")
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "Mail server unavailable"),
        ) from None
    finally:
        # A failed login leaves the connection in a state logout may object to.
        # Nothing here is worth masking the real result.
        with contextlib.suppress(Exception):
            conn.logout()


# Webmail sign-in failures get their own bucket rather than sharing the
# dashboard's. Two different credential spaces happen to be keyed by email
# address, so a shared bucket would let failed webmail sign-ins lock a
# dashboard account that has nothing to do with them.
#
# _bump_failure_row and _login_attempt_blocked are reused rather than
# reimplemented: the exponential backoff, the two-dimension (IP and email)
# tracking and the window semantics are security behaviour that must not
# quietly diverge between tiers. They are private to the package, not to the
# module, and both already take the `service` argument that makes this work.
_WEBMAIL_SERVICE = "webmail"


def mailbox_login_blocked(cursor, client_ip: str, email: str) -> bool:
    return _login_attempt_blocked(cursor, client_ip, email, service=_WEBMAIL_SERVICE)


def record_mailbox_login_failure(cursor, client_ip: str, email: str) -> None:
    """Bump both dimensions -- IP and email -- for a failed webmail sign-in."""
    _bump_failure_row(cursor, client_ip=client_ip, username=None, service=_WEBMAIL_SERVICE)
    _bump_failure_row(cursor, client_ip="", username=email, service=_WEBMAIL_SERVICE)


def clear_mailbox_login_failures(cursor, client_ip: str, email: str) -> None:
    cursor.execute(
        "DELETE FROM failed_auth_attempts WHERE service = %s "
        "AND ((client_ip = %s AND username IS NULL) OR (username = %s AND client_ip = ''))",
        (_WEBMAIL_SERVICE, client_ip, email),
    )


def create_mailbox_session(
    cursor,
    *,
    email_account_id: str,
    organization_id: str,
    client_ip: str | None,
    user_agent: str | None,
    mfa_satisfied: bool = True,
    client_platform: str | None = None,
) -> str:
    """Insert a mailbox_sessions row and return the raw session token.

    The raw token is returned exactly once, here, and never stored -- only its
    sha256 goes to the database, same as web_sessions.

    client_platform (already normalised by client_platform_from_request)
    picks the lifetimes and is stored on the row along with the idle window
    it produced, so the per-request slide honours it without re-deriving it.
    """
    now = datetime.now()
    session_token = generate_session_token()
    idle, absolute = session_lifetimes(client_platform)

    cursor.execute(
        "INSERT INTO mailbox_sessions "
        "(id, email_account_id, organization_id, token_hash, ip_address, "
        " user_agent, expires_at, absolute_expiry, mfa_satisfied, "
        " client_platform, idle_timeout_seconds) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            generate_ulid(),
            email_account_id,
            organization_id,
            hash_session_token(session_token),
            client_ip,
            (user_agent or "")[:500] or None,
            now + idle,
            now + absolute,
            1 if mfa_satisfied else 0,
            client_platform,
            int(idle.total_seconds()),
        ),
    )
    return session_token


def set_mailbox_session_cookies(
    response: Response,
    session_token: str,
    csrf_token: str,
    max_age: int | None = None,
) -> None:
    """max_age defaults to the browser absolute lifetime; sign-in passes the
    platform's own so the cookie and the row expire together."""
    secure = cookie_secure()
    cookie_max_age = _MAILBOX_COOKIE_MAX_AGE if max_age is None else max_age
    response.set_cookie(
        MAILBOX_SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=cookie_max_age,
    )
    # Readable by JS on purpose -- the double-submit CSRF pattern needs the
    # client to echo it in X-CSRF-Token. Same reasoning as the dashboard's.
    response.set_cookie(
        MAILBOX_CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=cookie_max_age,
    )


def clear_mailbox_session_cookies(response: Response) -> None:
    response.delete_cookie(MAILBOX_SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(MAILBOX_CSRF_COOKIE_NAME, path="/")


def _mailbox_token_from_request(request: Request) -> tuple[str | None, bool]:
    """Return (token, came_from_cookie).

    Two transports, matching how _resolve_auth already distinguishes API keys
    from browser sessions:

    * `Authorization: Bearer <token>` -- the webmail's BFF calling us
      server-to-server. Carries no ambient browser credential, so CSRF does
      not apply, for exactly the reason the API-key path is exempt.
    * the session cookie -- a browser talking to us directly. CSRF applies.

    The BFF path is what ships; the cookie path is what makes this API usable
    without a BFF at all, which an open-source deployment may well want.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return token, False
    return request.cookies.get(MAILBOX_SESSION_COOKIE_NAME), True


def _verify_mailbox_csrf(request: Request) -> None:
    csrf_cookie = request.cookies.get(MAILBOX_CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=403,
            detail=create_api_response("error", "CSRF verification failed"),
        )


# Methods that cannot change state, so cannot be the target of a CSRF attack.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _resolve_mailbox_session(
    request: Request,
    *,
    allow_pending_mfa: bool,
    allow_password_change_required: bool,
) -> dict:
    """Resolve the mailbox session, or 401. Shared body of every variant
    built by mailbox_session_dependency below -- see that for why the
    exemption flags are bound at construction time and never appear in a
    signature FastAPI can see.

    Returns the mailbox context every /api/v1/mailbox/* handler needs:
    account id, email address, organization, the session id, and the
    forced-password-change state.

    **No handler takes an account id from the path or body.** The session is
    the only thing that says which mailbox this is, which is what makes it
    structurally impossible for one mailbox to address another's mail. The
    Laravel routes this replaces were built the same way; keep it.

    Declared `def`, not `async def`, deliberately: it does blocking DB work,
    so FastAPI must run it in the threadpool. An `async def` here would block
    the event loop for every request in the process -- the failure mode
    already recorded against ~128 endpoints in this service.
    """
    token, from_cookie = _mailbox_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail=create_api_response("error", "Authentication required"),
        )

    if from_cookie and request.method not in _SAFE_METHODS:
        _verify_mailbox_csrf(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT s.id AS session_id, s.expires_at, s.absolute_expiry,
                   s.revoked_at, s.mfa_satisfied, s.idle_timeout_seconds,
                   a.id AS email_account_id, a.email, a.organization_id, a.status,
                   a.must_change_password, a.password_change_reason
            FROM mailbox_sessions s
            JOIN email_accounts a ON a.id = s.email_account_id
            WHERE s.token_hash = %s
            """,
            (hash_session_token(token),),
        )
        row = cursor.fetchone()

        now = datetime.now()
        if (
            not row
            or row["revoked_at"] is not None
            or row["status"] != "active"
            or row["expires_at"] < now
            or row["absolute_expiry"] < now
        ):
            raise HTTPException(
                status_code=401,
                detail=create_api_response("error", "Session expired or invalid"),
            )

        # A session still awaiting its TOTP code authenticates the password
        # step and nothing else (Phase 4). Distinct status so the webmail can
        # tell "prompt for a code" from "start over".
        #
        # allow_pending_mfa exists for exactly one caller: sign-out. Someone
        # who abandons the code prompt must still be able to revoke the
        # half-finished session, and refusing that would leave it live until
        # it expired on its own.
        if not row["mfa_satisfied"] and not allow_pending_mfa:
            raise HTTPException(
                status_code=403,
                detail=create_api_response(
                    "error",
                    "Two-factor authentication required",
                    error_code="mfa_required",
                ),
            )

        # A temporary password (admin reset with temporary=true) buys a
        # session that can do exactly two things: set a real password, and
        # sign out. Checked AFTER the MFA gate on purpose -- a second factor
        # is still a second factor, and the password screen must not become
        # a way around it. Distinct error_code so a client can route to its
        # "choose a new password" screen instead of showing a generic 403.
        must_change_password = bool(row.get("must_change_password"))
        if must_change_password and not allow_password_change_required:
            raise HTTPException(
                status_code=403,
                detail=create_api_response(
                    "error",
                    "Set a new password to continue",
                    error_code="password_change_required",
                ),
            )

        # Sliding idle timeout, capped by the absolute expiry. The window is
        # THIS session's own (stamped at sign-in from its platform), read
        # back off the row -- adding a module constant here is what used to
        # make every session browser-shaped one request after it was issued.
        idle_seconds = row.get("idle_timeout_seconds") or int(
            MAILBOX_SESSION_IDLE_TIMEOUT.total_seconds()
        )
        cursor.execute(
            "UPDATE mailbox_sessions SET expires_at = %s WHERE id = %s",
            (
                min(now + timedelta(seconds=int(idle_seconds)), row["absolute_expiry"]),
                row["session_id"],
            ),
        )
        conn.commit()

        context = {
            "email_account_id": row["email_account_id"],
            "email": row["email"],
            "organization_id": row["organization_id"],
            "session_id": row["session_id"],
            "must_change_password": must_change_password,
            "password_change_reason": row.get("password_change_reason"),
        }
        request.state.mailbox = context
        request.state.organization_id = row["organization_id"]
        return context

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Mailbox session validation error: {exc}")
        raise HTTPException(
            status_code=401,
            detail=create_api_response("error", "Authentication failed"),
        ) from None
    finally:
        cursor.close()
        conn.close()


def mailbox_session_dependency(
    *,
    allow_pending_mfa: bool = False,
    allow_password_change_required: bool = False,
):
    """Build a FastAPI dependency whose exemptions are fixed at build time.

    The exemptions MUST be closed over, never parameters of the dependency
    itself. FastAPI reads a dependency's full signature -- keyword-only
    parameters included -- and turns every plain-typed one into a QUERY
    parameter. The previous `require_mailbox(request, *, allow_pending_mfa=
    False)` therefore accepted `?allow_pending_mfa=true` on every
    /api/v1/mailbox/* route, which let a session that had passed the
    password step but not the TOTP step skip the second factor entirely
    (verified with fastapi.dependencies.utils.get_dependant, and pinned by
    tests/unit/test_mailbox_sessions.py). The returned callable's signature
    is exactly `(request: Request)`, so there is nothing for a caller to
    supply.
    """

    def _dependency(request: Request) -> dict:
        return _resolve_mailbox_session(
            request,
            allow_pending_mfa=allow_pending_mfa,
            allow_password_change_required=allow_password_change_required,
        )

    _dependency.__doc__ = _resolve_mailbox_session.__doc__
    return _dependency


# The dependency every ordinary /api/v1/mailbox/* handler uses: full session,
# second factor satisfied, no password change outstanding.
require_mailbox = mailbox_session_dependency()

# POST /api/v1/mailbox/security/password -- the one route a holder carrying a
# temporary password may reach, because it is how they stop carrying one.
require_mailbox_for_password_change = mailbox_session_dependency(
    allow_password_change_required=True
)

# POST /api/v1/mailbox-auth/logout -- sign-out must always be possible: from a
# session parked at the TOTP prompt, and from one parked at the password
# screen. Refusing either would leave a live session nobody can revoke.
require_mailbox_for_logout = mailbox_session_dependency(
    allow_pending_mfa=True, allow_password_change_required=True
)


# ---------------------------------------------------------------------------
# Two-factor at sign-in
# ---------------------------------------------------------------------------

TOTP_SERVICE_URL = os.getenv("TOTP_SERVICE_URL", "http://totp:8103")


def totp_key(email_address: str) -> str:
    """Namespaced key for this mailbox's TOTP secret.

    See routes/mailbox.py::_totp_key for the full reasoning -- totp_secrets is
    keyed by a UNIQUE user_email shared with the operator tier, so the two must
    not collide. Defined in both places from one convention; changing it is a
    breaking change that would orphan every existing enrolment.
    """
    return f"mailbox:{email_address}"


def mailbox_two_factor_enabled(email_address: str) -> bool:
    """Whether this mailbox requires a second factor to sign in.

    Read straight from totp_secrets rather than through the totp service: this
    runs on every sign-in, it is a single indexed lookup, and the service is
    still the only thing that implements the TOTP algorithm itself (mail-server
    phase-06 task 6.7). A read here does not duplicate that.

    Fails CLOSED on a database error -- refusing the sign-in is recoverable,
    while treating an unreadable enrolment as "no 2FA" would silently strip the
    protection off exactly the accounts that asked for it.
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=503,
            detail=create_api_response("error", "Cannot verify two-factor status"),
        )
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT enabled FROM totp_secrets WHERE user_email = %s",
            (totp_key(email_address),),
        )
        row = cursor.fetchone()
        return bool(row and row["enabled"])
    except Exception as exc:
        logger.error("Two-factor lookup failed for %s: %s", email_address, exc)
        raise HTTPException(
            status_code=503,
            detail=create_api_response("error", "Cannot verify two-factor status"),
        ) from None
    finally:
        cursor.close()
        conn.close()


def verify_mailbox_two_factor(email_address: str, code: str) -> bool:
    """Check a TOTP or recovery code via the totp service.

    The service owns the algorithm, the clock tolerance, its own rate limit and
    single-use consumption of recovery codes. Reimplementing any of that here
    would be the second implementation task 6.7 forbids.
    """
    try:
        response = requests.post(
            f"{TOTP_SERVICE_URL}/totp/verify",
            json={"user_email": totp_key(email_address), "token": code},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.error("TOTP service unreachable during sign-in: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "Two-factor service unavailable"),
        ) from None
    return response.status_code == 200
