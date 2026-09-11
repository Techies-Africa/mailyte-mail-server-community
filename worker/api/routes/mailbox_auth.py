"""
Mailbox-holder sign-in -- POST /api/v1/mailbox-auth/{login,logout}

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 1. Replaces
mailyte-api's /api/v1/mailbox-auth/*, keeping the same paths and the same
request/response shapes so the webmail changes only its base URL.

Unauthenticated by design (there is no credential to present yet) and
rate-limited on two dimensions, matching /api/v1/auth/login.
"""

import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from utils.auth import create_api_response
from utils.database import get_db_connection
from utils.mailbox_auth import (
    clear_mailbox_login_failures,
    clear_mailbox_session_cookies,
    client_platform_from_request,
    create_mailbox_session,
    mailbox_login_blocked,
    mailbox_two_factor_enabled,
    record_mailbox_login_failure,
    require_mailbox_for_logout,
    session_lifetimes,
    set_mailbox_session_cookies,
    verify_mailbox_credentials,
    verify_mailbox_two_factor,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class MailboxLoginRequest(BaseModel):
    email_address: str = Field(..., max_length=255)
    password: str = Field(..., max_length=1024)
    # Supplied on the second attempt, after the first answered
    # two_factor_required. Same field name the Laravel route used, so the
    # webmail's existing prompt keeps working.
    two_factor_code: str | None = Field(None, max_length=32)


@router.post(
    "/login",
    summary="Sign in to the webmail with mailbox credentials",
    description=(
        "Exchanges a mailbox's own email and password for a webmail session. "
        "The password is verified by IMAP LOGIN against Dovecot, never against a "
        "stored copy, so this can never disagree with what a mail client would "
        "accept. Unauthenticated by design, rate-limited by IP and by address. "
        "Send `X-Client-Platform: ios|android|macos|windows|linux` from a native app "
        "to receive the native session lifetimes (default 30 days idle / 180 days "
        "absolute) instead of the browser's 8 hours / 7 days. If the account carries "
        "a temporary password the session is still issued, `data.must_change_password` "
        "is true, and every /api/v1/mailbox/* route except "
        "POST /security/password and logout answers 403 `password_change_required` "
        "until a new password is set."
    ),
)
def login(
    body: MailboxLoginRequest,
    request: Request,
    response: Response,
    x_forwarded_for: str | None = Header(None, alias="X-Forwarded-For"),
):
    """`def`, not `async def` -- this does blocking IMAP and DB work and must
    run in the threadpool. See require_mailbox for the full reasoning."""
    client_ip = (x_forwarded_for.split(",")[0].strip() if x_forwarded_for else None) or (
        request.client.host if request.client else "unknown"
    )
    email = body.email_address.strip().lower()

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )

    cursor = conn.cursor(dictionary=True)
    try:
        if mailbox_login_blocked(cursor, client_ip, email):
            raise HTTPException(
                status_code=429,
                detail=create_api_response("error", "Too many failed attempts. Try again later."),
            )

        cursor.execute(
            "SELECT id, email, name, organization_id, status, "
            "       must_change_password, password_change_reason "
            "FROM email_accounts WHERE email = %s",
            (email,),
        )
        account = cursor.fetchone()

        # Ask Dovecot even when the account is unknown here. Skipping the IMAP
        # round-trip on a miss would make a non-existent address answer
        # measurably faster than a real one with a wrong password, which is
        # exactly the enumeration oracle /api/v1/auth/login's dummy-hash check
        # exists to avoid.
        password_ok = verify_mailbox_credentials(email, body.password)

        if not account or account["status"] != "active" or not password_ok:
            record_mailbox_login_failure(cursor, client_ip, email)
            conn.commit()
            raise HTTPException(
                status_code=401,
                detail=create_api_response("error", "Invalid email or password"),
            )

        # Second factor, if this mailbox has one. Checked AFTER the password
        # so an attacker cannot use this endpoint to discover which mailboxes
        # have 2FA enabled without already holding the password.
        if mailbox_two_factor_enabled(email):
            if not body.two_factor_code:
                # A prompt, not a session (PRD S3). No credential is issued
                # until the second factor is actually satisfied.
                return create_api_response(
                    "success",
                    "Enter the code from your authenticator app",
                    {"two_factor_required": True},
                )
            if not verify_mailbox_two_factor(email, body.two_factor_code):
                record_mailbox_login_failure(cursor, client_ip, email)
                conn.commit()
                # Deliberately the same message as a wrong password:
                # distinguishing them tells an attacker which half they
                # already hold.
                raise HTTPException(
                    status_code=401,
                    detail=create_api_response("error", "Invalid email or password"),
                )

        clear_mailbox_login_failures(cursor, client_ip, email)

        # Platform-aware lifetimes (mobile team 2026-08-31 §4). Read once,
        # here; the row carries the idle window it produced from then on.
        client_platform = client_platform_from_request(request)
        idle_timeout, absolute_timeout = session_lifetimes(client_platform)

        session_token = create_mailbox_session(
            cursor,
            email_account_id=account["id"],
            organization_id=account["organization_id"],
            client_ip=client_ip,
            user_agent=request.headers.get("User-Agent"),
            client_platform=client_platform,
        )
        conn.commit()
        # Timezone-AWARE, so the ISO string carries an offset. A naive
        # isoformat() ("...T19:15:09") is parsed as LOCAL time by
        # JavaScript, and the webmail feeds this straight into its cookie
        # `expires`. On a host at UTC+1 that silently shortened every
        # session by an hour; further east, by more. Laravel sent an
        # offset here and this must too.
        expires_at = datetime.now(UTC) + absolute_timeout

        csrf_token = secrets.token_urlsafe(32)
        set_mailbox_session_cookies(
            response, session_token, csrf_token, max_age=int(absolute_timeout.total_seconds())
        )

        # The token is also returned in the body because the webmail's BFF is
        # server-side: it holds this in its own HttpOnly cookie and replays it
        # as a Bearer token. A browser talking to this API directly uses the
        # cookies set above instead and can ignore this field.
        return create_api_response(
            "success",
            "Signed in",
            {
                "token": session_token,
                "csrf_token": csrf_token,
                # expires_at and email_account are named exactly as the Laravel
                # route named them: the webmail's BFF reads all three by name to
                # set its own cookie's lifetime, so renaming any of them logs
                # every user out at the moment of cutover.
                "expires_at": expires_at.isoformat(),
                "email_address": account["email"],
                "email_account": {
                    "id": account["id"],
                    "email_address": account["email"],
                    "name": account.get("name"),
                },
                # Additive (mobile team 2026-08-31 §2/§4). must_change_password
                # tells the client to go straight to its new-password screen:
                # every other /api/v1/mailbox/* call will be refused with 403
                # password_change_required until POST /security/password
                # succeeds. client_platform echoes what the server recognised
                # (null = treated as web) so a misspelt header is visible.
                "must_change_password": bool(account.get("must_change_password")),
                "password_change_reason": account.get("password_change_reason"),
                "client_platform": client_platform,
                "idle_timeout_seconds": int(idle_timeout.total_seconds()),
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Mailbox login error: {exc}")
        # `from None`: the cause is already logged above with full detail, and
        # chaining it onto the response risks a driver message reaching someone
        # who failed to sign in.
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Sign-in failed"),
        ) from None
    finally:
        cursor.close()
        conn.close()


@router.post(
    "/logout",
    summary="End the current webmail session",
    description="Revokes the session server-side and clears its cookies. Idempotent.",
)
def logout(request: Request, response: Response):
    # The logout variant admits a session parked at the two-factor prompt AND
    # one parked at the forced-password-change screen: both exist and should
    # be revocable, not refused. See mailbox_session_dependency for why the
    # exemptions are baked into the dependency rather than passed as
    # arguments.
    mailbox = require_mailbox_for_logout(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )

    cursor = conn.cursor(dictionary=True)
    try:
        # Revoked, not deleted -- Phase 4's sign-in history reads these rows,
        # and "you signed out from this device at 14:02" is only answerable if
        # the row survives.
        cursor.execute(
            "UPDATE mailbox_sessions SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
            (mailbox["session_id"],),
        )
        conn.commit()
    except Exception as exc:
        logger.error(f"Mailbox logout error: {exc}")
    finally:
        cursor.close()
        conn.close()

    clear_mailbox_session_cookies(response)
    return create_api_response("success", "Signed out")
