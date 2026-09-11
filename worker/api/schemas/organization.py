"""
Response models for worker/api/routes/organizations.py (phase-05 task 5.2,
priority 3 -- "zero typed responses today; the console is built on it").

Field lists match Organization.to_dict() (database/models/core.py) exactly.
webhook_secret is never present -- Organization.to_dict() itself omits it,
so there is nothing to strip here (unlike EmailAccount.password, which the
mailbox routes strip explicitly before serialising).
"""

from pydantic import BaseModel

from .common import Pagination


class OrganizationResponse(BaseModel):
    id: str
    external_id: str | None = None
    name: str
    description: str | None = None
    active: bool
    admin_email: str | None = None
    admin_name: str | None = None
    settings: dict | None = None
    rate_limits: dict | None = None
    storage_quotas: dict | None = None
    webhook_urls: list | None = None
    # Console phase-02 SS2.6 decision (a) -- migration 0009. Defaulted so a
    # response built before the migration lands still validates.
    quota_override: bool = False
    quota_override_at: str | None = None
    quota_override_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class OrganizationListItem(OrganizationResponse):
    domain_count: int
    email_account_count: int
    total_storage_used: int
    # phase-02 SS2.1's tenant list shows "storage used / quota" as one
    # column; the denominator (SUM(domains.max_quota)) had no field to
    # travel in, and response_model filtering means a field absent here is
    # silently dropped from the payload however the route builds it.
    storage_quota: int = 0


class OrganizationListData(BaseModel):
    items: list[OrganizationListItem]
    pagination: Pagination


class OrganizationListResponse(BaseModel):
    type: str = "success"
    msg: str
    data: OrganizationListData


class OrganizationStorageStatistics(BaseModel):
    total_storage_used: int
    total_quota: int
    usage_percentage: float


class OrganizationDetailData(OrganizationResponse):
    """GET /{organization_id} and GET /by-external-id/{external_id} -- both
    return org.to_dict() plus the full (loosely typed, see mailbox.py's
    same convention for nested ORM rows) domain list and aggregated stats."""

    domains: list[dict] = []
    domain_count: int
    email_account_count: int
    storage_statistics: OrganizationStorageStatistics


class OrganizationDetailResponse(BaseModel):
    type: str = "success"
    msg: str
    data: OrganizationDetailData


class OrganizationWriteResponse(BaseModel):
    """POST / and PUT /{organization_id} -- return the bare org.to_dict()
    row, no nested domains/stats."""

    type: str = "success"
    msg: str
    data: OrganizationResponse


class OrganizationQuotaDomainSummary(BaseModel):
    domain: str
    storage_used: int
    quota: int
    usage_percentage: float
    email_accounts: int
    max_users: int


class OrganizationQuotaStorageSummary(BaseModel):
    total_storage_used: int
    total_quota: int
    domains: list[OrganizationQuotaDomainSummary]


class OrganizationQuotaData(BaseModel):
    organization_id: str
    organization_quotas: dict
    total_domains: int
    total_email_accounts: int
    storage_summary: OrganizationQuotaStorageSummary
    # The console's Quotas tab has to show whether an operator override is
    # standing before offering an edit -- otherwise staff cannot tell a
    # plan-derived quota from one a colleague set by hand (phase-02 SS2.6).
    quota_override: bool = False
    quota_override_at: str | None = None
    quota_override_by: str | None = None


class OrganizationQuotaResponse(BaseModel):
    type: str = "success"
    msg: str
    data: OrganizationQuotaData


class OrganizationQuotaUpdateData(BaseModel):
    organization_id: str
    storage_quotas: dict | None = None
    rate_limits: dict | None = None
    quota_override: bool = False
    quota_override_at: str | None = None
    quota_override_by: str | None = None


class OrganizationQuotaOverrideClearData(BaseModel):
    organization_id: str
    quota_override: bool = False


class OrganizationQuotaOverrideClearResponse(BaseModel):
    type: str = "success"
    msg: str
    data: OrganizationQuotaOverrideClearData


class OrganizationQuotaUpdateResponse(BaseModel):
    type: str = "success"
    msg: str
    data: OrganizationQuotaUpdateData
