#!/usr/bin/env python3
"""
Operator Auth API (ADR-002, ADR-004)

CE's entire login path for the Mailyte Console. Replaces the static
ADMIN_TOKEN_SECRET shared password with real, individually-revocable
identities: a shared secret is not a person, is not individually revocable,
is not MFA-capable, and produces no attributable audit trail.

On a CE box the operator is the self-hoster themselves -- ADR-004's insight
is that a self-hoster IS the platform operator of their own instance, which
is why the same tables, roles, and endpoints serve both deployments.

Login is two-step: POST /login exchanges email+password for a partial session
(mfa_satisfied=0, which grants nothing except the two /mfa endpoints); POST
/mfa exchanges a TOTP code for a full session.

DIFFERENCE FROM mailyte-email-server: EE delegates the TOTP algorithm to a
separate `totp` container over HTTP. CE's docker-compose.yml has no such
service, so this router calls utils/totp.py in-process instead -- same
RFC 6238 algorithm, same `totp_secrets` table, no service hop. See
utils/totp.py's own docstring.
"""

import contextlib
import logging
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from utils import totp
from utils.auth import (
    OPERATOR_CSRF_COOKIE_NAME,
    OPERATOR_SESSION_ABSOLUTE_TIMEOUT,
    OPERATOR_SESSION_COOKIE_NAME,
    OPERATOR_SESSION_IDLE_TIMEOUT,
    AuthContext,
    _dummy_password_hash,
    _operator_login_blocked,
    clear_operator_login_failures,
    cookie_secure,
    create_api_response,
    generate_session_token,
    get_pending_operator_session,
    hash_password,
    hash_session_token,
    record_operator_login_failure,
    require_scope,
    validate_password_strength,
    verify_password,
)
from utils.database import get_db_connection

from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)
router = APIRouter()

_OPERATOR_SESSION_COOKIE_MAX_AGE = int(OPERATOR_SESSION_ABSOLUTE_TIMEOUT.total_seconds())

# Matches app.py's own constant (duplicated rather than imported, to avoid a
# route module importing the app module that imports it).
OPERATOR_BOOTSTRAP_TOKEN_PATH = "/app/data/operator-bootstrap-token"


def _mfa_optional() -> bool:
    """ADR-004's homelab escape hatch: `MAILYTE_CONSOLE_MFA=optional`.

    The default stays ON, in both editions and in every deployment mode --
    anything other than the exact string "optional" leaves MFA mandatory, so
    a typo fails closed. The hatch exists because a single-operator CE
    instance with a lost authenticator and no second owner is bricked, and
    the ADR judged that a self-hoster on their own hardware may make that
    trade knowingly. It is never appropriate for a hosted deployment, and
    every login taken through it is logged at WARNING.
    """
    return os.getenv("MAILYTE_CONSOLE_MFA", "required").strip().lower() == "optional"


class OperatorLoginRequest(BaseModel):
    email: str = Field(..., description="Operator email")
    password: str = Field(..., description="Operator password")


class OperatorMfaRequest(BaseModel):
    token: str = Field(
        ..., description="6-digit TOTP code from the authenticator app, or a backup code"
    )


class OperatorBootstrapRequest(BaseModel):
    email: str = Field(..., description="First operator's email")
    full_name: str = Field(
        ..., min_length=1, max_length=255, description="First operator's display name"
    )
    password: str = Field(
        ...,
        description="Password for the first operator "
        "(utils.auth.validate_password_strength's policy)",
    )

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        valid, message = validate_password_strength(v)
        if not valid:
            raise ValueError(message)
        return v


@router.post(
    "/bootstrap",
    summary="One-time first-operator bootstrap",
    description="Creates the first platform operator (role='owner'), single-use, guarded by the "
    "X-Bootstrap-Token app.py writes to /app/data/operator-bootstrap-token at startup "
    "when no operator exists yet. Refuses with 409 if any operator already exists, "
    "regardless of token validity. MFA enrollment happens on first login "
    "(POST /mfa/setup), not here.",
)
async def operator_bootstrap(
    body: OperatorBootstrapRequest, x_bootstrap_token: str = Header(..., alias="X-Bootstrap-Token")
):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM platform_operators")
        (operator_count,) = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    if operator_count > 0:
        raise HTTPException(
            status_code=409,
            detail=create_api_response(
                "error", "Already bootstrapped -- an operator already exists"
            ),
        )

    if not os.path.isfile(OPERATOR_BOOTSTRAP_TOKEN_PATH):
        raise HTTPException(
            status_code=409,
            detail=create_api_response("error", "Operator bootstrap is not available"),
        )

    with open(OPERATOR_BOOTSTRAP_TOKEN_PATH) as f:
        expected_token = f.read().strip()

    if not expected_token or not secrets.compare_digest(x_bootstrap_token, expected_token):
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Invalid bootstrap token")
        )

    email = body.email.strip().lower()
    operator_id = generate_ulid()
    password_hash = hash_password(body.password)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO platform_operators "
            "(id, email, full_name, password_hash, role, mfa_required, is_active) "
            "VALUES (%s, %s, %s, %s, 'owner', 1, 1)",
            (operator_id, email, body.full_name, password_hash),
        )
        conn.commit()
        cursor.close()
    except Exception as exc:
        logger.error(f"Operator bootstrap failed: {exc}")
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Bootstrap failed")
        ) from exc
    finally:
        conn.close()

    # Single-use: the token is consumed whether or not anything else goes
    # right after this point. A failure to unlink is not worth failing the
    # bootstrap over -- the 409 on operator_count above is the real guard.
    with contextlib.suppress(OSError):
        os.remove(OPERATOR_BOOTSTRAP_TOKEN_PATH)

    logger.info(f"Operator bootstrap complete: operator={operator_id} email={email} role=owner")

    return create_api_response(
        "success",
        "Operator bootstrap complete -- log in and complete MFA enrollment next",
        {
            "operator_id": operator_id,
            "email": email,
            "role": "owner",
        },
    )


def _set_operator_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    secure = cookie_secure()
    response.set_cookie(
        OPERATOR_SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=_OPERATOR_SESSION_COOKIE_MAX_AGE,
    )
    response.set_cookie(
        OPERATOR_CSRF_COOKIE_NAME,
        csrf_token,
        # Readable by the console's JS on purpose -- it has to echo the value
        # back in X-CSRF-Token for the double-submit check to mean anything.
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=_OPERATOR_SESSION_COOKIE_MAX_AGE,
    )


def _clear_operator_session_cookies(response: Response) -> None:
    response.delete_cookie(OPERATOR_SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(OPERATOR_CSRF_COOKIE_NAME, path="/")


@router.post(
    "/login",
    summary="Operator login, step 1 of 2",
    description="Exchanges operator email+password for a partial session (mfa_satisfied=0), which "
    "grants nothing except POST /mfa and POST /mfa/setup. Rate-limited harder than any "
    "tenant credential: 3 failures in 15 minutes triggers a lockout on both the IP and "
    "the email. When MAILYTE_CONSOLE_MFA=optional (ADR-004's homelab hatch, off by "
    "default) and the operator has no enrolled authenticator, the session is issued "
    "complete and no second step is required.",
)
async def operator_login(
    body: OperatorLoginRequest,
    request: Request,
    response: Response,
    x_forwarded_for: str | None = Header(None, alias="X-Forwarded-For"),
):
    client_ip = (x_forwarded_for.split(",")[0].strip() if x_forwarded_for else None) or (
        request.client.host if request.client else "unknown"
    )
    email = body.email.strip().lower()

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )

    try:
        cursor = conn.cursor(dictionary=True)

        if _operator_login_blocked(cursor, client_ip, email):
            raise HTTPException(
                status_code=429,
                detail=create_api_response("error", "Too many failed attempts. Try again later."),
            )

        cursor.execute("SELECT * FROM platform_operators WHERE email = %s", (email,))
        operator = cursor.fetchone()

        # Constant-time regardless of whether the account exists -- otherwise
        # response latency alone enumerates valid operator emails.
        password_ok = verify_password(
            body.password, operator["password_hash"] if operator else _dummy_password_hash()
        )

        if not operator or not operator["is_active"] or not password_ok:
            record_operator_login_failure(cursor, client_ip, email)
            conn.commit()
            raise HTTPException(
                status_code=401, detail=create_api_response("error", "Invalid email or password")
            )

        clear_operator_login_failures(cursor, client_ip, email)

        mfa_enrolled = totp.is_enrolled(operator["email"])
        # The hatch only applies to an operator with no authenticator at all.
        # Once enrolled, MFA is enforced regardless of the env var: silently
        # dropping a second factor an operator already set up is how a
        # config change becomes a downgrade attack.
        skip_mfa = _mfa_optional() and not mfa_enrolled

        now = datetime.now()
        session_token = generate_session_token()
        session_row_id = generate_ulid()
        expires_at = now + OPERATOR_SESSION_IDLE_TIMEOUT
        absolute_expiry = now + OPERATOR_SESSION_ABSOLUTE_TIMEOUT

        cursor.execute(
            "INSERT INTO operator_sessions "
            "(id, operator_id, token_hash, ip_address, user_agent, mfa_satisfied, "
            "expires_at, absolute_expiry) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                session_row_id,
                operator["id"],
                hash_session_token(session_token),
                client_ip,
                request.headers.get("User-Agent"),
                1 if skip_mfa else 0,
                expires_at,
                absolute_expiry,
            ),
        )
        if skip_mfa:
            cursor.execute(
                "UPDATE platform_operators SET last_login_at = %s WHERE id = %s",
                (now, operator["id"]),
            )
            logger.warning(
                "Operator %s logged in WITHOUT MFA: MAILYTE_CONSOLE_MFA=optional and no "
                "authenticator is enrolled. This is ADR-004's homelab escape hatch and is "
                "never appropriate for an instance reachable by anyone but you.",
                operator["email"],
            )
        conn.commit()

        csrf_token = generate_session_token()
        _set_operator_session_cookies(response, session_token, csrf_token)

        return create_api_response(
            "success",
            "Signed in" if skip_mfa else "Password verified -- MFA required",
            {
                "operator": {
                    "id": operator["id"],
                    "email": operator["email"],
                    "role": operator["role"],
                },
                "mfa_required": not skip_mfa,
                "mfa_enrolled": mfa_enrolled,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Operator login error: {e}")
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Login failed")
        ) from e
    finally:
        cursor.close()
        conn.close()


@router.post(
    "/mfa/setup",
    summary="Begin MFA enrollment",
    description="Generates a new TOTP secret for the operator on the pending (post-password) "
    "session and returns the otpauth:// URI to render as a QR code, plus one-time backup "
    "codes. The backup codes are shown exactly once and are the only way back in on a "
    "single-operator instance. Call POST /mfa with the first code to finish.",
)
async def operator_mfa_setup(request: Request):
    session = get_pending_operator_session(request)

    if totp.is_enrolled(session["email"]):
        # Re-enrolling would silently invalidate the authenticator the
        # operator is currently using. Resetting MFA is an owner-only action
        # (POST /api/v1/platform/operators/{id}/mfa-reset), on purpose: a
        # stolen session must not be able to swap out the second factor of
        # the account it stole.
        raise HTTPException(
            status_code=409,
            detail=create_api_response(
                "error",
                "MFA is already enrolled for this operator. An owner must reset it first.",
            ),
        )

    enrollment = totp.begin_enrollment(session["email"])
    if not enrollment:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "MFA enrollment failed")
        )

    return create_api_response(
        "success", "Scan the QR code, then confirm with POST /mfa", enrollment
    )


@router.post(
    "/mfa",
    summary="Operator login, step 2 of 2",
    description="Exchanges a TOTP code for a full session. If the operator has no enrolled TOTP "
    "secret yet, this confirms enrollment (call /mfa/setup first); otherwise it verifies "
    "a normal login code, or one single-use backup code. Either way, success promotes the "
    "pending session to mfa_satisfied=1.",
)
async def operator_mfa(body: OperatorMfaRequest, request: Request):
    session = get_pending_operator_session(request)
    enrolled = totp.is_enrolled(session["email"])

    verified = (
        totp.verify_login_token(session["email"], body.token)
        if enrolled
        else totp.confirm_enrollment(session["email"], body.token)
    )
    if not verified:
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Invalid MFA code")
        )

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE operator_sessions SET mfa_satisfied = 1 WHERE id = %s", (session["session_id"],)
        )
        cursor.execute(
            "UPDATE platform_operators SET last_login_at = %s WHERE id = %s",
            (datetime.now(), session["operator_id"]),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()

    return create_api_response(
        "success",
        "MFA verified",
        {
            "operator": {
                "id": session["operator_id"],
                "email": session["email"],
                "role": session["role"],
            },
        },
    )


@router.post(
    "/logout",
    summary="Log out",
    description="Revokes the current operator session and clears its cookies.",
)
async def operator_logout(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(require_scope("platform", "read")),
):
    token = request.cookies.get(OPERATOR_SESSION_COOKIE_NAME)
    if token:
        conn = get_db_connection()
        if not conn:
            raise HTTPException(
                status_code=500, detail=create_api_response("error", "Database connection failed")
            )
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE operator_sessions SET revoked_at = NOW() WHERE token_hash = %s",
                (hash_session_token(token),),
            )
            conn.commit()
            cursor.close()
        finally:
            conn.close()

    _clear_operator_session_cookies(response)
    return create_api_response("success", "Logged out")


@router.get(
    "/me",
    summary="Current operator identity",
    description="Returns the caller's operator id, email, and role. Requires a full "
    "(MFA-satisfied) session.",
)
async def operator_me(
    request: Request, ctx: AuthContext = Depends(require_scope("platform", "read"))
):
    return create_api_response(
        "success",
        "OK",
        {
            "operator_id": ctx["operator_id"],
            "email": getattr(request.state, "operator_email", None),
            "role": ctx["role"],
        },
    )
