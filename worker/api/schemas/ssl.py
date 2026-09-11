"""
Response model for worker/api/routes/ssl.py's `GET /accounts` (phase-05
task 5.2 -- the one endpoint from this file in the "consumed set",
plans/01-mailyte-email-server/consumed-endpoints.md).

Unlike storage.py/queue.py/rate_limiter.py, ssl.py is NOT a proxy --
list_acme_accounts() queries `acme_account_stats` directly and returns
create_api_response('success', ..., {'accounts': [...], 'total': N}), so the
shape is fully known and typed precisely here (no loose/extra='allow'
model needed).
"""

from pydantic import BaseModel


class AcmeAccountItem(BaseModel):
    id: str
    email: str
    certs_issued: int
    success_count: int
    failure_count: int
    success_rate: float | None = None
    last_used: str | None = None
    registered_at: str | None = None


class AcmeAccountsData(BaseModel):
    accounts: list[AcmeAccountItem]
    total: int


class AcmeAccountsResponse(BaseModel):
    type: str = "success"
    msg: str
    data: AcmeAccountsData
