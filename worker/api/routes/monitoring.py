#!/usr/bin/env python3
"""
Monitoring API Routes

Service health, per-service status, system metrics, dashboard stats, restart,
and auto-heal -- the Infrastructure section of the Mailyte Console (ADR-004).

This module is a thin, authenticated proxy over a monitoring service. It owns
no state of its own; it exists so the console has one authenticated surface
instead of needing network reach to internal containers.

CE PACKAGING NOTE -- read before filing a bug about a 503 here. This repo's
docker-compose.yml ships no monitoring container (mailyte-email-server has
one; CE does not). Every route below therefore returns 503 with an explicit
message unless MONITORING_SERVICE_URL is set to something that exists. That
check runs FIRST, before the admin-token check on the mutating routes, so an
operator gets "there is no monitoring service configured" rather than a
misleading "admin token required". There is deliberately no default URL: the
value mailyte-email-server defaults to (http://0.0.0.0:8080) is this API's
own bind port inside the container, so a default would make /health proxy to
itself and report nonsense instead of failing honestly.

Scope: every route here is scope='platform' -- a tenant credential can never
reach it regardless of its own permission flags, because none of these routes
have an organization concept at all (they describe infrastructure, not
tenants). Reads require role='support'; the destructive POSTs require
role='operator', because ADR-002 §4 makes "restart services" an operator
capability and explicitly denies it to support.
"""

import os
from datetime import datetime

import requests

# requests is blocking. In an `async def` handler it runs ON the event loop and
# stalls every other request this worker is serving. Measured on
# mailyte-email-server before the same fix: 10.3s for a call the monitoring
# service answered in 0.18s. Latent here only because CE ships no monitoring
# container -- but docker-compose.yml invites operators to set
# MONITORING_SERVICE_URL, and the moment they do, this bites.
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from utils.auth import require_api_key

router = APIRouter()

# No default -- see the CE packaging note above.
MONITORING_SERVICE_URL = os.getenv("MONITORING_SERVICE_URL", "").strip()

_NO_SERVICE_MESSAGE = (
    "No monitoring service is configured on this deployment. The Community Edition "
    "compose file ships no monitoring container; set MONITORING_SERVICE_URL on the api "
    "service to point at one to enable these endpoints."
)


def _unavailable() -> JSONResponse:
    """503, not 501: the endpoint exists and its contract is real -- there is
    simply nothing behind it on this deployment. The console renders an
    'unavailable' state from this rather than treating it as a bug."""
    return JSONResponse(
        content={
            "type": "error",
            "msg": _NO_SERVICE_MESSAGE,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
        status_code=503,
    )


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class ServiceRestartRequest(BaseModel):
    """Request body for restarting a monitored service."""

    force: bool | None = Field(
        False,
        description="If true, force-kill the service before restarting instead of a "
        "graceful restart",
    )
    reason: str | None = Field(
        None, description="Human-readable reason for the restart, recorded in the audit log"
    )


class AutoHealRequest(BaseModel):
    """Request body for triggering auto-heal on all services."""

    services: list | None = Field(
        None,
        description="Limit auto-heal to specific services by name. Omit to heal all "
        "unhealthy services.",
    )
    dry_run: bool | None = Field(
        False, description="If true, report which services would be healed without taking action"
    )


class WebhookTestRequest(BaseModel):
    """Request body for testing configured webhook endpoints."""

    webhook_urls: list | None = Field(
        None, description="Specific webhook URLs to test. Omit to test all configured webhooks."
    )
    payload_type: str | None = Field(
        "ping", description="Type of test payload to send: 'ping', 'alert', or 'recovery'"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    summary="System health check",
    description="Returns the overall health of all email server components including Postfix, "
    "Dovecot, Rspamd, MySQL, and Redis. Any component failure is reported with error "
    "details. NOT the container healthcheck -- that is the unauthenticated GET /health "
    "in app.py. 503 when this deployment has no monitoring service.",
)
@require_api_key("read", scope="platform", role="support")
async def health_check():
    """Get overall monitoring service health status."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()
    try:
        response = await run_in_threadpool(
            lambda: requests.get(f"{MONITORING_SERVICE_URL}/health", timeout=10)
        )
        if response.status_code == 200:
            return {
                "status": "healthy",
                "monitoring_service": response.json(),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        return JSONResponse(
            content={
                "status": "unhealthy",
                "error": f"Monitoring service returned {response.status_code}",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )
    except Exception as e:
        return JSONResponse(
            content={
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )


@router.get(
    "/services",
    summary="Get all services status",
    description="Returns the current status of every monitored service including Postfix, "
    "Dovecot, Rspamd, MySQL, and Redis. Each service entry includes uptime, last "
    "heartbeat, and any active alerts.",
)
@require_api_key("read", scope="platform", role="support")
async def get_services_status():
    """Get status of all monitored services."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()
    try:
        response = await run_in_threadpool(
            lambda: requests.get(f"{MONITORING_SERVICE_URL}/heartbeat", timeout=15)
        )
        if response.status_code == 200:
            return response.json()
        return JSONResponse(
            content={
                "error": "Failed to get services status",
                "status_code": response.status_code,
            },
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.utcnow().isoformat() + "Z"},
            status_code=503,
        )


@router.get(
    "/services/{service_name}",
    summary="Get single service status",
    description="Returns the health and performance details for a specific monitored service. "
    "Includes process status, resource consumption, last heartbeat time, and recent "
    "error history.",
)
@require_api_key("read", scope="platform", role="support")
async def get_service_status(service_name: str):
    """Get status of a specific service."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()
    try:
        response = await run_in_threadpool(lambda: requests.get(
            f"{MONITORING_SERVICE_URL}/heartbeat", params={"service": service_name}, timeout=10
        ))
        if response.status_code == 200:
            return response.json()
        return JSONResponse(
            content={
                "error": f"Failed to get status for service {service_name}",
                "status_code": response.status_code,
            },
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={
                "error": str(e),
                "service": service_name,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )


@router.get(
    "/metrics",
    summary="Get system metrics",
    description="Returns current system and mail server metrics including CPU, memory, disk "
    "usage, mail throughput rates, queue depths, and connection counts. Suitable for "
    "feeding into external monitoring dashboards.",
)
@require_api_key("read", scope="platform", role="support")
async def get_system_metrics():
    """Get current system and mail server metrics."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()
    try:
        response = await run_in_threadpool(
            lambda: requests.get(f"{MONITORING_SERVICE_URL}/api/metrics", timeout=15)
        )
        if response.status_code == 200:
            return response.json()
        return JSONResponse(
            content={"error": "Failed to get system metrics", "status_code": response.status_code},
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.utcnow().isoformat() + "Z"},
            status_code=503,
        )


@router.get(
    "/stats",
    summary="Get dashboard statistics",
    description="Returns pre-aggregated statistics optimised for monitoring dashboards. Includes "
    "mail volume trends, delivery success rates, spam ratios, and storage utilisation "
    "summaries over configurable time windows.",
)
@require_api_key("read", scope="platform", role="support")
async def get_dashboard_stats():
    """Get dashboard statistics for monitoring display."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()
    try:
        response = await run_in_threadpool(
            lambda: requests.get(f"{MONITORING_SERVICE_URL}/api/stats", timeout=10)
        )
        if response.status_code == 200:
            return response.json()
        return JSONResponse(
            content={"error": "Failed to get dashboard stats", "status_code": response.status_code},
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.utcnow().isoformat() + "Z"},
            status_code=503,
        )


@router.post(
    "/services/{service_name}/restart",
    summary="Restart a service",
    description="Initiates a restart of the specified email server component. Requires an "
    "operator-role platform credential, plus the downstream monitoring service's own "
    "X-Admin-Token, which is forwarded unchanged. Supports graceful and forced restart "
    "modes. The operation is written to operator_audit by middleware.",
)
@require_api_key("admin", scope="platform", role="operator")
async def restart_service(
    service_name: str, request: Request, body: ServiceRestartRequest | None = None
):
    """Restart a specific service."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()

    # Forwarded, not validated here: this gateway has already authenticated
    # and role-checked the caller; the header exists because the downstream
    # monitoring service performs its own independent check.
    admin_token = request.headers.get("X-Admin-Token")
    if not admin_token:
        return JSONResponse(
            content={
                "error": "Admin token required for service restart",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=401,
        )

    try:
        headers = {"X-Admin-Token": admin_token}
        response = await run_in_threadpool(lambda: requests.post(
            f"{MONITORING_SERVICE_URL}/restart/{service_name}", headers=headers, timeout=30
        ))

        if response.status_code == 200:
            return response.json()
        if response.status_code == 401:
            return JSONResponse(
                content={
                    "error": "Invalid admin token",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
                status_code=401,
            )
        return JSONResponse(
            content={
                "error": f"Failed to restart service {service_name}",
                "status_code": response.status_code,
                "response": response.text,
            },
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={
                "error": str(e),
                "service": service_name,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )


@router.post(
    "/auto-heal",
    summary="Trigger auto-heal for all services",
    description="Runs the auto-healing procedure across all monitored services. Detects unhealthy "
    "components and attempts automatic recovery (restart, config reload, cache flush). "
    "Requires an operator-role platform credential plus the downstream service's "
    "X-Admin-Token. Supports dry-run mode.",
)
@require_api_key("admin", scope="platform", role="operator")
async def trigger_auto_heal(request: Request, body: AutoHealRequest | None = None):
    """Trigger auto-healing for all services."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()

    admin_token = request.headers.get("X-Admin-Token")
    if not admin_token:
        return JSONResponse(
            content={
                "error": "Admin token required for auto-heal operation",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=401,
        )

    try:
        headers = {"X-Admin-Token": admin_token}
        response = await run_in_threadpool(
            lambda: requests.post(
                f"{MONITORING_SERVICE_URL}/auto-heal", headers=headers, timeout=60
            )
        )

        if response.status_code == 200:
            return response.json()
        if response.status_code == 401:
            return JSONResponse(
                content={
                    "error": "Invalid admin token",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
                status_code=401,
            )
        return JSONResponse(
            content={
                "error": "Failed to trigger auto-heal",
                "status_code": response.status_code,
                "response": response.text,
            },
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.utcnow().isoformat() + "Z"},
            status_code=503,
        )


@router.post(
    "/webhooks/test",
    summary="Test webhook endpoints",
    description="Sends a test payload to all configured webhook endpoints (or a specified subset) "
    "to verify connectivity and correct response handling. Requires an operator-role "
    "platform credential plus the downstream service's X-Admin-Token. Returns per-webhook "
    "delivery status and response times.",
)
@require_api_key("admin", scope="platform", role="operator")
async def test_webhooks(request: Request, body: WebhookTestRequest | None = None):
    """Test all configured webhook endpoints."""
    if not MONITORING_SERVICE_URL:
        return _unavailable()

    admin_token = request.headers.get("X-Admin-Token")
    if not admin_token:
        return JSONResponse(
            content={
                "error": "Admin token required for webhook testing",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=401,
        )

    try:
        headers = {"X-Admin-Token": admin_token}
        response = await run_in_threadpool(lambda: requests.post(
            f"{MONITORING_SERVICE_URL}/test/webhooks", headers=headers, timeout=30
        ))

        if response.status_code == 200:
            return response.json()
        if response.status_code == 401:
            return JSONResponse(
                content={
                    "error": "Invalid admin token",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
                status_code=401,
            )
        return JSONResponse(
            content={
                "error": "Failed to test webhooks",
                "status_code": response.status_code,
                "response": response.text,
            },
            status_code=response.status_code,
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.utcnow().isoformat() + "Z"},
            status_code=503,
        )
