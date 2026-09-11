"""
Response models for worker/api/routes/webhooks.py (phase-05 task 5.2),
scoped to GET /endpoints (list_endpoints), GET /deliveries
(list_deliveries), and GET /dead-letters (list_dead_letters). Every other
endpoint in this file (create/get/update/delete/test endpoint) is out of
scope for this task.

All three target handlers return create_api_response()'s
{"type","msg","data"} envelope as a literal dict (not wrapped in a Response
object) on success, so FastAPI DOES validate/serialize the return value
against these models at runtime -- field types below are taken from the
literal SELECT column lists in webhooks.py and cross-checked against the
real DB schema (alembic/versions/0001_baseline.py), not guessed:
  - webhook_urls.id and webhook_delivery_logs.id are CHAR(26) ULIDs (str)
    -- both tables went through the ULID primary-key conversion
    (database/migrations/sql/009_ulid_safe.sql).
  - webhook_dead_letters.id is still BIGINT AUTO_INCREMENT (int) -- that
    table was NOT included in the ULID conversion, unlike the other two.
  - webhook_urls.active is TINYINT(1) (bool).
"""

from typing import Any

from pydantic import BaseModel

from .common import Pagination


class WebhookEndpointItem(BaseModel):
    id: str
    organization_id: str | None = None
    url: str
    description: str | None = None
    active: bool
    event_types: Any | None = None
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        extra = "allow"


class WebhookEndpointListData(BaseModel):
    items: list[WebhookEndpointItem]
    pagination: Pagination


class WebhookEndpointListResponse(BaseModel):
    type: str = "success"
    msg: str
    data: WebhookEndpointListData


class WebhookDeliveryItem(BaseModel):
    id: str
    event_type: str | None = None
    webhook_url: str | None = None
    delivery_status: str | None = None
    http_status_code: int | None = None
    attempts: int | None = None
    error_message: str | None = None
    created_at: str | None = None
    delivered_at: str | None = None

    class Config:
        extra = "allow"


class WebhookDeliveryListData(BaseModel):
    items: list[WebhookDeliveryItem]
    pagination: Pagination


class WebhookDeliveryListResponse(BaseModel):
    type: str = "success"
    msg: str
    data: WebhookDeliveryListData


class WebhookDeadLetterItem(BaseModel):
    """Corrected 2026-08-20 (console PRD SS12 gap #8b): the three fields
    below were previously declared as webhook_url / error_message /
    retry_count, copied from webhook_delivery_logs rather than read off
    webhook_dead_letters. That table has never had those columns -- its real
    ones are endpoint_url / last_error / attempt_count (0001_baseline) --
    so list_dead_letters' SELECT errored before this model was ever reached.
    `status` is the ('pending','retrying','resolved','abandoned') enum the
    replay endpoints transition."""

    id: int
    organization_id: str | None = None
    event_type: str | None = None
    endpoint_url: str | None = None
    last_error: str | None = None
    attempt_count: int | None = None
    status: str | None = None
    created_at: str | None = None
    last_attempted_at: str | None = None
    resolved_at: str | None = None

    class Config:
        extra = "allow"


class WebhookDeadLetterListData(BaseModel):
    items: list[WebhookDeadLetterItem]
    pagination: Pagination


class WebhookDeadLetterListResponse(BaseModel):
    type: str = "success"
    msg: str
    data: WebhookDeadLetterListData
