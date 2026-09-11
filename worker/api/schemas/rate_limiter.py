"""
Response model for worker/api/routes/rate_limiter.py's `POST /rate-limits/
reset` (phase-05 task 5.2 -- the one endpoint from this file in the
"consumed set", plans/01-mailyte-email-server/consumed-endpoints.md).

Same pure-proxy situation as schemas/storage.py and schemas/queue.py:
reset_limits() calls proxy_to_rate_limiter() and returns
`JSONResponse(content=data, status_code=status)` with the downstream body/
status verbatim -- never create_api_response()'s envelope. And the same path
mismatch too: this file proxies to `/rate-limits/reset`, which does not
exist on rate_limiter's own app.py (registered routes are `/check_rate_limit`,
`/increment_usage`, `/get_usage/{entity_type}/{identifier}`, `/set_limits`,
`/policy`, `/health`, `/stats`). The response shape is therefore not
knowable from this gateway. RateLimiterProxyResponse is deliberately loose
(extra='allow', no required fields), mirroring StorageProxyResponse /
QueueProxyResponse / schemas/mailbox.py's LegacyMailboxItem.

proxy_to_rate_limiter()'s own `except` branch returns {"error": "Rate
limiter service unavailable"} at 503 -- also not the {type,msg}
ErrorResponse shape, so 503 uses this same loose model.
"""

from pydantic import BaseModel


class RateLimiterProxyResponse(BaseModel):
    """Passthrough body from the rate_limiter microservice (200/other) or
    this gateway's own proxy_to_rate_limiter() unavailability fallback
    (503). Field-less + extra='allow' on purpose -- see module docstring."""

    class Config:
        extra = "allow"
