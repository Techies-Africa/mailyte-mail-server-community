"""
Idempotency-Key replay layer (phase-04, tasks 4.1-4.2).

`enforce_idempotency()` is called from inside `require_api_key`'s decorator
in `utils/auth.py` -- the same choke point phase-03 used for session auth --
so every one of the ~174 existing mutating endpoints gets Idempotency-Key
support without any route file being touched. It no-ops immediately if the
client sends no `Idempotency-Key` header, so read endpoints and callers that
don't opt in are unaffected.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .database import get_db_connection

logger = logging.getLogger(__name__)

_IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_IDEMPOTENCY_TTL = timedelta(hours=24)
# Doc's own risk table: cap stored bodies at 64KB, store status only above
# that and skip replay of the body (still replay the status code).
_IDEMPOTENCY_BODY_CAP = 65536


def _generate_id():
    try:
        from shared.ulid_utils import generate_ulid

        return generate_ulid()
    except ImportError:
        import uuid

        return str(uuid.uuid4()).replace("-", "")[:26]


async def enforce_idempotency(request: Request, organization_id, call_handler):
    """Runs `call_handler()` (the real route) under Idempotency-Key semantics.

    Returns whatever the client should receive -- either a fresh result from
    `call_handler()`, or a replayed response for a key already completed.
    Raises HTTPException for the 409/422 idempotency-conflict cases, exactly
    like the routes it wraps already do for their own errors.
    """
    from .auth import create_api_response  # deferred: auth.py imports this module

    key = request.headers.get("Idempotency-Key")
    if not key or request.method not in _IDEMPOTENT_METHODS or not organization_id:
        return await call_handler()

    body = await request.body()
    request_hash = hashlib.sha256(body).hexdigest()

    conn = get_db_connection()
    if not conn:
        # Idempotency store unavailable -- fail open. Refusing to provision
        # because a bookkeeping table is unreachable would be worse than the
        # duplicate-request risk this table exists to prevent.
        logger.error(
            "Idempotency store unavailable (db connection failed); proceeding without replay protection"
        )
        return await call_handler()

    reserved_id = _generate_id()
    expires_at = datetime.utcnow() + _IDEMPOTENCY_TTL

    try:
        cursor = conn.cursor(dictionary=True)

        # Insert-first, not check-then-insert (phase-04 doc's explicit
        # concurrency guidance) -- two concurrent requests racing a
        # check-then-insert can both see "unseen" and both proceed.
        try:
            cursor.execute(
                """INSERT INTO idempotency_keys
                   (id, organization_id, idempotency_key, request_method, request_path,
                    request_hash, state, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'in_progress', %s)""",
                (
                    reserved_id,
                    organization_id,
                    key,
                    request.method,
                    request.url.path,
                    request_hash,
                    expires_at,
                ),
            )
            conn.commit()
        except Exception as exc:
            if "Duplicate entry" not in str(exc):
                raise

            cursor.execute(
                "SELECT * FROM idempotency_keys WHERE organization_id = %s AND idempotency_key = %s",
                (organization_id, key),
            )
            row = cursor.fetchone()

            if row["state"] == "completed":
                if row["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=422,
                        detail=create_api_response(
                            "error",
                            "Idempotency-Key was already used with a different request body",
                            error_code="IDEMPOTENCY_KEY_REUSED",
                        ),
                    )
                stored_body = row["response_body"]
                payload = (
                    json.loads(stored_body)
                    if stored_body
                    else create_api_response(
                        "success",
                        "Request already processed (original response body exceeded the replay size cap)",
                    )
                )
                return JSONResponse(
                    content=payload,
                    status_code=row["response_status"],
                    headers={"Idempotency-Replayed": "true"},
                )

            if row["state"] == "in_progress":
                raise HTTPException(
                    status_code=409,
                    detail=create_api_response(
                        "error",
                        "A request with this Idempotency-Key is already in progress",
                        error_code="IDEMPOTENCY_IN_PROGRESS",
                    ),
                    headers={"Retry-After": "1"},
                )

            # state == 'failed' -- the previous attempt never reached a
            # deterministic outcome (5xx / unhandled exception). Allow retry
            # by reclaiming the same row rather than leaving the key stuck.
            reserved_id = row["id"]
            cursor.execute(
                """UPDATE idempotency_keys SET state='in_progress', request_hash=%s,
                   request_method=%s, request_path=%s, response_status=NULL,
                   response_body=NULL, created_at=NOW(), expires_at=%s WHERE id=%s""",
                (request_hash, request.method, request.url.path, expires_at, reserved_id),
            )
            conn.commit()
    finally:
        cursor.close()
        conn.close()

    exc_response = None
    try:
        result = await call_handler()
        status_code, body_dict = _normalize_response(result)
    except HTTPException as exc:
        exc_response = exc
        status_code = exc.status_code
        body_dict = (
            exc.detail
            if isinstance(exc.detail, dict)
            else create_api_response("error", str(exc.detail))
        )
    except Exception:
        # Truly unexpected -- don't cache a broken outcome, let a retry try again.
        _mark_idempotency_failed(reserved_id)
        raise

    # A deterministic response (2xx/4xx) is safe to replay verbatim. A 5xx
    # is presumed transient (e.g. "Database connection failed") -- caching
    # it would strand the key on a stale error even after the fault clears.
    if status_code >= 500:
        _mark_idempotency_failed(reserved_id)
    else:
        _store_idempotency_result(reserved_id, status_code, body_dict)

    if exc_response is not None:
        raise exc_response
    return result


def _normalize_response(result):
    if isinstance(result, JSONResponse):
        return result.status_code, json.loads(result.body)
    if isinstance(result, dict):
        return 200, result
    return 200, None


def _store_idempotency_result(reserved_id, status_code, body_dict):
    conn = get_db_connection()
    if not conn:
        logger.error("Could not persist idempotency result (db connection failed)")
        return
    try:
        cursor = conn.cursor()
        body_json = None
        if body_dict is not None:
            serialized = json.dumps(body_dict)
            if len(serialized.encode("utf-8")) <= _IDEMPOTENCY_BODY_CAP:
                body_json = serialized
        cursor.execute(
            "UPDATE idempotency_keys SET state='completed', response_status=%s, response_body=%s WHERE id=%s",
            (status_code, body_json, reserved_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error(f"Failed to persist idempotency result: {exc}")
    finally:
        conn.close()


def _mark_idempotency_failed(reserved_id):
    conn = get_db_connection()
    if not conn:
        logger.error("Could not mark idempotency key failed (db connection failed)")
        return
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE idempotency_keys SET state='failed' WHERE id=%s", (reserved_id,))
        conn.commit()
    except Exception as exc:
        logger.error(f"Failed to mark idempotency key as failed: {exc}")
    finally:
        conn.close()
