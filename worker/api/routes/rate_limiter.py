#!/usr/bin/env python3
"""
Rate Limiter Module API Routes
Exposes controlled rate limiting functionality through the API gateway
"""

import logging
import os

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

RATE_LIMITER_API_BASE = f"http://0.0.0.0:{os.getenv('RATE_LIMITER_API_PORT', 5003)}"


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


@router.get("/rate-limits/domain/{domain}")
@require_api_key("read")
async def get_domain_limits(domain: str, request: Request):
    """Get rate limits for a domain"""
    data, status = await proxy_to_rate_limiter(
        request, f"/rate-limits/domain/{domain}", method="GET"
    )
    return JSONResponse(content=data, status_code=status)


@router.post("/rate-limits/domain/{domain}")
@require_api_key("write")
async def set_domain_limits(domain: str, request: Request):
    """Set rate limits for a domain"""
    data, status = await proxy_to_rate_limiter(
        request, f"/rate-limits/domain/{domain}", method="POST", data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)


@router.get("/rate-limits/mailbox/{email}")
@require_api_key("read")
async def get_mailbox_limits(email: str, request: Request):
    """Get rate limits for a mailbox"""
    data, status = await proxy_to_rate_limiter(
        request, f"/rate-limits/mailbox/{email}", method="GET"
    )
    return JSONResponse(content=data, status_code=status)


@router.post("/rate-limits/mailbox/{email}")
@require_api_key("write")
async def set_mailbox_limits(email: str, request: Request):
    """Set rate limits for a mailbox"""
    data, status = await proxy_to_rate_limiter(
        request, f"/rate-limits/mailbox/{email}", method="POST", data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)


@router.get("/rate-limits/usage/{domain}")
@require_api_key("read")
async def get_domain_usage(domain: str, request: Request):
    """Get current usage statistics for a domain"""
    data, status = await proxy_to_rate_limiter(
        request, f"/rate-limits/usage/{domain}", method="GET", params=dict(request.query_params)
    )
    return JSONResponse(content=data, status_code=status)


@router.post("/rate-limits/reset")
@require_api_key("write")
async def reset_limits(request: Request):
    """Reset rate limit counters"""
    data, status = await proxy_to_rate_limiter(
        request, "/rate-limits/reset", method="POST", data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)


@router.get("/quotas/domain/{domain}")
@require_api_key("read")
async def get_domain_quotas(domain: str, request: Request):
    """Get daily/monthly quotas for a domain"""
    data, status = await proxy_to_rate_limiter(request, f"/quotas/domain/{domain}", method="GET")
    return JSONResponse(content=data, status_code=status)


@router.post("/quotas/domain/{domain}")
@require_api_key("write")
async def set_domain_quotas(domain: str, request: Request):
    """Set daily/monthly quotas for a domain"""
    data, status = await proxy_to_rate_limiter(
        request, f"/quotas/domain/{domain}", method="POST", data=await request.json()
    )
    return JSONResponse(content=data, status_code=status)
