#!/usr/bin/env python3
"""
Platform Console API (Mailyte Console PRD SS12 gaps #1 and #3, phase-01 SS1.7/SS1.8)

Three groups of endpoints, all platform-scope, none of them tenant-facing:

1. GET /overview -- the single aggregate the console polls every 60s to
   paint its landing screen AND every nav badge (PRD SS5.1 + SS7). It exists
   because the alternative -- the console fanning out to ~10 separate
   endpoints on every poll -- multiplies request volume by ten for a screen
   whose whole job is "is the platform healthy?", and makes the screen only
   as available as its least available dependency.

2. /operators/* and /sessions/* -- operator lifecycle management. ADR-002
   SS4 reserves this to `owner` alongside GDPR erasure, for the same reason:
   both are irreversible and the ability to mint operators IS the ability to
   self-escalate. phase-06 shipped bootstrap/login/mfa/logout/me in
   routes/platform_auth.py; everything after "the first owner exists" was
   left unbuilt, which is what this adds.

3. /audit/* -- read-only views over operator_audit (phase-01 SS1.8: "the
   console must be able to audit itself"). The table is append-only by
   design (ADR-002 SS6) and is written exclusively by app.py's
   operator_audit_middleware, so there is deliberately NO write endpoint
   here and there never should be one -- an audit trail an operator can
   edit is not an audit trail.

Every route below carries its own server-side scope+role check via
Depends(require_scope(...)). ADR-002 SS7: "Client-side gating is
presentation only. Hiding a button is not a security control." The console
hides what a role cannot use; this file is what actually stops it.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

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

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
# Imported for the provisioning-failure queue's schema introspection, not to
# build ORM queries: this file is raw-SQL throughout. AccountStatus is the
# single source of truth for which mailbox states actually exist, so the
# queue derives its failure/pending state lists from it rather than
# hard-coding states the enum does not have (see SS"Provisioning failure
# queue" below).
from starlette.concurrency import run_in_threadpool

from database.models.enums import AccountStatus

# Alert-channel signing secrets (GAP #6). Encrypted, never hashed: the fire
# path needs the plaintext back to compute the HMAC, which a one-way hash
# cannot give. Same AES-256-GCM-under-a-mounted-KEK scheme, and the same
# module, that 0003_encrypt_private_keys gave DKIM/PGP/S-MIME private keys.
from shared.envelope_encryption import (
    KEKNotConfiguredError,
    decrypt_private_key,
    encrypt_private_key,
)
from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)
router = APIRouter()

# Same env var and default as routes/monitoring.py -- the overview's service
# tile must agree with what /api/v1/monitoring/services reports, so both
# must resolve to the same monitoring instance.
# 0.0.0.0 is a BIND address, never a CONNECT address -- inside the api
# container it resolves to the api itself. See routes/monitoring.py.
# This is what the console overview's "Services" tile reads, so the bad
# default is why that tile rendered a dash instead of a service count.
MONITORING_SERVICE_URL = os.getenv("MONITORING_SERVICE_URL", "http://monitoring:8085")

# ADR-002 SS4's four tiers. Validated here rather than as a DB enum for the
# same reason migration 0006 gave: adding a tier shouldn't need a migration.
_VALID_ROLES = ("support", "operator", "admin", "owner")

# operator_audit.result's three values, as written by utils/operator_audit.py.
_VALID_AUDIT_RESULTS = ("success", "failure", "denied")

# A session is "live" when it is neither revoked nor past either of its two
# expiries. Both are required: expires_at slides forward on activity (idle
# timeout), absolute_expiry never does (utils/auth.py, ADR-002 SS7), so
# checking only one of them would keep a 3-day-old session alive.
_SESSION_LIVE_SQL = "s.revoked_at IS NULL AND s.expires_at > NOW() AND s.absolute_expiry > NOW()"

_OVERVIEW_BUCKETS = 24  # hours of history in the two time series


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _iso(value):
    """DATETIME -> ISO8601 string, passing None straight through. Every
    timestamp this file returns goes through here so the console never has
    to special-case one endpoint's date format against another's."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _as_int(value) -> int:
    """SUM()/COUNT() come back as Decimal or None depending on the aggregate
    and whether any rows matched; the console's counters are plain ints."""
    if value is None:
        return 0
    return int(value)


def _json_field(value):
    """operator_audit.request_body is a JSON column. mysql-connector hands
    it back as str or bytes depending on version/config -- decode it here so
    the console gets a real object rather than a string containing JSON."""
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


# ---------------------------------------------------------------------------
# Shared counter blocks -- one function per question, two callers
#
# GAP #1's /overview and GAP #6's alert evaluator ask the same things of the
# same tables ("how deep is the queue", "how many certificates expire soon",
# "what is the 24h bounce rate"). Each question is answered by exactly one
# function below and BOTH callers go through it, so a rule can never fire on
# a number the overview screen disagrees with. Two implementations of
# "queue depth" that drift apart is exactly how operators learn to distrust
# an alert, and a distrusted alert is worse than no alert.
# ---------------------------------------------------------------------------


def _query_one(conn, sql: str, params=None) -> dict:
    """One row as a dict, `{}` when nothing matched. Cursor always closed."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params or ())
        return cursor.fetchone() or {}
    finally:
        cursor.close()


def _execute(conn, sql: str, params=None) -> None:
    """Run a write and commit it. The counterpart to _query_one/_query_all,
    added for the alert evaluator -- every other writer here opens its own
    cursor inline, which is fine for one statement in a request handler and
    unreadable in a loop that issues several per rule."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params or ())
        conn.commit()
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


def _counter(degraded: list, name: str, fn):
    """Run one counter block, degrading it to null on any failure.

    The overview is the console's landing screen: if the quarantine table is
    missing on a CE install, or the monitoring container is restarting, the
    operator still needs to see the other nine tiles. A 500 here blanks the
    whole screen -- and the screen exists to tell you something is wrong.
    The failed block's name goes into `degraded` so the console can render
    "unavailable" rather than a misleading zero.
    """
    try:
        return fn()
    except Exception as exc:
        logger.warning(f"platform overview: '{name}' block degraded: {exc}")
        degraded.append(name)
        return None


def _block_services() -> dict:
    """Sourced from the monitoring service's own /heartbeat, which is what
    routes/monitoring.py's GET /services proxies (it has no reusable helper
    -- it forwards the response verbatim). Reading the same upstream keeps
    the overview tile and the Infrastructure screen from ever disagreeing
    about how many services are up."""
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
    # `down` is derived here rather than by each caller: the alert metric
    # `services_down` and the overview tile must not disagree about whether
    # "unknown" counts as down (it does -- anything not 'up' is unhealthy).
    return {"up": up, "total": total, "down": max(total - up, 0), "unhealthy": unhealthy}


def _block_queue(conn) -> dict:
    """mail_queue is the DB-side queue (routes/queue.py's counters come from
    Postfix via the queue service, which is a different, later stage of the
    same pipeline -- the two are not expected to match). 'sending' counts
    toward depth: a message a worker has picked up but not finished is still
    backlog from an operator's point of view."""
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
        # message" and "the oldest message is 0s old" are different facts
        # and the tile renders them differently (and an alert rule on the
        # metric must not fire on an empty queue).
        "oldest_age_seconds": int(oldest) if oldest is not None else None,
    }


def _block_organizations(conn, seven_days_ago: datetime) -> dict:
    """There is no `status` column on organizations -- suspension is
    expressed as active=0 (see the baseline schema and
    routes/organizations.py's PUT, which toggles exactly that flag)."""
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
    this repo (the storage_usage worker maintains its own `users` table
    instead), so summing it would report 0 on every install. email_accounts
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
        # Percentage lives here rather than in the alert evaluator so both
        # callers round it identically. 0.0 (not null) when no quota is set
        # at all -- "unlimited" is 0% used, not "unknown".
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
        # Days until the EARLIEST still-valid active certificate expires.
        # Null when there is no active certificate at all -- which is not
        # the same as "expires in 0 days", and an alert rule on
        # certificates_expiring_days must not fire on an install that has
        # no certificates rather than one that is about to lose them.
        "soonest_expiry_days": (round(int(soonest) / 86400, 2) if soonest is not None else None),
    }


def _block_quarantine(conn) -> dict:
    """The `quarantine` table (migration 004_transport_rules.sql), NOT
    mail_logs. routes/message_trace.py's GET /quarantine filters mail_logs on
    status='quarantined', but mail_logs.status is an ENUM whose members are
    queued|sending|sent|delivered|bounced|rejected|deferred -- 'quarantined'
    is not one of them, so that query can never match a row. Filed as a
    finding; this counter reads the table that actually holds quarantined
    mail."""
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


def _block_migrations(conn) -> dict:
    row = _query_one(conn, "SELECT COUNT(*) AS failed FROM migration_jobs WHERE status = 'failed'")
    return {"failed": _as_int(row.get("failed"))}


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

    mail_logs has no `direction` column (verified against the alembic
    baseline), so inbound vs outbound is derived from which side of the
    message carries a domain this server hosts: a message whose sender is
    local was sent by us; one whose recipient is local was received by us.
    bounced/deferred come straight off the status enum and are counted
    regardless of direction.
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
# GAP #1 -- GET /overview
# ---------------------------------------------------------------------------


@router.get(
    "/overview",
    summary="Platform overview counters",
    description="One aggregate powering the console's overview screen and every nav badge "
    "(PRD SS5.1, SS7): service health, queue depth, tenant/domain/mailbox totals with "
    "7-day deltas, storage, certificate expiry, quarantine, webhook dead-letters, failed "
    "migrations, 24h security and mail-volume series, and the last 10 operator actions. "
    "Each counter is computed independently: a subsystem that fails degrades its own "
    "field to null and is named in `degraded` rather than failing the whole response. "
    "Every counter here is the same function the alert evaluator calls, so an alert and "
    "this screen can never report different numbers for the same thing.",
)
def platform_overview(
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    # Deliberately `def`, not `async def`. Every block below is blocking I/O --
    # `requests.get` to the monitoring container plus a dozen pymysql queries --
    # and there is not a single `await` in the body. Declared `async`, FastAPI
    # runs it directly ON the event loop, so one overview call froze the whole
    # api process for its duration.
    #
    # That produced a self-deadlock that looked like a monitoring outage:
    # _block_services calls monitoring's /heartbeat, and /heartbeat health-checks
    # the `api` service -- which could not answer, because it was blocked inside
    # this very function. The circular wait ran out the 8s timeout and the
    # overview reported `services: null`, blaming a dependency that was in fact
    # answering in ~0.05s.
    #
    # As `def`, FastAPI runs it in a threadpool and the loop stays free. This
    # matters far beyond this one screen: useOverview drives the sidebar badges
    # and header on EVERY console page, polling every 60s, so the stall hit the
    # entire platform API once a minute. See routes/monitoring.py, which wraps
    # the identical call in run_in_threadpool for the same reason.
    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)
    buckets = _hour_buckets(now)
    degraded: list = []

    # Runs before the DB connection is taken: it is an HTTP call to the
    # monitoring container and holding a MySQL connection across it for no
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
            "migrations": _counter(degraded, "migrations", lambda: _block_migrations(conn)),
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
# GAP #3 -- operator management (ADR-002 SS4: owner only, no exceptions)
# ---------------------------------------------------------------------------


class OperatorCreateRequest(BaseModel):
    email: str = Field(..., description="Operator email (also the TOTP identity)")
    full_name: str = Field(..., min_length=1, max_length=255, description="Display name")
    password: str = Field(..., description="Initial password (validate_password_strength policy)")
    role: str = Field(..., description="One of support|operator|admin|owner (ADR-002 SS4)")


class OperatorUpdateRequest(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    role: str | None = Field(None, description="One of support|operator|admin|owner")
    is_active: bool | None = Field(
        None,
        description="Deactivate rather than delete -- operator_audit rows reference this "
        "operator forever (phase-04 SS4.7)",
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
        # shared with mailbox/tenant TOTP (task 6.7 reused it rather than
        # building a second implementation), so enrollment is looked up by
        # the operator's email address.
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
    description="Paginated list of staff identities with MFA enrollment and live session counts. "
    "Owner-only (ADR-002 SS4) -- knowing who holds platform access is itself privileged.",
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
    description="Creates a staff identity. Owner-only (ADR-002 SS4: operator management is "
    "reserved to owner precisely because minting an operator is self-escalation). "
    "mfa_required is always 1 -- MFA is mandatory, never optional (ADR-002 SS3); the new "
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

    # Same policy as every other password in the system (phase-07 H7) --
    # one function, so an operator credential can't be weaker than a
    # mailbox one.
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
    description="Updates full_name, role, or is_active. Owner-only. An operator can never "
    "change their OWN role or active status (ADR-002 SS4, phase-04 verification 6) -- that "
    "is the self-escalation path the owner-only gate exists to close, and a gate you can "
    "walk through from the inside is not a gate. There is deliberately no delete: "
    "operator_audit rows reference this identity permanently (phase-04 SS4.7), so removing "
    "access means is_active=false, never a row disappearing from under the audit trail.",
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

        # Disable in place rather than DELETE: totp_secrets.secret is NOT
        # NULL so it is blanked instead of nulled, and keeping the row means
        # the TOTP service's own ON DUPLICATE KEY UPDATE re-enrollment path
        # (security/totp_service.py TOTPStore.create) works unchanged, while
        # created_at survives as evidence of when MFA was first set up.
        cursor.execute(
            "UPDATE totp_secrets SET secret = '', backup_codes = NULL, enabled = 0, verified = 0 "
            "WHERE user_email = %s",
            (operator["email"],),
        )
        mfa_rows = cursor.rowcount

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
                "mfa_cleared": mfa_rows > 0,
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
    description="Force-logs-out one operator everywhere. ADR-002 SS7 requires platform "
    "sessions be 'instantly revocable'; this is that control. Already-expired and "
    "already-revoked rows are left alone so the returned count means 'sessions this call "
    "actually killed'.",
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
    description="Every currently-valid platform session across all operators, newest first, "
    "joined to the operator's email and role. `is_current` marks the caller's own session "
    "so the console can warn before an owner revokes the session they are using. Token "
    "hashes are compared server-side and never returned.",
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
    description="Revokes one session by id -- the targeted version of the per-operator revoke, "
    "for kicking a single suspicious login without logging the operator out everywhere. "
    "404 if the session does not exist or is already dead.",
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
# Operator audit -- READ ONLY (phase-01 SS1.8, ADR-002 SS6)
#
# operator_audit is append-only and written solely by app.py's
# operator_audit_middleware. No endpoint in this section mutates it, and
# none ever should: the value of an audit trail is exactly that the people
# it records cannot edit it.
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
    description="Paginated, newest-first read over operator_audit with the filters phase-01 "
    "SS1.8 requires (operator, action, target, tenant, result, scope, correlation id, date "
    "range, free text). per_page allows up to 500 because the console streams its CSV "
    "incident export through this same endpoint. Read-only: the table is append-only.",
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

        # From operator_audit itself, not platform_operators: the point is
        # to filter rows that exist, and the audit trail deliberately
        # outlives the identities in it (phase-04 SS4.7).
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
    description="Full detail for a single operator_audit row, including the redacted "
    "request_body and correlation id used to tie the action back to its request logs.",
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


# ---------------------------------------------------------------------------
# Provisioning failure queue (console phase-02 SS2.7)
# ---------------------------------------------------------------------------
#
# SCHEMA FINDING, verified against the real schema before a line of this was
# written: the mail server has no provisioning state machine at all.
#
#   * email_accounts.status is enum('active','inactive','suspended')
#     (001_init_schema.sql line 136; database/models/enums.py AccountStatus)
#     -- there is no 'failed', 'pending' or 'provisioning' member.
#   * `domains` has no status column whatsoever. Its only state is the
#     `active` boolean; DNS/DKIM verification is computed live by
#     POST /domains/{id}/verify-dns and never persisted.
#   * Neither table carries an error message or an attempt counter --
#     nothing in the frozen SQL chain nor alembic 0002-0008 adds one.
#
# The state machine phase-02 SS2.7 points at lives in Laravel
# (03-mailyte-api/phase-02), not here. Three options were rejected:
# inventing the columns (conventions SS4 rule 15 -- a service asks for a
# migration, it does not provision its own schema, and this task explicitly
# forbade one), reading 'inactive'/'active=0' as "failed" (that invents
# semantics: a deliberately disabled mailbox is not a failed one), and
# returning a bare empty list (indistinguishable from "no failures today",
# which is the console lying to an operator).
#
# Instead the failure states are DERIVED: the states this would query are
# intersected with the enum members that actually exist. Today that
# intersection is empty, so the endpoint returns zero rows plus an explicit
# `schema_note` naming exactly what is missing -- and it starts returning
# real rows the moment a failed state lands, with no code change here.
_CANDIDATE_FAILURE_STATES = ("failed", "provisioning_failed", "provisioning_error", "error")
_CANDIDATE_PENDING_STATES = ("pending", "provisioning", "pending_provisioning")

_ACCOUNT_STATUS_VALUES = frozenset(member.value for member in AccountStatus)
_ACCOUNT_FAILURE_STATES = tuple(
    state for state in _CANDIDATE_FAILURE_STATES if state in _ACCOUNT_STATUS_VALUES
)
_ACCOUNT_PENDING_STATES = tuple(
    state for state in _CANDIDATE_PENDING_STATES if state in _ACCOUNT_STATUS_VALUES
)

# `domains` has no status column, so there is nothing to intersect against
# and no query to build. Kept as named empty tuples rather than inlined
# `False` so the two resource types read identically below and a future
# domains.status migration only has to change these two lines.
_DOMAIN_FAILURE_STATES: tuple = ()
_DOMAIN_PENDING_STATES: tuple = ()

_RESOURCE_TYPES = ("mailbox", "domain")

_PROVISIONING_SCHEMA_NOTE = (
    "This mail server has no provisioning state machine: email_accounts.status is "
    "enum('active','inactive','suspended') with no failed or pending member, `domains` has no "
    "status column at all, and neither table has an error or attempt-count column. The state "
    "machine lives in mailyte-api (03-mailyte-api/phase-02); until it is mirrored into this "
    "schema the queue is structurally empty and `error`/`attempts` are always null. Read the "
    "authoritative failure list from mailyte-api, not from here."
)


def _provisioning_row(row: dict) -> dict:
    """One queue row for the wire.

    `error` and `attempts` are unconditionally null: no column holds either
    (see the block comment above). They are still present in the shape so
    the console can render the columns phase-02 SS2.7 asks for and show
    them as unavailable, rather than the field silently not existing.
    """
    return {
        "resource_type": row["resource_type"],
        "id": row["id"],
        "name": row["name"],
        "organization_id": row["organization_id"],
        "organization_name": row.get("organization_name"),
        "status": row["status"],
        "error": None,
        "attempts": None,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


@router.get(
    "/provisioning-failures",
    summary="Cross-tenant provisioning failure queue",
    description="Every mailbox and domain sitting in a failed provisioning state, across all "
    "tenants (phase-02 SS2.7) -- the proactive-support surface where staff see failures before "
    "customers report them. NOTE: this mail server's schema has no provisioning state machine "
    "(no failed status on email_accounts, no status column on domains, no error or attempt "
    "columns anywhere), so the queue is currently always empty and `error`/`attempts` are "
    "always null; `schema_note` on the response says so explicitly rather than letting an empty "
    "list read as 'no failures'.",
)
async def list_provisioning_failures(
    resource_type: str | None = Query(None, description="mailbox | domain"),
    organization_id: str | None = Query(None, description="Narrow to one tenant"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    if resource_type and resource_type not in _RESOURCE_TYPES:
        return JSONResponse(
            content=create_api_response(
                "error", f"resource_type must be one of {', '.join(_RESOURCE_TYPES)}"
            ),
            status_code=422,
        )

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    # One SELECT per resource type, UNION ALL'd so MySQL does the paging.
    # Merging two full result sets in Python and slicing would be correct
    # only while the queue is small, and this is exactly the screen that
    # gets opened when it is not.
    selects: list = []
    params: list = []

    if (not resource_type or resource_type == "mailbox") and _ACCOUNT_FAILURE_STATES:
        # The IN placeholders are generated from a module constant, never
        # from caller input -- the only bound values are the state strings
        # themselves and the optional org id.
        placeholders = ", ".join(["%s"] * len(_ACCOUNT_FAILURE_STATES))
        clause = f"WHERE a.status IN ({placeholders})"
        params.extend(_ACCOUNT_FAILURE_STATES)
        if organization_id:
            clause += " AND a.organization_id = %s"
            params.append(organization_id)
        selects.append(
            f"""
            SELECT 'mailbox' AS resource_type, a.id AS id, a.email AS name,
                   a.organization_id AS organization_id, o.name AS organization_name,
                   a.status AS status, a.created_at AS created_at, a.updated_at AS updated_at
            FROM email_accounts a
            LEFT JOIN organizations o ON o.id = a.organization_id
            {clause}
            """
        )

    if (not resource_type or resource_type == "domain") and _DOMAIN_FAILURE_STATES:
        placeholders = ", ".join(["%s"] * len(_DOMAIN_FAILURE_STATES))
        clause = f"WHERE d.status IN ({placeholders})"
        params.extend(_DOMAIN_FAILURE_STATES)
        if organization_id:
            clause += " AND d.organization_id = %s"
            params.append(organization_id)
        selects.append(
            f"""
            SELECT 'domain' AS resource_type, d.id AS id, d.domain AS name,
                   d.organization_id AS organization_id, o.name AS organization_name,
                   d.status AS status, d.created_at AS created_at, d.updated_at AS updated_at
            FROM domains d
            LEFT JOIN organizations o ON o.id = d.organization_id
            {clause}
            """
        )

    # No queryable failure state exists -- do not run a `status IN ()`,
    # which is not valid SQL, and do not pretend the answer is "none".
    if not selects:
        return create_api_response(
            "success",
            "No provisioning failure state exists in this schema",
            {
                "items": [],
                "pagination": _paginate(page, per_page, 0),
                "schema_note": _PROVISIONING_SCHEMA_NOTE,
            },
        )

    union_sql = " UNION ALL ".join(f"({select})" for select in selects)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM ({union_sql}) AS failures", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"{union_sql} ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "Provisioning failures retrieved",
            {
                "items": [_provisioning_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
                "schema_note": _PROVISIONING_SCHEMA_NOTE,
            },
        )
    except Exception as exc:
        logger.error(f"List provisioning failures failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve provisioning failures"),
            status_code=500,
        )
    finally:
        conn.close()


class ProvisioningRetryRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        description="Why this resource is being retried. Recorded by the audit middleware "
        "(phase-02 cross-cutting: destructive and corrective actions must carry a reason).",
    )


@router.post(
    "/provisioning-failures/{resource_type}/{resource_id}/retry",
    summary="Retry a failed provisioning attempt",
    description="Resets one mailbox or domain to its pending provisioning state so the normal "
    "provisioning path picks it up again (phase-02 SS2.7). Returns 501 on this schema: there is "
    "no pending/provisioning state to reset to -- see GET /provisioning-failures. An honest 501 "
    "beats a button that reports success and does nothing.",
)
async def retry_provisioning_failure(
    resource_type: str,
    resource_id: str,
    body: ProvisioningRetryRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    if resource_type not in _RESOURCE_TYPES:
        return JSONResponse(
            content=create_api_response(
                "error", f"resource_type must be one of {', '.join(_RESOURCE_TYPES)}"
            ),
            status_code=422,
        )

    if resource_type == "mailbox":
        table, id_column, pending_states = "email_accounts", "id", _ACCOUNT_PENDING_STATES
    else:
        table, id_column, pending_states = "domains", "id", _DOMAIN_PENDING_STATES

    # 501, not 200-with-a-shrug and not 500: the request is well-formed and
    # the caller is authorised -- the server genuinely cannot perform it,
    # and the message names precisely what is missing so whoever hits this
    # knows what has to be built rather than filing a bug against the
    # console.
    if not pending_states:
        missing = (
            "email_accounts.status has no 'pending'/'provisioning' member "
            "(enum is 'active','inactive','suspended')"
            if resource_type == "mailbox"
            else "`domains` has no status column at all"
        )
        logger.warning(
            f"Provisioning retry unavailable: type={resource_type} id={resource_id} "
            f"by={ctx['operator_id']}"
        )
        return JSONResponse(
            content=create_api_response(
                "error",
                f"Provisioning retry is not implemented on this mail server: {missing}, and "
                "neither table records an error or attempt count. The provisioning state "
                "machine lives in mailyte-api (03-mailyte-api/phase-02) -- retry there, or "
                "mirror the state machine into this schema first.",
            ),
            status_code=501,
        )

    # Reachable only once a pending state exists. Reset is deliberately
    # conditional on the row still being in a failure state: two operators
    # clicking retry on the same row must not bounce an already-recovering
    # resource back to pending.
    failure_states = (
        _ACCOUNT_FAILURE_STATES if resource_type == "mailbox" else _DOMAIN_FAILURE_STATES
    )
    failure_placeholders = ", ".join(["%s"] * len(failure_states))

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT {id_column} FROM {table} WHERE {id_column} = %s", (resource_id,))
        if not cursor.fetchone():
            cursor.close()
            return JSONResponse(
                content=create_api_response("error", "Resource not found"), status_code=404
            )

        cursor.execute(
            f"UPDATE {table} SET status = %s, updated_at = NOW() "
            f"WHERE {id_column} = %s AND status IN ({failure_placeholders})",
            [pending_states[0], resource_id, *failure_states],
        )
        reset = cursor.rowcount
        conn.commit()
        cursor.close()

        if not reset:
            return JSONResponse(
                content=create_api_response(
                    "error", "Resource is not in a failed provisioning state"
                ),
                status_code=409,
            )

        logger.warning(
            f"Provisioning retry queued: type={resource_type} id={resource_id} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            "Provisioning retry queued",
            {
                "resource_type": resource_type,
                "id": resource_id,
                "status": pending_states[0],
            },
        )
    except Exception as exc:
        logger.error(f"Retry provisioning failed: {exc}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retry provisioning"), status_code=500
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GAP #7 -- GET /metrics/range (authenticated Prometheus range proxy)
# ---------------------------------------------------------------------------
#
# PRD SS12 gap #7 is explicitly "auth'd, allowlisted queries", and the
# allowlist is the entire security argument for this endpoint existing.
#
# Forwarding a caller-supplied PromQL string would make this a general
# purpose request forwarder into an unauthenticated internal service: the
# metrics backend has no auth of its own (docker-compose.yml runs Prometheus
# with no --web.config.file), sits on mailserver_network reachable from this
# container, and its query language can enumerate every series in the TSDB --
# including per-tenant labels from services this operator may not be
# entitled to see, and, via label matchers on `up`/`*_info`, the internal
# topology of the whole deployment. It is also a denial-of-service surface:
# one unbounded matcher over a 30-day retention window is enough to pin
# Prometheus. So the console does not send PromQL at all. It sends a NAME
# from the table below, and the server chooses the expression.
#
# Expressions here are written against what this stack actually exports
# (shared/metrics.py emits `<service>_<metric>` per scraped service, hence
# the __name__ regex matchers rather than a bare metric name -- there is no
# single cross-service metric to select). A name whose underlying exporter
# is not deployed returns an empty `series` list, which the console renders
# as "no data"; that is correct and is not an error.
_PROMQL_ALLOWLIST = {
    # shared/metrics.py::_add_system_metrics, emitted by every scraped
    # service under its own prefix (api_cpu_usage_percent, ...).
    "cpu_usage_percent": '{__name__=~".+_cpu_usage_percent"}',
    "memory_usage_percent": '{__name__=~".+_memory_usage_percent"}',
    "memory_usage_bytes": '{__name__=~".+_memory_usage_bytes"}',
    # postfix-exporter's queue gauge, the same series
    # monitoring/prometheus/rules/mail_alerts.yml alerts on.
    "mail_queue_depth": "postfix_queue_length",
    # shared/metrics.py exports request timings as a pre-computed summary
    # with quantile labels, not histogram buckets -- so this selects the
    # exported p95 directly rather than calling histogram_quantile(), which
    # would find no _bucket series to work with.
    "mail_delivery_latency_p95_seconds": (
        '{__name__=~".+_http_request_duration_seconds",quantile="0.95"}'
    ),
    "http_request_rate": 'sum by (job) (rate({__name__=~".+_http_requests_total"}[5m]))',
    "http_error_rate": 'sum by (job) (rate({__name__=~".+_http_errors_total"}[5m]))',
    # Prometheus' own per-target scrape health.
    "service_up": "up",
}

# No default. Prometheus is an optional part of this stack (it is absent
# from CE entirely -- ADR-004), so defaulting to http://prometheus:9090
# would turn "this deployment has no metrics backend" into an 8-second
# connection timeout on every panel load instead of an immediate, honest
# "unavailable". Set PROMETHEUS_URL on the api service to enable the panel.
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "").strip().rstrip("/")

_METRICS_RANGE_MAX_HOURS = 168  # 7 days; matches the console's longest selector
_METRICS_RANGE_TIMEOUT = 15


def _derive_step(hours: int) -> str:
    """A step that keeps any window under ~600 points.

    Prometheus refuses a query_range whose point count exceeds 11,000 and
    the console cannot draw more than a few hundred usefully, so the step is
    derived from the window rather than left to the caller when absent.
    """
    if hours <= 6:
        return "1m"
    if hours <= 24:
        return "5m"
    if hours <= 72:
        return "15m"
    return "1h"


@router.get(
    "/metrics/range",
    summary="Range query against the metrics backend",
    description="Authenticated, allowlisted proxy for Prometheus range queries (PRD SS12 gap #7). "
    "`query` names one of a fixed set of safe metrics -- arbitrary PromQL is rejected, never "
    "forwarded. Returns 404 when no metrics backend is configured or reachable, which the "
    "console renders as an unavailable panel rather than an error.",
)
async def metrics_range(
    query: str = Query(..., description="Allowlisted metric NAME, not PromQL"),
    hours: int = Query(24, ge=1, description=f"Window size, capped at {_METRICS_RANGE_MAX_HOURS}"),
    step: str | None = Query(
        None, description="Resolution, e.g. '5m'. Derived from hours if absent"
    ),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    expression = _PROMQL_ALLOWLIST.get(query)
    if expression is None:
        # 422 and not 400: the shape is valid, the value is not. The
        # permitted names are listed because the console is the only caller
        # and a mismatch is a wiring bug someone has to fix by name.
        return JSONResponse(
            content=create_api_response(
                "error",
                "Unknown metric name. Arbitrary PromQL is not accepted; permitted names: "
                + ", ".join(sorted(_PROMQL_ALLOWLIST)),
            ),
            status_code=422,
        )

    if not PROMETHEUS_URL:
        return JSONResponse(
            content=create_api_response(
                "error",
                "No metrics backend is configured on this instance. Set PROMETHEUS_URL on the "
                "api service (e.g. http://prometheus:9090) to enable metrics panels.",
            ),
            status_code=404,
        )

    hours = min(hours, _METRICS_RANGE_MAX_HOURS)
    end = datetime.now()
    start = end - timedelta(hours=hours)
    # A caller-supplied step is passed through as-is -- it lands in a query
    # STRING parameter Prometheus parses as a duration, not in the
    # expression, so a malformed value produces a 400 from Prometheus rather
    # than altering what is queried.
    resolved_step = step or _derive_step(hours)

    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={
                "query": expression,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": resolved_step,
            },
            timeout=_METRICS_RANGE_TIMEOUT,
        )
    except Exception as exc:
        # 404, not 502/500: from the console's point of view an unreachable
        # metrics backend and an absent one are the same fact -- this panel
        # cannot be drawn -- and the PRD asks for that to render as
        # "unavailable on this instance" rather than a red error state.
        logger.warning(f"Prometheus range query unreachable at {PROMETHEUS_URL}: {exc}")
        return JSONResponse(
            content=create_api_response(
                "error",
                "The metrics backend is not reachable from this service. Check that Prometheus "
                f"is running and that PROMETHEUS_URL ({PROMETHEUS_URL}) is correct.",
            ),
            status_code=404,
        )

    if response.status_code >= 400:
        logger.warning(
            f"Prometheus range query failed: HTTP {response.status_code} for name={query!r}"
        )
        return JSONResponse(
            content=create_api_response(
                "error",
                f"The metrics backend rejected the query (HTTP {response.status_code}). "
                "This is a server-side allowlist entry that needs fixing, not a client error.",
            ),
            status_code=502,
        )

    payload = response.json()
    series = []
    for result in (payload.get("data") or {}).get("result") or []:
        labels = result.get("metric") or {}
        # __name__ first, then job, then whatever identifies the series --
        # the console shows one line per series and needs a stable, human
        # label for the legend.
        name = labels.get("__name__") or query
        label = labels.get("job") or labels.get("instance") or name
        points = []
        for entry in result.get("values") or []:
            try:
                timestamp, raw_value = entry[0], entry[1]
                # Prometheus encodes sample values as strings, and NaN/Inf
                # are legal ones -- float() accepts both and json.dumps then
                # emits bare NaN, which is not valid JSON. Dropped instead.
                value = float(raw_value)
            except (TypeError, ValueError, IndexError):
                continue
            if value != value or value in (float("inf"), float("-inf")):
                continue
            points.append({"t": datetime.fromtimestamp(float(timestamp)).isoformat(), "v": value})
        series.append({"metric": name, "label": label, "points": points})

    return create_api_response(
        "success",
        "Metrics range retrieved",
        {
            "series": series,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": resolved_step,
        },
    )


# ---------------------------------------------------------------------------
# GAP #8c -- GET /logs (service log tail)
# ---------------------------------------------------------------------------
#
# Where this stack's logs actually are, established before writing anything:
#
#   * There is NO log table. 0001_baseline has audit_logs, mail_logs,
#     user_logins and template_render_log -- all domain records, none of
#     them application log lines.
#   * shared/logging_config.py writes to FILES. setup_logging() (what
#     worker/api/app.py calls) creates ./logs/application.log relative to
#     the process cwd, which is /app in the container. ServiceLogger, the
#     other entry point, writes ./logs/<service>/<service>.log plus
#     _errors.log and _performance.log siblings.
#   * docker-compose.yml bind-mounts each service's own directory onto its
#     own /app/logs (api gets ./logs/worker/api, webhooks gets
#     ./logs/worker/webhooks, and so on). Every container therefore sees its
#     OWN log files and no other service's.
#
# So this endpoint is implementable, but only for the log files this
# container can actually read -- which is the api service's own. It does NOT
# shell out to `docker logs`: that needs a Docker socket, and handing the API
# container the Docker socket to read logs would be a container-escape
# primitive traded for a convenience feature (phase-07 C1 removed exactly
# that mount from cert_manager for this reason).
#
# Requesting a service whose file is not present returns a 501 naming the
# mount that would make it readable, rather than an empty list that reads as
# "that service logged nothing".
#
# Redaction (conventions + phase-03 SS3.4) happens on every line before it
# leaves this process. shared/logging_config.RedactingFilter already scrubs
# at write time, but it only covers key=value shapes and only for records
# that went through a configured handler -- it is not a reason to skip
# scrubbing on read. A log tail is the single easiest place to exfiltrate a
# credential from a running system.

LOG_DIR = Path(os.getenv("MAILYTE_LOG_DIR", "logs"))

_LOG_LINES_DEFAULT = 500
_LOG_LINES_MAX = 2000
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Read at most this many bytes back from the end of a file. 2000 lines of
# this format is well under 1MB; the cap stops a single enormous line (a
# stack trace, a dumped payload) from pulling the whole file into memory.
_LOG_TAIL_MAX_BYTES = 4 * 1024 * 1024

_SECRET_PATTERNS = (
    # Header shapes. The auth-scheme word is consumed as part of the match
    # rather than kept: `Authorization: (\S+)` alone redacts the literal
    # "Bearer" and leaves the actual token in the response, which is the
    # exact opposite of the intent (caught by running this against a real
    # bearer line, not by reading the pattern).
    # The `"?` on either side of the separator is what makes these match the
    # JSON-ish shape a dumped headers dict logs -- {"Authorization": "Basic
    # ..."} -- as well as the raw header shape. Without it the quote breaks
    # the match and the credential sails straight through.
    (re.compile(r"(?i)(x-api-key\"?\s*[:=]\s*\"?)([^\s,;\"']+)"), r"\1***REDACTED***"),
    (
        re.compile(
            r"(?i)(authorization\"?\s*[:=]\s*\"?)(?:bearer|basic|token|digest)?\s*([^\s\"']+)"
        ),
        r"\1***REDACTED***",
    ),
    (re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"), r"\1***REDACTED***"),
    # JWTs: three dot-separated base64url segments. The generic long-run
    # patterns below never catch these, because the dots break every run
    # into sub-32-char pieces.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"), "***REDACTED***"),
    # key=value / key: value / "key": "value" shapes. No leading \b -- real
    # names here are compound (DB_PASSWORD, WEBHOOK_SECRET), and `_` is a
    # word character so \bpassword\b never matches inside DB_PASSWORD. Same
    # reasoning, and same bug, as RedactingFilter's own comment.
    (
        re.compile(
            r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)"
            r"(\"?\s*[:=]\s*\"?)([^\s,\"'}]+)"
        ),
        r"\1\2***REDACTED***",
    ),
    # Long opaque runs: hex/base64url blobs of this length are key material,
    # session tokens or hashes far more often than they are prose. Applied
    # last so the labelled cases above keep their more precise redaction.
    # 32 hex covers an MD5/SHA fingerprint and secrets.token_hex(16)+;
    # 40 base64url covers secrets.token_urlsafe(32) (43 chars), which is
    # what generate_session_token() produces. ULIDs (26 chars) sit under
    # both thresholds on purpose -- they are identifiers the operator needs
    # to read, not secrets.
    #
    # '/' is deliberately NOT in the base64 class. Standard base64 is
    # indistinguishable from a URL path at this length, and including it
    # redacted "/api/v1/domains/<ulid>" out of every request log line --
    # found by running this against real lines rather than by reading it.
    # The labelled patterns above cover the credentials that actually reach
    # these logs; a bare standard-base64 blob with no surrounding key name
    # is the one case this trades away.
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "***REDACTED***"),
    (re.compile(r"\b[A-Za-z0-9_-]{40,}={0,2}"), "***REDACTED***"),
)

# Both formats shared/logging_config.py produces:
#   setup_logging():  "TS - logger - LEVEL - [correlation] - message"
#                     (worker/api/app.py re-formats the root handlers to
#                      include the [correlation_id] segment -- phase-04 4.7)
#   ServiceLogger():  "TS | logger | LEVEL | module:line | message"
_LOG_LINE_PATTERNS = (
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+-\s+"
        r"(?P<name>[^-]+?)\s+-\s+(?P<level>[A-Z]+)\s+-\s+"
        r"(?:\[(?P<corr>[^\]]*)\]\s+-\s+)?(?P<msg>.*)$"
    ),
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s*\|\s*"
        r"(?P<name>[^|]+?)\s*\|\s*(?P<level>[A-Z]+)\s*\|\s*"
        r"(?:(?P<module>[^|]+?)\s*\|\s*)?(?P<msg>.*)$"
    ),
)


def _redact(line: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        line = pattern.sub(replacement, line)
    return line


def _discover_log_files() -> dict:
    """Map service name -> log file path, from what is actually on disk.

    The `service` query parameter is matched against these keys and never
    used to build a path -- a caller cannot reach `../../etc/passwd` or any
    file outside the log directory, because no caller-supplied string ever
    reaches the filesystem.
    """
    found: dict = {}
    if not LOG_DIR.is_dir():
        return found
    try:
        for entry in sorted(LOG_DIR.iterdir()):
            if entry.is_file() and entry.suffix == ".log":
                # ./logs/application.log -- setup_logging()'s output, which
                # for this container is the api service's own log.
                name = "api" if entry.stem == "application" else entry.stem
                found.setdefault(name, entry)
            elif entry.is_dir():
                # ./logs/<service>/<service>.log -- ServiceLogger's layout.
                primary = entry / f"{entry.name}.log"
                if primary.is_file():
                    found.setdefault(entry.name, primary)
    except OSError as exc:
        logger.warning(f"Log directory {LOG_DIR} is not readable: {exc}")
    return found


def _tail_lines(path: Path, limit: int) -> tuple:
    """Return (lines, truncated). Reads from the end rather than the start:
    the interesting end of a rotating 10MB log is the last few hundred
    lines, and streaming the whole file to reach them wastes the memory this
    endpoint is most likely to be called under pressure."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        read_bytes = min(size, _LOG_TAIL_MAX_BYTES)
        handle.seek(size - read_bytes)
        chunk = handle.read(read_bytes)

    text = chunk.decode("utf-8", errors="replace")
    if read_bytes < size:
        # The first line of the chunk is almost certainly a partial line.
        text = text.split("\n", 1)[-1]
    lines = [line for line in text.splitlines() if line.strip()]
    truncated = read_bytes < size or len(lines) > limit
    return lines[-limit:], truncated


def _parse_log_line(raw: str, service: str) -> dict:
    for pattern in _LOG_LINE_PATTERNS:
        match = pattern.match(raw)
        if match:
            groups = match.groupdict()
            return {
                "timestamp": groups["ts"].replace(",", "."),
                "service": service,
                "level": groups["level"],
                "message": groups["msg"],
                "correlation_id": (groups.get("corr") or None) or None,
            }
    # Continuation lines (tracebacks) and anything else unparseable are
    # returned verbatim rather than dropped -- a traceback with its first
    # line missing is worse than useless.
    return {
        "timestamp": None,
        "service": service,
        "level": None,
        "message": raw,
        "correlation_id": None,
    }


@router.get(
    "/logs",
    summary="Tail this service's log",
    description="Reads the log files this container can actually see, filtered by service, "
    "level, correlation id, free text and time, with secrets redacted server-side before "
    "the response is built (PRD SS12 gap #8c). Returns 501 -- naming the missing mount -- for "
    "a service whose log file is not reachable from here, rather than an empty list that "
    "would read as 'that service logged nothing'.",
)
async def tail_logs(
    service: str | None = Query(None, description="Service name; defaults to every readable log"),
    level: str | None = Query(None, description="Minimum level: DEBUG|INFO|WARNING|ERROR|CRITICAL"),
    q: str | None = Query(None, description="Case-insensitive substring match on the message"),
    correlation_id: str | None = Query(
        None, description="Exact correlation id from X-Correlation-Id"
    ),
    since: str | None = Query(None, description="ISO8601 date or datetime lower bound"),
    lines: int = Query(
        _LOG_LINES_DEFAULT, ge=1, description=f"Max lines, hard cap {_LOG_LINES_MAX}"
    ),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    lines = min(lines, _LOG_LINES_MAX)

    if level and level.upper() not in _LOG_LEVELS:
        return JSONResponse(
            content=create_api_response("error", f"level must be one of {', '.join(_LOG_LEVELS)}"),
            status_code=422,
        )

    since_dt = None
    if since:
        try:
            since_dt = _parse_iso_datetime(since)
        except ValueError:
            return JSONResponse(
                content=create_api_response("error", "since must be an ISO8601 date or datetime"),
                status_code=422,
            )

    available = _discover_log_files()

    if not available:
        return JSONResponse(
            content=create_api_response(
                "error",
                f"No log files are readable from this service. Expected them under "
                f"{LOG_DIR}/ (shared/logging_config.py writes ./logs/application.log and "
                "./logs/<service>/<service>.log). If this deployment ships container output "
                "to stdout only, the log tail cannot work from inside the API container -- "
                "bind-mount a log directory onto /app/logs for the api service (as "
                "docker-compose.yml already does with ./logs/worker/api), or read the logs "
                "with `docker compose logs <service>` on the host.",
            ),
            status_code=501,
        )

    if service:
        if service not in available:
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"No log file for service '{service}' is reachable from the api container. "
                    "Each service bind-mounts only its OWN ./logs/<service> directory onto its "
                    "own /app/logs (docker-compose.yml), so this container can read: "
                    f"{', '.join(sorted(available))}. To tail another service from here, mount "
                    f"its log directory into the api container as well, or use "
                    f"`docker compose logs {service}` on the host.",
                ),
                status_code=501,
            )
        selected = {service: available[service]}
    else:
        selected = available

    min_rank = _LOG_LEVELS.index(level.upper()) if level else 0
    needle = q.lower() if q else None

    collected: list = []
    truncated = False
    for name, path in selected.items():
        try:
            raw_lines, was_truncated = _tail_lines(path, lines)
        except OSError as exc:
            logger.warning(f"Could not read log file {path}: {exc}")
            continue
        truncated = truncated or was_truncated

        for raw in raw_lines:
            entry = _parse_log_line(raw, name)

            if level and (
                entry["level"] not in _LOG_LEVELS or _LOG_LEVELS.index(entry["level"]) < min_rank
            ):
                continue
            if correlation_id and entry["correlation_id"] != correlation_id:
                continue
            if since_dt and entry["timestamp"]:
                try:
                    if datetime.fromisoformat(entry["timestamp"]) < since_dt:
                        continue
                except ValueError:
                    pass
            if needle and needle not in entry["message"].lower():
                continue

            # Redaction is the last thing before the entry is kept, and it
            # covers the message only after filtering -- so a `q` search for
            # a token still cannot be used to confirm that token's value
            # from the response, because the response never carries it.
            entry["message"] = _redact(entry["message"])
            collected.append(entry)

    # Sorted newest-last across files. Unparseable lines (no timestamp) keep
    # their position relative to the line above by sorting as empty string
    # only after everything timestamped -- so they are not silently reordered
    # away from the traceback they belong to when a single file is selected.
    if len(selected) > 1:
        collected.sort(key=lambda entry: entry["timestamp"] or "")

    if len(collected) > lines:
        collected = collected[-lines:]
        truncated = True

    return create_api_response(
        "success",
        "Log lines retrieved",
        {
            "lines": collected,
            "truncated": truncated,
            "source": f"file:{LOG_DIR}",
            "available_services": sorted(available),
        },
    )


# ---------------------------------------------------------------------------
# GAP #2 -- platform-wide analytics rollups (PRD SS6, SS12 gap #2)
# ---------------------------------------------------------------------------
#
# What existed before this: worker/analytics/app.py, which answers
# /email-volume, /engagement and /deliverability for ONE domain at a time,
# resolved by name. The console's analytics screens are platform-wide -- "how
# much mail did the estate send last quarter", not "how much did acme.com
# send" -- and fanning the per-domain endpoint out across every hosted domain
# would be one query per domain per panel. These three endpoints answer the
# platform question directly, one grouped query per result set.
#
# DIRECTION. `mail_logs` has NO direction column (verified against
# 0001_baseline, the mysqldump of the live DB). analytics/app.py's
# get_email_volume derives it from which side of the message carries the
# domain being asked about -- `SUM(sender LIKE '%@acme.com')` is outbound,
# `SUM(recipient LIKE '%@acme.com')` is inbound. The platform-wide
# generalisation of "is this address on the domain I asked about" is "is this
# address on ANY domain this server hosts", which is the fragment GET
# /overview's volume_24h block already uses:
#
#     SUBSTRING_INDEX(sender, '@', -1) IN (SELECT domain FROM domains)
#
# Reused verbatim below rather than invented afresh. Two screens disagreeing
# about what "sent" means is worse than either definition being imperfect.
# The known imperfection: a local-to-local message is BOTH outbound and
# inbound and is counted in `sent` and `received` alike -- same as
# /overview, same as analytics/app.py.
#
# One deliberate divergence from /overview's volume_24h, called out here so
# nobody reads it as a bug: volume_24h counts bounced/deferred regardless of
# direction. These endpoints scope delivered/bounced/deferred to OUTBOUND,
# because they feed `delivery_rate` and PRD SS6 defines that as a sender-side
# deliverability measure (delivered / (delivered+bounced+deferred)). Folding
# in bounces we generated while accepting inbound mail would make that number
# mean nothing.
#
# TIMEZONE. Bucketing uses MySQL's DATE()/HOUR()/WEEKDAY(), which read the
# connection's session timezone -- i.e. the server's. PRD SS6 lists a
# timezone control on the analytics screens; that is a display-side concern
# and is not implemented here, so the console must label these buckets as
# server time rather than the operator's.

# One `IN (SELECT ...)` against domains.domain, which is UNIQUE and therefore
# materialised once and probed by index rather than re-run per row.
_OUTBOUND_SQL = "SUBSTRING_INDEX(sender, '@', -1) IN (SELECT domain FROM domains)"
_INBOUND_SQL = "SUBSTRING_INDEX(recipient, '@', -1) IN (SELECT domain FROM domains)"

_GRANULARITIES = ("hour", "day", "week", "month")

# Only these two columns may ever be bucketed. Neither is caller-supplied --
# the whitelist exists so a future call site cannot turn _bucket_expr() into
# an injection point by passing a column name through from a query param.
_BUCKET_COLUMNS = ("timestamp", "created_at")

# A 366-day window at hourly granularity is 8784 points -- a response no
# chart can draw and no browser should have to parse. Rejected with a 422
# naming the fix rather than silently coarsening the granularity or
# truncating the window, either of which would misreport the data.
_ANALYTICS_MAX_BUCKETS = 4000

_ANALYTICS_DOMAIN_LIMIT = 100  # PRD SS6 league tables; ordered by sent desc

# Mail-log league table and heatmap only make sense for domains we host, so
# both join `domains` rather than filtering on a LIKE.


class _BucketWindowTooLarge(ValueError):
    """days x granularity would produce more buckets than we will serialise."""


def _bucket_expr(column: str, granularity: str, alias: str = "") -> str:
    """SQL that truncates `column` to the start of its `granularity` bucket.

    Deliberately built from DATE()/HOUR()/WEEKDAY()/DAYOFMONTH() and INTERVAL
    arithmetic rather than DATE_FORMAT()/YEARWEEK(): mysql-connector
    %-formats the query text whenever params are supplied, so a literal '%Y'
    inside the SQL blows up on substitution (the same reason the /overview
    security block avoids DATE_FORMAT). These all return a DATE or DATETIME,
    which the driver hands back as a real date object -- no string parsing on
    the way out, and the week bucket lands on Monday, matching WEEKDAY().
    """
    if column not in _BUCKET_COLUMNS:
        raise ValueError(f"Refusing to bucket unwhitelisted column '{column}'")
    if granularity not in _GRANULARITIES:
        raise ValueError(f"Unknown granularity '{granularity}'")
    if alias and not alias.isalpha():
        raise ValueError(f"Refusing to build SQL with table alias '{alias}'")
    col = f"{alias}.`{column}`" if alias else f"`{column}`"
    if granularity == "hour":
        return f"DATE_ADD(DATE({col}), INTERVAL HOUR({col}) HOUR)"
    if granularity == "day":
        return f"DATE({col})"
    if granularity == "week":
        return f"(DATE({col}) - INTERVAL WEEKDAY({col}) DAY)"
    return f"(DATE({col}) - INTERVAL (DAYOFMONTH({col}) - 1) DAY)"


def _floor_bucket(value: datetime, granularity: str) -> datetime:
    """Python twin of _bucket_expr -- must agree with it exactly, or the
    zero-fill below would file real rows under buckets the series does not
    contain and silently drop them."""
    if granularity == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "day":
        return midnight
    if granularity == "week":
        # weekday() is 0=Monday, exactly what MySQL's WEEKDAY() returns.
        return midnight - timedelta(days=midnight.weekday())
    return midnight.replace(day=1)


def _next_bucket(value: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return value + timedelta(hours=1)
    if granularity == "day":
        return value + timedelta(days=1)
    if granularity == "week":
        return value + timedelta(days=7)
    # Land on the 28th first so this is correct in February and in leap years.
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def _bucket_list(start: datetime, end: datetime, granularity: str) -> list:
    """Every bucket between start and end inclusive, oldest first.

    Generated in Python rather than read off the rows so a period with no
    mail still appears as an explicit zero. A missing point is not a gap to a
    line chart -- it is interpolated straight across, which turns an outage
    into a smooth line at the wrong height.
    """
    cursor = _floor_bucket(start, granularity)
    last = _floor_bucket(end, granularity)
    buckets: list = []
    while cursor <= last:
        buckets.append(cursor)
        if len(buckets) > _ANALYTICS_MAX_BUCKETS:
            raise _BucketWindowTooLarge(
                f"That window and granularity produce more than "
                f"{_ANALYTICS_MAX_BUCKETS} buckets. Use a coarser granularity "
                f"or a shorter `days`."
            )
        cursor = _next_bucket(cursor, granularity)
    return buckets


def _bucket_key(value):
    """Normalise whatever the driver returned for a bucket column to a
    datetime, so it can be looked up against _bucket_list()'s keys. DATE()
    comes back as datetime.date, DATE_ADD(...) as datetime.datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return value


def _rate(numerator: int, denominator: int) -> float:
    """Percentage to 2dp, matching worker/analytics/app.py's rate fields
    (open_rate, click_rate) -- the console must not have to remember which
    endpoint returns 0-1 and which returns 0-100."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _too_large(exc: _BucketWindowTooLarge) -> JSONResponse:
    # 422 and not 400: the shape is valid, the combination of values is not.
    return JSONResponse(content=create_api_response("error", str(exc)), status_code=422)


@router.get(
    "/analytics/overview",
    summary="Platform-wide mail volume and deliverability rollup",
    description="Estate-wide send/receive volume, deliverability and a per-domain league table "
    "over an arbitrary window (PRD SS6, SS12 gap #2). Outbound vs inbound is derived the same way "
    "worker/analytics/app.py does it -- mail_logs has no direction column, so a message whose "
    "SENDER is on a hosted domain counts as sent and one whose RECIPIENT is counts as received; "
    "a local-to-local message counts as both. `sent` is total outbound volume in the bucket "
    "whatever its status; delivered/bounced/deferred are outbound-only too, so that "
    "`delivery_rate` (delivered / (delivered+bounced+deferred), PRD SS6) means sender-side "
    "deliverability. `complaint_rate` is feedback-loop complaints / `sent` for that same domain "
    "and window -- NOT the SES-style complaints/delivered, chosen so a domain with complaints but "
    "no delivery confirmations still shows a non-zero rate. Both rates are percentages (0-100) to "
    "2dp. The series is zero-filled: every bucket in "
    "the range is present even when nothing happened in it. `complaint_rate` is null -- never 0 -- "
    "when the feedback-loop table could not be read; 0.0 means it was read and held no complaints. "
    "Buckets are in SERVER time.",
)
async def analytics_overview(
    days: int = Query(30, ge=1, description="Window size in days, capped at 366"),
    granularity: str = Query("day", description="One of hour|day|week|month"),
    organization_id: str | None = Query(
        None, description="Narrow every panel to one tenant. Platform scope sees all by default."
    ),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    if granularity not in _GRANULARITIES:
        return JSONResponse(
            content=create_api_response(
                "error", "granularity must be one of: " + ", ".join(_GRANULARITIES)
            ),
            status_code=422,
        )
    days = min(days, 366)
    end = datetime.now()
    start = _floor_bucket(end - timedelta(days=days), granularity)

    try:
        buckets = _bucket_list(start, end, granularity)
    except _BucketWindowTooLarge as exc:
        return _too_large(exc)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    bucket_sql = _bucket_expr("timestamp", granularity, alias="ml")
    # Narrowing rides on mail_logs.organization_id (indexed as fk_mail_logs_org)
    # in BOTH queries below rather than on domains.organization_id in one and
    # mail_logs in the other -- otherwise the league table could sum to more
    # than the totals it sits underneath.
    org_clause = ""
    org_params: list = []
    if organization_id:
        org_clause = " AND ml.organization_id = %s"
        org_params = [organization_id]

    try:
        cursor = conn.cursor(dictionary=True)
        try:
            # Query 1/3 -- the whole time series in one pass. The range
            # predicate on `timestamp` is what idx_mail_logs_timestamp (and
            # idx_mail_logs_time_status) exists for; nothing here filters on
            # an unindexed column.
            cursor.execute(
                f"""
                SELECT {bucket_sql} AS bucket,
                       SUM(CASE WHEN {_OUTBOUND_SQL} THEN 1 ELSE 0 END) AS sent,
                       SUM(CASE WHEN {_OUTBOUND_SQL} AND status = 'delivered'
                                THEN 1 ELSE 0 END) AS delivered,
                       SUM(CASE WHEN {_OUTBOUND_SQL} AND status = 'bounced'
                                THEN 1 ELSE 0 END) AS bounced,
                       SUM(CASE WHEN {_OUTBOUND_SQL} AND status = 'deferred'
                                THEN 1 ELSE 0 END) AS deferred,
                       SUM(CASE WHEN {_INBOUND_SQL} THEN 1 ELSE 0 END) AS received
                FROM mail_logs ml
                WHERE ml.`timestamp` >= %s AND ml.`timestamp` < %s{org_clause}
                GROUP BY bucket
                """,
                tuple([start, end] + org_params),
            )
            by_bucket = {_bucket_key(row["bucket"]): row for row in cursor.fetchall()}

            # Query 2/3 -- the per-domain league table. ONE grouped query,
            # not one query per domain: the JOIN onto domains.domain (UNIQUE,
            # so an index lookup) both restricts to hosted senders and
            # supplies the authoritative owning org, which mail_logs'
            # nullable organization_id cannot be trusted for.
            cursor.execute(
                f"""
                SELECT d.domain AS domain,
                       d.organization_id AS organization_id,
                       COUNT(*) AS sent,
                       SUM(CASE WHEN ml.status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
                       SUM(CASE WHEN ml.status = 'bounced' THEN 1 ELSE 0 END) AS bounced,
                       SUM(CASE WHEN ml.status = 'deferred' THEN 1 ELSE 0 END) AS deferred
                FROM mail_logs ml
                JOIN domains d ON d.domain = SUBSTRING_INDEX(ml.sender, '@', -1)
                WHERE ml.`timestamp` >= %s AND ml.`timestamp` < %s{org_clause}
                GROUP BY d.domain, d.organization_id
                ORDER BY sent DESC
                LIMIT {_ANALYTICS_DOMAIN_LIMIT}
                """,
                tuple([start, end] + org_params),
            )
            domain_rows = cursor.fetchall() or []

            # Query 3/3 -- complaints per sending domain, joined in Python on
            # the domain string. feedback_loops.sender is the ORIGINAL
            # message's sender (original_recipient is the person who
            # complained), so grouping it by domain is the complaint count
            # for that sender domain. Degrades to null rather than 0 if it
            # fails: "no complaints" and "we could not tell" are different
            # facts and an operator will act on them differently.
            complaints_by_domain = None
            try:
                fbl_org_clause = " AND organization_id = %s" if organization_id else ""
                cursor.execute(
                    f"""
                    SELECT SUBSTRING_INDEX(sender, '@', -1) AS domain,
                           COUNT(*) AS complaints
                    FROM feedback_loops
                    WHERE created_at >= %s AND created_at < %s
                      AND sender IS NOT NULL{fbl_org_clause}
                    GROUP BY domain
                    """,
                    tuple([start, end] + org_params),
                )
                complaints_by_domain = {
                    row["domain"]: _as_int(row["complaints"]) for row in cursor.fetchall()
                }
            except Exception as exc:
                logger.warning(f"platform analytics: complaint_rate unavailable: {exc}")
        finally:
            cursor.close()
    finally:
        conn.close()

    series = []
    totals = {"sent": 0, "delivered": 0, "bounced": 0, "deferred": 0, "received": 0}
    for bucket in buckets:
        row = by_bucket.get(bucket) or {}
        point = {"bucket": bucket.isoformat()}
        for field in ("sent", "delivered", "bounced", "deferred", "received"):
            value = _as_int(row.get(field))
            point[field] = value
            totals[field] += value
        series.append(point)

    # PRD SS6: deliverability rate is delivered / (delivered + bounced +
    # deferred) -- attempted deliveries with a known outcome, not everything
    # that entered the log (a message still 'queued' has no outcome yet and
    # must not drag the rate down).
    totals["delivery_rate"] = _rate(
        totals["delivered"], totals["delivered"] + totals["bounced"] + totals["deferred"]
    )

    domains = []
    for row in domain_rows:
        sent = _as_int(row["sent"])
        delivered = _as_int(row["delivered"])
        bounced = _as_int(row["bounced"])
        deferred = _as_int(row["deferred"])
        complaint_rate = None
        if complaints_by_domain is not None:
            complaint_rate = _rate(complaints_by_domain.get(row["domain"], 0), sent)
        domains.append(
            {
                "domain": row["domain"],
                "organization_id": row["organization_id"],
                "sent": sent,
                "delivered": delivered,
                "bounced": bounced,
                "delivery_rate": _rate(delivered, delivered + bounced + deferred),
                "complaint_rate": complaint_rate,
            }
        )

    return create_api_response(
        "success",
        "Platform analytics overview retrieved",
        {
            "range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "granularity": granularity,
            },
            "totals": totals,
            "series": series,
            "domains": domains,
        },
    )


@router.get(
    "/analytics/growth",
    summary="Cumulative estate growth curve",
    description="Organizations, domains and mailboxes that EXISTED as of each bucket -- a "
    "cumulative estate curve, not new-signups-per-period (PRD SS6 'Growth'). Derived from the "
    "created_at columns on organizations/domains/email_accounts. `storage_bytes` is the one "
    "series this schema cannot backfill: nothing in this repo has ever written a "
    "usage_type='storage_quota' row to usage_history, and storage_usage has no writer either, so "
    "there is no historical storage measurement to read. It is therefore reported as the CURRENT "
    "SUM(email_accounts.storage_used) on the final bucket and NULL on every earlier one. A flat "
    "line would read as 'storage is not growing', which is a claim this data cannot support.",
)
async def analytics_growth(
    days: int = Query(90, ge=1, description="Window size in days, capped at 730"),
    granularity: str = Query("day", description="One of hour|day|week|month"),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    if granularity not in _GRANULARITIES:
        return JSONResponse(
            content=create_api_response(
                "error", "granularity must be one of: " + ", ".join(_GRANULARITIES)
            ),
            status_code=422,
        )
    days = min(days, 730)
    end = datetime.now()
    start = _floor_bucket(end - timedelta(days=days), granularity)

    try:
        buckets = _bucket_list(start, end, granularity)
    except _BucketWindowTooLarge as exc:
        return _too_large(exc)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    def _cumulative(cursor, table: str) -> dict:
        """One query per table: rows created before the window collapse into
        a single NULL bucket (the opening balance), rows inside it group
        normally. Python then runs the cumulative sum. The alternative -- a
        correlated COUNT(*) per bucket -- is one query per point."""
        bucket_sql = _bucket_expr("created_at", granularity)
        cursor.execute(
            f"""
            SELECT CASE WHEN created_at < %s THEN NULL ELSE {bucket_sql} END AS bucket,
                   COUNT(*) AS n
            FROM {table}
            WHERE created_at < %s
            GROUP BY bucket
            """,
            (start, end),
        )
        counts: dict = {}
        for row in cursor.fetchall():
            counts[_bucket_key(row["bucket"])] = _as_int(row["n"])
        return counts

    try:
        cursor = conn.cursor(dictionary=True)
        try:
            # Table names are literals from this function, never caller
            # input. All three tables are small (orgs/domains in the
            # hundreds, mailboxes in the thousands), so a full scan is the
            # right plan here and no index hint is worth writing.
            per_kind = {
                "organizations": _cumulative(cursor, "organizations"),
                "domains": _cumulative(cursor, "domains"),
                "mailboxes": _cumulative(cursor, "email_accounts"),
            }
            # 4th and last query: the current storage total. See the endpoint
            # description for why this is a point value and not a series.
            cursor.execute(
                "SELECT COALESCE(SUM(storage_used), 0) AS used_bytes FROM email_accounts"
            )
            storage_bytes = _as_int((cursor.fetchone() or {}).get("used_bytes"))
        finally:
            cursor.close()
    finally:
        conn.close()

    running = {kind: counts.get(None, 0) for kind, counts in per_kind.items()}
    series = []
    for index, bucket in enumerate(buckets):
        for kind, counts in per_kind.items():
            running[kind] += counts.get(bucket, 0)
        series.append(
            {
                "bucket": bucket.isoformat(),
                "organizations": running["organizations"],
                "domains": running["domains"],
                "mailboxes": running["mailboxes"],
                "storage_bytes": storage_bytes if index == len(buckets) - 1 else None,
            }
        )

    return create_api_response(
        "success",
        "Platform growth series retrieved",
        {
            "series": series,
            "totals": {
                "organizations": running["organizations"],
                "domains": running["domains"],
                "mailboxes": running["mailboxes"],
                "storage_bytes": storage_bytes,
            },
        },
    )


@router.get(
    "/analytics/heatmap",
    summary="Outbound send pattern by hour of week",
    description="Outbound message count per (weekday, hour) over the window -- PRD SS6's capacity "
    "planning heatmap. WEEKDAY 0 IS MONDAY and 6 IS SUNDAY: this uses MySQL's WEEKDAY(), not "
    "DAYOFWEEK() (which is 1=Sunday..7=Saturday), so label the axis Mon-Sun. All 168 cells are "
    "always returned, zero-filled, so the grid never has holes in it. `max` is the largest cell "
    "count and is what the colour scale should be normalised against; it is 0 when the window "
    "held no outbound mail. Outbound is derived the same way /analytics/overview derives it. "
    "Hours are in SERVER time.",
)
async def analytics_heatmap(
    days: int = Query(30, ge=1, description="Window size in days, capped at 366"),
    organization_id: str | None = Query(None, description="Narrow to one tenant"),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    days = min(days, 366)
    end = datetime.now()
    start = end - timedelta(days=days)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    org_clause = ""
    params: list = [start, end]
    if organization_id:
        org_clause = " AND organization_id = %s"
        params.append(organization_id)

    try:
        cursor = conn.cursor(dictionary=True)
        try:
            # One grouped query for all 168 cells. Range predicate on
            # `timestamp` uses idx_mail_logs_timestamp.
            cursor.execute(
                f"""
                SELECT WEEKDAY(`timestamp`) AS weekday,
                       HOUR(`timestamp`) AS hour,
                       COUNT(*) AS count
                FROM mail_logs
                WHERE `timestamp` >= %s AND `timestamp` < %s
                  AND {_OUTBOUND_SQL}{org_clause}
                GROUP BY weekday, hour
                """,
                tuple(params),
            )
            counts = {
                (int(row["weekday"]), int(row["hour"])): _as_int(row["count"])
                for row in cursor.fetchall()
            }
        finally:
            cursor.close()
    finally:
        conn.close()

    cells = [
        {"weekday": weekday, "hour": hour, "count": counts.get((weekday, hour), 0)}
        for weekday in range(7)
        for hour in range(24)
    ]

    return create_api_response(
        "success",
        "Outbound send heatmap retrieved",
        {"cells": cells, "max": max(counts.values()) if counts else 0},
    )


# ---------------------------------------------------------------------------
# GAP #6 -- /alerts (rules, channels, history)
# ---------------------------------------------------------------------------
#
# Three resources behind one prefix:
#
#   /alerts/rules     -- "fire when <metric> <comparator> <threshold> holds
#                        for <for_seconds>". Stored here, evaluated by
#                        whatever runs them on a schedule; this file owns
#                        the definition and the ONE-SHOT evaluator that lets
#                        an operator test a rule before trusting it.
#   /alerts/channels  -- where a firing goes. v1 is email + generic webhook
#                        (PRD SS17), nothing else.
#   /alerts/history   -- what actually fired, read-only.
#
# `metric` is an ALLOWLIST, not a free-text field. Every permitted value maps
# to a `_block_*` function above -- the same function GET /overview renders --
# so a rule can only ever be written against a number the platform actually
# computes, and it computes it exactly once. A free-form metric string would
# be either dead config (nothing evaluates it) or an injection surface
# (something interpolates it into SQL/PromQL); the allowlist is what makes it
# neither.
#
# NOTE ON THE EXISTING `alerts` TABLE: it is not this. `alerts`
# (0001_baseline) is a fired rate-limit/storage-quota notice written by the
# rate_limiter and storage_usage workers -- alert_type ENUM
# ('rate_limit','storage_quota'), alert_level ENUM
# ('warning','critical','exceeded'), current_usage/limit_value/
# usage_percentage, webhook_sent/webhook_attempts, resolved/resolved_at,
# scoped to organization_id/domain_id/email_account_id. It has no rule, no
# comparator, no channel, and its severity vocabulary does not overlap this
# one. Migration 0010 adds alert_rules/alert_channels/alert_events beside it
# rather than overloading it; `alerts` is untouched.
# ---------------------------------------------------------------------------


_ALERT_COMPARATORS = (">", ">=", "<", "<=", "==")
_ALERT_SEVERITIES = ("critical", "warning", "info")
# PRD SS17, owner decision: v1 is email and a generic webhook. Not a DB enum
# for the same reason roles aren't (migration 0006) -- but validated here on
# every write, so "not an enum" never means "anything goes".
_ALERT_CHANNEL_TYPES = ("email", "webhook")
_ALERT_EVENT_STATES = ("firing", "resolved")

# Sortable columns are a closed map from a caller-facing name to a real
# column name. The value is never taken from the request, so no request
# string ever reaches the ORDER BY clause.
_ALERT_RULE_SORTS = {
    "name": "r.name",
    "metric": "r.metric",
    "severity": "r.severity",
    "created_at": "r.created_at",
    "updated_at": "r.updated_at",
    "last_fired_at": "r.last_fired_at",
}
_ALERT_EVENT_SORTS = {"created_at": "e.created_at", "severity": "e.severity"}
_SORT_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}

_EMAIL_ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Why a test-fire to an email channel cannot succeed from this container.
# Stated in full rather than as "not implemented" because the whole point of
# a test button is that its failure tells you what to go fix.
_EMAIL_DELIVERY_GAP = (
    "No email delivery path exists in the api container. worker/api ships no SMTP client "
    "(the only smtplib import in the service tree is "
    "worker/monitoring/services/service_monitor.py, which opens port 25 to health-check it "
    "and never sends a message); the `api` service in docker-compose.yml declares no "
    "SMTP_HOST/SMTP_PORT/ALERT_FROM_ADDRESS; and `mail_queue` has no producer or consumer "
    "anywhere in this repository -- GET /platform/overview only counts it. To close this: "
    "give the api service a relay target (the `postfix` container is already on "
    "mailserver_network), a From address, and one shared send helper the alert fire path "
    "and every future notification can call. Webhook channels deliver today; email channels "
    "are stored and validated but cannot fire."
)


# --- metric allowlist ------------------------------------------------------
#
# Each entry: unit, human description, and a callable (conn, now) -> number
# or None. Every callable is a one-line projection of a `_block_*` function
# above -- deliberately, so there is no second implementation of "queue
# depth" to drift away from the one the overview screen renders.


def _metric_bounce_rate_percent(conn, now: datetime):
    """Bounced as a percentage of outbound attempts over the last 24h.

    Denominator is sent + bounced, not sent + received + bounced: a bounce
    is a failed *outbound* delivery, so folding inbound volume into the
    denominator would quietly shrink the rate on a server that receives more
    than it sends -- precisely when the number matters.
    """
    volume = _block_volume(conn, _hour_buckets(now))
    sent = sum(row["sent"] for row in volume)
    bounced = sum(row["bounced"] for row in volume)
    return _rate(bounced, sent + bounced)


_ALERT_METRICS: dict = {
    "queue_depth": {
        "unit": "messages",
        "description": "mail_queue rows in queued/sending/deferred",
        "value": lambda conn, now: _block_queue(conn)["depth"],
    },
    "queue_oldest_age_seconds": {
        "unit": "seconds",
        "description": "Age of the oldest undelivered mail_queue row; null on an empty queue",
        "value": lambda conn, now: _block_queue(conn)["oldest_age_seconds"],
    },
    "services_down": {
        "unit": "services",
        "description": "Monitored services not reporting 'up' on the monitoring /heartbeat",
        "value": lambda conn, now: _block_services()["down"],
    },
    "certificates_expiring_days": {
        "unit": "days",
        "description": "Days until the EARLIEST still-valid active certificate expires; null "
        "when the install has no active certificate. Pair with '<=' -- 'fire when the "
        "soonest expiry is 14 days out or less'.",
        "value": lambda conn, now: _block_certificates(conn, now)["soonest_expiry_days"],
    },
    "quarantine_pending": {
        "unit": "messages",
        "description": "Messages sitting in `quarantine` with status='quarantined'",
        "value": lambda conn, now: _block_quarantine(conn)["pending"],
    },
    "webhook_dead_letters": {
        "unit": "events",
        "description": "webhook_dead_letters rows not yet resolved",
        "value": lambda conn, now: _block_webhooks(conn)["dead_letters"],
    },
    "failed_auth_24h": {
        "unit": "attempts",
        "description": "SUM(attempt_count) over failed_auth_attempts in the last 24h",
        "value": lambda conn, now: _block_security(
            conn, now - timedelta(hours=24), _hour_buckets(now)
        )["failed_auth_24h"],
    },
    "denied_operator_actions_24h": {
        "unit": "actions",
        "description": "operator_audit rows with result='denied' in the last 24h",
        "value": lambda conn, now: _block_security(
            conn, now - timedelta(hours=24), _hour_buckets(now)
        )["denied_actions_24h"],
    },
    "storage_used_percent": {
        "unit": "percent",
        "description": "SUM(storage_used) as a percentage of SUM(storage_quota) across mailboxes",
        "value": lambda conn, now: _block_storage(conn)["used_percent"],
    },
    "bounce_rate_percent": {
        "unit": "percent",
        "description": "Bounced as a percentage of outbound attempts (sent + bounced) over 24h",
        "value": _metric_bounce_rate_percent,
    },
}


def _metric_names() -> str:
    return ", ".join(sorted(_ALERT_METRICS))


def _compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == ">":
        return value > threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "<":
        return value < threshold
    if comparator == "<=":
        return value <= threshold
    return value == threshold


def _evaluate_metric(conn, metric: str, comparator: str, threshold: float, now: datetime) -> dict:
    """Compute one metric now and say whether it breaches.

    Never raises: a metric whose table is missing on this install (CE has no
    `quarantine`, a bare install has no certificates) must report "cannot be
    evaluated, here is why" rather than 500 -- the operator is here to find
    out whether the rule works, and an opaque failure answers nothing.
    """
    spec = _ALERT_METRICS.get(metric)
    if spec is None:
        return {
            "value": None,
            "unit": None,
            "breached": False,
            "unavailable_reason": f"metric {metric!r} is not in the server-side allowlist "
            f"({_metric_names()})",
        }
    try:
        value = spec["value"](conn, now)
    except Exception as exc:
        logger.warning(f"alert metric '{metric}' could not be evaluated: {exc}")
        return {
            "value": None,
            "unit": spec["unit"],
            "breached": False,
            "unavailable_reason": f"{metric} could not be computed on this install: {exc}",
        }
    if value is None:
        return {
            "value": None,
            "unit": spec["unit"],
            "breached": False,
            # A null metric is NOT a breach. An empty queue has no oldest
            # message and an install with no certificates has no soonest
            # expiry; treating either as 0 would fire "oldest message is
            # 0s old" and "certificate expires today" on a healthy system.
            "unavailable_reason": f"{metric} has no value right now (see its description in "
            f"GET /alerts/metrics) -- a null metric never fires",
        }
    value = float(value)
    return {
        "value": value,
        "unit": spec["unit"],
        "breached": _compare(value, comparator, threshold),
        "unavailable_reason": None,
    }


# --- row shaping -----------------------------------------------------------


def _channel_id_list(raw) -> list:
    """alert_rules.channel_ids is a JSON array. Coerce defensively: a row
    hand-edited into a non-array shape must not break the whole list read."""
    parsed = _json_field(raw)
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


_ALERT_RULE_SELECT = """
    SELECT r.id, r.name, r.description, r.metric, r.comparator, r.threshold,
           r.for_seconds, r.severity, r.enabled, r.channel_ids, r.organization_id,
           r.last_fired_at, r.created_at, r.updated_at
    FROM alert_rules r
"""

# The signing secret is never in the projection. It cannot leak by omission
# from a shaping function that was never handed it (same discipline as
# _OPERATOR_SELECT and password_hash).
_ALERT_CHANNEL_SELECT = """
    SELECT c.id, c.name, c.`type`, c.target, c.enabled, c.last_used_at, c.last_error,
           c.created_at,
           (c.signing_secret_ciphertext IS NOT NULL) AS has_signing_secret
    FROM alert_channels c
"""

_ALERT_EVENT_SELECT = """
    SELECT e.id, e.rule_id, e.rule_name, e.severity, e.`value`, e.threshold, e.state,
           e.message, e.created_at, e.resolved_at
    FROM alert_events e
"""


def _alert_rule_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "metric": row["metric"],
        "comparator": row["comparator"],
        "threshold": float(row["threshold"]),
        "for_seconds": _as_int(row["for_seconds"]),
        "severity": row["severity"],
        "enabled": bool(row["enabled"]),
        "channel_ids": _channel_id_list(row["channel_ids"]),
        "organization_id": row["organization_id"],
        "last_fired_at": _iso(row["last_fired_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _alert_channel_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "target": row["target"],
        "enabled": bool(row["enabled"]),
        # Whether a secret is configured, never the secret. An operator
        # needs to know a webhook is signed; nobody needs it read back.
        "has_signing_secret": bool(row.get("has_signing_secret")),
        "last_used_at": _iso(row["last_used_at"]),
        "last_error": row["last_error"],
        "created_at": _iso(row["created_at"]),
    }


def _alert_event_row(row: dict) -> dict:
    return {
        "id": _as_int(row["id"]),
        "rule_id": row["rule_id"],
        "rule_name": row["rule_name"],
        "severity": row["severity"],
        "value": float(row["value"]) if row["value"] is not None else None,
        "threshold": float(row["threshold"]),
        "state": row["state"],
        "message": row["message"],
        "created_at": _iso(row["created_at"]),
        "resolved_at": _iso(row["resolved_at"]),
    }


# --- delivery --------------------------------------------------------------


def _channel_secret(row: dict) -> str | None:
    """Decrypt a webhook channel's signing secret for the fire path only.

    Envelope encryption, NOT a hash: signing an outbound request needs the
    plaintext back, so bcrypt/SHA is structurally the wrong tool here even
    though it is the right one for passwords. Same AES-256-GCM-under-a-
    mounted-KEK scheme, and the same three columns, that
    0003_encrypt_private_keys gave dkim_keys/pgp_keys/smime_certs. The
    result is held in a local and never logged, returned, or stored.
    """
    ciphertext = row.get("signing_secret_ciphertext")
    if not ciphertext:
        return None
    return decrypt_private_key(
        bytes(ciphertext),
        bytes(row["signing_secret_nonce"]),
        _as_int(row.get("signing_secret_key_version")) or 1,
    )


_SEVERITY_COLORS = {"critical": "#d7263d", "warning": "#f2a33c", "info": "#3d8bfd"}


def _deliver_notification(channel: dict, secret: str | None, title: str, body: str, fields: dict):
    """Deliver one notification. Returns (delivered: bool, error: str|None).

    Webhook payload is Slack-compatible (`text` + `attachments[]` with
    `title`/`text`/`fields`/`color`) so a Slack or Mattermost incoming-webhook
    URL works with no adapter, while a generic receiver can just read `text`.
    Signed with HMAC-SHA256 as `X-Webhook-Signature: sha256=<hex>` over the
    exact bytes sent -- byte-identical to shared/webhook_dispatcher.py's
    scheme, so a receiver already verifying Mailyte webhooks needs no second
    verification path.
    """
    if channel["type"] == "webhook":
        payload = {
            "text": title,
            "attachments": [
                {
                    "color": _SEVERITY_COLORS.get(fields.get("severity"), "#3d8bfd"),
                    "title": title,
                    "text": body,
                    "fields": [
                        {"title": key, "value": str(value), "short": True}
                        for key, value in fields.items()
                    ],
                    "ts": int(datetime.now().timestamp()),
                }
            ],
        }
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mailyte-Alerts/1.0",
            "X-Webhook-Source": "mailyte-platform-alerts",
        }
        if secret:
            headers["X-Webhook-Signature"] = (
                "sha256="
                + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            )
        try:
            response = requests.post(
                channel["target"], data=payload_bytes, headers=headers, timeout=10
            )
        except requests.RequestException as exc:
            return False, f"POST to the channel target failed: {exc}"
        if response.status_code >= 400:
            return False, (
                f"Channel target returned HTTP {response.status_code}: "
                f"{(response.text or '')[:200]}"
            )
        return True, None

    # type == 'email'
    #
    # Relayed through the postfix container, which is on this same network and
    # already accepts mail on 25. This closes the gap _EMAIL_DELIVERY_GAP used
    # to describe: an alert channel that could be created, tested, and never
    # deliver anything.
    #
    # SMTP_HOST unset keeps the old behaviour rather than guessing. A
    # deployment that relays through something other than the bundled postfix
    # should say so explicitly, and one that has no relay at all should keep
    # being told why its email channels cannot work.
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        return False, _EMAIL_DELIVERY_GAP

    from_address = (
        os.getenv("ALERT_FROM_ADDRESS", "").strip() or os.getenv("ADMIN_EMAIL", "").strip()
    )
    if not from_address:
        return False, (
            "SMTP_HOST is set but ALERT_FROM_ADDRESS is not. An alert email needs an envelope "
            "sender the receiving side will accept -- PRD SS17 fixes this to the instance "
            "ADMIN_EMAIL identity."
        )

    try:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = title
        message["From"] = from_address
        message["To"] = channel["target"]
        # The webhook payload's fields, rendered for a human. Same content,
        # same order, so the two channel types cannot disagree about what
        # fired.
        detail = "\n".join(f"{k}: {v}" for k, v in fields.items())
        message.set_content(f"{body}\n\n{detail}\n")

        with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", "25")), timeout=10) as smtp:
            smtp.send_message(message)
        return True, None
    except Exception as exc:
        return False, f"SMTP delivery to {smtp_host} failed: {exc}"


# --- request models --------------------------------------------------------


class AlertReasonRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        description="Why this is being deleted. Recorded by the audit middleware (phase-02 "
        "cross-cutting: destructive actions carry a reason).",
    )


class AlertRuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    metric: str = Field(
        ..., description="One of the server-side allowlist; see GET /alerts/metrics"
    )
    comparator: str = Field(..., description="> | >= | < | <= | ==")
    threshold: float = Field(...)
    for_seconds: int = Field(
        0,
        ge=0,
        le=86400,
        description="Dwell time the breach must hold before firing. 0 = fire on first breach.",
    )
    severity: str = Field("warning", description="critical | warning | info")
    enabled: bool = Field(True)
    channel_ids: list[str] = Field(default_factory=list)
    organization_id: str | None = Field(
        None, description="Scope the rule to one tenant. Null = platform-wide."
    )


class AlertRuleUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    metric: str | None = Field(None)
    comparator: str | None = Field(None)
    threshold: float | None = Field(None)
    for_seconds: int | None = Field(None, ge=0, le=86400)
    severity: str | None = Field(None)
    enabled: bool | None = Field(None)
    channel_ids: list[str] | None = Field(None)
    organization_id: str | None = Field(None)


class AlertChannelCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., description="email | webhook (PRD SS17 -- v1 has no other type)")
    target: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Email address for type=email; absolute http(s) URL for type=webhook",
    )
    enabled: bool = Field(True)
    signing_secret: str | None = Field(
        None,
        max_length=512,
        description="Webhook HMAC signing secret. Stored envelope-encrypted and NEVER returned "
        "by any endpoint -- there is no read-back path, so keep your own copy.",
    )


def _reject(message: str, status_code: int = 422) -> JSONResponse:
    return JSONResponse(content=create_api_response("error", message), status_code=status_code)


def _validate_rule_values(metric, comparator, severity):
    """Shared by POST and PUT so a rule can never be updated into a state
    that create would have rejected."""
    if metric is not None and metric not in _ALERT_METRICS:
        return _reject(
            f"metric must be one of {_metric_names()} -- it is a server-side allowlist, not a "
            f"free-form field, because every permitted value maps to a query this server "
            f"already computes for GET /platform/overview"
        )
    if comparator is not None and comparator not in _ALERT_COMPARATORS:
        return _reject(f"comparator must be one of {', '.join(_ALERT_COMPARATORS)}")
    if severity is not None and severity not in _ALERT_SEVERITIES:
        return _reject(f"severity must be one of {', '.join(_ALERT_SEVERITIES)}")
    return None


def _validate_channel_ids(conn, channel_ids: list):
    """Every referenced channel must exist. channel_ids is a JSON column
    rather than a join table, so this check is the only thing standing in for
    the FK -- which is exactly why it runs on every write."""
    if not channel_ids:
        return None
    unique = list(dict.fromkeys(str(item) for item in channel_ids))
    placeholders = ", ".join(["%s"] * len(unique))
    rows = _query_all(
        conn, f"SELECT id FROM alert_channels WHERE id IN ({placeholders})", tuple(unique)
    )
    found = {row["id"] for row in rows}
    missing = [item for item in unique if item not in found]
    if missing:
        return _reject(f"unknown channel_ids: {', '.join(missing)}")
    return None


def _validate_channel_target(channel_type: str, target: str):
    if channel_type == "email":
        if not _EMAIL_ADDRESS_RE.match(target.strip()):
            return _reject("target must be a valid email address for type=email")
        return None
    # Scheme allowlist: the server POSTs to this URL from inside the
    # container, so an unrestricted target is an SSRF primitive. Restricting
    # the scheme does not make it safe against internal addresses -- see the
    # endpoint description -- but it does stop file://, gopher:// and the
    # rest outright.
    if not target.startswith(("http://", "https://")):
        return _reject("target must be an absolute http:// or https:// URL for type=webhook")
    return None


# --- rules -----------------------------------------------------------------


@router.get(
    "/alerts/metrics",
    summary="List the alertable metrics",
    description="The server-side allowlist `metric` is validated against, with each metric's "
    "unit and what it actually measures. Exposed so the console can build the metric picker "
    "from the server rather than hard-coding a list that silently drifts. Every entry maps to "
    "a counter GET /platform/overview already renders -- same function, same number.",
)
async def list_alert_metrics(
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    return create_api_response(
        "success",
        "Alert metrics retrieved",
        {
            "items": [
                {"metric": name, "unit": spec["unit"], "description": spec["description"]}
                for name, spec in sorted(_ALERT_METRICS.items())
            ],
            "comparators": list(_ALERT_COMPARATORS),
            "severities": list(_ALERT_SEVERITIES),
            "channel_types": list(_ALERT_CHANNEL_TYPES),
        },
    )


@router.get(
    "/alerts/rules",
    summary="List alert rules",
    description="Paginated alert rules with their channel bindings. Filters on enabled, "
    "severity, metric, tenant, and free text over name/description. `sort` is a closed map "
    "of caller-facing names to columns -- no request string reaches the ORDER BY.",
)
async def list_alert_rules(
    q: str | None = Query(None, description="Free text over name and description"),
    metric: str | None = Query(None),
    severity: str | None = Query(None),
    enabled: bool | None = Query(None),
    organization_id: str | None = Query(None),
    sort: str = Query(
        "created_at",
        description="name|metric|severity|created_at|updated_at|last_fired_at",
    ),
    direction: str = Query("desc", description="asc|desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    if sort not in _ALERT_RULE_SORTS:
        return _reject(f"sort must be one of {', '.join(sorted(_ALERT_RULE_SORTS))}")
    if direction not in _SORT_DIRECTIONS:
        return _reject("direction must be one of asc, desc")
    invalid = _validate_rule_values(metric, None, severity)
    if invalid:
        return invalid

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conditions: list = []
    params: list = []
    if q:
        conditions.append("(r.name LIKE %s OR r.description LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if metric:
        conditions.append("r.metric = %s")
        params.append(metric)
    if severity:
        conditions.append("r.severity = %s")
        params.append(severity)
    if enabled is not None:
        conditions.append("r.enabled = %s")
        params.append(1 if enabled else 0)
    if organization_id:
        conditions.append("r.organization_id = %s")
        params.append(organization_id)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order = f"{_ALERT_RULE_SORTS[sort]} {_SORT_DIRECTIONS[direction]}"

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        total = _as_int(
            _query_one(conn, f"SELECT COUNT(*) AS total FROM alert_rules r {where}", params).get(
                "total"
            )
        )
        rows = _query_all(
            conn,
            f"{_ALERT_RULE_SELECT} {where} ORDER BY {order}, r.id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return create_api_response(
            "success",
            "Alert rules retrieved",
            {
                "items": [_alert_rule_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List alert rules failed: {exc}")
        return _reject("Failed to retrieve alert rules", 500)
    finally:
        conn.close()


@router.get(
    "/alerts/rules/{rule_id}",
    summary="Get one alert rule",
    description="Single rule with the same fields as the list view.",
)
async def get_alert_rule(
    rule_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        row = _query_one(conn, f"{_ALERT_RULE_SELECT} WHERE r.id = %s", (rule_id,))
        if not row:
            return _reject("Alert rule not found", 404)
        return create_api_response("success", "Alert rule retrieved", _alert_rule_row(row))
    except Exception as exc:
        logger.error(f"Get alert rule failed: {exc}")
        return _reject("Failed to retrieve alert rule", 500)
    finally:
        conn.close()


@router.post(
    "/alerts/rules",
    summary="Create an alert rule",
    description="`metric` is validated against a server-side allowlist (GET /alerts/metrics) and "
    "422s with the permitted names on anything else; so are `comparator` and `severity`. Every "
    "id in `channel_ids` must already exist -- channel_ids is a JSON column, so this check is "
    "what stands in for the foreign key a join table would have given. A rule is created "
    "enabled by default but has fired nothing until something evaluates it: use "
    "POST /alerts/rules/{id}/evaluate to see what it would do against live data first.",
)
async def create_alert_rule(
    body: AlertRuleCreateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    invalid = _validate_rule_values(body.metric, body.comparator, body.severity)
    if invalid:
        return invalid

    rule_id = generate_ulid()
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        invalid = _validate_channel_ids(conn, body.channel_ids)
        if invalid:
            return invalid
        if body.organization_id:
            if not _query_one(
                conn, "SELECT id FROM organizations WHERE id = %s", (body.organization_id,)
            ):
                return _reject("organization_id does not exist")

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "INSERT INTO alert_rules "
                "(id, name, description, metric, comparator, threshold, for_seconds, severity, "
                " enabled, channel_ids, organization_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    rule_id,
                    body.name,
                    body.description,
                    body.metric,
                    body.comparator,
                    body.threshold,
                    body.for_seconds,
                    body.severity,
                    1 if body.enabled else 0,
                    json.dumps([str(item) for item in body.channel_ids]),
                    body.organization_id,
                ),
            )
            conn.commit()
        finally:
            cursor.close()

        row = _query_one(conn, f"{_ALERT_RULE_SELECT} WHERE r.id = %s", (rule_id,))
        logger.info(
            f"Alert rule created: rule={rule_id} metric={body.metric} "
            f"{body.comparator} {body.threshold} by={ctx['operator_id']}"
        )
        return JSONResponse(
            content=create_api_response("success", "Alert rule created", _alert_rule_row(row)),
            status_code=201,
        )
    except Exception as exc:
        logger.error(f"Create alert rule failed: {exc}")
        return _reject("Failed to create alert rule", 500)
    finally:
        conn.close()


@router.put(
    "/alerts/rules/{rule_id}",
    summary="Update an alert rule",
    description="Partial update. Every supplied field goes through the same allowlist "
    "validation as create -- a rule can never be edited into a state create would have "
    "rejected. Sending organization_id: null moves a tenant-scoped rule back to platform-wide.",
)
async def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    invalid = _validate_rule_values(body.metric, body.comparator, body.severity)
    if invalid:
        return invalid

    # fields_set, not "is not None": organization_id and description are
    # legitimately settable TO null, and a plain None check cannot tell
    # "clear this" from "leave it alone".
    supplied = body.model_fields_set
    assignments: list = []
    params: list = []
    for field, column, transform in (
        ("name", "name", None),
        ("description", "description", None),
        ("metric", "metric", None),
        ("comparator", "comparator", None),
        ("threshold", "threshold", None),
        ("for_seconds", "for_seconds", None),
        ("severity", "severity", None),
        ("enabled", "enabled", lambda v: 1 if v else 0),
        ("organization_id", "organization_id", None),
    ):
        if field in supplied:
            value = getattr(body, field)
            assignments.append(f"{column} = %s")
            params.append(transform(value) if transform else value)
    if "channel_ids" in supplied:
        assignments.append("channel_ids = %s")
        params.append(json.dumps([str(item) for item in (body.channel_ids or [])]))

    if not assignments:
        return _reject("No updatable fields provided", 400)

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        if not _query_one(conn, "SELECT id FROM alert_rules WHERE id = %s", (rule_id,)):
            return _reject("Alert rule not found", 404)
        if "channel_ids" in supplied:
            invalid = _validate_channel_ids(conn, body.channel_ids or [])
            if invalid:
                return invalid
        if body.organization_id:
            if not _query_one(
                conn, "SELECT id FROM organizations WHERE id = %s", (body.organization_id,)
            ):
                return _reject("organization_id does not exist")

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                f"UPDATE alert_rules SET {', '.join(assignments)} WHERE id = %s",
                params + [rule_id],
            )
            conn.commit()
        finally:
            cursor.close()

        row = _query_one(conn, f"{_ALERT_RULE_SELECT} WHERE r.id = %s", (rule_id,))
        logger.info(f"Alert rule updated: rule={rule_id} by={ctx['operator_id']}")
        return create_api_response("success", "Alert rule updated", _alert_rule_row(row))
    except Exception as exc:
        logger.error(f"Update alert rule failed: {exc}")
        return _reject("Failed to update alert rule", 500)
    finally:
        conn.close()


@router.delete(
    "/alerts/rules/{rule_id}",
    summary="Delete an alert rule",
    description="Removes the rule. Its history in /alerts/history SURVIVES: alert_events.rule_id "
    "is ON DELETE SET NULL and the event carries its own rule_name/severity/threshold, so "
    "deleting a rule cannot rewrite the record of what it did. Requires a reason, recorded by "
    "the audit middleware.",
)
async def delete_alert_rule(
    rule_id: str,
    body: AlertReasonRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        row = _query_one(conn, "SELECT id, name FROM alert_rules WHERE id = %s", (rule_id,))
        if not row:
            return _reject("Alert rule not found", 404)

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("DELETE FROM alert_rules WHERE id = %s", (rule_id,))
            conn.commit()
        finally:
            cursor.close()

        logger.warning(
            f"Alert rule deleted: rule={rule_id} name={row['name']!r} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response(
            "success", "Alert rule deleted", {"id": rule_id, "deleted": True}
        )
    except Exception as exc:
        logger.error(f"Delete alert rule failed: {exc}")
        return _reject("Failed to delete alert rule", 500)
    finally:
        conn.close()


@router.post(
    "/alerts/rules/{rule_id}/evaluate",
    summary="Evaluate one alert rule against live data now",
    description="Runs the rule's metric against the platform RIGHT NOW and reports the value and "
    "whether it would fire. This is what makes a rule testable before it is trusted -- a "
    "threshold nobody has ever seen evaluated is a guess. Read-only in every sense: it sends no "
    "notification, writes no alert_events row, and does not touch last_fired_at. "
    "`for_seconds` is REPORTED but NOT honoured: a single instantaneous evaluation cannot "
    "observe a dwell time, so a rule that would fire here may still not fire under a scheduler "
    "that requires the breach to hold. A metric with no value right now (empty queue, no "
    "certificates) returns value: null, would_fire: false, and names the reason -- a null "
    "metric never fires.",
)
async def evaluate_alert_rule(
    rule_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        rule = _query_one(conn, f"{_ALERT_RULE_SELECT} WHERE r.id = %s", (rule_id,))
        if not rule:
            return _reject("Alert rule not found", 404)

        now = datetime.now()
        threshold = float(rule["threshold"])
        outcome = _evaluate_metric(conn, rule["metric"], rule["comparator"], threshold, now)

        if outcome["unavailable_reason"]:
            message = (
                f"{rule['name']}: {rule['metric']} could not be evaluated -- "
                f"{outcome['unavailable_reason']}"
            )
        elif outcome["breached"]:
            message = (
                f"{rule['name']}: {rule['metric']} is {outcome['value']} "
                f"{outcome['unit']} ({rule['comparator']} {threshold})"
            )
        else:
            message = (
                f"{rule['name']}: {rule['metric']} is {outcome['value']} "
                f"{outcome['unit']}, within threshold ({rule['comparator']} {threshold})"
            )

        return create_api_response(
            "success",
            "Alert rule evaluated",
            {
                "rule_id": rule_id,
                "name": rule["name"],
                "metric": rule["metric"],
                "comparator": rule["comparator"],
                "threshold": threshold,
                "unit": outcome["unit"],
                "value": outcome["value"],
                "would_fire": outcome["breached"],
                "severity": rule["severity"],
                "enabled": bool(rule["enabled"]),
                "for_seconds": _as_int(rule["for_seconds"]),
                "for_seconds_honoured": False,
                "unavailable_reason": outcome["unavailable_reason"],
                "message": message,
                "evaluated_at": now.isoformat(),
                "notified": False,
                "recorded": False,
            },
        )
    except Exception as exc:
        logger.error(f"Evaluate alert rule failed: {exc}")
        return _reject("Failed to evaluate alert rule", 500)
    finally:
        conn.close()


# --- channels --------------------------------------------------------------


@router.get(
    "/alerts/channels",
    summary="List notification channels",
    description="Every channel with its last delivery attempt and last error. The webhook "
    "signing secret is NOT in the projection -- there is no read-back path for it anywhere in "
    "this API; `has_signing_secret` reports only whether one is configured.",
)
async def list_alert_channels(
    channel_type: str | None = Query(None, alias="type", description="email | webhook"),
    enabled: bool | None = Query(None),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    if channel_type is not None and channel_type not in _ALERT_CHANNEL_TYPES:
        return _reject(f"type must be one of {', '.join(_ALERT_CHANNEL_TYPES)}")

    conditions: list = []
    params: list = []
    if channel_type:
        conditions.append("c.`type` = %s")
        params.append(channel_type)
    if enabled is not None:
        conditions.append("c.enabled = %s")
        params.append(1 if enabled else 0)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        rows = _query_all(
            conn, f"{_ALERT_CHANNEL_SELECT} {where} ORDER BY c.created_at DESC", params
        )
        return create_api_response(
            "success",
            "Alert channels retrieved",
            {"items": [_alert_channel_row(row) for row in rows]},
        )
    except Exception as exc:
        logger.error(f"List alert channels failed: {exc}")
        return _reject("Failed to retrieve alert channels", 500)
    finally:
        conn.close()


@router.post(
    "/alerts/channels",
    summary="Create a notification channel",
    description="`type` is email or webhook and nothing else (PRD SS17 -- v1 scope). A webhook "
    "channel may carry a signing secret: it is stored ENVELOPE-ENCRYPTED (AES-256-GCM under the "
    "mounted KEK, the same scheme 0003 gave DKIM/PGP/S-MIME private keys) and is never returned "
    "by this or any other endpoint, so keep your own copy. It is encrypted rather than hashed "
    "because the fire path needs the plaintext back to compute the HMAC -- a one-way hash is the "
    "right tool for a password and the wrong one for a signing key. NOTE: a webhook target is a "
    "URL this server will POST to from inside the container; only http/https are accepted, but "
    "there is no allowlist of destinations, so treat channel creation as the privileged "
    "operation it is (it is operator-role for that reason).",
)
async def create_alert_channel(
    body: AlertChannelCreateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    if body.type not in _ALERT_CHANNEL_TYPES:
        return _reject(f"type must be one of {', '.join(_ALERT_CHANNEL_TYPES)}")
    invalid = _validate_channel_target(body.type, body.target)
    if invalid:
        return invalid
    if body.signing_secret and body.type != "webhook":
        return _reject("signing_secret is only meaningful for type=webhook")

    ciphertext = nonce = None
    key_version = 1
    if body.signing_secret:
        try:
            encrypted = encrypt_private_key(body.signing_secret)
        except KEKNotConfiguredError as exc:
            # Fail closed. Storing the secret in plaintext because the KEK
            # is missing is precisely the outcome 0003 existed to end.
            return _reject(
                f"Cannot store a signing secret: {exc}. Create the channel without a secret, "
                f"or mount the KEK first.",
                503,
            )
        ciphertext, nonce, key_version = (
            encrypted.ciphertext,
            encrypted.nonce,
            encrypted.key_version,
        )

    channel_id = generate_ulid()
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "INSERT INTO alert_channels "
                "(id, name, `type`, target, enabled, signing_secret_ciphertext, "
                " signing_secret_nonce, signing_secret_key_version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    channel_id,
                    body.name,
                    body.type,
                    body.target.strip(),
                    1 if body.enabled else 0,
                    ciphertext,
                    nonce,
                    key_version,
                ),
            )
            conn.commit()
        finally:
            cursor.close()

        row = _query_one(conn, f"{_ALERT_CHANNEL_SELECT} WHERE c.id = %s", (channel_id,))
        # Deliberately never logs body.signing_secret, not even its length.
        logger.info(
            f"Alert channel created: channel={channel_id} type={body.type} "
            f"signed={bool(body.signing_secret)} by={ctx['operator_id']}"
        )
        return JSONResponse(
            content=create_api_response(
                "success", "Alert channel created", _alert_channel_row(row)
            ),
            status_code=201,
        )
    except Exception as exc:
        logger.error(f"Create alert channel failed: {exc}")
        return _reject("Failed to create alert channel", 500)
    finally:
        conn.close()


@router.delete(
    "/alerts/channels/{channel_id}",
    summary="Delete a notification channel",
    description="Removes the channel and its stored signing secret. Rules referencing it keep "
    "the dangling id in channel_ids -- deleting a channel must not silently edit unrelated "
    "rules, and the response names every rule that referenced it so the operator can decide. "
    "Requires a reason.",
)
async def delete_alert_channel(
    channel_id: str,
    body: AlertReasonRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        row = _query_one(conn, "SELECT id, name FROM alert_channels WHERE id = %s", (channel_id,))
        if not row:
            return _reject("Alert channel not found", 404)

        # JSON_CONTAINS on the JSON array rather than a LIKE over the text:
        # LIKE '%id%' matches a channel id that is a substring of another.
        referencing = _query_all(
            conn,
            "SELECT id, name FROM alert_rules "
            "WHERE channel_ids IS NOT NULL AND JSON_CONTAINS(channel_ids, %s)",
            (json.dumps(channel_id),),
        )

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("DELETE FROM alert_channels WHERE id = %s", (channel_id,))
            conn.commit()
        finally:
            cursor.close()

        logger.warning(
            f"Alert channel deleted: channel={channel_id} name={row['name']!r} "
            f"referencing_rules={len(referencing)} by={ctx['operator_id']} "
            f"reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            "Alert channel deleted",
            {
                "id": channel_id,
                "deleted": True,
                "referencing_rules": [
                    {"id": rule["id"], "name": rule["name"]} for rule in referencing
                ],
            },
        )
    except Exception as exc:
        logger.error(f"Delete alert channel failed: {exc}")
        return _reject("Failed to delete alert channel", 500)
    finally:
        conn.close()


@router.post(
    "/alerts/channels/{channel_id}/test",
    summary="Send a test notification through a channel",
    description="Actually delivers -- this is not a dry run. Returns {delivered, error} and "
    "records the attempt on the channel as last_used_at/last_error, success or failure, so the "
    "list view shows the truth about a channel rather than what someone hoped when they created "
    "it. Webhook channels POST a Slack-compatible body, signed X-Webhook-Signature: sha256=<hex> "
    "when the channel has a secret. EMAIL CHANNELS RETURN delivered: false with an error naming "
    "exactly what is missing: there is no email send path in the api container. That is the "
    "honest answer and it is the point of a test button -- a button that reported success while "
    "sending nothing would be worse than no button.",
)
async def test_alert_channel(
    channel_id: str,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        row = _query_one(
            conn,
            "SELECT id, name, `type`, target, enabled, signing_secret_ciphertext, "
            "signing_secret_nonce, signing_secret_key_version "
            "FROM alert_channels WHERE id = %s",
            (channel_id,),
        )
        if not row:
            return _reject("Alert channel not found", 404)

        try:
            secret = _channel_secret(row)
        except Exception as exc:
            # A secret that will not decrypt is a real delivery failure and
            # is reported as one -- not swallowed into an unsigned send that
            # the receiver would reject anyway.
            secret = None
            delivered, error = False, f"Stored signing secret could not be decrypted: {exc}"
        else:
            now = datetime.now()
            delivered, error = _deliver_notification(
                row,
                secret,
                f"Mailyte test notification: {row['name']}",
                "This is a test notification from the Mailyte platform console. No alert rule "
                "fired; an operator pressed Test on this channel.",
                {
                    "severity": "info",
                    "channel": row["name"],
                    "type": row["type"],
                    # AuthContext carries operator_id, not the email (see
                    # utils/auth.py) -- don't reach for a key that isn't there.
                    "sent_by": ctx["operator_id"] or "platform operator",
                    "sent_at": now.isoformat(),
                },
            )

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "UPDATE alert_channels SET last_used_at = NOW(), last_error = %s WHERE id = %s",
                (None if delivered else (error or "")[:2000], channel_id),
            )
            conn.commit()
        finally:
            cursor.close()

        logger.info(
            f"Alert channel test: channel={channel_id} type={row['type']} "
            f"delivered={delivered} by={ctx['operator_id']}"
        )
        return create_api_response(
            "success" if delivered else "error",
            "Test notification delivered" if delivered else "Test notification not delivered",
            {"channel_id": channel_id, "delivered": delivered, "error": error},
        )
    except Exception as exc:
        logger.error(f"Test alert channel failed: {exc}")
        return _reject("Failed to test alert channel", 500)
    finally:
        conn.close()


# --- history ---------------------------------------------------------------


@router.get(
    "/alerts/history",
    summary="Search fired alerts",
    description="Paginated, newest-first read over alert_events -- what fired, when, at what "
    "value, and whether it has resolved. Filters on rule, state and severity. Rows survive the "
    "deletion of the rule that produced them (rule_id nulls out, rule_name/severity/threshold "
    "are carried on the event), so history cannot be edited by deleting a rule. Read-only: "
    "nothing in this API writes an alert_events row yet -- the scheduled evaluator that will is "
    "not part of this change, and POST /alerts/rules/{id}/evaluate deliberately does not record "
    "its result. Expect this to be empty until that runner exists.",
)
async def list_alert_history(
    rule_id: str | None = Query(None),
    state: str | None = Query(None, description="firing | resolved"),
    severity: str | None = Query(None, description="critical | warning | info"),
    date_from: str | None = Query(None, description="ISO date or datetime, inclusive"),
    date_to: str | None = Query(None, description="ISO date or datetime, inclusive"),
    sort: str = Query("created_at", description="created_at|severity"),
    direction: str = Query("desc", description="asc|desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="support")),
):
    if state is not None and state not in _ALERT_EVENT_STATES:
        return _reject(f"state must be one of {', '.join(_ALERT_EVENT_STATES)}")
    if severity is not None and severity not in _ALERT_SEVERITIES:
        return _reject(f"severity must be one of {', '.join(_ALERT_SEVERITIES)}")
    if sort not in _ALERT_EVENT_SORTS:
        return _reject(f"sort must be one of {', '.join(sorted(_ALERT_EVENT_SORTS))}")
    if direction not in _SORT_DIRECTIONS:
        return _reject("direction must be one of asc, desc")

    per_page = min(per_page, 500)
    offset = (page - 1) * per_page

    conditions: list = []
    params: list = []
    if rule_id:
        conditions.append("e.rule_id = %s")
        params.append(rule_id)
    if state:
        conditions.append("e.state = %s")
        params.append(state)
    if severity:
        conditions.append("e.severity = %s")
        params.append(severity)
    try:
        if date_from:
            conditions.append("e.created_at >= %s")
            params.append(_parse_iso_datetime(date_from))
        if date_to:
            conditions.append("e.created_at <= %s")
            params.append(_parse_iso_datetime(date_to, end_of_day=True))
    except ValueError:
        return _reject("date_from/date_to must be an ISO date or datetime")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order = f"{_ALERT_EVENT_SORTS[sort]} {_SORT_DIRECTIONS[direction]}"

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        total = _as_int(
            _query_one(conn, f"SELECT COUNT(*) AS total FROM alert_events e {where}", params).get(
                "total"
            )
        )
        rows = _query_all(
            conn,
            f"{_ALERT_EVENT_SELECT} {where} ORDER BY {order}, e.id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return create_api_response(
            "success",
            "Alert history retrieved",
            {
                "items": [_alert_event_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List alert history failed: {exc}")
        return _reject("Failed to retrieve alert history", 500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GAP #9 -- /backups (READ ONLY)
# ---------------------------------------------------------------------------
#
# UPDATED 2026-08-22 (plans/06-operations, DR-1). The description that used to
# live here -- "the table is real and readable, and on most installs it will be
# empty" -- was accurate and is now obsolete:
#
# * `scripts/backup.sh` writes backup_history on every run. It is driven by
#   host systemd timers (nightly full, hourly incremental) as well as by
#   deployment/deploy.sh, so the table has real rows on a healthy install.
# * The web server cannot reach this database at all, so backup-web.sh reports
#   its runs through POST /backups/report below. That endpoint is the ONLY
#   writer in this file, and it exists because the alternative was giving a
#   second machine a MySQL credential for this host.
# * worker/archiver's /backup/* endpoints -- which INSERTed backup_path and
#   file_size, columns that do not exist -- have been deleted. backup.sh is
#   the sole producer.
#
# An empty list here now genuinely does mean no backup has run, which is what
# makes the absence alerts (monitoring/prometheus/rules/backup_alerts.yml)
# meaningful.
#
# Still no trigger-backup and no restore endpoint: a backup needs host
# filesystem and S3 credentials the api container does not have, and restoring
# a mail server is not a web-button operation.
# ---------------------------------------------------------------------------


_BACKUP_STATUSES = ("running", "completed", "failed")
_BACKUP_TARGETS = ("database", "mail", "config", "all")
_BACKUP_TYPES = ("full", "incremental", "differential")
_BACKUP_SORTS = {"started_at": "started_at", "completed_at": "completed_at", "id": "id"}
_BACKUP_STATUS_RUNS = 10


def _backup_run_row(row: dict) -> dict:
    """One backup_history row on the wire.

    `finished_at` is the baseline's completed_at and `error` is its
    error_message -- renamed for the console, not invented. backup_type and
    storage_path are extra real columns the console's shape does not name;
    they are included because "which backup" and "where did it go" are the
    first two questions anyone asks of this list.
    """
    size = row.get("size_bytes")
    return {
        "id": _as_int(row["id"]),
        "backup_id": row.get("backup_id"),
        # Which machine produced it. Two hosts back up into one table now, and
        # "the backups are fine" is only ever true per host.
        "hostname": row.get("hostname"),
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["completed_at"]),
        "status": row["status"],
        "size_bytes": int(size) if size is not None else None,
        "target": row["target"],
        "backup_type": row["backup_type"],
        "storage_path": row["storage_path"],
        "checksum": row.get("checksum"),
        # False marks a pre-DR-1 backup holding plaintext DKIM keys and .env.
        "encrypted": bool(row.get("encrypted")),
        "error": row["error_message"],
    }


_BACKUP_RUN_SELECT = """
    SELECT id, backup_id, hostname, backup_type, target, storage_path, size_bytes,
           checksum, encrypted, status, error_message, started_at, completed_at
    FROM backup_history
"""


@router.get(
    "/backups/status",
    summary="Backup health summary",
    description="Last success, last failure, total stored bytes and the ten most recent runs, "
    "read from `backup_history`. `schedule` and `retention_days` come from BACKUP_SCHEDULE / "
    "BACKUP_RETENTION_DAYS, which the `api` service does not declare in docker-compose.yml -- "
    "they are returned as NULL rather than as the .env.example defaults, because reporting a "
    "schedule this container cannot actually see would be a guess dressed as a fact. `notes` "
    "explains every null and warns when the table is empty. Read-only: there is deliberately no "
    "trigger-backup and no restore endpoint here.",
)
async def backup_status(
    ctx: AuthContext = Depends(require_scope("platform", "read", role="admin")),
):
    # Read, don't assume: these are unset in the api container today, which
    # is exactly why they are read from the environment rather than
    # hard-coded from .env.example -- the day someone passes them through,
    # this starts reporting the truth with no code change.
    schedule = os.getenv("BACKUP_SCHEDULE") or None
    retention_raw = os.getenv("BACKUP_RETENTION_DAYS")
    try:
        retention_days = int(retention_raw) if retention_raw else None
    except ValueError:
        retention_days = None

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        summary = _query_one(
            conn,
            """
            SELECT
                MAX(CASE WHEN status = 'completed'
                         THEN COALESCE(completed_at, started_at) END) AS last_success_at,
                MAX(CASE WHEN status = 'failed'
                         THEN COALESCE(completed_at, started_at) END) AS last_failure_at,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN size_bytes ELSE 0 END), 0)
                    AS total_size_bytes,
                COUNT(*) AS total_runs,
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running
            FROM backup_history
            """,
        )
        runs = _query_all(
            conn,
            f"{_BACKUP_RUN_SELECT} ORDER BY started_at DESC, id DESC LIMIT %s",
            (_BACKUP_STATUS_RUNS,),
        )
        # Per-host freshness. A single "last_success_at" across both machines
        # hides the case that matters: one host backing up nightly while the
        # other has been silent for a week still produces a recent timestamp.
        host_rows = _query_all(
            conn,
            """
            SELECT hostname,
                   MAX(CASE WHEN status = 'completed'
                            THEN COALESCE(completed_at, started_at) END) AS last_success_at,
                   SUM(CASE WHEN status = 'completed' AND encrypted = 0 THEN 1 ELSE 0 END)
                       AS unencrypted_runs
            FROM backup_history
            GROUP BY hostname
            """,
        )
        unencrypted = sum(_as_int(r.get("unencrypted_runs")) for r in host_rows)
        stale_hosts = []
        for r in host_rows:
            last = r.get("last_success_at")
            if last is None or (datetime.now() - last) > timedelta(hours=26):
                stale_hosts.append(r.get("hostname") or "unknown")
    except Exception as exc:
        logger.error(f"Backup status failed: {exc}")
        return _reject("Failed to retrieve backup status", 500)
    finally:
        conn.close()

    total_runs = _as_int(summary.get("total_runs"))
    notes = []
    if schedule is None:
        notes.append(
            "schedule is null: BACKUP_SCHEDULE is not passed to the api container "
            "(the `api` service in docker-compose.yml declares no BACKUP_* environment). "
            "Add it there to have this report the real cron expression."
        )
    if retention_days is None:
        notes.append(
            "retention_days is null: BACKUP_RETENTION_DAYS is not passed to the api container, "
            "same fix as schedule."
        )
    notes.append(
        "Rows are written by scripts/backup.sh on the mail server (host systemd timers: nightly "
        "full at 02:30, hourly incremental at :15) and by scripts/backup-web.sh on the web "
        "server via POST /backups/report. storage/backups/ on the host is still not mounted into "
        "this container, so storage_path is informational here."
    )
    if total_runs == 0:
        notes.append(
            "backup_history is EMPTY. Unlike before DR-1, this now means exactly what it looks "
            "like: no backup has recorded a run. Check `systemctl status mailyte-backup-full` on "
            "the mail server and whether secrets/dr.env exists."
        )
    if unencrypted:
        notes.append(
            f"{unencrypted} completed run(s) are not encrypted. Those archives contain DKIM "
            "private keys and a full .env in plaintext -- shred them and confirm "
            "DR_AGE_RECIPIENT is set in secrets/dr.env."
        )
    if stale_hosts:
        notes.append(
            "Hosts with no successful backup in the last 26 h: " + ", ".join(stale_hosts) + "."
        )

    return create_api_response(
        "success",
        "Backup status retrieved",
        {
            "last_success_at": _iso(summary.get("last_success_at")),
            "last_failure_at": _iso(summary.get("last_failure_at")),
            "schedule": schedule,
            "retention_days": retention_days,
            "total_size_bytes": _as_int(summary.get("total_size_bytes")),
            "total_runs": total_runs,
            "running": _as_int(summary.get("running")),
            "runs": [_backup_run_row(row) for row in runs],
            "hosts": [
                {
                    "hostname": r.get("hostname"),
                    "last_success_at": _iso(r.get("last_success_at")),
                    "unencrypted_runs": _as_int(r.get("unencrypted_runs")),
                    "stale": (r.get("hostname") or "unknown") in stale_hosts,
                }
                for r in host_rows
            ],
            "notes": notes,
        },
    )


@router.get(
    "/backups",
    summary="List backup runs",
    description="Paginated, newest-first read over `backup_history` with status/target/type "
    "filters, every one of them an allowlist. `finished_at` is the schema's completed_at and "
    "`error` is its error_message. See GET /backups/status for why this list is likely empty "
    "even on a host that backs up nightly. Read-only.",
)
async def list_backups(
    status: str | None = Query(None, description="running | completed | failed"),
    target: str | None = Query(None, description="database | mail | config | all"),
    backup_type: str | None = Query(None, description="full | incremental | differential"),
    sort: str = Query("started_at", description="started_at|completed_at|id"),
    direction: str = Query("desc", description="asc|desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="admin")),
):
    if status is not None and status not in _BACKUP_STATUSES:
        return _reject(f"status must be one of {', '.join(_BACKUP_STATUSES)}")
    if target is not None and target not in _BACKUP_TARGETS:
        return _reject(f"target must be one of {', '.join(_BACKUP_TARGETS)}")
    if backup_type is not None and backup_type not in _BACKUP_TYPES:
        return _reject(f"backup_type must be one of {', '.join(_BACKUP_TYPES)}")
    if sort not in _BACKUP_SORTS:
        return _reject(f"sort must be one of {', '.join(sorted(_BACKUP_SORTS))}")
    if direction not in _SORT_DIRECTIONS:
        return _reject("direction must be one of asc, desc")

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conditions: list = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if target:
        conditions.append("target = %s")
        params.append(target)
    if backup_type:
        conditions.append("backup_type = %s")
        params.append(backup_type)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order = f"{_BACKUP_SORTS[sort]} {_SORT_DIRECTIONS[direction]}"

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        total = _as_int(
            _query_one(conn, f"SELECT COUNT(*) AS total FROM backup_history {where}", params).get(
                "total"
            )
        )
        rows = _query_all(
            conn,
            f"{_BACKUP_RUN_SELECT} {where} ORDER BY {order}, id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        return create_api_response(
            "success",
            "Backup runs retrieved",
            {
                "items": [_backup_run_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List backups failed: {exc}")
        return _reject("Failed to retrieve backup runs", 500)
    finally:
        conn.close()


class BackupReportRequest(BaseModel):
    """One finished backup run, reported by a host that cannot reach this DB."""

    backup_id: str = Field(..., max_length=32, description="YYYYmmdd_HHMMSS stamp")
    hostname: str = Field(..., max_length=255)
    backup_type: str = Field("full", description="full | incremental | differential")
    target: str = Field("all", description="database | mail | config | all")
    status: str = Field(..., description="running | completed | failed")
    size_bytes: int = Field(0, ge=0)
    storage_path: str | None = Field(None, max_length=1000)
    checksum: str | None = Field(None, max_length=128)
    encrypted: bool = True
    error_message: str | None = None


@router.post(
    "/backups/report",
    summary="Record a backup run from another host",
    description="The web server runs scripts/backup-web.sh against the Laravel database and has "
    "no route to this mail server's MySQL (it is bound to 127.0.0.1 there). Rather than exporting "
    "a database credential to a second machine, that host POSTs its result here. Idempotent on "
    "(backup_id, hostname), so a retried report updates the row instead of duplicating it. This "
    "is the only writer to backup_history in this file; scripts/backup.sh writes its own rows "
    "directly.",
)
async def report_backup(
    req: BackupReportRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="admin")),
):
    if req.status not in _BACKUP_STATUSES:
        return _reject(f"status must be one of {', '.join(_BACKUP_STATUSES)}")
    if req.target not in _BACKUP_TARGETS:
        return _reject(f"target must be one of {', '.join(_BACKUP_TARGETS)}")
    if req.backup_type not in _BACKUP_TYPES:
        return _reject(f"backup_type must be one of {', '.join(_BACKUP_TYPES)}")

    conn = get_db_connection()
    if not conn:
        return _reject("Database connection failed", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        # (backup_id, hostname) is not a unique key -- adding one would mean a
        # migration on a table two shell scripts already write -- so the
        # upsert is done explicitly.
        cursor.execute(
            "SELECT id FROM backup_history WHERE backup_id = %s AND hostname = %s LIMIT 1",
            (req.backup_id, req.hostname),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE backup_history
                   SET backup_type = %s, target = %s, status = %s, size_bytes = %s,
                       storage_path = %s, checksum = %s, encrypted = %s,
                       error_message = %s, completed_at = NOW()
                 WHERE id = %s
                """,
                (
                    req.backup_type,
                    req.target,
                    req.status,
                    req.size_bytes,
                    req.storage_path,
                    req.checksum,
                    int(req.encrypted),
                    req.error_message,
                    existing["id"],
                ),
            )
            row_id = existing["id"]
        else:
            cursor.execute(
                """
                INSERT INTO backup_history
                    (backup_id, hostname, backup_type, target, status, size_bytes,
                     storage_path, checksum, encrypted, error_message,
                     started_at, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    req.backup_id,
                    req.hostname,
                    req.backup_type,
                    req.target,
                    req.status,
                    req.size_bytes,
                    req.storage_path,
                    req.checksum,
                    int(req.encrypted),
                    req.error_message,
                ),
            )
            row_id = cursor.lastrowid

        conn.commit()
        cursor.close()
    except Exception as exc:
        logger.error(f"Backup report failed: {exc}")
        return _reject("Failed to record backup run", 500)
    finally:
        conn.close()

    return create_api_response(
        "success", "Backup run recorded", {"id": _as_int(row_id), "backup_id": req.backup_id}
    )


# ---------------------------------------------------------------------------
# Alert evaluation
# ---------------------------------------------------------------------------
#
# Until this existed, alert rules and channels could be created, listed,
# ordered and test-fired -- and no rule ever fired on its own. Nothing read
# `alert_rules` except the API that wrote it. A monitoring feature that is
# only believed when someone is already looking is worse than none, because it
# is trusted in exactly the situation where nobody is looking.

_ALERT_EVAL_INTERVAL_SECONDS = int(os.getenv("ALERT_EVAL_INTERVAL_SECONDS", "60"))

# rule_id -> datetime the current unbroken breach began. In memory rather than
# a column because `for_seconds` only needs to survive between ticks of one
# evaluator, and only one evaluator runs at a time (see the lock below). The
# cost of a restart is that a breach's timer starts again, delaying an alert by
# at most for_seconds -- acceptable against adding schema and a write per tick.
_alert_breach_since: dict[str, datetime] = {}

# Only one process may evaluate. `api` runs replicas: 2 in production
# (docker-compose.prod.yml), and two evaluators would double every
# notification -- the failure mode where an incident pages you twice and you
# start distrusting the count. A MySQL advisory lock is used rather than a
# leader election because the database is already the thing both replicas
# share, and GET_LOCK is released automatically if the holder's connection
# dies, which is exactly the semantics wanted for a crashed evaluator.
_ALERT_LOCK_NAME = "mailyte_alert_evaluator"


def _channels_for_rule(conn, rule: dict) -> list:
    """The enabled channels a rule notifies.

    alert_rules.channel_ids is a JSON array, so this cannot be a join.
    Disabled channels are skipped rather than attempted: an operator who
    disables a channel has said "stop sending here", and failing to reach it
    would otherwise be recorded as an error against the channel every tick.
    """
    ids = _channel_id_list(rule.get("channel_ids"))
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    return _query_all(
        conn,
        "SELECT id, name, `type`, target, enabled, "
        "signing_secret_ciphertext AS secret_ciphertext, "
        "signing_secret_nonce AS secret_nonce, "
        "signing_secret_key_version AS key_version "
        f"FROM alert_channels WHERE id IN ({placeholders}) AND enabled = 1",
        tuple(ids),
    )


def _fire_alert(conn, rule: dict, outcome: dict) -> None:
    """Record a firing event and notify every channel attached to the rule."""
    title = f"[{rule['severity'].upper()}] {rule['name']}"
    body = (
        f"{rule['metric']} is {outcome['value']} {outcome.get('unit') or ''}".strip()
        + f", which is {rule['comparator']} {rule['threshold']}."
    )
    fields = {
        "metric": rule["metric"],
        "value": outcome["value"],
        "threshold": rule["threshold"],
        "comparator": rule["comparator"],
        "severity": rule["severity"],
    }

    # No id column: alert_events.id is bigint AUTO_INCREMENT, unlike most
    # tables here which carry a CHAR(26) ULID. Supplying a ULID truncated it to
    # a bigint and the insert failed on every fire.
    _execute(
        conn,
        "INSERT INTO alert_events (rule_id, rule_name, severity, `value`, threshold, "
        "state, message, created_at) VALUES (%s, %s, %s, %s, %s, 'firing', %s, NOW())",
        (
            rule["id"],
            rule["name"],
            rule["severity"],
            outcome["value"],
            rule["threshold"],
            body,
        ),
    )
    _execute(conn, "UPDATE alert_rules SET last_fired_at = NOW() WHERE id = %s", (rule["id"],))

    for channel in _channels_for_rule(conn, rule):
        secret = None
        if channel.get("secret_ciphertext"):
            try:
                secret = decrypt_private_key(
                    channel["secret_ciphertext"],
                    channel["secret_nonce"],
                    channel.get("key_version") or 1,
                )
            except Exception as exc:  # a bad secret must not stop the others
                logger.error(f"Alert channel {channel['id']} secret undecryptable: {exc}")

        delivered, error = _deliver_notification(channel, secret, title, body, fields)
        _execute(
            conn,
            "UPDATE alert_channels SET last_used_at = NOW(), last_error = %s WHERE id = %s",
            (None if delivered else (error or "unknown error")[:500], channel["id"]),
        )
        if not delivered:
            # Logged, not raised. One unreachable channel must not suppress the
            # others, and the failure is already recorded on the channel row
            # where the operator will look for it.
            logger.warning(f"Alert channel {channel['id']} delivery failed: {error}")


def _resolve_alert(conn, rule: dict) -> None:
    _execute(
        conn,
        "UPDATE alert_events SET state = 'resolved', resolved_at = NOW() "
        "WHERE rule_id = %s AND state = 'firing'",
        (rule["id"],),
    )


def _evaluate_all_rules(conn, now: datetime) -> None:
    rules = _query_all(conn, f"{_ALERT_RULE_SELECT} WHERE r.enabled = 1")
    for rule in rules:
        try:
            outcome = _evaluate_metric(
                conn, rule["metric"], rule["comparator"], float(rule["threshold"]), now
            )
        except Exception as exc:
            logger.error(f"Alert rule {rule['id']} could not be evaluated: {exc}")
            continue

        # A metric with no value right now (empty queue, no certificates) is
        # not a breach and not a resolution -- it is an absence. Treating it as
        # "recovered" would resolve a firing alert the moment its table went
        # briefly empty.
        if outcome.get("value") is None:
            continue

        currently_firing = bool(
            _query_one(
                conn,
                "SELECT id FROM alert_events WHERE rule_id = %s AND state = 'firing' LIMIT 1",
                (rule["id"],),
            )
        )

        if outcome["breached"]:
            began = _alert_breach_since.setdefault(rule["id"], now)
            held_for = (now - began).total_seconds()
            if held_for >= (rule.get("for_seconds") or 0) and not currently_firing:
                _fire_alert(conn, rule, outcome)
        else:
            _alert_breach_since.pop(rule["id"], None)
            if currently_firing:
                _resolve_alert(conn, rule)


async def _alert_evaluation_loop() -> None:
    while True:
        await asyncio.sleep(_ALERT_EVAL_INTERVAL_SECONDS)
        conn = None
        try:
            conn = get_db_connection()
            if not conn:
                continue
            # 0 = do not wait. If the other replica holds it, skip this tick
            # rather than queue up behind it.
            got = _query_one(conn, "SELECT GET_LOCK(%s, 0) AS ok", (_ALERT_LOCK_NAME,))
            if not got or not got.get("ok"):
                continue
            try:
                await run_in_threadpool(_evaluate_all_rules, conn, datetime.now())
            finally:
                # _query_one, not _execute: RELEASE_LOCK is a SELECT and
                # returns a row. Running it through the write helper never
                # fetches that row, and the connector then refuses the next
                # statement on the connection with "Unread result found" --
                # which killed every tick after the first.
                _query_one(conn, "SELECT RELEASE_LOCK(%s) AS released", (_ALERT_LOCK_NAME,))
        except Exception as exc:
            # The loop must outlive any single failure; a monitoring system
            # that stops monitoring after one bad tick is the worst outcome
            # here.
            logger.error(f"Alert evaluation tick failed: {exc}")
        finally:
            if conn:
                conn.close()
