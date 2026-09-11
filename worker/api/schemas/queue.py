"""
Response models for worker/api/routes/queue.py (phase-05 task 5.2,
priority 6 -- "console infrastructure screens").

Same situation as schemas/storage.py: queue.py is a pure proxy
(proxy_to_queue()) that returns `JSONResponse(content=data,
status_code=status)` with the downstream body/status verbatim, never
create_api_response()'s envelope. And the mismatch is the same too -- this
file proxies to paths like `/queue/status`, `/mail-queue/flush`, `/queue/
health`, none of which exist on queue_manager's own app.py (which registers
`/flush`, `/health`, `/api/status`, `/api/statistics` instead). The response
shape on any given call is therefore not knowable from this gateway.
QueueProxyResponse is deliberately loose (extra='allow', no required fields),
mirroring StorageProxyResponse / schemas/mailbox.py's LegacyMailboxItem.

proxy_to_queue()'s own `except` branch returns {"error": "Queue service
unavailable"} at 503 -- not the {type,msg} ErrorResponse shape either, so 503
uses this same loose model.
"""

from pydantic import BaseModel


class QueueProxyResponse(BaseModel):
    """Passthrough body from the queue_manager microservice (200/other) or
    this gateway's own proxy_to_queue() unavailability fallback (503).
    Field-less + extra='allow' on purpose -- see module docstring."""

    class Config:
        extra = "allow"
