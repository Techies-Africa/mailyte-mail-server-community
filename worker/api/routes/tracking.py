#!/usr/bin/env python3
"""
Tracking Module API Routes
Exposes controlled tracking functionality through the API gateway with organization support.

This module provides API endpoints for:
- Email tracking pixel and click handling
- Tracking statistics and analytics
- Suppression list management
- Integration with the new organization/domain/email account structure

All endpoints maintain backward compatibility while supporting the enhanced
organization hierarchy for multi-tenant environments.
"""

import logging
import os
import re
import sys
from pathlib import Path

import aiohttp
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from schemas.tracking import TrackingProxyError, TrackingProxyResponse
from utils.auth import (
    create_api_response,
    org_filter,
    require_api_key,
    verify_domain_scope,
)
from utils.database import get_db_connection

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.ulid_utils import generate_ulid

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class TrackingPixelResponse(BaseModel):
    """Response model for the tracking pixel endpoint."""

    description: str = "1x1 transparent tracking pixel (image/png)"


class ClickTrackingResponse(BaseModel):
    """Response model for the click tracking endpoint."""

    description: str = "HTTP 302 redirect to the original URL"


class UnsubscribeRequest(BaseModel):
    """Request model for unsubscribe POST actions."""

    reason: str | None = Field(None, description="Optional reason for unsubscribing")
    feedback: str | None = Field(None, description="Optional feedback from the recipient")


class SuppressionCreate(BaseModel):
    """Request model for adding an email to the suppression list."""

    email: str = Field(..., description="Email address to suppress", examples=["user@example.com"])
    reason: str | None = Field(
        None, description="Reason for suppression", examples=["bounce", "complaint", "manual"]
    )


class DomainStatsResponse(BaseModel):
    """Response model for domain-level tracking statistics."""

    domain: str = Field(..., description="The domain name")
    total_sent: int = Field(0, description="Total emails sent")
    total_opens: int = Field(0, description="Total open events recorded")
    total_clicks: int = Field(0, description="Total click events recorded")


class EmailStatsResponse(BaseModel):
    """Response model for email-level tracking statistics."""

    email_id: str = Field(..., description="The unique email identifier")
    opens: int = Field(0, description="Number of open events")
    clicks: int = Field(0, description="Number of click events")
    first_opened_at: str | None = Field(None, description="ISO 8601 timestamp of the first open")
    last_opened_at: str | None = Field(
        None, description="ISO 8601 timestamp of the most recent open"
    )


logger = logging.getLogger(__name__)
router = APIRouter()

# 0.0.0.0 (old default) resolves to the *api* container's own loopback, not
# the separate tracking container. Real host/port: tracking:8086.
#
# Real routes (worker/tracking/api/{tracking,stats,health,suppression}_api.py):
# /open/{id}, /click/{id}, /api/tracking/{bounce,complaint,inject},
# /api/tracking/stats/{email_id}, /api/tracking/stats/domain/{domain},
# /api/tracking/tenant/{id}/stats, /api/tracking/stats/summary,
# /api/tracking/health, /health(/detailed), /ready, /live,
# /tracking/suppress (POST/DELETE), /tracking/unsubscribe/{id} (GET/POST).
# unsubscribe/stats-domain/suppress were originally a genuine "no backend to
# wire to" gap (SuppressionService existed but was never instantiated or
# routed) -- built out 2026-08-08, see analytics_tracking_ee_proxy_broken.md.
TRACKING_API_BASE = os.getenv("TRACKING_API_BASE", "http://tracking:8086")

# Confirm page served for unsubscribe GETs -- same raw-read-and-serve
# approach as landing.html in app.py, with a str.replace placeholder instead
# of a template engine.
UNSUBSCRIBE_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "unsubscribe.html"


async def proxy_to_tracking(request: Request, endpoint, method="GET", data=None, params=None):
    """Proxy request to tracking service"""
    try:
        headers = {}
        if "X-API-Key" in request.headers:
            headers["X-API-Key"] = request.headers["X-API-Key"]
        if "Content-Type" in request.headers:
            headers["Content-Type"] = request.headers["Content-Type"]

        async with (
            aiohttp.ClientSession() as session,
            session.request(
                method=method,
                url=f"{TRACKING_API_BASE}{endpoint}",
                headers=headers,
                json=data,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response,
        ):
            response_data = await response.json()
            return response_data, response.status
    except Exception as e:
        logger.error(f"Tracking service proxy error: {e}")
        return {"error": "Tracking service unavailable"}, 503


async def proxy_tracking_raw(request: Request, endpoint, params=None) -> Response:
    """Like proxy_to_tracking, but for the two routes whose real responses
    are never JSON: /open (an actual image/png pixel) and /click (an HTTP
    302 redirect to the destination URL). proxy_to_tracking's
    `await response.json()` throws on both -- caught by its broad except,
    misreported as 'service unavailable' even when the real service
    answered 200/302 correctly. allow_redirects=False is required here: the
    default is to have aiohttp itself follow the redirect server-side and
    hand back the *destination's* body, instead of passing the 302 back to
    the actual recipient's email client, which is what must happen."""
    try:
        headers = {}
        if "X-API-Key" in request.headers:
            headers["X-API-Key"] = request.headers["X-API-Key"]

        async with (
            aiohttp.ClientSession() as session,
            session.request(
                method="GET",
                url=f"{TRACKING_API_BASE}{endpoint}",
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response,
        ):
            body = await response.read()
            content_type = response.headers.get("Content-Type", "application/octet-stream")

            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                return Response(status_code=response.status, headers={"Location": location})

            return Response(content=body, status_code=response.status, media_type=content_type)
    except Exception as e:
        logger.error(f"Tracking service proxy error: {e}")
        return JSONResponse({"error": "Tracking service unavailable"}, status_code=503)


@router.get(
    "/pixel/{tracking_id}",
    summary="Track email open",
    description="Returns a 1x1 transparent pixel. When loaded by the recipient's email client, records an open event with timestamp, IP, and user agent. Public and unauthenticated by design -- recipients have no account.",
)
async def track_pixel(tracking_id: str, request: Request):
    """Track email open event.

    Phase-06 fix: previously carried @require_api_key('read'), which made it
    unreachable by real recipients -- an email client rendering this <img>
    src sends neither an X-API-Key header nor a session cookie, so every
    real open would have 401'd. deep-audit.md SS2.3 and
    tests/integration/test_auth_coverage.py's own PUBLIC_PATH_PREFIXES both
    already documented this as intentionally public; the decorator
    contradicted both. Verified live before removing it: tracking_id itself
    is the only identity this needs, and it's unguessable (see the
    downstream tracking service's own generation).
    """
    return await proxy_tracking_raw(
        request, f"/open/{tracking_id}", params=dict(request.query_params)
    )


@router.get(
    "/click/{tracking_id}",
    summary="Track email link click",
    description="Records a click event for the tracked link and returns an HTTP 302 redirect to the original destination URL. Captures timestamp, IP, and user agent. Public and unauthenticated by design -- recipients have no account.",
)
async def track_click(tracking_id: str, request: Request):
    """Track email click event. Phase-06 fix -- see track_pixel's docstring;
    same bug, same reasoning."""
    return await proxy_tracking_raw(
        request, f"/click/{tracking_id}", params=dict(request.query_params)
    )


@router.get(
    "/unsubscribe/{tracking_id}",
    summary="Unsubscribe confirmation page",
    description="Serves a branded HTML confirmation page. Nothing is recorded on GET -- the page's confirm button (or its no-JS form fallback) POSTs to the endpoint below, which performs the actual unsubscribe. Public and unauthenticated by design -- recipients have no account.",
)
async def handle_unsubscribe_get(tracking_id: str, request: Request):
    """Render the unsubscribe confirm page.

    Phase-02 change: this was a JSON proxy that unsubscribed on GET -- which
    meant every link-scanning mail gateway that prefetches URLs silently
    unsubscribed the recipient, and a human clicking the link got raw JSON.
    Now the GET renders blind (no call to the tracking worker at all): the
    POST is the only mutation and the only validation, so scanner prefetch
    is harmless and a bad/expired id still gets a page whose confirm click
    surfaces the error state. RFC 8058 one-click clients POST directly and
    never see this page.
    """
    # The id lands inside a JS string and a form action. Real ids are
    # urlsafe-base64 (see the tracking service's generate_tracking_id), so
    # strip anything outside that alphabet rather than trusting the raw
    # path segment -- percent-encoded quotes/angle brackets arrive decoded.
    safe_id = re.sub(r"[^A-Za-z0-9_=-]", "", tracking_id)
    with open(UNSUBSCRIBE_TEMPLATE_PATH) as f:
        html = f.read()
    return HTMLResponse(html.replace("{{TRACKING_ID}}", safe_id))


@router.post(
    "/unsubscribe/{tracking_id}",
    summary="Handle unsubscribe (POST)",
    description="Processes an unsubscribe request via POST, optionally accepting a JSON body with a reason or feedback. Conforms to RFC 8058 one-click unsubscribe.",
)
async def handle_unsubscribe_post(tracking_id: str, request: Request):
    """Handle unsubscribe requests (POST)"""
    body = None
    content_type = request.headers.get("Content-Type", "")
    if "json" in content_type:
        body = await request.json()
    data, status = await proxy_to_tracking(
        request,
        f"/tracking/unsubscribe/{tracking_id}",
        method="POST",
        data=body,
        params=dict(request.query_params),
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/stats/domain/{domain}",
    summary="Get domain tracking statistics",
    description="Retrieves aggregated tracking statistics (opens, clicks, bounces, etc.) for all emails sent from the specified domain. Supports optional query parameters for date range filtering.",
)
@require_api_key("read")
async def get_domain_stats(domain: str, request: Request):
    """Get tracking statistics for a domain"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor(dictionary=True)
        verify_domain_scope(cursor, domain, request.state.auth_context)
    finally:
        conn.close()
    data, status = await proxy_to_tracking(
        request,
        f"/api/tracking/stats/domain/{domain}",
        method="GET",
        params=dict(request.query_params),
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/stats/email/{email_id}",
    summary="Get email tracking statistics",
    description="Retrieves detailed tracking statistics for a single email, including open count, click count, timestamps of first and last engagement, and per-link click breakdown.",
)
@require_api_key("read")
async def get_email_stats(email_id: str, request: Request):
    """Get tracking statistics for a specific email.

    Known gap (phase-06): email_id identifies a sent-message tracking
    record in the tracking service's own store, not a row in this service's
    database -- there's no local table to check ownership against without
    inventing one (conventions.md SS1 rule 5). Scope stays 'organization'
    (the default); closing this needs the tracking service to expose
    email_id -> organization_id ownership.
    """
    data, status = await proxy_to_tracking(
        request, f"/api/tracking/stats/{email_id}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.post(
    "/suppress",
    summary="Add email to suppression list",
    description="Adds an email address to the suppression list, preventing future messages from being sent to it. Use this for manual unsubscribes, known bounces, or compliance removals.",
    response_model=TrackingProxyResponse,
    responses={503: {"model": TrackingProxyError, "description": "Tracking service unavailable"}},
)
@require_api_key("write")
async def add_suppression(request: Request):
    """Add email to suppression list.

    Known gap (phase-06): a suppressed address is typically an external
    recipient, not one of the caller's own mailboxes, so verify_mailbox_scope
    doesn't apply here and there's no other local ownership relation to
    check. Scope stays 'organization' (the default).

    The caller's org is injected into the proxied body here: the tracking
    service falls back to organization_id "default" when the body omits it,
    so before this injection every tenant's suppression landed under org
    "default" -- recorded, never matched by any org-scoped read, and never
    removable through this API. A tenant credential always writes to its own
    org and a caller-supplied organization_id is overwritten, never honoured
    (same reasoning as org_filter). A platform caller has no org of its own
    and must name one -- organization_id is NOT NULL and part of the table's
    unique key, so there is no cross-org suppression to fall back to (same
    contract as bulk_suppress below).
    """
    ctx = request.state.auth_context
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(
            content=create_api_response("error", "Request body must be a JSON object"),
            status_code=422,
        )

    if ctx["scope"] == "organization":
        body["organization_id"] = ctx["organization_id"]
    elif not body.get("organization_id"):
        return JSONResponse(
            content=create_api_response(
                "error", "organization_id is required for platform-scope callers"
            ),
            status_code=422,
        )

    data, status = await proxy_to_tracking(request, "/tracking/suppress", method="POST", data=body)
    return JSONResponse(content=data, status_code=status)


@router.delete("/suppress/{email}")
@require_api_key("write")
async def remove_suppression(email: str, request: Request):
    """Remove email from suppression list.

    Same org scoping as add_suppression: this used to proxy with no
    organization_id at all, so the tracking service deactivated rows under
    its "default" org -- meaning no real tenant could ever remove a
    suppression through this API. A tenant credential removes from its own
    org only; a platform caller names the org via the organization_id query
    parameter.
    """
    ctx = request.state.auth_context
    if ctx["scope"] == "organization":
        target_org = ctx["organization_id"]
    else:
        target_org = request.query_params.get("organization_id")
        if not target_org:
            return JSONResponse(
                content=create_api_response(
                    "error", "organization_id is required for platform-scope callers"
                ),
                status_code=422,
            )
    data, status = await proxy_to_tracking(
        request,
        f"/tracking/suppress/{email}",
        method="DELETE",
        params={"organization_id": target_org},
    )
    return JSONResponse(content=data, status_code=status)


# ---------------------------------------------------------------------------
# Suppression list read + bulk write (console Suppressions screen)
# ---------------------------------------------------------------------------
#
# POST /suppress and DELETE /suppress/{email} above proxy single-address
# writes to the tracking service, and that was the entire suppression
# surface: there was no way to LIST suppressions at all, so the console's
# Suppressions screen had nothing to render. These two read `email_suppressions`
# directly rather than adding another proxy hop -- the table lives in this
# service's own database (0001_baseline), the tracking service writes to the
# same table (worker/tracking/services/suppression_service.py), and a list
# view has no reason to depend on that container being up.
#
# Real columns, read off 0001_baseline rather than database/models/ (which
# has drifted elsewhere in this schema):
#   id CHAR(26) -- no DB-side default, every INSERT supplies a ULID
#   email, organization_id VARCHAR(100) NOT NULL, suppression_type ENUM,
#   reason TEXT, source, expires_at, bounce_type, bounce_count,
#   last_bounce_reason, active TINYINT(1), created_at, updated_at
#   UNIQUE (email, organization_id, suppression_type)

# The enum members the column actually declares. Validated in Python rather
# than left to MySQL: an out-of-range enum value inserts as '' under a
# non-strict sql_mode instead of erroring, which would silently create a
# suppression nothing can ever match or remove.
_SUPPRESSION_TYPES = ("BOUNCE", "COMPLAINT", "UNSUBSCRIBE", "MANUAL")

# ORDER BY takes no bound parameter, so the sort key has to be interpolated
# -- which means it must come from a closed set, never from caller input.
# Same reasoning (and same shape) as _DOMAIN_SORT_KEYS in routes/domains.py.
_SUPPRESSION_SORT_KEYS = {"created_at": "created_at", "email": "email"}
_SUPPRESSION_SORT_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}

_SUPPRESSION_BULK_MAX = 1000

# Deliberately permissive: this is a do-not-send list, and refusing to
# suppress a weird-but-real address is worse than accepting one. It exists
# to catch obvious paste errors (whitespace, missing @) before they become
# rows nothing will ever match.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SuppressionBulkCreate(BaseModel):
    """Request body for POST /suppressions/bulk."""

    emails: list[str] = Field(
        ...,
        min_length=1,
        description=f"Addresses to suppress. At most {_SUPPRESSION_BULK_MAX} per call.",
    )
    suppression_type: str = Field(
        "MANUAL",
        description=f"One of {', '.join(_SUPPRESSION_TYPES)}.",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Why these addresses are being suppressed. Stored on every row and shown "
        "in the console -- a suppression nobody can explain later is one nobody dares remove.",
    )
    organization_id: str | None = Field(
        None,
        description="Owning organization. Required for platform-scope callers, who have no "
        "organization of their own; ignored for tenant credentials, which always write to "
        "their own org. email_suppressions.organization_id is NOT NULL and part of the "
        "table's unique key, so there is no 'global' suppression to fall back to.",
    )


@router.get(
    "/suppressions",
    summary="List suppressed addresses",
    description="Paginated, filterable view over the suppression list -- the read side the "
    "console's Suppressions screen needs. Platform scope sees every organization and may "
    "narrow to one with `organization_id`; a tenant credential always sees only its own, "
    "and a caller-supplied organization_id is ignored rather than honoured.",
)
@require_api_key("read", role="support")
async def list_suppressions(
    request: Request,
    organization_id: str | None = Query(
        None, description="Platform scope only -- narrow to one org"
    ),
    suppression_type: str | None = Query(
        None, description=f"One of {', '.join(_SUPPRESSION_TYPES)}"
    ),
    active: bool | None = Query(None, description="Filter on the active flag"),
    q: str | None = Query(None, description="Substring match on the email address"),
    date_from: str | None = Query(None, description="ISO date/datetime lower bound on created_at"),
    date_to: str | None = Query(None, description="ISO date/datetime upper bound on created_at"),
    sort_by: str = Query("created_at", description="created_at | email"),
    sort_dir: str = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
):
    ctx = request.state.auth_context

    if sort_by not in _SUPPRESSION_SORT_KEYS:
        return JSONResponse(
            content=create_api_response(
                "error", f"sort_by must be one of {', '.join(sorted(_SUPPRESSION_SORT_KEYS))}"
            ),
            status_code=422,
        )
    if sort_dir.lower() not in _SUPPRESSION_SORT_DIRECTIONS:
        return JSONResponse(
            content=create_api_response("error", "sort_dir must be 'asc' or 'desc'"),
            status_code=422,
        )
    if suppression_type and suppression_type.upper() not in _SUPPRESSION_TYPES:
        return JSONResponse(
            content=create_api_response(
                "error", f"suppression_type must be one of {', '.join(_SUPPRESSION_TYPES)}"
            ),
            status_code=422,
        )

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    # The single helper that decides "which orgs can this caller see"
    # (utils/auth.py task 6.4). A caller-supplied organization_id is
    # silently dropped for organization scope by design -- see its docstring
    # on why dropping beats validating here.
    org_sql, params = org_filter(ctx, organization_id)
    conditions = [org_sql]

    if suppression_type:
        conditions.append("suppression_type = %s")
        params.append(suppression_type.upper())
    if active is not None:
        conditions.append("active = %s")
        params.append(1 if active else 0)
    if q:
        conditions.append("email LIKE %s")
        # The wildcards are added to the *bound value*, not to the SQL --
        # the LIKE pattern is still a parameter, so a caller cannot inject
        # through it.
        params.append(f"%{q}%")
    if date_from:
        conditions.append("created_at >= %s")
        params.append(date_from)
    if date_to:
        # A bare date as an upper bound means "through the end of that day",
        # otherwise date_from=X&date_to=X returns nothing -- the single most
        # likely filter a human types. Same handling as platform.py's
        # _parse_iso_datetime.
        conditions.append("created_at <= %s")
        params.append(f"{date_to} 23:59:59" if len(date_to.strip()) == 10 else date_to)

    where = "WHERE " + " AND ".join(conditions)
    order_sql = (
        f"{_SUPPRESSION_SORT_KEYS[sort_by]} {_SUPPRESSION_SORT_DIRECTIONS[sort_dir.lower()]}"
    )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM email_suppressions {where}", params)
        total = (cursor.fetchone() or {}).get("total") or 0

        cursor.execute(
            f"""
            SELECT id, email, organization_id, suppression_type, reason, source,
                   bounce_type, bounce_count, active, expires_at, created_at, updated_at
            FROM email_suppressions
            {where}
            ORDER BY {order_sql}, id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        for row in rows:
            row["active"] = bool(row["active"])
            for column in ("expires_at", "created_at", "updated_at"):
                if row.get(column):
                    row[column] = row[column].isoformat()

        return create_api_response(
            "success",
            "Suppressions retrieved",
            {
                "items": rows,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": (total + per_page - 1) // per_page,
                },
            },
        )

    except Exception as e:
        logger.error(f"List suppressions error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve suppressions"),
            status_code=500,
        )
    finally:
        conn.close()


@router.post(
    "/suppressions/bulk",
    summary="Suppress several addresses at once",
    description="Adds up to 1000 addresses to the suppression list in one call and reports the "
    "outcome of each. Individual failures come back in `failed` with their reason -- they are "
    "never swallowed, because a bulk import that silently drops 3 of 900 addresses is how a "
    "suppressed recipient gets mailed anyway.",
)
@require_api_key("write", role="operator")
async def bulk_suppress(request: Request, body: SuppressionBulkCreate):
    ctx = request.state.auth_context

    if len(body.emails) > _SUPPRESSION_BULK_MAX:
        return JSONResponse(
            content=create_api_response(
                "error",
                f"At most {_SUPPRESSION_BULK_MAX} emails per call ({len(body.emails)} supplied)",
            ),
            status_code=422,
        )

    suppression_type = body.suppression_type.upper()
    if suppression_type not in _SUPPRESSION_TYPES:
        return JSONResponse(
            content=create_api_response(
                "error", f"suppression_type must be one of {', '.join(_SUPPRESSION_TYPES)}"
            ),
            status_code=422,
        )

    # A tenant credential writes to its own org and cannot be talked out of
    # it. A platform caller has no org of its own, and the column is NOT
    # NULL and part of the unique key, so it must name one -- there is no
    # cross-org suppression this schema can express.
    if ctx["scope"] == "organization":
        target_org = ctx["organization_id"]
    else:
        target_org = body.organization_id
        if not target_org:
            return JSONResponse(
                content=create_api_response(
                    "error",
                    "organization_id is required for platform-scope callers: "
                    "email_suppressions.organization_id is NOT NULL and forms part of the "
                    "table's unique key, so a suppression must belong to exactly one tenant.",
                ),
                status_code=422,
            )

    # De-duplicated case-insensitively while preserving the caller's order:
    # the same address twice in one paste must not produce two results
    # disagreeing about whether it worked.
    seen: set = set()
    requested: list = []
    for raw in body.emails:
        candidate = (raw or "").strip()
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        requested.append(candidate)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        suppressed: list = []
        failed: list = []

        for email in requested:
            if not _EMAIL_SHAPE.match(email):
                failed.append({"email": email, "error": "not a valid email address"})
                continue
            try:
                # ON DUPLICATE KEY UPDATE against the (email, organization_id,
                # suppression_type) unique key -- re-suppressing an address
                # already on the list reactivates it and refreshes the
                # reason rather than erroring, which is what an operator
                # re-importing a bounce list actually means. Same idiom
                # routes/message_trace.py and the tracking service use.
                cursor.execute(
                    """
                    INSERT INTO email_suppressions
                        (id, email, organization_id, suppression_type, reason, source,
                         active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'console', 1, NOW(), NOW())
                    ON DUPLICATE KEY UPDATE
                        reason = VALUES(reason),
                        source = VALUES(source),
                        active = 1,
                        updated_at = NOW()
                    """,
                    (generate_ulid(), email, target_org, suppression_type, body.reason),
                )
            except Exception as exc:
                # Per-row, not per-batch: one bad address must not roll back
                # the 999 good ones, and the caller has to be told which.
                failed.append({"email": email, "error": str(exc)[:200]})
                continue
            suppressed.append({"email": email, "suppression_type": suppression_type})

        conn.commit()
        cursor.close()

        logger.warning(
            f"Bulk suppression: org={target_org} type={suppression_type} "
            f"requested={len(requested)} suppressed={len(suppressed)} failed={len(failed)} "
            f"by={ctx.get('operator_id')} reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            f"{len(suppressed)} of {len(requested)} addresses suppressed",
            {
                "suppressed": suppressed,
                "failed": failed,
                "requested": len(requested),
                "organization_id": target_org,
            },
        )

    except Exception as e:
        logger.error(f"Bulk suppress error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to suppress addresses"), status_code=500
        )
    finally:
        conn.close()
