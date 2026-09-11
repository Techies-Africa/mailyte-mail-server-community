"""
Response models for worker/api/routes/domains.py (phase-05 task 5.2,
priority 2 -- "DNS screens (04-mailyte-web/phase-02 SS2.1)").

Field lists match Domain.to_dict() (database/models/core.py) exactly.
Several endpoints (verify-dns, quotas, policy, stats) build custom dicts in
the route body rather than returning to_dict() directly -- those response
models were built by reading each handler's actual SELECT/response body,
not by mirroring the ORM.
"""

from pydantic import BaseModel

from .common import Pagination


class DomainResponse(BaseModel):
    id: str
    domain: str
    external_id: str | None = None
    organization_id: str
    description: str | None = None
    active: bool
    max_quota: int
    max_users: int
    dkim_enabled: bool
    dkim_selector: str
    rate_limits: dict | None = None
    storage_quotas: dict | None = None
    total_storage_used: int
    total_attachment_storage: int
    total_email_storage: int
    total_email_accounts: int
    total_emails: int
    total_attachments: int
    rate_usage_data: dict | None = None
    last_storage_calculation: str | None = None
    storage_calculation_time_ms: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DomainListItem(DomainResponse):
    email_account_count: int
    organization_name: str


class DomainListData(BaseModel):
    items: list[DomainListItem]
    pagination: Pagination


class DomainListResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainListData


class DomainUsageStatistics(BaseModel):
    account_usage_percentage: float
    storage_usage_percentage: float
    total_account_storage: int


class DomainDNSRecordItem(BaseModel):
    """Mirrors the inline DNSRecord doc model already defined in
    routes/domains.py (kept as a separate definition here to avoid a
    routes -> schemas -> routes import cycle).

    Declared above DomainDetailData rather than below it purely so that model
    can reference it without a forward declaration."""

    type: str
    name: str
    value: str
    priority: int | None = None
    description: str


class DomainDetailData(DomainResponse):
    """GET /{domain_id} -- to_dict() plus the nested organization row, the
    full email account list (loosely typed as dict, mirroring mailbox.py's
    convention for nested ORM rows), computed usage percentages, and the
    domain's active DKIM record.

    The DKIM fields mirror DomainCreateData's. They must be declared here or
    FastAPI's response_model filtering silently drops them from the payload,
    which is what made every consumer that fetched a domain after creation
    see dkim_record: null and report DKIM as "not configured".

    `dns_records` and `dkim_selector` are here for exactly the same reason,
    and were missed the first time round. The handler set them, FastAPI
    filtered them straight back out, and the consumer stored the values it got
    at creation instead -- which is how a container ID ended up being served
    to customers as their MX host months after the hostname itself was fixed.
    Anything the handler adds to domain_data must be declared here or it does
    not survive serialisation."""

    organization: dict | None = None
    email_accounts: list[dict] = []
    email_account_count: int
    usage_statistics: DomainUsageStatistics
    dkim_record: str | None = None
    dkim_dns_name: str | None = None
    dkim_selector: str | None = None
    dns_records: list[DomainDNSRecordItem] = []


class DomainDetailResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainDetailData


class DomainCreateData(DomainResponse):
    """POST / -- to_dict() plus the DKIM record (only set when DKIM
    generation succeeded -- `if dkim_record:` in the handler) and the DNS
    records the customer must configure (always present)."""

    dkim_record: str | None = None
    dkim_dns_name: str | None = None
    dns_records: list[DomainDNSRecordItem]


class DomainCreateResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainCreateData


class DomainWriteResponse(BaseModel):
    """PUT /{domain_id} -- bare to_dict() row, no nested data."""

    type: str = "success"
    msg: str
    data: DomainResponse


class DNSCheckResult(BaseModel):
    """Shape varies per record type (mx/spf/dkim/dmarc each populate a
    different subset of these keys) -- all fields optional except status,
    read directly off verify_domain_dns()'s handler body."""

    status: str
    expected: str | None = None
    actual: str | None = None
    found: bool | None = None
    policy: str | None = None
    message: str | None = None


class DomainDNSVerification(BaseModel):
    mx: DNSCheckResult
    spf: DNSCheckResult
    dkim: DNSCheckResult
    dmarc: DNSCheckResult


class DomainVerifyDNSData(BaseModel):
    domain: str
    verification: DomainDNSVerification


class DomainVerifyDNSResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainVerifyDNSData


class DomainQuotaEmailAccountSummary(BaseModel):
    id: str
    email: str
    storage_quota: int
    storage_used: int
    usage_percentage: float


class DomainQuotaData(BaseModel):
    domain_id: str
    domain: str
    max_quota: int
    max_users: int
    storage_used: int
    usage_percentage: float
    account_usage_percentage: float
    rate_limits: dict
    storage_quotas: dict
    email_accounts: list[DomainQuotaEmailAccountSummary]


class DomainQuotaResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainQuotaData


class DomainQuotaUpdateData(BaseModel):
    domain_id: str
    max_quota: int
    max_users: int
    rate_limits: dict | None = None
    storage_quotas: dict | None = None


class DomainQuotaUpdateResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainQuotaUpdateData


class LegacyDomainResultItem(BaseModel):
    """POST /edit (legacy)."""

    domain: str
    status: str
    msg: str


class DomainEditResponse(BaseModel):
    type: str = "success"
    msg: str
    data: list[LegacyDomainResultItem]


class LegacyDomainDeleteStats(BaseModel):
    mailboxes_deleted: int
    aliases_deleted: int


class LegacyDomainDeleteResultItem(BaseModel):
    """POST /delete/domain (legacy) -- stats only present on success."""

    domain: str
    status: str
    msg: str
    stats: LegacyDomainDeleteStats | None = None


class DomainDeleteResponse(BaseModel):
    type: str = "success"
    msg: str
    data: list[LegacyDomainDeleteResultItem]


class DomainPolicyData(BaseModel):
    """GET /get/domain/policy/{domain} -- raw SQL row from a LEFT JOIN
    against domain_policy, so the policy_* columns are None when no policy
    row exists yet for the domain. rl_value/rl_frame are legacy
    mailcow-compatible columns that live on the `domains` table but are not
    mapped by the Domain ORM model (see Domain.to_dict(), which omits
    them)."""

    domain: str
    rl_value: int | None = None
    rl_frame: int | None = None
    active: bool | None = None
    policy_bl_only: int | None = None
    policy_reject_spam: int | None = None
    policy_greylist: int | None = None
    policy_rbl: int | None = None


class DomainPolicyResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainPolicyData


class DomainStatsData(BaseModel):
    """GET /stats/{domain}. domain_details is `SELECT * FROM domains`, a raw
    row not guaranteed to match Domain.to_dict()'s column set (the same
    legacy mailcow-compatible columns noted on DomainPolicyData live in this
    table) -- left as a permissive dict rather than a typed model, matching
    mailbox.py's LegacyMailboxItem precedent for raw `SELECT *` results."""

    domain: str
    mailbox_count: int
    alias_count: int
    total_quota_used: int
    domain_details: dict


class DomainStatsResponse(BaseModel):
    type: str = "success"
    msg: str
    data: DomainStatsData
