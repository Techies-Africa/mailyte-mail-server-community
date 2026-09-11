"""
Response models for worker/api/routes/monitoring.py (phase-05 task 5.2),
scoped to GET /health, GET /services, GET /metrics, GET /stats,
POST /auto-heal, and POST /webhooks/test. GET /services/{service_name} is
out of scope for this task.

Every route in this file talks to the internal monitoring service via
`requests` and, on success, returns its JSON largely as-is (`return
response.json()`, or a thin dict wrapper like `{'status': 'healthy',
'monitoring_service': response.json(), 'timestamp': ...}`) as a PLAIN
DICT -- unlike the proxy files (analytics.py/rag.py/tracking.py), these
success paths are NOT wrapped in a Response object, so FastAPI DOES
validate/serialize them against response_model at runtime. Verified live
against the actual downstream implementation (worker/monitoring/app.py):
every one of /health, /heartbeat, /api/metrics, /api/stats, /auto-heal,
/test/webhooks returns a JSON *object* (dict) at the top level, never a
bare array -- so a BaseModel response_model is safe here, but the exact
key set is dynamic enough (varies per monitored service) that the models
below stay permissive (extra='allow') rather than enumerating every field.

Failure branches in this file return JSONResponse(...) directly (bypasses
create_api_response() and app.py's exception handlers, and skips
response_model validation same as the proxy files). Several of those
forward the downstream service's own response.status_code verbatim
(`status_code=response.status_code`) -- that value is only known at
request time, not at spec-authoring time, so it is not enumerable here;
only the codes this file's own logic produces literally (401 missing/
invalid admin token on the 3 POST routes, 500 on a local exception, 503 for
/health's "downstream returned non-200") are documented in the route
decorators.
"""

from pydantic import BaseModel


class MonitoringProxyResponse(BaseModel):
    """Permissive placeholder -- pass-through of the internal monitoring
    service's own JSON (confirmed dict-shaped), or this file's own small
    wrapper dict around it."""

    class Config:
        extra = "allow"


class MonitoringErrorResponse(BaseModel):
    """This file's own ad-hoc error shape -- NOT the {"type","msg","data"}
    envelope (these routes return JSONResponse directly, bypassing
    create_api_response and app.py's exception handlers)."""

    error: str
    timestamp: str | None = None

    class Config:
        extra = "allow"
