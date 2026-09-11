#!/usr/bin/env python3
"""
Operator Auth API (phase-06 task 6.7, ADR-002)

Replaces the static ADMIN_TOKEN_SECRET shared-password mechanism with real,
individually-revocable staff identities. Structurally parallel to
routes/auth.py (tenant dashboard login) but deliberately not sharing any of
its cookies, tables, or session machinery -- ADR-002's entire premise is
that a tenant credential must never reach platform scope and vice versa.

Login is two-step: POST /login exchanges email+password for a partial
session (mfa_satisfied=0, grants nothing else); POST /mfa exchanges a TOTP
code for a full session. MFA is mandatory, never optional, for every
operator (ADR-002 SS3) -- there is no single-step login path.
"""

import logging
import os
import secrets
from datetime import datetime
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
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

project_root = Path(__file__).parent.parent.parent.parent
import sys

sys.path.insert(0, str(project_root))
from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)
router = APIRouter()

TOTP_SERVICE_URL = os.getenv("TOTP_SERVICE_URL", "http://totp:8103")
_OPERATOR_SESSION_COOKIE_MAX_AGE = int(OPERATOR_SESSION_ABSOLUTE_TIMEOUT.total_seconds())

# Matches app.py's own constant (duplicated rather than imported -- same
# pattern routes/bootstrap.py already uses for BOOTSTRAP_TOKEN_PATH).
OPERATOR_BOOTSTRAP_TOKEN_PATH = "/app/data/operator-bootstrap-token"


class OperatorLoginRequest(BaseModel):
    email: str = Field(..., description="Operator email")
    password: str = Field(..., description="Operator password")


class OperatorMfaRequest(BaseModel):
    token: str = Field(..., description="6-digit TOTP code from the authenticator app")


class OperatorBootstrapRequest(BaseModel):
    email: str = Field(..., description="First operator's email")
    full_name: str = Field(
        ..., min_length=1, max_length=255, description="First operator's display name"
    )
    password: str = Field(
        ...,
        description="Password for the first operator (utils.auth.validate_password_strength's policy)",
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
    "when no operator exists yet. Independent of POST /api/v1/bootstrap's own token -- "
    "an install can have organizations with zero operators or vice versa. Refuses with "
    "409 if any operator already exists, regardless of token validity. MFA enrollment "
    "happens on first login (POST /mfa/setup), not here.",
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
            "INSERT INTO platform_operators (id, email, full_name, password_hash, role, mfa_required, is_active) "
            "VALUES (%s, %s, %s, %s, 'owner', 1, 1)",
            (operator_id, email, body.full_name, password_hash),
        )
        conn.commit()
        cursor.close()
    except Exception as exc:
        logger.error(f"Operator bootstrap failed: {exc}")
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Bootstrap failed")
        )
    finally:
        conn.close()

    try:
        os.remove(OPERATOR_BOOTSTRAP_TOKEN_PATH)
    except OSError:
        pass

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
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=_OPERATOR_SESSION_COOKIE_MAX_AGE,
    )


def _clear_operator_session_cookies(response: Response) -> None:
    response.delete_cookie(OPERATOR_SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(OPERATOR_CSRF_COOKIE_NAME, path="/")


def _totp_enrolled(email: str) -> bool:
    """Direct read of totp_secrets rather than a round-trip to the totp
    service -- this is metadata (is a secret already set up and enabled?),
    not the TOTP algorithm itself, which stays exclusively in that service
    (task 6.7: "reuse totp_secrets, don't build a second implementation")."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT enabled FROM totp_secrets WHERE user_email = %s", (email,))
        row = cursor.fetchone()
        return bool(row and row["enabled"])
    finally:
        conn.close()


@router.post(
    "/login",
    summary="Operator login, step 1 of 2",
    description="Exchanges operator email+password for a partial session (mfa_satisfied=0), which "
    "grants nothing except POST /mfa and POST /mfa/setup. Rate-limited harder than tenant "
    "login: 3 failures in 15 minutes triggers a lockout.",
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

        # Constant-time regardless of whether the account exists (matches
        # routes/auth.py's tenant login -- same timing-based enumeration risk).
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

        now = datetime.now()
        session_token = generate_session_token()
        session_row_id = generate_ulid()
        expires_at = now + OPERATOR_SESSION_IDLE_TIMEOUT
        absolute_expiry = now + OPERATOR_SESSION_ABSOLUTE_TIMEOUT

        cursor.execute(
            "INSERT INTO operator_sessions "
            "(id, operator_id, token_hash, ip_address, user_agent, mfa_satisfied, expires_at, absolute_expiry) "
            "VALUES (%s, %s, %s, %s, %s, 0, %s, %s)",
            (
                session_row_id,
                operator["id"],
                hash_session_token(session_token),
                client_ip,
                request.headers.get("User-Agent"),
                expires_at,
                absolute_expiry,
            ),
        )
        conn.commit()

        csrf_token = generate_session_token()
        _set_operator_session_cookies(response, session_token, csrf_token)

        return create_api_response(
            "success",
            "Password verified -- MFA required",
            {
                "operator": {
                    "id": operator["id"],
                    "email": operator["email"],
                    "role": operator["role"],
                },
                "mfa_required": True,
                "mfa_enrolled": _totp_enrolled(operator["email"]),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Operator login error: {e}")
        raise HTTPException(status_code=500, detail=create_api_response("error", "Login failed"))
    finally:
        cursor.close()
        conn.close()


@router.post(
    "/mfa/setup",
    summary="Begin MFA enrollment",
    description="Generates a new TOTP secret for the operator on the pending (post-password) session "
    "and returns the QR otpauth:// URI plus one-time backup codes. Only usable before "
    "enrollment completes -- call POST /mfa with the first code to finish.",
)
async def operator_mfa_setup(request: Request):
    session = get_pending_operator_session(request)

    try:
        resp = requests.post(
            f"{TOTP_SERVICE_URL}/totp/setup",
            json={"user_email": session["email"]},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error(f"TOTP service unreachable during setup: {e}")
        raise HTTPException(
            status_code=503, detail=create_api_response("error", "MFA service unavailable")
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code, detail=create_api_response("error", "MFA setup failed")
        )

    return create_api_response(
        "success", "Scan the QR code, then confirm with POST /mfa", resp.json().get("data")
    )


@router.post(
    "/mfa",
    summary="Operator login, step 2 of 2",
    description="Exchanges a TOTP code for a full session. If the operator has no enrolled TOTP secret "
    "yet, this confirms enrollment (call /mfa/setup first); otherwise it verifies a normal "
    "login code. Either way, success promotes the pending session to mfa_satisfied=1.",
)
async def operator_mfa(body: OperatorMfaRequest, request: Request):
    session = get_pending_operator_session(request)
    enrolled = _totp_enrolled(session["email"])
    totp_endpoint = "verify" if enrolled else "enable"

    try:
        resp = requests.post(
            f"{TOTP_SERVICE_URL}/totp/{totp_endpoint}",
            json={"user_email": session["email"], "token": body.token},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error(f"TOTP service unreachable during verify: {e}")
        raise HTTPException(
            status_code=503, detail=create_api_response("error", "MFA service unavailable")
        )

    if resp.status_code != 200:
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
    description="Returns the caller's operator id, email, and role. Requires a full (MFA-satisfied) session.",
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
