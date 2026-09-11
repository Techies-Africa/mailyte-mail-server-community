"""
Mailbox insights -- /api/v1/mailbox/insights and /messages/{id}/context.

Mobile spec v2 SS18 (replacing v1 SS13b) and the header half of v1 SS13c. The
Insights page currently ships behind sample data; these endpoints replace it
with real aggregation over the authenticated mailbox's own headers.

Mounted by app.py at the SAME prefix as routes/mailbox.py ("/api/v1/mailbox"),
in a separate module purely so the two can evolve without touching each other.
Everything routes/mailbox.py promises holds here too:

* **No handler takes an account id.** The session (require_mailbox) is the
  only thing that says whose mailbox this is.
* Handlers are sync `def`, so FastAPI runs them in its threadpool -- the
  IMAP work in here is exactly the blocking kind that must never run on the
  event loop (the ~128-endpoint failure mode already recorded against this
  service).
* Dovecot problems are 502s, never 4xx.

Cost model: a rollup reads headers only, under a per-folder message cap and a
wall-clock budget, through the same per-mailbox IMAP slots the webmail uses;
the result is cached in Redis for 6h per (mailbox, window) with the compute
time in window.to (stale-but-labelled, spec 18d). `?refresh=true` bypasses
the cache at most once per 5 minutes per mailbox; a throttled refresh serves
the cached copy instead of erroring.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from utils.auth import create_api_response
from utils.database import get_db_connection
from utils.insights import cache, engine, fetch, topics
from utils.mailbox_auth import require_mailbox

from shared.imap_mail import ImapUnavailableError, imap_session, split_message_id

logger = logging.getLogger(__name__)

router = APIRouter()

WINDOWS = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}
DEFAULT_WINDOW = "90d"


def _unavailable(exc: ImapUnavailableError) -> HTTPException:
    """Same contract as routes/mailbox.py: Dovecot problems are 502s."""
    logger.error("Insights IMAP unavailable: %s", exc)
    return HTTPException(
        status_code=502,
        detail=create_api_response("error", str(exc) or "Mail server unavailable"),
    )


def _holder_timezone(mailbox: dict) -> tuple[ZoneInfo, str, str]:
    """(tzinfo, name, source) for bucketing the holder's hours.

    A `timezone` column on mailbox_preferences wins when one exists and holds
    a valid IANA name -- read via SELECT * so this works the day such a column
    lands without a change here (0016 does not ship one yet). Otherwise the
    deployment default: INSIGHTS_DEFAULT_TIMEZONE, then the compose file's
    TIMEZONE, then UTC. The source is reported in the rhythm block so the
    client can caption the chart honestly.
    """
    name: str | None = None
    source = "deployment_default"

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT * FROM mailbox_preferences WHERE email_account_id = %s",
                (mailbox["email_account_id"],),
            )
            row = cursor.fetchone() or {}
            preferred = row.get("timezone")
            if preferred and str(preferred).strip():
                name = str(preferred).strip()
                source = "mailbox_preference"
        except Exception as exc:
            logger.debug("Timezone preference lookup failed: %s", exc)
        finally:
            cursor.close()
            conn.close()

    if not name:
        name = (
            os.getenv("INSIGHTS_DEFAULT_TIMEZONE", "").strip()
            or os.getenv("TIMEZONE", "").strip()
            or "UTC"
        )
    try:
        return ZoneInfo(name), name, source
    except Exception:
        logger.warning("Unusable insights timezone %r; falling back to UTC", name)
        return ZoneInfo("UTC"), "UTC", "deployment_default"


def _compute_rollup(mailbox: dict, window: str, days: int) -> dict:
    """One full rollup: fetch headers, aggregate, label. Called off-cache only."""
    with cache.rollup_slot():
        tz, tz_name, tz_source = _holder_timezone(mailbox)
        now = datetime.now(UTC)
        window_from = now - timedelta(days=days)
        deadline = time.monotonic() + fetch.TIME_BUDGET_SECONDS

        with imap_session(mailbox["email"]) as conn:
            collected = fetch.collect_window(conn, window_from, deadline)
            inbox_folders = collected.roles.get("inbox") or ["INBOX"]
            try:
                oldest_unread = fetch.oldest_unread_at(conn, inbox_folders[0])
            except ImapUnavailableError:
                oldest_unread = None

        payload = engine.build_rollup(
            collected.records,
            holder=mailbox["email"],
            now=now,
            window_from=window_from,
            tz=tz,
            tz_name=tz_name,
            tz_source=tz_source,
            oldest_unread=oldest_unread,
            window_meta={
                "label": window,
                "folders": collected.folders,
                "skipped": collected.skipped,
                "truncated": collected.truncated,
                "cap_per_folder": fetch.MAX_PER_FOLDER,
                "cached": False,
            },
        )

        if topics.topics_enabled():
            try:
                block = topics.compute_topics(collected.records)
            except Exception:
                logger.exception("Insights topics failed; omitting the block")
                block = None
            if block is not None:
                payload["topics"] = block

        return payload


@router.get(
    "/insights",
    summary="The mailbox's own analytics",
    description=(
        "Server-side aggregation over this mailbox's INBOX, Sent and Drafts "
        "headers: attention backlog, response times, weekly rhythm, "
        "relationships and hygiene, threaded by RFC 5322 identifiers. Every "
        "top-level block is independent -- a block that cannot be computed is "
        "omitted, and the client hides that section. Results are cached per "
        "window; window.to is the compute time. `refresh=true` recomputes, "
        "rate-limited per mailbox; when throttled, the cached copy is served."
    ),
)
def insights(
    window: str = Query(DEFAULT_WINDOW, pattern=r"^(30|90|180|365)d$"),
    refresh: bool = Query(False),
    mailbox: dict = Depends(require_mailbox),  # noqa: B008
):
    days = WINDOWS[window]
    email = mailbox["email"]
    key = cache.rollup_key(email, window)

    def cached_response():
        cached = cache.get_json(key)
        if cached is None:
            return None
        cached.setdefault("window", {})["cached"] = True
        return create_api_response("success", "Insights retrieved successfully", cached)

    if refresh:
        # One recompute per mailbox per interval, whichever window it names.
        # A denied refresh is not an error: the cached rollup is the answer,
        # labelled with its compute time.
        if not cache.try_acquire(cache.refresh_key(email), cache.REFRESH_MIN_INTERVAL_SECONDS):
            throttled = cached_response()
            if throttled is not None:
                return throttled
    else:
        hit = cached_response()
        if hit is not None:
            return hit

    # Stampede guard: when another request is already computing this exact
    # rollup, wait for its result instead of opening a second IMAP sweep.
    acquired_lock = cache.try_acquire(cache.lock_key(email, window), cache.LOCK_TTL_SECONDS)
    if not acquired_lock:
        waited = cache.wait_for(key)
        if waited is not None:
            waited.setdefault("window", {})["cached"] = True
            return create_api_response("success", "Insights retrieved successfully", waited)
        acquired_lock = True  # the other worker died; compute, and clean up after

    try:
        payload = _compute_rollup(mailbox, window, days)
    except cache.InsightsBusyError:
        raise HTTPException(
            status_code=503,
            detail=create_api_response(
                "error",
                "Insights are busy for this server, try again shortly",
                error_code="insights_busy",
            ),
            headers={"Retry-After": "30"},
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None
    finally:
        if acquired_lock:
            cache.release(cache.lock_key(email, window))

    cache.set_json(key, payload, cache.CACHE_TTL_SECONDS)
    return create_api_response("success", "Insights computed successfully", payload)


@router.get(
    "/messages/{message_id}/context",
    summary="Who this message is with, and the shape of its conversation",
    description=(
        "Header-only context for one message: the history with its "
        "correspondent and the thread it belongs to. Always available -- no "
        "AI is involved and none is required. A message with no identifiable "
        "correspondent (a bounce, a self-note) omits the correspondent block."
    ),
)
def message_context(message_id: str, mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    folder, uid = split_message_id(message_id)
    if not uid.isdigit():
        raise HTTPException(
            status_code=400,
            detail=create_api_response("error", "Malformed message id"),
        )
    folder = folder or "INBOX"
    email = mailbox["email"]

    key = cache.context_key(email, f"{folder}:{uid}")
    cached = cache.get_json(key)
    if cached is not None:
        return create_api_response("success", "Context retrieved successfully", cached)

    now = datetime.now(UTC)
    deadline = time.monotonic() + fetch.CONTEXT_TIME_BUDGET_SECONDS
    try:
        with imap_session(email) as conn:
            roles = fetch.resolve_roles(conn)
            seed = fetch.fetch_one(conn, folder, fetch.role_for(folder, roles), uid)
            if seed is None:
                raise HTTPException(
                    status_code=404,
                    detail=create_api_response("error", "Message not found"),
                )
            address = engine.other_party(seed, email)
            correspondent_records = (
                fetch.collect_correspondent(conn, roles, address, deadline) if address else []
            )
            thread_records = fetch.collect_thread(conn, roles, seed, deadline)
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None

    payload: dict = {"message_id": seed.id}
    if address:
        correspondent = engine.correspondent_summary(
            correspondent_records + [seed], email, address, now
        )
        if correspondent is not None:
            payload["correspondent"] = correspondent
    thread = engine.thread_summary(thread_records, email, now)
    if thread is not None:
        payload["thread"] = thread
    # The "ai" block of spec v1 SS13c is deliberately absent here: AI gating
    # (consent, entitlements) is owned elsewhere, and these two blocks must
    # work regardless of AI state.

    cache.set_json(key, payload, cache.CONTEXT_TTL_SECONDS)
    return create_api_response("success", "Context retrieved successfully", payload)
