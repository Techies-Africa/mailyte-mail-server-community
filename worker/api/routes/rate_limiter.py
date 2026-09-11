#!/usr/bin/env python3
"""
Rate Limiter Module API Routes
Exposes controlled rate limiting functionality through the API gateway
"""

import logging
import os

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from schemas.rate_limiter import RateLimiterProxyResponse
from utils.auth import (
    create_api_response,
    require_api_key,
    verify_domain_scope,
    verify_mailbox_scope,
)
from utils.database import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()

# 0.0.0.0 (this file's old default) resolves to the *api* container's own
# loopback, not the separate rate_limiter container -- every proxy call
# failed with "service unavailable" regardless of the path fixes below.
# rate_limiter's real host/port (docker-compose service name + its actual
# listening port, config.py's RATE_LIMITER_PORT default) is rate_limiter:8082.
RATE_LIMITER_API_BASE = os.getenv("RATE_LIMITER_API_BASE", "http://rate_limiter:8082")


def _verify_domain(domain: str, request: Request) -> None:
    """Phase-06 finding: every route in this file forwarded its `domain`/
    `email` path param straight to the downstream service with no check it
    belonged to the caller's org -- deep-audit.md SS2.2 classifies the read
    routes here as tenant-scoped ("own limits"), which this file never
    actually enforced. This file has no DB access of its own (pure proxy),
    so open a connection just for the check."""
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


def _verify_mailbox(email: str, request: Request) -> None:
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor(dictionary=True)
        verify_mailbox_scope(cursor, email, request.state.auth_context)
    finally:
        conn.close()


def _verify_organization(organization_id: str, request: Request) -> None:
    """An organization has no domains/email_accounts row to check against --
    it just has to be the caller's own org id (platform-scope callers see
    everything, same as verify_domain_scope/verify_mailbox_scope)."""
    ctx = request.state.auth_context
    if ctx["scope"] != "platform" and str(organization_id) != str(ctx["organization_id"]):
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Organization not found")
        )


def _verify_entity(entity_type: str, identifier: str, request: Request) -> None:
    """Ownership check for reset_limits, which takes its entity type/id from
    the request body rather than a path param, so it can span all three
    entity kinds."""
    if entity_type == "domain":
        _verify_domain(identifier, request)
    elif entity_type == "mailbox":
        _verify_mailbox(identifier, request)
    elif entity_type == "organization":
        _verify_organization(identifier, request)
    else:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", "Invalid entity type")
        )


async def proxy_to_rate_limiter(request: Request, endpoint, method="GET", data=None, params=None):
    """Proxy request to rate limiter service"""
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
                url=f"{RATE_LIMITER_API_BASE}{endpoint}",
                headers=headers,
                json=data,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response,
        ):
            response_data = await response.json()
            return response_data, response.status
    except Exception as e:
        logger.error(f"Rate limiter service proxy error: {e}")
        return {"error": "Rate limiter service unavailable"}, 503


# The real rate_limiter service (worker/rate_limiter/app.py) has no
# domain/mailbox-shaped "limits" or "quotas" endpoints at all -- it exposes
# a generic /get_usage/{entity_type}/{identifier} (limits+usage bundled
# together as `config`+`usage`) and a generic /set_limits that replaces the
# WHOLE rule for that entity, defaulting any field not in the request body
# to 0 ("no limit"). A naive proxy that only forwards the one field a
# caller cares about would silently zero out every other limit already
# configured for that entity -- so every "set" path here reads the current
# rule first and merges on top of it, rather than overwriting wholesale.
_SET_LIMIT_FIELDS = (
    "second_limit",
    "minute_limit",
    "hourly_limit",
    "daily_limit",
    "monthly_limit",
    "burst_limit",
    "active",
    "priority",
    "warning_threshold",
    "critical_threshold",
    "description",
)


async def _get_usage(request: Request, entity_type: str, identifier: str, direction: str):
    """Call the real /get_usage/{entity_type}/{identifier} endpoint."""
    data, status = await proxy_to_rate_limiter(
        request,
        f"/get_usage/{entity_type}/{identifier}",
        method="GET",
        params={"direction": direction},
    )
    return data, status


async def _merge_and_set_limits(
    request: Request, entity_type: str, identifier: str, incoming: dict
):
    """Merge an incoming (possibly partial, possibly Laravel-vocabulary)
    limits payload onto the entity's current rule, then call the real
    /set_limits endpoint with the full merged rule.

    `max_outbound_per_day` is Laravel's own field name (used by
    SyncPlanLimitsJob/SuspendOverLimitOrganizationsJob -- the two real,
    live callers of this path) for what the mail server calls
    `daily_limit`; both names are accepted, `max_outbound_per_day` wins if
    both are present.
    """
    direction = incoming.get("direction", "outbound")

    current, current_status = await _get_usage(request, entity_type, identifier, direction)
    current_config = current.get("data", {}).get("config", {}) if current_status == 200 else {}

    merged = {field: current_config.get(field) for field in _SET_LIMIT_FIELDS}
    merged = {k: v for k, v in merged.items() if v is not None}

    for field in _SET_LIMIT_FIELDS:
        if field in incoming:
            merged[field] = incoming[field]

    if "max_outbound_per_day" in incoming:
        merged["daily_limit"] = incoming["max_outbound_per_day"]

    payload = {
        "type": entity_type,
        "identifier": identifier,
        "direction": direction,
        **merged,
        "created_by": incoming.get("created_by", "mailyte-api"),
    }

    data, status = await proxy_to_rate_limiter(request, "/set_limits", method="POST", data=payload)
    return data, status


@router.get("/rate-limits/domain/{domain}")
@require_api_key("read")
async def get_domain_limits(domain: str, request: Request):
    """Get rate limits for a domain"""
    _verify_domain(domain, request)
    direction = request.query_params.get("direction", "outbound")
    data, status = await _get_usage(request, "domain", domain, direction)
    return JSONResponse(content=data, status_code=status)


@router.post("/rate-limits/domain/{domain}")
@require_api_key("write")
async def set_domain_limits(domain: str, request: Request):
    """Set rate limits for a domain.

    scope="platform", role="operator" (this route's setting until this fix)
    can never be satisfied by an API key -- roles only exist for operator
    console sessions (see _resolve_scope_auth/_role_satisfies), so
    SuspendOverLimitOrganizationsJob and SyncPlanLimitsJob, the two real
    Laravel callers of this exact path, could never actually reach it. Uses
    the same organization-scope + ownership check as the read routes above
    instead: any valid key can set limits for domains it owns, not just an
    operator session.
    """
    _verify_domain(domain, request)
    incoming = await request.json()
    data, status = await _merge_and_set_limits(request, "domain", domain, incoming)
    return JSONResponse(content=data, status_code=status)


@router.get("/rate-limits/mailbox/{email}")
@require_api_key("read")
async def get_mailbox_limits(email: str, request: Request):
    """Get rate limits for a mailbox"""
    _verify_mailbox(email, request)
    direction = request.query_params.get("direction", "outbound")
    data, status = await _get_usage(request, "mailbox", email, direction)
    return JSONResponse(content=data, status_code=status)


@router.post("/rate-limits/mailbox/{email}")
@require_api_key("write")
async def set_mailbox_limits(email: str, request: Request):
    """Set rate limits for a mailbox (see set_domain_limits docstring for
    why this is organization-scope + ownership, not platform+operator)."""
    _verify_mailbox(email, request)
    incoming = await request.json()
    data, status = await _merge_and_set_limits(request, "mailbox", email, incoming)
    return JSONResponse(content=data, status_code=status)


@router.get("/rate-limits/usage/{domain}")
@require_api_key("read")
async def get_domain_usage(domain: str, request: Request):
    """Get current usage statistics for a domain"""
    _verify_domain(domain, request)
    direction = request.query_params.get("direction", "outbound")
    data, status = await _get_usage(request, "domain", domain, direction)
    return JSONResponse(content=data, status_code=status)


@router.post(
    "/rate-limits/reset",
    response_model=RateLimiterProxyResponse,
    responses={
        503: {
            "model": RateLimiterProxyResponse,
            "description": "Rate limiter microservice unavailable",
        },
    },
)
@require_api_key("write")
async def reset_limits(request: Request):
    """Reset rate limit counters for an organization/domain/mailbox.

    Body: {"type": "domain"|"mailbox"|"organization", "identifier": "...",
    "direction": "inbound"|"outbound"}. Forwards to the real
    POST /reset_counters (added alongside this fix -- the underlying
    RateLimitCacheService.reset_counters() already existed but had no route
    exposing it at all, not even a path mismatch). Organization-scope +
    ownership check (see set_domain_limits docstring) instead of the
    previous platform+operator gate no API key could ever satisfy.
    """
    body = await request.json()
    _verify_entity(body.get("type"), body.get("identifier"), request)
    data, status = await proxy_to_rate_limiter(request, "/reset_counters", method="POST", data=body)
    return JSONResponse(content=data, status_code=status)


@router.get("/quotas/domain/{domain}")
@require_api_key("read")
async def get_domain_quotas(domain: str, request: Request):
    """Get daily/monthly quotas for a domain.

    The real service has no distinct "quota" concept from a rate limit --
    monthly_limit is the closest equivalent -- so this reads the same
    /get_usage/domain/{domain} rule every domain-limits call reads.
    """
    _verify_domain(domain, request)
    direction = request.query_params.get("direction", "outbound")
    data, status = await _get_usage(request, "domain", domain, direction)
    return JSONResponse(content=data, status_code=status)


@router.post("/quotas/domain/{domain}")
@require_api_key("write")
async def set_domain_quotas(domain: str, request: Request):
    """Set daily/monthly quotas for a domain (see get_domain_quotas and
    set_domain_limits docstrings)."""
    _verify_domain(domain, request)
    incoming = await request.json()
    data, status = await _merge_and_set_limits(request, "domain", domain, incoming)
    return JSONResponse(content=data, status_code=status)


# Send credits. These existed on the rate_limiter service from the day it was
# written but were never proxied here, so Laravel's SyncEntitlementsJob has
# been posting to /api/v1/rate-limiter/send_credits/{org} and taking a 404
# every time -- the credit balance and monthly app-sending allowance have
# never reached the enforcer. It failed silently because enforcement reads a
# missing allowance as 0 and a missing balance as "no opinion", both of which
# allow the send.
#
# Unlike the limits/quotas routes above, these need no read-merge-write: the
# real endpoint applies only the fields present in the body, so a field the
# caller omits is left alone rather than defaulted to zero.


@router.get("/send_credits/{organization_id}")
@require_api_key("read")
async def get_send_credits(organization_id: str, request: Request):
    """What enforcement currently believes about this organization's credits,
    for reconciliation against the Laravel ledger."""
    _verify_organization(organization_id, request)
    data, status = await proxy_to_rate_limiter(
        request, f"/send_credits/{organization_id}", "GET"
    )
    return JSONResponse(content=data, status_code=status)


@router.post("/send_credits/{organization_id}")
@require_api_key("write")
async def set_send_credits(organization_id: str, request: Request):
    """Push an organization's credit balance and app-sending allowance down.
    Laravel is the ledger of record; this is the enforced copy."""
    _verify_organization(organization_id, request)
    incoming = await request.json()
    data, status = await proxy_to_rate_limiter(
        request, f"/send_credits/{organization_id}", "POST", data=incoming
    )
    return JSONResponse(content=data, status_code=status)
