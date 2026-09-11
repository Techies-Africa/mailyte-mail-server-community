#!/usr/bin/env python3
"""
Email Tracking Service - Tracking API Endpoints

This module provides the main tracking API endpoints:
- /track/open/<tracking_id> - Handle email open tracking
- /track/click/<tracking_id> - Handle email click tracking
- /api/tracking/inject - Inject tracking into email content
- /api/tracking/stats/<email_id> - Get tracking statistics

All endpoints include comprehensive error handling, rate limiting,
and webhook notifications.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

# Ensure project root is on sys.path for shared imports
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)

# Create router for tracking endpoints
tracking_api = APIRouter()


@tracking_api.get("/open/{tracking_id}")
async def track_open(tracking_id: str, request: Request):
    """
    Handle email open tracking via tracking pixel.

    This endpoint serves a 1x1 transparent pixel and logs the open event.
    It includes comprehensive request metadata collection and rate limiting.

    Args:
        tracking_id: Base64-encoded tracking identifier

    Returns:
        Response: 1x1 transparent PNG pixel
    """
    try:
        # Rate limiting check. check_rate_limit()/RateLimitExceeded don't
        # exist on the real RateLimiter -- it's is_rate_limited(ip) -> bool,
        # never a raised exception -- so this always threw AttributeError,
        # caught by the outer try below, which skipped straight to serving
        # the pixel without ever reaching the tracking_data decode/log below
        # it. Every real open silently never got logged.
        if request.app.rate_limiter.is_rate_limited(request.client.host):
            logger.warning(f"Rate limit exceeded for IP {request.client.host}")
            # Still serve pixel but don't log the event
            return _serve_tracking_pixel()

        # Decode tracking information
        tracking_data = request.app.tracking_service.decode_tracking_id(tracking_id)
        if not tracking_data:
            logger.warning(f"Invalid tracking ID: {tracking_id}")
            return _serve_tracking_pixel()

        # Collect request metadata
        request_info = {
            "timestamp": datetime.utcnow(),
            "ip_address": request.client.host,
            "user_agent": request.headers.get("User-Agent", ""),
            "referer": request.headers.get("Referer", ""),
            "accept_language": request.headers.get("Accept-Language", ""),
            "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
            "x_real_ip": request.headers.get("X-Real-IP", ""),
        }

        # Log tracking event
        success = request.app.database_service.log_tracking_event(
            "opened", tracking_data, request_info
        )

        if success:
            # Send webhook notification via centralized dispatcher
            dispatch_event(
                Events.TRACKING_OPEN,
                data={
                    "email_id": tracking_data.get("email_id"),
                    # Bare ULID for exact correlation (campaign_recipients
                    # stores it without the @mailyte.local suffix).
                    "message_id": (tracking_data.get("email_id") or "").split("@", 1)[0] or None,
                    "recipient": tracking_data.get("recipient"),
                    # Without this the Laravel intake could not attribute the
                    # event to a tenant (external recipients resolve nothing),
                    # so every tracking event landed with organization_id NULL
                    # -- invisible to email logs, deliverability, and campaign
                    # stats (found live 2026-09-08).
                    "organization_id": tracking_data.get("tenant_id"),
                    "ip_address": request.client.host,
                    "user_agent": request.headers.get("User-Agent", ""),
                },
                domain=tracking_data.get("domain_id"),
                source_service="tracking",
            )
            logger.debug(f"Open event logged for email {tracking_data['email_id']}")

        return _serve_tracking_pixel()

    except Exception as e:
        logger.error(f"Error processing open tracking: {e}")
        return _serve_tracking_pixel()


@tracking_api.get("/click/{tracking_id}")
async def track_click(tracking_id: str, request: Request, url: str = Query(default=None)):
    """
    Handle email click tracking and redirect to original URL.

    This endpoint logs the click event and redirects the user to the
    original destination URL with minimal delay.

    Args:
        tracking_id: Base64-encoded tracking identifier

    Returns:
        Response: HTTP redirect to original URL
    """
    try:
        # Rate limiting check (see track_open's comment -- same bug, same fix)
        if request.app.rate_limiter.is_rate_limited(request.client.host):
            logger.warning(f"Rate limit exceeded for IP {request.client.host}")
            # Still redirect but don't log the event
            if url:
                return RedirectResponse(url=unquote(url))
            return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)

        # Get original URL from query parameters
        original_url = url
        if not original_url:
            logger.warning("Missing original URL in click tracking")
            return JSONResponse({"error": "Missing destination URL"}, status_code=400)

        # Decode and sanitize URL
        try:
            original_url = unquote(original_url)
        except Exception as e:
            logger.warning(f"Failed to decode URL: {e}")
            return JSONResponse({"error": "Invalid URL encoding"}, status_code=400)

        # Decode tracking information
        tracking_data = request.app.tracking_service.decode_tracking_id(tracking_id)
        if not tracking_data:
            logger.warning(f"Invalid tracking ID: {tracking_id}")
            return RedirectResponse(url=original_url)

        # Collect request metadata
        request_info = {
            "timestamp": datetime.utcnow(),
            "ip_address": request.client.host,
            "user_agent": request.headers.get("User-Agent", ""),
            "referer": request.headers.get("Referer", ""),
            "accept_language": request.headers.get("Accept-Language", ""),
            "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
            "x_real_ip": request.headers.get("X-Real-IP", ""),
        }

        # Additional click data
        additional_data = {"original_url": original_url, "query_params": dict(request.query_params)}

        # Log tracking event
        success = request.app.database_service.log_tracking_event(
            "clicked", tracking_data, request_info, additional_data
        )

        if success:
            # Send webhook notification via centralized dispatcher
            dispatch_event(
                Events.TRACKING_CLICK,
                data={
                    "email_id": tracking_data.get("email_id"),
                    "message_id": (tracking_data.get("email_id") or "").split("@", 1)[0] or None,
                    "recipient": tracking_data.get("recipient"),
                    # Tenant attribution -- see the open dispatch above.
                    "organization_id": tracking_data.get("tenant_id"),
                    "url": original_url,
                    "ip_address": request.client.host,
                    "user_agent": request.headers.get("User-Agent", ""),
                },
                domain=tracking_data.get("domain_id"),
                source_service="tracking",
            )
            logger.debug(f"Click event logged for email {tracking_data['email_id']}")

        # Redirect to original URL
        return RedirectResponse(url=original_url)

    except Exception as e:
        logger.error(f"Error processing click tracking: {e}")
        # Try to redirect to original URL even if tracking failed
        if url:
            try:
                return RedirectResponse(url=unquote(url))
            except:
                pass
        return JSONResponse({"error": "Tracking error"}, status_code=500)


@tracking_api.post("/api/tracking/bounce")
async def track_bounce(request: Request):
    """
    Handle bounce tracking from mail server.

    Expected JSON payload:
    {
        "recipient": "user@example.com",
        "bounce_type": "HARD|SOFT|BLOCK",
        "bounce_reason": "Bounce reason text",
        "tracking_info": {...}
    }
    """
    try:
        data = await request.json()
        if not data:
            return JSONResponse({"error": "No JSON data provided"}, status_code=400)

        required_fields = ["recipient", "bounce_type", "bounce_reason"]
        for field in required_fields:
            if field not in data:
                return JSONResponse({"error": f"Missing required field: {field}"}, status_code=400)

        # Extract tracking information if available
        tracking_info = data.get("tracking_info", {})

        # log_bounce_event() doesn't exist on DatabaseService and never has
        # -- every real bounce webhook 500'd before reaching suppression at
        # all. log_tracking_event() is the real method (same one
        # track_complaint uses below); it needs email_id/tenant_id/domain_id
        # in tracking_data and a timestamp in request_info, neither of which
        # bounce_event/tracking_info reliably carries, so both are built
        # with safe fallbacks rather than assumed present.
        tracking_data = {
            "email_id": tracking_info.get("email_id", data["recipient"]),
            "recipient": data["recipient"],
            "tenant_id": tracking_info.get("organization_id", "default"),
            "domain_id": tracking_info.get("domain_id"),
        }
        request_info = {"timestamp": datetime.utcnow()}

        success = request.app.database_service.log_tracking_event(
            "bounced",
            tracking_data,
            request_info,
            additional_data={
                "bounce_type": data["bounce_type"],
                "bounce_reason": data["bounce_reason"],
            },
        )

        if success:
            # Add to suppression list if needed
            request.app.suppression_service.add_bounce_suppression(
                email=data["recipient"],
                bounce_type=data["bounce_type"],
                bounce_reason=data["bounce_reason"],
                organization_id=tracking_info.get("organization_id", "default"),
            )

            # Send webhook notification via centralized dispatcher
            dispatch_event(
                Events.EMAIL_BOUNCED,
                data={
                    "recipient": data["recipient"],
                    "bounce_type": data["bounce_type"],
                    "bounce_reason": data["bounce_reason"],
                    "organization_id": tracking_info.get("organization_id"),
                },
                source_service="tracking",
            )

            logger.info(f"Bounce event logged for {data['recipient']}")
            return JSONResponse({"status": "success"}, status_code=200)
        else:
            return JSONResponse({"error": "Failed to log bounce event"}, status_code=500)

    except Exception as e:
        logger.error(f"Error processing bounce tracking: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@tracking_api.post("/api/tracking/complaint")
async def track_complaint(request: Request):
    """Handle spam complaint tracking."""
    try:
        data = await request.json()
        if not data or "recipient" not in data:
            return JSONResponse({"error": "Missing recipient"}, status_code=400)

        # Same shape requirement as track_bounce above: tracking_data needs
        # email_id/tenant_id/domain_id and request_info needs a timestamp,
        # or _log_tracking_event_sync KeyErrors on the first one it reads
        # (confirmed live: a real complaint webhook 500'd on 'email_id').
        tracking_data = {
            "email_id": data.get("email_id", data["recipient"]),
            "recipient": data["recipient"],
            "tenant_id": data.get("organization_id", "default"),
            "domain_id": data.get("domain_id"),
        }
        request_info = {"timestamp": datetime.utcnow()}

        success = request.app.database_service.log_tracking_event(
            "complained",
            tracking_data,
            request_info,
            additional_data={
                "complaint_type": data.get("complaint_type", "spam"),
                **data.get("additional_data", {}),
            },
        )

        if success:
            # Add to suppression list
            request.app.suppression_service.add_complaint_suppression(
                email=data["recipient"],
                complaint_reason=data.get("complaint_type", "spam"),
                organization_id=data.get("organization_id", "default"),
            )

            # Send webhook notification via centralized dispatcher
            dispatch_event(
                Events.DELIVERY_COMPLAINT,
                data={
                    "recipient": data["recipient"],
                    "complaint_type": data.get("complaint_type", "spam"),
                    "organization_id": data.get("organization_id"),
                },
                source_service="tracking",
            )

            return JSONResponse({"status": "success"}, status_code=200)
        else:
            return JSONResponse({"error": "Failed to log complaint"}, status_code=500)

    except Exception as e:
        logger.error(f"Error processing complaint: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


def _serve_tracking_pixel():
    """
    Serve a 1x1 transparent PNG tracking pixel.

    Returns:
        Response: PNG image response with appropriate headers
    """
    # 1x1 transparent PNG pixel (base64 encoded)
    pixel_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"

    return Response(
        content=pixel_data,
        media_type="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Content-Length": str(len(pixel_data)),
        },
    )
