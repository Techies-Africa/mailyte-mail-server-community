"""
Response models for worker/api/routes/mailboxes.py (phase-05 task 5.2,
priority 1 -- "most-used resource; Laravel + both UIs depend on it").

Field lists match EmailAccount.to_dict() (database/models/core.py) exactly,
minus `password` (always stripped before serialisation in every route).
"""

from pydantic import BaseModel

from .common import Pagination


class MailboxResponse(BaseModel):
    id: str
    email: str
    external_id: str | None = None
    local_part: str
    domain_id: str
    organization_id: str
    name: str | None = None
    status: str
    storage_quota: int
    storage_used: int
    attachment_storage_used: int
    email_storage_used: int
    total_files: int
    total_attachments: int
    total_emails: int
    rate_usage_data: dict | None = None
    rate_limits: dict | None = None
    storage_quotas: dict | None = None
    forward_enabled: bool
    forward_destination: str | None = None
    vacation_enabled: bool
    vacation_message: str | None = None
    last_login: str | None = None
    last_activity: str | None = None
    last_storage_calculation: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MailboxListItem(MailboxResponse):
    domain_name: str
    organization_name: str


class MailboxListData(BaseModel):
    items: list[MailboxListItem]
    pagination: Pagination


class MailboxListResponse(BaseModel):
    type: str = "success"
    msg: str
    data: MailboxListData


class MailboxDetailData(MailboxResponse):
    domain: dict | None = None
    organization: dict | None = None


class MailboxDetailResponse(BaseModel):
    type: str = "success"
    msg: str
    data: MailboxDetailData


class MailboxWriteResponse(BaseModel):
    """POST/PUT /email-accounts -- returns the full mailbox row, no nested domain/org."""

    type: str = "success"
    msg: str
    data: MailboxResponse


class MailboxQuotaData(BaseModel):
    account_id: str
    email: str
    storage_quota: int
    storage_used: int
    attachment_storage_used: int
    email_storage_used: int
    usage_percentage: float
    total_files: int
    total_attachments: int
    total_emails: int
    rate_limits: dict
    storage_quotas: dict
    last_storage_calculation: str | None = None
    over_threshold: bool


class MailboxQuotaResponse(BaseModel):
    type: str = "success"
    msg: str
    data: MailboxQuotaData


class MailboxQuotaUpdateData(BaseModel):
    account_id: str
    storage_quota: int
    rate_limits: dict
    storage_quotas: dict


class MailboxQuotaUpdateResponse(BaseModel):
    type: str = "success"
    msg: str
    data: MailboxQuotaUpdateData


class MailboxAddData(BaseModel):
    # Pre-existing bug, out of scope to fix here (same class as aliases.py's
    # identical issue, schemas/alias.py): the route does
    # `user_id = cursor.lastrowid`, but email_accounts.id is a ULID
    # (CHAR(26), no AUTO_INCREMENT) -- lastrowid is always 0 for a
    # non-autoincrement insert. Typed to match what the endpoint actually
    # returns today (verified live: a real request returns user_id: 0),
    # not what it should return (the generated mailbox_id).
    user_id: int
    email: str


class MailboxAddResponse(BaseModel):
    """POST /add (legacy)."""

    type: str = "success"
    msg: str
    data: MailboxAddData


class LegacyMailboxStats(BaseModel):
    message_count: int
    total_size: int


class LegacyMailboxItem(BaseModel):
    """Shape returned by GET /get/{mailbox_id} (legacy raw-SQL path) --
    a superset of email_accounts columns via `SELECT ea.*, d.domain AS
    domain_name`, plus computed stats/quota fields. Deliberately loose
    (extra='allow' semantics via permissive Optional fields) since this
    endpoint selects `ea.*` and the exact raw-SQL column set is not the
    ORM's to_dict() shape."""

    id: str
    email: str
    domain_name: str | None = None
    stats: LegacyMailboxStats
    quota_usage_percent: float

    class Config:
        extra = "allow"


class MailboxGetLegacyResponse(BaseModel):
    type: str = "success"
    msg: str
    data: list[LegacyMailboxItem]


class LegacyBulkResultItem(BaseModel):
    mailbox: str
    status: str
    msg: str
    stats: dict | None = None


class MailboxEditResponse(BaseModel):
    """POST /edit and POST /delete (legacy) -- both return a per-item result list."""

    type: str = "success"
    msg: str
    data: list[LegacyBulkResultItem]


class MailboxLegacyQuotaFolder(BaseModel):
    folder: str | None = None
    message_count: int | None = None
    folder_size: int | None = None


class MailboxLegacyQuotaData(BaseModel):
    email: str
    storage_quota: int | None = None
    storage_used: int | None = None
    usage_percent: float | None = None
    available: int | None = None
    folder_breakdown: list[MailboxLegacyQuotaFolder] = []


class MailboxLegacyQuotaResponse(BaseModel):
    """GET /get/quota/{mailbox} (legacy)."""

    type: str = "success"
    msg: str
    data: MailboxLegacyQuotaData


class MailboxLegacyQuotaUpdateResponse(BaseModel):
    """POST /edit/quota (legacy) -- bare success message, no data payload."""

    type: str = "success"
    msg: str


class MailboxStatsMessageStats(BaseModel):
    total_messages: int | None = None
    unread_messages: int | None = None
    avg_message_size: float | None = None
    largest_message_size: int | None = None
    oldest_message: str | None = None
    newest_message: str | None = None


class MailboxStatsLoginStats(BaseModel):
    total_logins: int | None = None
    last_login: str | None = None
    unique_ips: int | None = None


class MailboxStatsData(BaseModel):
    mailbox_info: dict
    message_stats: MailboxStatsMessageStats
    login_stats: MailboxStatsLoginStats


class MailboxStatsResponse(BaseModel):
    """GET /get/stats/{mailbox} (legacy)."""

    type: str = "success"
    msg: str
    data: MailboxStatsData
