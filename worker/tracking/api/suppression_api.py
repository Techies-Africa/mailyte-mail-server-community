#!/usr/bin/env python3
"""
Email Tracking Service - Suppression & Unsubscribe API

Direct HTTP access to SuppressionService's add/remove suppression and the
one-click unsubscribe flow. Previously SuppressionService was only ever
reached internally from bounce/complaint auto-suppression inside
tracking_api.py -- there was no route for a caller to add/remove a
suppression directly, or to actually process an unsubscribe link.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)

suppression_api = APIRouter()


@suppression_api.post("/tracking/suppress")
async def add_suppression(request: Request):
    """
    Add an email address to the suppression list.

    Request body:
    {
        "email": "user@example.com",
        "organization_id": "...",
        "reason": "manual bounce/complaint removal, compliance, etc."
    }
    """
    try:
        data = await request.json()
        email = data.get("email")
        if not email:
            return JSONResponse({"error": "Missing email"}, status_code=400)

        # No "default" fallback: organization_id is NOT NULL and part of
        # email_suppressions' unique key, and rows written under a made-up
        # org are never matched by any org-scoped read -- recorded but
        # useless. The gateway (worker/api/routes/tracking.py) always sends
        # the authenticated caller's org; anything that doesn't is a bug at
        # the call site and must hear about it, not be papered over.
        organization_id = data.get("organization_id")
        if not organization_id:
            return JSONResponse({"error": "Missing organization_id"}, status_code=400)
        reason = data.get("reason", "manual")

        success = request.app.suppression_service.add_unsubscribe_suppression(
            email=email, organization_id=organization_id, unsubscribe_method=reason
        )

        if success:
            return JSONResponse({"status": "success", "email": email}, status_code=200)
        return JSONResponse({"error": "Failed to add suppression"}, status_code=500)

    except Exception as e:
        logger.error(f"Error adding suppression: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@suppression_api.delete("/tracking/suppress/{email}")
async def remove_suppression(email: str, request: Request):
    """Remove an email address from the suppression list."""
    try:
        # Same contract as add_suppression above: a remove without an org
        # used to deactivate rows under "default" -- a silent no-op against
        # every real tenant's list.
        organization_id = request.query_params.get("organization_id")
        if not organization_id:
            return JSONResponse({"error": "Missing organization_id"}, status_code=400)
        success = request.app.suppression_service.remove_suppression(email, organization_id)

        if success:
            return JSONResponse({"status": "success", "email": email}, status_code=200)
        return JSONResponse({"error": "Failed to remove suppression"}, status_code=500)

    except Exception as e:
        logger.error(f"Error removing suppression: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


async def _process_unsubscribe(tracking_id: str, request: Request, reason: str) -> JSONResponse:
    """Shared by the GET (List-Unsubscribe header) and POST (RFC 8058
    one-click) handlers below -- same tracking_id decode -> suppress ->
    log flow either way, only the trigger differs."""
    tracking_data = request.app.tracking_service.decode_tracking_id(tracking_id)
    if not tracking_data:
        logger.warning(f"Invalid tracking ID for unsubscribe: {tracking_id}")
        return JSONResponse({"error": "Invalid tracking ID"}, status_code=404)

    email = tracking_data["recipient"]
    organization_id = tracking_data["tenant_id"]

    success = request.app.suppression_service.add_unsubscribe_suppression(
        email=email, organization_id=organization_id, unsubscribe_method=reason
    )

    if not success:
        return JSONResponse({"error": "Failed to process unsubscribe"}, status_code=500)

    request.app.database_service.log_tracking_event(
        "unsubscribed",
        tracking_data,
        {"timestamp": datetime.utcnow()},
        additional_data={"reason": reason},
    )

    dispatch_event(
        Events.TRACKING_UNSUBSCRIBE,
        data={
            "email": email,
            "reason": reason,
            "email_id": tracking_data.get("email_id"),
            "message_id": (tracking_data.get("email_id") or "").split("@", 1)[0] or None,
            # Tenant attribution (see tracking_api open/click dispatches).
            "organization_id": tracking_data.get("tenant_id"),
        },
        domain=tracking_data.get("domain_id"),
        source_service="tracking",
    )

    logger.info(f"Processed unsubscribe for {email}")
    return JSONResponse({"status": "unsubscribed", "email": email}, status_code=200)


@suppression_api.get("/tracking/unsubscribe/{tracking_id}")
async def handle_unsubscribe_get(tracking_id: str, request: Request):
    """One-click unsubscribe via GET -- typically triggered by the
    List-Unsubscribe header in email clients."""
    return await _process_unsubscribe(tracking_id, request, reason="list-unsubscribe-header")


@suppression_api.post("/tracking/unsubscribe/{tracking_id}")
async def handle_unsubscribe_post(tracking_id: str, request: Request):
    """One-click unsubscribe via POST, conforming to RFC 8058."""
    reason = "one-click-post"
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("reason"):
            reason = body["reason"]
    except Exception:
        pass

    return await _process_unsubscribe(tracking_id, request, reason=reason)
