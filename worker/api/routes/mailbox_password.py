"""
Mailbox-holder password change -- POST /api/v1/mailbox/security/password

Mounted under the same /api/v1/mailbox prefix as routes/mailbox.py, in its
own module because it has a property no other mailbox route has: it is
reachable by a session that every other route refuses. A holder signed in
with a temporary password (admin reset, temporary=true) is 403
password_change_required everywhere except here and sign-out, and this is
the route that clears the flag. Its dependency is therefore
require_mailbox_for_password_change, not require_mailbox, and keeping it
out of mailbox.py keeps that exception visible rather than one Depends()
among sixty.

Verification is the same IMAP LOGIN against Dovecot that sign-in uses. The
stored hash is not consulted: it is what we are about to replace, and
asking Dovecot is the only check that cannot disagree with what a mail
client would have accepted a moment ago (see utils/mailbox_auth).

Filed as the mobile team's 2026-08-31 §2 (P0).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from utils.auth import create_api_response, hash_password, validate_password_strength
from utils.database import get_db_connection
from utils.mailbox_auth import (
    clear_mailbox_login_failures,
    mailbox_login_blocked,
    record_mailbox_login_failure,
    require_mailbox_for_password_change,
    verify_mailbox_credentials,
)
from utils.smtp_credentials import flush_auth_cache, source_ip_of

from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)

router = APIRouter()


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=1, max_length=1024)


@router.post(
    "/security/password",
    summary="Change this mailbox's password",
    description=(
        "Verifies `current_password` by IMAP LOGIN against Dovecot (the same check "
        "sign-in uses), enforces the platform password policy on `new_password`, "
        "stores the new bcrypt hash, flushes Dovecot's auth cache so IMAP/SMTP honour "
        "the change immediately, and signs out every OTHER session of this mailbox. "
        "The calling session stays signed in. Clears a pending forced change "
        "(`must_change_password`), so this is the one route a session carrying a "
        "temporary password may call besides sign-out. Failed `current_password` "
        "attempts count against the same lockout as sign-in."
    ),
    responses={
        401: {"description": "`error_code: wrong_password` -- Dovecot refused current_password"},
        409: {"description": "`error_code: password_reused` -- new password equals the current"},
        422: {"description": "`error_code: weak_password` -- `msg` carries the failed rule"},
        429: {"description": "Too many failed attempts (shared with sign-in lockout)"},
        502: {"description": "Dovecot unreachable -- the password was not checked"},
    },
)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    mailbox: dict = Depends(require_mailbox_for_password_change),  # noqa: B008
):
    """`def`, not `async def`: blocking IMAP and DB work, threadpool only."""
    # Policy first, before any network round-trip: it reveals nothing about
    # the current password and saves Dovecot a login for input we would
    # reject anyway. Same function every other password in the system goes
    # through (utils.auth, phase-07 H7) -- the rule text in `msg` is the
    # actual failing rule, not a summary of the policy.
    valid, message = validate_password_strength(body.new_password)
    if not valid:
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", message, error_code="weak_password"),
        )

    email = mailbox["email"]
    account_id = mailbox["email_account_id"]
    client_ip = source_ip_of(request) or "unknown"

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )

    cursor = conn.cursor(dictionary=True)
    try:
        # A stolen session guessing current_password is the same attack as a
        # stolen address guessing the sign-in password, so it shares the
        # sign-in bucket: enough misses here lock the webmail sign-in for
        # this address too, which is the response we want.
        if mailbox_login_blocked(cursor, client_ip, email):
            raise HTTPException(
                status_code=429,
                detail=create_api_response("error", "Too many failed attempts. Try again later."),
            )

        if not verify_mailbox_credentials(email, body.current_password):
            record_mailbox_login_failure(cursor, client_ip, email)
            conn.commit()
            raise HTTPException(
                status_code=401,
                detail=create_api_response(
                    "error", "Current password is incorrect", error_code="wrong_password"
                ),
            )

        # Only meaningful once we know current_password is right; before that
        # it would be answering a question about a credential the caller has
        # not yet proven they hold.
        if body.new_password == body.current_password:
            raise HTTPException(
                status_code=409,
                detail=create_api_response(
                    "error",
                    "New password must be different from the current one",
                    error_code="password_reused",
                ),
            )

        clear_mailbox_login_failures(cursor, client_ip, email)

        cursor.execute(
            "UPDATE email_accounts "
            "SET password = %s, must_change_password = 0, password_change_reason = NULL, "
            "    password_changed_at = NOW(), updated_at = NOW() "
            "WHERE id = %s",
            (hash_password(body.new_password), account_id),
        )

        # Every other device signs out; the one that just proved it knows
        # the password keeps its session. Revoked rather than deleted so the
        # sessions list can still show "signed out by password change".
        cursor.execute(
            "UPDATE mailbox_sessions SET revoked_at = NOW() "
            "WHERE email_account_id = %s AND id <> %s AND revoked_at IS NULL",
            (account_id, mailbox["session_id"]),
        )
        sessions_revoked = int(cursor.rowcount or 0)
        conn.commit()

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Mailbox password change error for %s: %s", email, exc)
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Password change failed"),
        ) from None
    finally:
        cursor.close()
        conn.close()

    # After the commit: the new hash is durable, so a flush failure only
    # degrades to Dovecot's cache TTL (up to 1h) for IMAP/SMTP, and is
    # reported rather than turned into a failed request.
    cache_flushed = flush_auth_cache(email)

    dispatch_event(
        Events.MAILBOX_PASSWORD_CHANGED,
        data={
            "account_id": account_id,
            "email": email,
            "organization_id": mailbox["organization_id"],
            "changed_by": "mailbox_holder",
            "sessions_revoked": sessions_revoked,
        },
        org_id=mailbox["organization_id"],
        source_service="api",
    )

    return create_api_response(
        "success",
        "Password changed",
        {"sessions_revoked": sessions_revoked, "cache_flushed": cache_flushed},
    )
