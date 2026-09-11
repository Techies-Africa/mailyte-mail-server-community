"""
Shared response envelope models (phase-05 task 5.2).

Every route wraps its payload in create_api_response()'s real shape --
{"type", "msg", "data"} (phase-04's correction to conventions.md §8, not
{"status", "message", "data"} as originally documented). response_model=
must match exactly what the handler returns, so every typed response model
in this package composes with these two rather than redefining the
envelope per resource.
"""

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Shape of every non-2xx JSON body (create_api_response('error', ...))."""

    type: str = "error"
    msg: str
    error_code: str | None = None
    correlation_id: str | None = None


class SimpleMessageResponse(BaseModel):
    """Bare success envelope with no `data` payload -- create_api_response('success', msg)."""

    type: str = "success"
    msg: str


class Pagination(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int
