"""
Response model for worker/api/routes/tracking.py's POST /suppress
(add_suppression) -- phase-05 task 5.2. Every other endpoint in this file
is out of scope for this task.

add_suppression is a pure proxy to the tracking microservice
(proxy_to_tracking()) -- the handler returns whatever JSON body and status
code that service sent, verbatim, via
JSONResponse(content=data, status_code=status), bypassing
create_api_response()/app.py's exception handlers entirely (a Response
object returned directly skips response_model validation, so this is
documentation-only). Downstream success shape is not knowable from this
codebase -- TrackingProxyResponse is deliberately permissive
(extra='allow'). The one shape this file DOES own locally is the 503
fallback in proxy_to_tracking()'s except branch.
"""

from pydantic import BaseModel


class TrackingProxyResponse(BaseModel):
    class Config:
        extra = "allow"


class TrackingProxyError(BaseModel):
    error: str
