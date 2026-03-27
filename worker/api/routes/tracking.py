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

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import logging
import aiohttp
import os
from utils.auth import require_api_key, create_api_response
from database.models import EmailTracking, TrackingStatistics
from database.models.core import Organization, Domain, EmailAccount


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
    reason: Optional[str] = Field(None, description="Optional reason for unsubscribing")
    feedback: Optional[str] = Field(None, description="Optional feedback from the recipient")

class SuppressionCreate(BaseModel):
    """Request model for adding an email to the suppression list."""
    email: str = Field(..., description="Email address to suppress", examples=["user@example.com"])
    reason: Optional[str] = Field(None, description="Reason for suppression", examples=["bounce", "complaint", "manual"])

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
    first_opened_at: Optional[str] = Field(None, description="ISO 8601 timestamp of the first open")
    last_opened_at: Optional[str] = Field(None, description="ISO 8601 timestamp of the most recent open")

logger = logging.getLogger(__name__)
router = APIRouter()

TRACKING_API_BASE = f"http://0.0.0.0:{os.getenv('TRACKING_API_PORT', 5001)}"

async def proxy_to_tracking(request: Request, endpoint, method='GET', data=None, params=None):
    """Proxy request to tracking service"""
    try:
        headers = {}
        if 'X-API-Key' in request.headers:
            headers['X-API-Key'] = request.headers['X-API-Key']
        if 'Content-Type' in request.headers:
            headers['Content-Type'] = request.headers['Content-Type']

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=method,
                url=f"{TRACKING_API_BASE}{endpoint}",
                headers=headers,
                json=data,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response_data = await response.json()
                return response_data, response.status
    except Exception as e:
        logger.error(f"Tracking service proxy error: {e}")
        return {"error": "Tracking service unavailable"}, 503

@router.get(
    '/pixel/{tracking_id}',
    summary="Track email open",
    description="Returns a 1x1 transparent pixel. When loaded by the recipient's email client, records an open event with timestamp, IP, and user agent.",
)
@require_api_key('read')
async def track_pixel(tracking_id: str, request: Request):
    """Track email open event"""
    data, status = await proxy_to_tracking(
        request,
        f'/tracking/pixel/{tracking_id}',
        method='GET',
        params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)

@router.get(
    '/click/{tracking_id}',
    summary="Track email link click",
    description="Records a click event for the tracked link and returns an HTTP 302 redirect to the original destination URL. Captures timestamp, IP, and user agent.",
)
@require_api_key('read')
async def track_click(tracking_id: str, request: Request):
    """Track email click event"""
    data, status = await proxy_to_tracking(
        request,
        f'/tracking/click/{tracking_id}',
        method='GET',
        params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)

@router.get(
    '/unsubscribe/{tracking_id}',
    summary="Handle unsubscribe (GET)",
    description="Processes a one-click unsubscribe request via GET. Typically triggered by the List-Unsubscribe header in email clients. Removes the recipient from future mailings for the associated campaign or sender.",
)
async def handle_unsubscribe_get(tracking_id: str, request: Request):
    """Handle unsubscribe requests (GET)"""
    data, status = await proxy_to_tracking(
        request,
        f'/tracking/unsubscribe/{tracking_id}',
        method='GET',
        params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)

@router.post(
    '/unsubscribe/{tracking_id}',
    summary="Handle unsubscribe (POST)",
    description="Processes an unsubscribe request via POST, optionally accepting a JSON body with a reason or feedback. Conforms to RFC 8058 one-click unsubscribe.",
)
async def handle_unsubscribe_post(tracking_id: str, request: Request):
    """Handle unsubscribe requests (POST)"""
    body = None
    content_type = request.headers.get('Content-Type', '')
    if 'json' in content_type:
        body = await request.json()
    data, status = await proxy_to_tracking(
        request,
        f'/tracking/unsubscribe/{tracking_id}',
        method='POST',
        data=body,
        params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)

@router.get(
    '/stats/domain/{domain}',
    summary="Get domain tracking statistics",
    description="Retrieves aggregated tracking statistics (opens, clicks, bounces, etc.) for all emails sent from the specified domain. Supports optional query parameters for date range filtering.",
)
@require_api_key('read')
async def get_domain_stats(domain: str, request: Request):
    """Get tracking statistics for a domain"""
    data, status = await proxy_to_tracking(
        request,
        f'/stats/domain/{domain}',
        method='GET',
        params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)

@router.get(
    '/stats/email/{email_id}',
    summary="Get email tracking statistics",
    description="Retrieves detailed tracking statistics for a single email, including open count, click count, timestamps of first and last engagement, and per-link click breakdown.",
)
@require_api_key('read')
async def get_email_stats(email_id: str, request: Request):
    """Get tracking statistics for a specific email"""
    data, status = await proxy_to_tracking(
        request,
        f'/stats/email/{email_id}',
        method='GET',
        params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)

@router.post(
    '/suppress',
    summary="Add email to suppression list",
    description="Adds an email address to the suppression list, preventing future messages from being sent to it. Use this for manual unsubscribes, known bounces, or compliance removals.",
)
@require_api_key('write')
async def add_suppression(request: Request):
    """Add email to suppression list"""
    data, status = await proxy_to_tracking(
        request,
        '/tracking/suppress',
        method='POST',
        data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)

@router.delete('/suppress/{email}')
@require_api_key('write')
async def remove_suppression(email: str, request: Request):
    """Remove email from suppression list"""
    data, status = await proxy_to_tracking(
        request,
        f'/tracking/suppress/{email}',
        method='DELETE'
    )
    return JSONResponse(content=data, status_code=status)
