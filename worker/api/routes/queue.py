#!/usr/bin/env python3
"""
Queue API Routes

Postfix mail queue status, deferred listing, and flush -- the Mail Flow >
Queue screen of the Mailyte Console (ADR-004). A thin, authenticated proxy
over a queue-manager service; it owns no state.

CE PACKAGING NOTE -- read before filing a bug about a 503 here. This repo's
docker-compose.yml ships no queue-manager container (mailyte-email-server has
one; CE does not). Every route below returns 503 with an explicit message
unless QUEUE_SERVICE_URL names something that exists. There is deliberately
no default: mailyte-email-server's default (http://0.0.0.0:5006) resolves to
the API container's own loopback, so a default here would produce a confusing
connection-refused rather than an honest "nothing is configured".

Note this is the *Postfix-side* queue, a different and later stage of the
pipeline than the `mail_queue` database table that GET
/api/v1/platform/overview counts. The two are not expected to agree, and
neither is wrong when they don't.

Scope: every route is scope='platform'. Reads require role='support'
(ADR-002 §4 gives support "view queue"); flushing requires role='operator'
("flush queue" is explicitly an operator capability, not a support one).
"""

import logging
import os

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

# No default -- see the CE packaging note above.
QUEUE_SERVICE_URL = os.getenv("QUEUE_SERVICE_URL", "").strip()

_NO_SERVICE_MESSAGE = (
    "No queue service is configured on this deployment. The Community Edition compose "
    "file ships no queue-manager container; set QUEUE_SERVICE_URL on the api service to "
    "point at one to enable these endpoints."
)


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class FlushQueueRequest(BaseModel):
    """Request body for flushing the mail queue."""

    domain: str | None = Field(
        None, description="Limit flush to a specific domain. Omit to flush the entire queue."
    )
    queue_name: str | None = Field(
        None, description="Target queue to flush: 'deferred', 'hold', or 'all' (default: 'all')"
    )
    older_than_minutes: int | None = Field(
        None, description="Only flush messages that have been queued longer than this many minutes"
    )
    recipient: str | None = Field(
        None, description="Only flush messages addressed to this specific recipient"
    )
    force: bool | None = Field(
        False, description="If true, force immediate delivery attempt for all matching messages"
    )


# ---------------------------------------------------------------------------
# Proxy Helper
# ---------------------------------------------------------------------------


async def proxy_to_queue(request: Request, endpoint, method="GET", data=None, params=None):
    """Proxy a request to the queue service.

    Returns (payload, status). 503 both when nothing is configured and when
    what is configured cannot be reached -- the caller cannot act differently
    on those two, and the message distinguishes them.
    """
    if not QUEUE_SERVICE_URL:
        return {"type": "error", "msg": _NO_SERVICE_MESSAGE}, 503
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
                url=f"{QUEUE_SERVICE_URL}{endpoint}",
                headers=headers,
                json=data,
                params=params,
                # Bounded so a wedged queue service degrades this screen
                # rather than holding a worker open indefinitely.
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response,
        ):
            response_data = await response.json()
            return response_data, response.status
    except Exception as e:
        logger.error(f"Queue service proxy error: {e}")
        return {"type": "error", "msg": "Queue service unavailable"}, 503


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/queue/status",
    summary="Get mail queue status",
    description="Returns the current state of the Postfix mail queue including pending, deferred, "
    "and held message counts. 503 when this deployment has no queue service.",
)
@require_api_key("read", scope="platform", role="support")
async def get_queue_status(request: Request):
    """Get overall queue status"""
    data, status = await proxy_to_queue(request, "/queue/status", method="GET")
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/queue/domain/{domain}",
    summary="Get domain queue status",
    description="Returns queue statistics scoped to a single domain, including counts of pending, "
    "deferred, and held messages and the oldest queued item age.",
)
@require_api_key("read", scope="platform", role="support")
async def get_domain_queue(domain: str, request: Request):
    """Get queue status for a specific domain"""
    data, status = await proxy_to_queue(
        request, f"/queue/domain/{domain}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/mail-queue/deferred",
    summary="Get deferred mail queue",
    description="Lists all messages currently in the Postfix deferred queue. Each entry includes "
    "the queue ID, sender, recipient, deferral reason, and time since first attempt.",
)
@require_api_key("read", scope="platform", role="support")
async def get_deferred_mail(request: Request):
    """Get deferred mail queue"""
    data, status = await proxy_to_queue(
        request, "/mail-queue/deferred", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.post(
    "/mail-queue/flush",
    summary="Flush mail queue",
    description="Triggers an immediate delivery attempt for queued messages. Can be scoped by "
    "domain, queue name, recipient, or message age. Use the force flag to bypass backoff "
    "timers on deferred messages. Operator role required.",
)
@require_api_key("write", scope="platform", role="operator")
async def flush_mail_queue(request: Request, body: FlushQueueRequest):
    """Flush mail queue"""
    data, status = await proxy_to_queue(
        request, "/mail-queue/flush", method="POST", data=body.model_dump(exclude_none=True)
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/jobs/{domain}",
    summary="Get domain queue jobs",
    description="Returns a detailed list of individual queued messages for the specified domain, "
    "including queue IDs, senders, recipients, statuses, and timestamps.",
)
@require_api_key("read", scope="platform", role="support")
async def get_domain_queue_jobs(domain: str, request: Request):
    """Get queue jobs for a domain"""
    data, status = await proxy_to_queue(
        request, f"/queue/jobs/{domain}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.get(
    "/health",
    summary="Queue health check",
    description="Returns the health status of the queue processing subsystem, including whether "
    "Postfix is responsive, current queue depths, and processing latency.",
)
@require_api_key("read", scope="platform", role="support")
async def queue_health(request: Request):
    data, status = await proxy_to_queue(request, "/queue/health", method="GET")
    return JSONResponse(content=data, status_code=status)
