#!/usr/bin/env python3
"""
Platform Console API (ADR-002, ADR-004)

Three groups of endpoints, all platform-scope, none of them tenant-facing:

1. GET /overview -- the single aggregate the console polls to paint its
   landing screen AND every nav badge. It exists because the alternative --
   the console fanning out to ~10 separate endpoints on every poll --
   multiplies request volume by ten for a screen whose whole job is "is this
   server healthy?", and makes the screen only as available as its least
   available dependency.

2. /operators/* and /sessions/* -- operator lifecycle. ADR-002 §4 reserves
   this to `owner`, for the reason that the ability to mint operators IS the
   ability to self-escalate.

3. /audit/* -- read-only views over operator_audit. The table is append-only
   by design (ADR-002 §6) and is written exclusively by app.py's
   operator_audit_middleware, so there is deliberately NO write endpoint here
   and there never should be one: an audit trail an operator can edit is not
   an audit trail.

Every route carries its own server-side scope+role check via
Depends(require_scope(...)). ADR-002 §7: "Client-side gating is presentation
only. Hiding a button is not a security control." The console hides what a
role cannot use; this file is what actually stops it.

DIFFERENCES FROM mailyte-email-server's copy of this router: its provisioning
queue, Prometheus range-proxy, log tail, analytics rollups, alert rules and
backup endpoints are not ported -- they are later console phases, not the
CE-02 prerequisite, and several of them depend on tables or services CE does
not ship. The three groups above are the whole console login-and-land path.
"""

import json
import logging
import os
from datetime import datetime, timedelta

import requests
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from utils.auth import (
    OPERATOR_SESSION_COOKIE_NAME,
    AuthContext,
    create_api_response,
    hash_password,
    hash_session_token,
    require_scope,
    validate_password_strength,
)
from utils.database import get_db_connection
from utils.totp import reset_enrollment

from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)
router = APIRouter()

# Same env var and (absent) default as routes/monitoring.py -- the overview's
# service tile must agree with what /api/v1/monitoring/services reports, so
# both resolve to the same monitoring instance or to neither.
MONITORING_SERVICE_URL = os.getenv("MONITORING_SERVICE_URL", "").strip()

# ADR-002 §4's four tiers. Validated here rather than as a DB enum for the
# same reason migration 0005 gave: adding a tier shouldn't need a migration.
_VALID_ROLES = ("support", "operator", "admin", "owner")

# operator_audit.result's three values, as written by utils/operator_audit.py.
_VALID_AUDIT_RESULTS = ("success", "failure", "denied")

# A session is "live" when it is neither revoked nor past either of its two
# expiries. Both are required: expires_at slides forward on activity (idle
# timeout), absolute_expiry never does (utils/auth.py, ADR-002 §7), so
# checking only one of them would keep a 3-day-old session alive.
_SESSION_LIVE_SQL = "s.revoked_at IS NULL AND s.expires_at > NOW() AND s.absolute_expiry > NOW()"

_OVERVIEW_BUCKETS = 24  # hours of history in the two time series


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _iso(value):
    """DATETIME -> ISO8601 string, passing None straight through. Every
    timestamp this file returns goes through here so the console never has to
    special-case one endpoint's date format against another's."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _as_int(value) -> int:
    """SUM()/COUNT() come back as Decimal or None depending on the aggregate
    and whether any rows matched; the console's counters are plain ints."""
    if value is None:
        return 0
    return int(value)


def _rate(numerator: int, denominator: int) -> float:
    """Percentage to 2dp. 0.0 (not null) on a zero denominator: "unlimited"
    is 0% used, not "unknown"."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _json_field(value):
    """operator_audit.request_body is a JSON column. mysql-connector hands it
    back as str or bytes depending on version/config -- decode it here so the
    console gets a real object rather than a string containing JSON."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _parse_iso_datetime(raw: str, *, end_of_day: bool = False) -> datetime:
    """Accept either a bare ISO date ('2026-08-20') or a full ISO datetime.

    A bare date used as an upper bound means "through the end of that day",
    not "at 00:00 that day" -- otherwise `date_from=X&date_to=X` returns
    nothing at all, which is the single most likely filter a human types.
    """
    parsed = datetime.fromisoformat(raw)
    if end_of_day and len(raw.strip()) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _paginate(page: int, per_page: int, total: int) -> dict:
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": (total + per_page - 1) // per_page if per_page else 0,
    }


def _query_one(conn, sql: str, params=None) -> dict:
    """One row as a dict, `{}` when nothing matched. Cursor always closed."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params or ())
        return cursor.fetchone() or {}
    finally:
        cursor.close()


def _query_all(conn, sql: str, params=None) -> list:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params or ())
        return cursor.fetchall() or []
    finally:
        cursor.close()


def _hour_buckets(now: datetime, count: int = _OVERVIEW_BUCKETS) -> list:
    """`count` hour-truncated buckets, oldest first, ending at the current
    hour. Built in Python rather than generated in SQL so an hour with no
    rows still appears as an explicit zero -- a sparkline with holes in it
    misreads as "nothing happened" when it actually means "no data"."""
    base = now.replace(minute=0, second=0, microsecond=0)
    return [base - timedelta(hours=offset) for offset in range(count - 1, -1, -1)]


# ---------------------------------------------------------------------------
# Counter blocks -- one function per question
# ---------------------------------------------------------------------------


def _counter(degraded: list, name: str, fn):
    """Run one counter block, degrading it to null on any failure.

    The overview is the console's landing screen: if a table is missing, or a
    dependency is restarting, the operator still needs to see the other
    tiles. A 500 here blanks the whole screen -- and the screen exists to
    tell you something is wrong. The failed block's name goes into `degraded`
    so the console renders "unavailable" rather than a misleading zero.
    """
    try:
        return fn()
    except Exception as exc:
        logger.warning(f"platform overview: '{name}' block degraded: {exc}")
        degraded.append(name)
        return None


def _block_services() -> dict:
    """Sourced from the monitoring service's own /heartbeat, which is what
    routes/monitoring.py's GET /services proxies -- reading the same upstream
    keeps this tile and the Infrastructure screen from ever disagreeing about
    how many services are up.

    CE ships no monitoring container, so on a stock Community install this
    block raises and lands in `degraded` by design (see the note on the
    overview endpoint). Setting MONITORING_SERVICE_URL turns it back on.
    """
    if not MONITORING_SERVICE_URL:
        raise RuntimeError(
            "no monitoring service configured (MONITORING_SERVICE_URL unset); "
            "the Community Edition compose file ships no monitoring container"
        )
    response = requests.get(f"{MONITORING_SERVICE_URL}/heartbeat", timeout=8)
    response.raise_for_status()
    payload = response.json()
    services = payload.get("services") or {}
    unhealthy = [
        {"name": name, "status": (state or {}).get("status", "unknown")}
        for name, state in services.items()
        if (state or {}).get("status") != "up"
    ]
    up = _as_int(payload.get("healthy_services"))
    total = _as_int(payload.get("total_services") or len(services))
    # `down` is derived here rather than by each caller so nothing can
    # disagree about whether "unknown" counts as down (it does -- anything
    # not 'up' is unhealthy).
    return {"up": up, "total": total, "down": max(total - up, 0), "unhealthy": unhealthy}


def _block_queue(conn) -> dict:
    """mail_queue is the DB-side queue. routes/queue.py's counters come from
    Postfix via the queue service, a different and later stage of the same
    pipeline -- the two are not expected to match. 'sending' counts toward
    depth: a message a worker has picked up but not finished is still backlog
    from an operator's point of view."""
    row = _query_one(
        conn,
        """
        SELECT
            SUM(CASE WHEN status IN ('queued','sending','deferred') THEN 1 ELSE 0 END)
                AS depth,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred,
            TIMESTAMPDIFF(
                SECOND,
                MIN(CASE WHEN status IN ('queued','sending','deferred')
                         THEN created_at END),
                NOW()
            ) AS oldest_age_seconds
        FROM mail_queue
        """,
    )
    oldest = row.get("oldest_age_seconds")
    return {
        "depth": _as_int(row.get("depth")),
        "deferred": _as_int(row.get("deferred")),
        # Deliberately null, not 0, when the queue is empty: "no oldest
        # message" and "the oldest message is 0s old" are different facts and
        # the tile renders them differently.
        "oldest_age_seconds": int(oldest) if oldest is not None else None,
    }


def _block_organizations(conn, seven_days_ago: datetime) -> dict:
    """There is no `status` column on organizations -- suspension is
    expressed as active=0 (0001_baseline; routes/organizations.py's PUT
    toggles exactly that flag)."""
    row = _query_one(
        conn,
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) AS suspended,
               SUM(CASE WHEN created_at >= %s THEN 1 ELSE 0 END) AS delta_7d
        FROM organizations
        """,
        (seven_days_ago,),
    )
    return {
        "total": _as_int(row.get("total")),
        "active": _as_int(row.get("active")),
        "suspended": _as_int(row.get("suspended")),
        "delta_7d": _as_int(row.get("delta_7d")),
    }


def _block_domains(conn, seven_days_ago: datetime) -> dict:
    row = _query_one(
        conn,
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN created_at >= %s THEN 1 ELSE 0 END) AS delta_7d
        FROM domains
        """,
        (seven_days_ago,),
    )
    return {"total": _as_int(row.get("total")), "delta_7d": _as_int(row.get("delta_7d"))}


def _block_mailboxes(conn, seven_days_ago: datetime) -> dict:
    row = _query_one(
        conn,
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN created_at >= %s THEN 1 ELSE 0 END) AS delta_7d
        FROM email_accounts
        """,
        (seven_days_ago,),
    )
    return {"total": _as_int(row.get("total")), "delta_7d": _as_int(row.get("delta_7d"))}


def _block_storage(conn) -> dict:
    """email_accounts.storage_used/storage_quota rather than the
    storage_usage table: storage_usage is a time-series with no writer in
    this repo, so summing it would report 0 on every install. email_accounts
    is maintained by mailbox CRUD and is the same source the Directory
    screens already show per mailbox."""
    totals = _query_one(
        conn,
        """
        SELECT COALESCE(SUM(storage_used), 0) AS used_bytes,
               COALESCE(SUM(storage_quota), 0) AS quota_bytes
        FROM email_accounts
        """,
    )
    top = _query_all(
        conn,
        """
        SELECT d.domain AS domain,
               COALESCE(SUM(ea.storage_used), 0) AS used_bytes
        FROM email_accounts ea
        JOIN domains d ON d.id = ea.domain_id
        GROUP BY d.domain
        ORDER BY used_bytes DESC
        LIMIT 5
        """,
    )
    used = _as_int(totals.get("used_bytes"))
    quota = _as_int(totals.get("quota_bytes"))
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "used_percent": _rate(used, quota),
        "top_consumers": [
            {"domain": row["domain"], "used_bytes": _as_int(row["used_bytes"])} for row in top
        ],
    }


def _block_certificates(conn, now: datetime) -> dict:
    row = _query_one(
        conn,
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'active'
                         AND valid_until BETWEEN NOW() AND %s
                        THEN 1 ELSE 0 END) AS expiring_30d,
               SUM(CASE WHEN status = 'active'
                         AND valid_until BETWEEN NOW() AND %s
                        THEN 1 ELSE 0 END) AS expiring_7d,
               TIMESTAMPDIFF(
                   SECOND,
                   NOW(),
                   MIN(CASE WHEN status = 'active' AND valid_until > NOW()
                            THEN valid_until END)
               ) AS soonest_expiry_seconds
        FROM ssl_certificates
        """,
        (now + timedelta(days=30), now + timedelta(days=7)),
    )
    soonest = row.get("soonest_expiry_seconds")
    return {
        "total": _as_int(row.get("total")),
        "expiring_30d": _as_int(row.get("expiring_30d")),
        "expiring_7d": _as_int(row.get("expiring_7d")),
        # Null when there is no active certificate at all -- which is not the
        # same as "expires in 0 days", and an install with no certificates
        # must not look like one about to lose them.
        "soonest_expiry_days": (round(int(soonest) / 86400, 2) if soonest is not None else None),
    }


def _block_quarantine(conn) -> dict:
    """The `quarantine` table, whose status enum really does have a
    'quarantined' member (0001_baseline)."""
    row = _query_one(
        conn, "SELECT COUNT(*) AS pending FROM quarantine WHERE status = 'quarantined'"
    )
    return {"pending": _as_int(row.get("pending"))}


def _block_webhooks(conn) -> dict:
    """Anything not yet resolved is work in the operator's queue --
    'abandoned' included, since abandoning delivery is precisely the state a
    human is meant to look at."""
    row = _query_one(
        conn,
        "SELECT COUNT(*) AS dead_letters FROM webhook_dead_letters WHERE status <> 'resolved'",
    )
    return {"dead_letters": _as_int(row.get("dead_letters"))}


def _block_security(conn, day_ago: datetime, buckets: list) -> dict:
    """SUM(attempt_count), not COUNT(*): failed_auth_attempts collapses
    repeated failures from one (ip, username, service) into a single row with
    a counter (utils/auth.py's _bump_failure_row), so row count would
    understate a brute-force run by orders of magnitude."""
    failed = _query_one(
        conn,
        """
        SELECT COALESCE(SUM(attempt_count), 0) AS failed_auth_24h
        FROM failed_auth_attempts
        WHERE last_attempt_at >= %s
        """,
        (day_ago,),
    )
    denied = _query_one(
        conn,
        """
        SELECT COUNT(*) AS denied_actions_24h
        FROM operator_audit
        WHERE result = 'denied' AND created_at >= %s
        """,
        (day_ago,),
    )
    # DATE()/HOUR() rather than DATE_FORMAT(): mysql-connector %-formats the
    # query text whenever params are supplied, so a literal '%Y' in the SQL
    # would blow up on substitution.
    series = _query_all(
        conn,
        """
        SELECT DATE(last_attempt_at) AS bucket_date,
               HOUR(last_attempt_at) AS bucket_hour,
               COALESCE(SUM(attempt_count), 0) AS count
        FROM failed_auth_attempts
        WHERE last_attempt_at >= %s
        GROUP BY bucket_date, bucket_hour
        """,
        (buckets[0],),
    )
    by_hour = {
        (row["bucket_date"], int(row["bucket_hour"])): _as_int(row["count"]) for row in series
    }
    return {
        "failed_auth_24h": _as_int(failed.get("failed_auth_24h")),
        "denied_actions_24h": _as_int(denied.get("denied_actions_24h")),
        "failed_auth_series": [
            {"hour": bucket.isoformat(), "count": by_hour.get((bucket.date(), bucket.hour), 0)}
            for bucket in buckets
        ],
    }


def _block_volume(conn, buckets: list) -> list:
    """24h mail volume by hour.

    mail_logs has no `direction` column (verified against 0001_baseline), so
    inbound vs outbound is derived from which side of the message carries a
    domain this server hosts: a message whose sender is local was sent by us;
    one whose recipient is local was received by us. bounced/deferred come
    straight off the status enum and are counted regardless of direction.
    """
    rows = _query_all(
        conn,
        """
        SELECT DATE(`timestamp`) AS bucket_date,
               HOUR(`timestamp`) AS bucket_hour,
               SUM(CASE WHEN status IN ('sent','delivered')
                         AND SUBSTRING_INDEX(sender, '@', -1)
                             IN (SELECT domain FROM domains)
                        THEN 1 ELSE 0 END) AS sent,
               SUM(CASE WHEN status IN ('sent','delivered')
                         AND SUBSTRING_INDEX(recipient, '@', -1)
                             IN (SELECT domain FROM domains)
                        THEN 1 ELSE 0 END) AS received,
               SUM(CASE WHEN status = 'bounced' THEN 1 ELSE 0 END) AS bounced,
               SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred
        FROM mail_logs
        WHERE `timestamp` >= %s
        GROUP BY bucket_date, bucket_hour
        """,
        (buckets[0],),
    )
    by_hour = {(row["bucket_date"], int(row["bucket_hour"])): row for row in rows}
    volume = []
    for bucket in buckets:
        row = by_hour.get((bucket.date(), bucket.hour)) or {}
        volume.append(
            {
                "hour": bucket.isoformat(),
                "sent": _as_int(row.get("sent")),
                "received": _as_int(row.get("received")),
                "bounced": _as_int(row.get("bounced")),
                "deferred": _as_int(row.get("deferred")),
            }
        )
    return volume


def _block_recent_actions(conn) -> list:
    rows = _query_all(
        conn,
        """
        SELECT id, operator_email, action, target_type, target_id, result, created_at
        FROM operator_audit
        ORDER BY id DESC
        LIMIT 10
        """,
    )
    return [
        {
            "id": row["id"],
            "operator_email": row["operator_email"],
            "action": row["action"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "result": row["result"],
            "created_at": _iso(row["created_at"]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# GET /overview
# ---------------------------------------------------------------------------

# Counters mailyte-email-server's overview reports that this one does not.
# Named in the response's `degraded` list on every call rather than silently
# omitted: a console that asks for a counter and gets nothing back cannot
# tell "this edition does not have it" from "this call failed", and both
# render very differently to an operator.
#
#   migrations  IMAP migration tooling is an enterprise capability (ADR-001).
#               CE's schema does carry a migration_jobs table -- it is part
#               of the frozen baseline -- but CE ships no migration router
#               and no worker to write rows into it, so a literal
#               "0 failed migrations" would be a number about a feature this
#               edition does not have. `GET /capabilities` omits `migration`
#               for the same reason, so the console hides the badge anyway.
_ABSENT_COUNTERS = ("migrations",)


@router.get(
    "/overview",
    summary="Platform overview counters",
    description="One aggregate powering the console's overview screen and every nav badge: "
    "service health, queue depth, tenant/domain/mailbox totals with 7-day deltas, storage, "
    "certificate expiry, quarantine, webhook dead-letters, 24h security and mail-volume "
    "series, and the last 10 operator actions. Each counter is computed independently: a "
    "subsystem that fails degrades its own field to null and is named in `degraded` rather "
    "than failing the whole response. On a stock Community install `services` is always "
    "degraded (no monitoring container ships with CE) and `migrations` is always degraded "
    "(enterprise capability).",
)
async def platform_overview(
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)
    buckets = _hour_buckets(now)
    degraded: list = list(_ABSENT_COUNTERS)

    # Runs before the DB connection is taken: it is an HTTP call out to
    # another container and holding a MySQL connection across it for no
    # reason is how connection pools get exhausted by a slow dependency.
    services_block = _counter(degraded, "services", _block_services)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        data = {
            "services": services_block,
            "queue": _counter(degraded, "queue", lambda: _block_queue(conn)),
            "organizations": _counter(
                degraded, "organizations", lambda: _block_organizations(conn, seven_days_ago)
            ),
            "domains": _counter(degraded, "domains", lambda: _block_domains(conn, seven_days_ago)),
            "mailboxes": _counter(
                degraded, "mailboxes", lambda: _block_mailboxes(conn, seven_days_ago)
            ),
            "storage": _counter(degraded, "storage", lambda: _block_storage(conn)),
            "certificates": _counter(
                degraded, "certificates", lambda: _block_certificates(conn, now)
            ),
            "quarantine": _counter(degraded, "quarantine", lambda: _block_quarantine(conn)),
            "webhooks": _counter(degraded, "webhooks", lambda: _block_webhooks(conn)),
            # Present as an explicit null so the console's shape is identical
            # across editions -- see _ABSENT_COUNTERS above.
            "migrations": None,
            "security": _counter(
                degraded, "security", lambda: _block_security(conn, day_ago, buckets)
            ),
            "volume_24h": _counter(degraded, "volume_24h", lambda: _block_volume(conn, buckets)),
            "recent_actions": _counter(
                degraded, "recent_actions", lambda: _block_recent_actions(conn)
            ),
            "degraded": degraded,
        }
    finally:
        conn.close()

    return create_api_response("success", "Platform overview retrieved", data)


# ---------------------------------------------------------------------------
# Operator management (ADR-002 §4: owner only, no exceptions)
# ---------------------------------------------------------------------------


class OperatorCreateRequest(BaseModel):
    email: str = Field(..., description="Operator email (also the TOTP identity)")
    full_name: str = Field(..., min_length=1, max_length=255, description="Display name")
    password: str = Field(..., description="Initial password (validate_password_strength policy)")
    role: str = Field(..., description="One of support|operator|admin|owner (ADR-002 §4)")


class OperatorUpdateRequest(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    role: str | None = Field(None, description="One of support|operator|admin|owner")
    is_active: bool | None = Field(
        None,
        description="Deactivate rather than delete -- operator_audit rows reference this "
        "operator forever",
    )


# One projection shared by the list and detail reads so the console never
# sees a field on one and not the other.
_OPERATOR_SELECT = f"""
    SELECT o.id, o.email, o.full_name, o.role, o.is_active, o.mfa_required,
           o.last_login_at, o.created_by, c.email AS created_by_email, o.created_at,
           (SELECT COUNT(*) FROM totp_secrets t
             WHERE t.user_email = o.email AND t.enabled = 1) AS mfa_enrolled,
           (SELECT COUNT(*) FROM operator_sessions s
             WHERE s.operator_id = o.id AND {_SESSION_LIVE_SQL}) AS active_session_count
    FROM platform_operators o
    LEFT JOIN platform_operators c ON c.id = o.created_by
"""


def _operator_row(row: dict) -> dict:
    """Shape one platform_operators row for the wire. password_hash is never
    selected in the first place, so it cannot leak by omission here."""
    return {
        "id": row["id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "mfa_required": bool(row["mfa_required"]),
        # totp_secrets is keyed on user_email, not operator_id -- it is
        # shared with mailbox TOTP, so enrollment is looked up by the
        # operator's email address.
        "mfa_enrolled": bool(row.get("mfa_enrolled")),
        "last_login_at": _iso(row["last_login_at"]),
        "created_by": row["created_by"],
        "created_by_email": row.get("created_by_email"),
        "created_at": _iso(row["created_at"]),
        "active_session_count": _as_int(row.get("active_session_count")),
    }


@router.get(
    "/operators",
    summary="List platform operators",
    description="Paginated list of operator identities with MFA enrollment and live session "
    "counts. Owner-only (ADR-002 §4) -- knowing who holds platform access is itself "
    "privileged.",
)
async def list_operators(
    q: str | None = Query(None, description="Free text over email and full_name"),
    role: str | None = Query(None, description="Filter by role"),
    is_active: bool | None = Query(None, description="Filter by active flag"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="owner")),
):
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    # WHERE built as fragments + a parallel params list -- never f-string
    # interpolation of caller input, including inside LIKE patterns (the %
    # wildcards go in the *value*, which the driver escapes).
    conditions: list = []
    params: list = []
    if q:
        conditions.append("(o.email LIKE %s OR o.full_name LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if role:
        if role not in _VALID_ROLES:
            return JSONResponse(
                content=create_api_response(
                    "error", f"role must be one of {', '.join(_VALID_ROLES)}"
                ),
                status_code=422,
            )
        conditions.append("o.role = %s")
        params.append(role)
    if is_active is not None:
        conditions.append("o.is_active = %s")
        params.append(1 if is_active else 0)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM platform_operators o {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"{_OPERATOR_SELECT} {where} ORDER BY o.created_at DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "Operators retrieved",
            {
                "items": [_operator_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List operators failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve operators"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/operators/{operator_id}",
    summary="Get one platform operator",
    description="Single operator with the same fields as the list view. Owner-only.",
)
async def get_operator(
    operator_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "read", role="owner")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"{_OPERATOR_SELECT} WHERE o.id = %s", (operator_id,))
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return JSONResponse(
                content=create_api_response("error", "Operator not found"), status_code=404
            )
        return create_api_response("success", "Operator retrieved", _operator_row(row))
    except Exception as exc:
        logger.error(f"Get operator failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve operator"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/operators",
    summary="Create a platform operator",
    description="Creates an operator identity. Owner-only (ADR-002 §4: operator management is "
    "reserved to owner precisely because minting an operator is self-escalation). "
    "mfa_required is always 1 -- MFA is mandatory, never optional (ADR-002 §3); the new "
    "operator enrolls via POST /api/v1/platform/auth/mfa/setup on first login. The "
    "password is bcrypt-hashed and is never echoed back in any form.",
)
async def create_operator(
    body: OperatorCreateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="owner")),
):
    if body.role not in _VALID_ROLES:
        return JSONResponse(
            content=create_api_response("error", f"role must be one of {', '.join(_VALID_ROLES)}"),
            status_code=422,
        )

    password_ok, password_message = validate_password_strength(body.password)
    if not password_ok:
        return JSONResponse(content=create_api_response("error", password_message), status_code=422)

    email = body.email.strip().lower()
    operator_id = generate_ulid()

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM platform_operators WHERE email = %s", (email,))
        if cursor.fetchone():
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "An operator with that email already exists"),
                status_code=409,
            )

        cursor.execute(
            "INSERT INTO platform_operators "
            "(id, email, full_name, password_hash, role, mfa_required, is_active, created_by) "
            "VALUES (%s, %s, %s, %s, %s, 1, 1, %s)",
            (
                operator_id,
                email,
                body.full_name,
                hash_password(body.password),
                body.role,
                ctx["operator_id"],
            ),
        )
        conn.commit()

        cursor.execute(f"{_OPERATOR_SELECT} WHERE o.id = %s", (operator_id,))
        row = cursor.fetchone()
        cursor.close()

        logger.info(
            f"Operator created: operator={operator_id} email={email} role={body.role} "
            f"by={ctx['operator_id']}"
        )
        return JSONResponse(
            content=create_api_response("success", "Operator created", _operator_row(row)),
            status_code=201,
        )
    except Exception as exc:
        # The uniqueness check above loses a race against a concurrent
        # create; uq_platform_operators_email is the real guarantee, so a
        # duplicate that reaches the INSERT is still a 409, not a 500.
        if "Duplicate entry" in str(exc):
            return JSONResponse(
                content=create_api_response("error", "An operator with that email already exists"),
                status_code=409,
            )
        logger.error(f"Create operator failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to create operator"), status_code=500
        )
    finally:
        conn.close()


@router.put(
    "/operators/{operator_id}",
    summary="Update a platform operator",
    description="Updates full_name, role, or is_active. Owner-only. An operator can never change "
    "their OWN role or active status -- that is the self-escalation path the owner-only "
    "gate exists to close, and a gate you can walk through from the inside is not a gate. "
    "There is deliberately no delete: operator_audit rows reference this identity "
    "permanently, so removing access means is_active=false, never a row disappearing from "
    "under the audit trail.",
)
async def update_operator(
    operator_id: str,
    body: OperatorUpdateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="owner")),
):
    if operator_id == ctx["operator_id"] and (body.role is not None or body.is_active is not None):
        return JSONResponse(
            content=create_api_response(
                "error", "An operator cannot change their own role or active status"
            ),
            status_code=403,
        )

    if body.role is not None and body.role not in _VALID_ROLES:
        return JSONResponse(
            content=create_api_response("error", f"role must be one of {', '.join(_VALID_ROLES)}"),
            status_code=422,
        )

    assignments: list = []
    params: list = []
    if body.full_name is not None:
        assignments.append("full_name = %s")
        params.append(body.full_name)
    if body.role is not None:
        assignments.append("role = %s")
        params.append(body.role)
    if body.is_active is not None:
        assignments.append("is_active = %s")
        params.append(1 if body.is_active else 0)

    if not assignments:
        return JSONResponse(
            content=create_api_response("error", "No updatable fields provided"), status_code=400
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM platform_operators WHERE id = %s", (operator_id,))
        if not cursor.fetchone():
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "Operator not found"), status_code=404
            )

        cursor.execute(
            f"UPDATE platform_operators SET {', '.join(assignments)} WHERE id = %s",
            params + [operator_id],
        )
        conn.commit()

        cursor.execute(f"{_OPERATOR_SELECT} WHERE o.id = %s", (operator_id,))
        row = cursor.fetchone()
        cursor.close()

        logger.info(f"Operator updated: operator={operator_id} by={ctx['operator_id']}")
        return create_api_response("success", "Operator updated", _operator_row(row))
    except Exception as exc:
        logger.error(f"Update operator failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update operator"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/operators/{operator_id}/mfa-reset",
    summary="Reset an operator's MFA enrollment",
    description="The MFA-lockout recovery path: clears the operator's TOTP secret so they "
    "re-enroll on next login, and revokes every live session they hold (a lost "
    "authenticator and a stolen one look identical from here, so the sessions go too). "
    "Owner-only. An operator may NOT reset their own MFA (403): a compromised session "
    "could otherwise drop the second factor on the very account it stole, turning MFA "
    "recovery into MFA removal.",
)
async def reset_operator_mfa(
    operator_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="owner")),
):
    if operator_id == ctx["operator_id"]:
        return JSONResponse(
            content=create_api_response(
                "error", "An operator cannot reset their own MFA enrollment"
            ),
            status_code=403,
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, email FROM platform_operators WHERE id = %s", (operator_id,))
        operator = cursor.fetchone()
        if not operator:
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "Operator not found"), status_code=404
            )

        # Delegated to utils/totp.py so the blank-in-place semantics live in
        # one place with the enrollment code that depends on them.
        mfa_cleared = reset_enrollment(operator["email"])

        cursor.execute(
            f"UPDATE operator_sessions s SET s.revoked_at = NOW() "
            f"WHERE s.operator_id = %s AND {_SESSION_LIVE_SQL}",
            (operator_id,),
        )
        revoked = cursor.rowcount
        conn.commit()
        cursor.close()

        logger.warning(
            f"Operator MFA reset: operator={operator_id} email={operator['email']} "
            f"sessions_revoked={revoked} by={ctx['operator_id']}"
        )
        return create_api_response(
            "success",
            "MFA enrollment reset -- the operator must re-enroll on next login",
            {
                "operator_id": operator_id,
                "mfa_cleared": mfa_cleared,
                "sessions_revoked": revoked,
            },
        )
    except Exception as exc:
        logger.error(f"Reset operator MFA failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to reset MFA enrollment"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/operators/{operator_id}/sessions",
    summary="List one operator's sessions",
    description="Every session row for this operator, live or not, with a computed is_active. "
    "Expired and revoked sessions are included on purpose -- during an incident the "
    "question is usually 'where was this account logged in from', which the dead rows "
    "answer and the live ones do not.",
)
async def list_operator_sessions(
    operator_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "read", role="owner")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM platform_operators WHERE id = %s", (operator_id,))
        if not cursor.fetchone():
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "Operator not found"), status_code=404
            )

        cursor.execute(
            f"""
            SELECT s.id, s.ip_address, s.user_agent, s.mfa_satisfied, s.created_at,
                   s.expires_at, s.absolute_expiry, s.revoked_at,
                   ({_SESSION_LIVE_SQL}) AS is_active
            FROM operator_sessions s
            WHERE s.operator_id = %s
            ORDER BY s.created_at DESC
            """,
            (operator_id,),
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "Operator sessions retrieved",
            {
                "items": [
                    {
                        "id": row["id"],
                        "ip_address": row["ip_address"],
                        "user_agent": row["user_agent"],
                        "mfa_satisfied": bool(row["mfa_satisfied"]),
                        "created_at": _iso(row["created_at"]),
                        "expires_at": _iso(row["expires_at"]),
                        "absolute_expiry": _iso(row["absolute_expiry"]),
                        "revoked_at": _iso(row["revoked_at"]),
                        "is_active": bool(row["is_active"]),
                    }
                    for row in rows
                ]
            },
        )
    except Exception as exc:
        logger.error(f"List operator sessions failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve sessions"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/operators/{operator_id}/sessions/revoke",
    summary="Revoke all of an operator's live sessions",
    description="Force-logs-out one operator everywhere. ADR-002 §7 requires platform sessions be "
    "'instantly revocable'; this is that control. Already-expired and already-revoked "
    "rows are left alone so the returned count means 'sessions this call actually killed'.",
)
async def revoke_operator_sessions(
    operator_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="owner")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM platform_operators WHERE id = %s", (operator_id,))
        if not cursor.fetchone():
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "Operator not found"), status_code=404
            )

        cursor.execute(
            f"UPDATE operator_sessions s SET s.revoked_at = NOW() "
            f"WHERE s.operator_id = %s AND {_SESSION_LIVE_SQL}",
            (operator_id,),
        )
        revoked = cursor.rowcount
        conn.commit()
        cursor.close()

        logger.warning(
            f"Operator sessions revoked: operator={operator_id} count={revoked} "
            f"by={ctx['operator_id']}"
        )
        return create_api_response(
            "success", "Sessions revoked", {"operator_id": operator_id, "revoked": revoked}
        )
    except Exception as exc:
        logger.error(f"Revoke operator sessions failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to revoke sessions"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/sessions",
    summary="List all live operator sessions",
    description="Every currently-valid platform session across all operators, newest first, joined "
    "to the operator's email and role. `is_current` marks the caller's own session so the "
    "console can warn before an owner revokes the session they are using. Token hashes "
    "are compared server-side and never returned.",
)
async def list_all_sessions(
    request: Request,
    ctx: AuthContext = Depends(require_scope("platform", "read", role="owner")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT s.id, s.token_hash, s.operator_id, o.email AS operator_email,
                   o.role AS operator_role, s.ip_address, s.user_agent, s.mfa_satisfied,
                   s.created_at, s.expires_at, s.absolute_expiry, s.revoked_at
            FROM operator_sessions s
            JOIN platform_operators o ON o.id = s.operator_id
            WHERE {_SESSION_LIVE_SQL}
            ORDER BY s.created_at DESC
            """
        )
        rows = cursor.fetchall() or []
        cursor.close()

        cookie = request.cookies.get(OPERATOR_SESSION_COOKIE_NAME)
        current_hash = hash_session_token(cookie) if cookie else None

        return create_api_response(
            "success",
            "Live operator sessions retrieved",
            {
                "items": [
                    {
                        "id": row["id"],
                        "operator_id": row["operator_id"],
                        "operator_email": row["operator_email"],
                        "operator_role": row["operator_role"],
                        "ip_address": row["ip_address"],
                        "user_agent": row["user_agent"],
                        "mfa_satisfied": bool(row["mfa_satisfied"]),
                        "created_at": _iso(row["created_at"]),
                        "expires_at": _iso(row["expires_at"]),
                        "absolute_expiry": _iso(row["absolute_expiry"]),
                        "revoked_at": _iso(row["revoked_at"]),
                        "is_active": True,
                        "is_current": bool(current_hash and row["token_hash"] == current_hash),
                    }
                    for row in rows
                ]
            },
        )
    except Exception as exc:
        logger.error(f"List sessions failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve sessions"), status_code=500
        )
    finally:
        conn.close()


@router.delete(
    "/sessions/{session_id}",
    summary="Revoke a single operator session",
    description="Revokes one session by id -- the targeted version of the per-operator revoke, for "
    "kicking a single suspicious login without logging the operator out everywhere. 404 "
    "if the session does not exist or is already dead.",
)
async def revoke_session(
    session_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="owner")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT s.id, s.operator_id FROM operator_sessions s "
            f"WHERE s.id = %s AND {_SESSION_LIVE_SQL}",
            (session_id,),
        )
        session = cursor.fetchone()
        if not session:
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "Session not found"), status_code=404
            )

        cursor.execute(
            "UPDATE operator_sessions SET revoked_at = NOW() WHERE id = %s", (session_id,)
        )
        conn.commit()
        cursor.close()

        logger.warning(
            f"Operator session revoked: session={session_id} "
            f"operator={session['operator_id']} by={ctx['operator_id']}"
        )
        return create_api_response(
            "success", "Session revoked", {"session_id": session_id, "revoked": 1}
        )
    except Exception as exc:
        logger.error(f"Revoke session failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to revoke session"), status_code=500
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Operator audit -- READ ONLY (ADR-002 §6)
#
# operator_audit is append-only and written solely by app.py's
# operator_audit_middleware. No endpoint in this section mutates it, and none
# ever should: the value of an audit trail is exactly that the people it
# records cannot edit it.
# ---------------------------------------------------------------------------


def _audit_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "operator_id": row["operator_id"],
        "operator_email": row["operator_email"],
        "caller_scope": row["caller_scope"],
        "action": row["action"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "organization_id": row["organization_id"],
        "request_body": _json_field(row["request_body"]),
        "result": row["result"],
        "ip_address": row["ip_address"],
        "correlation_id": row["correlation_id"],
        "created_at": _iso(row["created_at"]),
    }


@router.get(
    "/audit",
    summary="Search the operator audit trail",
    description="Paginated, newest-first read over operator_audit, filterable by operator, action, "
    "target, tenant, result, scope, correlation id, date range, and free text. per_page "
    "allows up to 500 because the console streams its CSV incident export through this "
    "same endpoint. Read-only: the table is append-only.",
)
async def list_audit(
    operator_id: str | None = Query(None),
    operator_email: str | None = Query(None),
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    organization_id: str | None = Query(None),
    result: str | None = Query(None, description="success | failure | denied"),
    caller_scope: str | None = Query(None, description="platform | organization"),
    correlation_id: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO date or datetime, inclusive"),
    date_to: str | None = Query(None, description="ISO date or datetime, inclusive"),
    q: str | None = Query(None, description="Free text over action, target_id, operator_email"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    per_page = min(per_page, 500)
    offset = (page - 1) * per_page

    conditions: list = []
    params: list = []

    # Every filter is an exact-match fragment with a bound parameter. The
    # audit trail is the one table where a SQL injection would let a caller
    # rewrite the record of what they did, so nothing below is interpolated.
    for column, value in (
        ("operator_id", operator_id),
        ("operator_email", operator_email),
        ("action", action),
        ("target_type", target_type),
        ("target_id", target_id),
        ("organization_id", organization_id),
        ("caller_scope", caller_scope),
        ("correlation_id", correlation_id),
    ):
        if value:
            conditions.append(f"{column} = %s")
            params.append(value)

    if result:
        if result not in _VALID_AUDIT_RESULTS:
            return JSONResponse(
                content=create_api_response(
                    "error", f"result must be one of {', '.join(_VALID_AUDIT_RESULTS)}"
                ),
                status_code=422,
            )
        conditions.append("result = %s")
        params.append(result)

    try:
        if date_from:
            conditions.append("created_at >= %s")
            params.append(_parse_iso_datetime(date_from))
        if date_to:
            conditions.append("created_at <= %s")
            params.append(_parse_iso_datetime(date_to, end_of_day=True))
    except ValueError:
        return JSONResponse(
            content=create_api_response(
                "error", "date_from/date_to must be ISO dates or datetimes"
            ),
            status_code=422,
        )

    if q:
        conditions.append("(action LIKE %s OR target_id LIKE %s OR operator_email LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM operator_audit {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"""
            SELECT id, operator_id, operator_email, caller_scope, action, target_type,
                   target_id, organization_id, request_body, result, ip_address,
                   correlation_id, created_at
            FROM operator_audit
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "Audit entries retrieved",
            {
                "items": [_audit_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List audit failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve audit entries"),
            status_code=500,
        )
    finally:
        conn.close()


# NOTE: /audit/facets MUST stay declared above /audit/{audit_id}. FastAPI
# matches routes in declaration order, so the parameterised route would
# otherwise swallow "facets" as an audit_id and 404.
@router.get(
    "/audit/facets",
    summary="Distinct values for audit filter dropdowns",
    description="The distinct actions, target types, and operators actually present in "
    "operator_audit, so the console's filter dropdowns offer only values that can return "
    "rows. Each list is capped at 200.",
)
async def audit_facets(
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT DISTINCT action FROM operator_audit WHERE action IS NOT NULL "
            "ORDER BY action LIMIT 200"
        )
        actions = [row["action"] for row in (cursor.fetchall() or [])]

        cursor.execute(
            "SELECT DISTINCT target_type FROM operator_audit WHERE target_type IS NOT NULL "
            "ORDER BY target_type LIMIT 200"
        )
        target_types = [row["target_type"] for row in (cursor.fetchall() or [])]

        # From operator_audit itself, not platform_operators: the point is to
        # filter rows that exist, and the audit trail deliberately outlives
        # the identities in it.
        cursor.execute(
            "SELECT DISTINCT operator_id, operator_email FROM operator_audit "
            "WHERE operator_id IS NOT NULL ORDER BY operator_email LIMIT 200"
        )
        operators = [
            {"id": row["operator_id"], "email": row["operator_email"]}
            for row in (cursor.fetchall() or [])
        ]
        cursor.close()

        return create_api_response(
            "success",
            "Audit facets retrieved",
            {"actions": actions, "target_types": target_types, "operators": operators},
        )
    except Exception as exc:
        logger.error(f"Audit facets failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve audit facets"),
            status_code=500,
        )
    finally:
        conn.close()


@router.get(
    "/audit/{audit_id}",
    summary="Get one audit entry",
    description="Full detail for a single operator_audit row, including the redacted request_body "
    "and correlation id used to tie the action back to its request logs.",
)
async def get_audit_entry(
    audit_id: int,
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, operator_id, operator_email, caller_scope, action, target_type,
                   target_id, organization_id, request_body, result, ip_address,
                   correlation_id, created_at
            FROM operator_audit
            WHERE id = %s
            """,
            (audit_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return JSONResponse(
                content=create_api_response("error", "Audit entry not found"), status_code=404
            )
        return create_api_response("success", "Audit entry retrieved", _audit_row(row))
    except Exception as exc:
        logger.error(f"Get audit entry failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve audit entry"),
            status_code=500,
        )
    finally:
        conn.close()
