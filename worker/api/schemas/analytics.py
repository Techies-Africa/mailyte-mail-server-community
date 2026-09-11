"""
Response models for worker/api/routes/analytics.py (phase-05 task 5.2),
scoped to GET /health (analytics_health), POST /reports/generate
(generate_report), and GET+POST /reports/scheduled (get_scheduled_reports /
create_scheduled_report).

Every target endpoint in this file is a pure proxy to the analytics
microservice (proxy_to_analytics()) -- the handler returns whatever JSON
body and status code that service sent, verbatim, via
JSONResponse(content=data, status_code=status). Returning a Response
object directly means FastAPI does NOT validate/coerce it against
response_model at runtime (that only happens for a plain dict/object
return) -- so response_model here is purely documentation, and the models
below being wrong in some field can't break the endpoint.

Confirmed by reading the actual downstream service (worker/analytics/app.py):
none of `/analytics/health`, `/reports/generate`, `/reports/scheduled` exist
there today -- this proxy targets endpoints that are not yet implemented
downstream. The success shape is therefore genuinely unknowable from this
codebase (not merely undocumented), so AnalyticsProxyResponse is
deliberately permissive (extra='allow') rather than guessing field names --
same pattern as mailbox.py's LegacyMailboxItem. The one shape this file DOES
own locally is the 503 fallback in proxy_to_analytics()'s except branch.
"""

from pydantic import BaseModel


class AnalyticsProxyResponse(BaseModel):
    """Permissive placeholder for whatever the analytics service returns.
    Real shape is owned by the analytics microservice, not this codebase."""

    class Config:
        extra = "allow"


class AnalyticsProxyError(BaseModel):
    """Local fallback body when the analytics service is unreachable
    (proxy_to_analytics()'s except branch) -- returned verbatim at 503."""

    error: str
