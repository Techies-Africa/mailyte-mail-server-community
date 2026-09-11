"""
Response models for worker/api/routes/aliases.py (phase-05 task 5.2,
priority 4 -- "tenant UI").

Every endpoint here is raw SQL -- there is no ORM-backed alias route (the
Alias class in database/models/core.py has no to_dict() and none of these
handlers import it). Field lists were read directly off each handler's
SELECT/INSERT statements and post-processing, not inferred from the ORM.
"""

from datetime import date, datetime

from pydantic import BaseModel


class AliasAddData(BaseModel):
    """POST /add. alias_id is generated as a ULID and then immediately
    overwritten by `cursor.lastrowid` before being returned -- aliases.id is
    a non-auto_increment ULID primary key, so lastrowid is not meaningful
    here (pre-existing behaviour in add_alias(), out of scope for this
    typing pass). Typed as str to match the ULID that should have been
    returned, mirroring MailboxAddResponse.user_id's same-shaped judgment
    call in schemas/mailbox.py."""

    alias_id: str
    source: str


class AliasAddResponse(BaseModel):
    type: str = "success"
    msg: str
    data: AliasAddData


class AliasItem(BaseModel):
    """Shape returned by GET /get/{alias_id} -- `SELECT a.*, d.description
    AS domain_description` (a.* mirrors the Alias table's columns in
    database/models/core.py) plus stats computed in the route body.
    extra='allow' defensively covers `aliases` table columns beyond what's
    listed here, matching LegacyMailboxItem's precedent for raw `a.*`
    queries in schemas/mailbox.py."""

    id: str
    domain_id: str
    organization_id: str
    source: str
    destination: str
    active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    domain_description: str | None = None
    destination_count: int
    destinations: list[str]
    monthly_forwards: int | None = None

    class Config:
        extra = "allow"


class AliasGetResponse(BaseModel):
    """GET /get/{alias_id} -- 'all' or a single id/source both return a
    list (0, 1, or many items; no 404 branch exists for a single miss, an
    empty list is returned instead)."""

    type: str = "success"
    msg: str
    data: list[AliasItem]


class LegacyAliasResultItem(BaseModel):
    """POST /edit and POST /delete -- both return a per-item result list.
    `source` is only populated by delete_alias() on success; edit_alias()
    never sets it."""

    alias: str
    status: str
    msg: str
    source: str | None = None


class AliasEditResponse(BaseModel):
    """POST /edit and POST /delete share this shape."""

    type: str = "success"
    msg: str
    data: list[LegacyAliasResultItem]


class AliasStatsBasic(BaseModel):
    """SUM(...) over zero matching rows is SQL NULL even though COUNT(*) is
    0, so active_aliases/inactive_aliases can be None for a domain with no
    aliases at all."""

    total_aliases: int
    active_aliases: int | None = None
    inactive_aliases: int | None = None


class AliasStatsDestination(BaseModel):
    destination: str
    usage_count: int


class AliasStatsMonthlyVolume(BaseModel):
    date: date
    forwards_count: int


class AliasStatsData(BaseModel):
    basic_stats: AliasStatsBasic
    top_destinations: list[AliasStatsDestination]
    monthly_volume: list[AliasStatsMonthlyVolume]


class AliasStatsResponse(BaseModel):
    type: str = "success"
    msg: str
    data: AliasStatsData


class AliasBulkResultItem(BaseModel):
    source: str
    status: str
    msg: str


class AliasBulkSummary(BaseModel):
    total: int
    success: int
    errors: int


class AliasBulkData(BaseModel):
    summary: AliasBulkSummary
    results: list[AliasBulkResultItem]


class AliasBulkResponse(BaseModel):
    type: str = "success"
    msg: str
    data: AliasBulkData
