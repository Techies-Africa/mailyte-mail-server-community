#!/usr/bin/env python3
"""
Analytics Module API Routes
Exposes controlled analytics functionality through the API gateway
"""

import logging
import os

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from schemas.analytics import AnalyticsProxyError, AnalyticsProxyResponse
from utils.auth import create_api_response, require_api_key, verify_domain_scope
from utils.database import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()

# 0.0.0.0 (old default) resolves to the *api* container's own loopback, not
# the separate analytics container -- every proxy call failed with "service
# unavailable" regardless of path. Real host/port: analytics:8085.
#
# Even after this fix, only /analytics/health below has a real endpoint to
# reach. The real worker/analytics/app.py is a small legacy, non-org-aware
# service (`/health`, `/stats`, `/user-activity/{email}`, an HTML
# `/dashboard`) querying the global `mail_logs` table directly -- it has no
# domain-scoped dashboard/email-volume/engagement/deliverability endpoints
# and no report generation/scheduling system at all. The other 8 routes in
# this file are not a wiring bug to fix: there is nothing on the other end,
# by design gap not typo. Fixing the host still matters for those, though --
# it turns a misleading "service unavailable" into an honest 404 (route not
# found on a real, reachable service) instead of masking the real problem
# behind a connection failure.
ANALYTICS_API_BASE = os.getenv("ANALYTICS_API_BASE", "http://analytics:8085")


def _verify_domain_ownership(domain: str, request: Request) -> None:
    """This file is a pure proxy to the analytics service with no DB access
    of its own -- previously the `domain` path param was forwarded straight
    through with no check it belonged to the caller's org at all (phase-06
    finding). Opens a connection just for the ownership check."""
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


async def proxy_to_analytics(request: Request, endpoint, method="GET", data=None, params=None):
    """Proxy request to analytics service"""
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
                url=f"{ANALYTICS_API_BASE}{endpoint}",
                headers=headers,
                json=data,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response,
        ):
            response_data = await response.json()
            return response_data, response.status
    except Exception as e:
        logger.error(f"Analytics service proxy error: {e}")
        return {"error": "Analytics service unavailable"}, 503


@router.get(
    "/dashboard/{domain}",
    summary="Domain analytics dashboard",
    description="Returns aggregate email metrics for the specified domain: total sent, delivered, bounced, opened, and clicked over the selected time period.",
)
@require_api_key("read")
async def get_dashboard_data(domain: str, request: Request):
    """Get analytics dashboard data for a domain"""
    _verify_domain_ownership(domain, request)
    data, status = await proxy_to_analytics(
        request, f"/analytics/dashboard/{domain}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/email-volume/{domain}",
    summary="Email volume analytics",
    description="Returns email volume trends for a domain broken down by time interval. Includes inbound and outbound counts, useful for capacity planning and usage monitoring.",
)
@require_api_key("read")
async def get_email_volume(domain: str, request: Request):
    """Get email volume analytics for a domain"""
    _verify_domain_ownership(domain, request)
    data, status = await proxy_to_analytics(
        request,
        f"/analytics/email-volume/{domain}",
        method="GET",
        params=dict(request.query_params),
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/engagement/{domain}",
    summary="Email engagement metrics",
    description="Returns engagement metrics for a domain including open rates, click-through rates, reply rates, and read times over the selected time period.",
)
@require_api_key("read")
async def get_engagement_metrics(domain: str, request: Request):
    """Get engagement metrics for a domain"""
    _verify_domain_ownership(domain, request)
    data, status = await proxy_to_analytics(
        request, f"/analytics/engagement/{domain}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/deliverability/{domain}",
    summary="Email deliverability metrics",
    description="Returns deliverability metrics for a domain including bounce rates, spam complaint rates, SPF/DKIM/DMARC pass rates, and inbox placement estimates.",
)
@require_api_key("read")
async def get_deliverability_metrics(domain: str, request: Request):
    """Get deliverability metrics for a domain"""
    _verify_domain_ownership(domain, request)
    data, status = await proxy_to_analytics(
        request,
        f"/analytics/deliverability/{domain}",
        method="GET",
        params=dict(request.query_params),
    )
    return JSONResponse(content=data, status_code=status)


@router.post(
    "/reports/generate",
    summary="Generate analytics report",
    description="Generate a new analytics report for the specified domain and time range. The report is created asynchronously and can be retrieved by its ID once complete.",
    response_model=AnalyticsProxyResponse,
    responses={503: {"model": AnalyticsProxyError, "description": "Analytics service unavailable"}},
)
@require_api_key("read")
async def generate_report(request: Request):
    """Generate analytics report"""
    data, status = await proxy_to_analytics(
        request, "/reports/generate", method="POST", data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/reports/scheduled",
    summary="List scheduled reports",
    description="List all scheduled report configurations. Scheduled reports are generated automatically at the defined interval and delivered to the specified recipients.",
    response_model=AnalyticsProxyResponse,
    responses={503: {"model": AnalyticsProxyError, "description": "Analytics service unavailable"}},
)
@require_api_key("read")
async def get_scheduled_reports(request: Request):
    """Get scheduled reports.

    Must be declared before GET /reports/{report_id} below -- FastAPI
    matches routes in declaration order, so with the generic {report_id}
    route first, a request for /reports/scheduled always matched that one
    instead, with "scheduled" bound to report_id. Confirmed live: listing
    schedules returned "Report not found or expired" every time.
    """
    data, status = await proxy_to_analytics(
        request, "/reports/scheduled", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.post(
    "/reports/scheduled",
    summary="Create scheduled report",
    description="Create a new scheduled report configuration. Define the domain, metrics, time range, delivery interval, and recipient list for automatic report generation.",
    response_model=AnalyticsProxyResponse,
    responses={503: {"model": AnalyticsProxyError, "description": "Analytics service unavailable"}},
)
@require_api_key("write")
async def create_scheduled_report(request: Request):
    """Create scheduled report"""
    data, status = await proxy_to_analytics(
        request, "/reports/scheduled", method="POST", data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/reports/{report_id}",
    summary="Get report by ID",
    description="Retrieve a previously generated analytics report by its unique ID. Returns the report data and metadata including generation status.",
)
@require_api_key("read")
async def get_report(report_id: str, request: Request):
    """Get generated report"""
    data, status = await proxy_to_analytics(request, f"/reports/{report_id}", method="GET")
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/metrics/{domain}",
    summary="Get domain metrics",
    description="Returns detailed metrics for a domain including message counts, storage usage, active users, and performance indicators over the requested time window.",
)
@require_api_key("read")
async def get_domain_metrics(domain: str, request: Request):
    """Get domain metrics"""
    _verify_domain_ownership(domain, request)
    data, status = await proxy_to_analytics(
        request, f"/analytics/metrics/{domain}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/health",
    summary="Analytics service health",
    description="Check the health of the analytics service including database connectivity, data pipeline status, and processing lag.",
    response_model=AnalyticsProxyResponse,
    responses={503: {"model": AnalyticsProxyError, "description": "Analytics service unavailable"}},
)
@require_api_key("read")
async def analytics_health(request: Request):
    data, status = await proxy_to_analytics(
        request, "/health", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)
