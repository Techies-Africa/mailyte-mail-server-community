#!/usr/bin/env python3
"""
Security & Abuse API (Mailyte Console PRD SS12 gap #4, phase-04 SS4.2/SS4.3/SS4.4)

Four groups, all platform-scope, none of them tenant-facing:

1. /failed-auth/*   -- phase-04 SS4.4's authentication-security screen. Reads
   `failed_auth_attempts`, the one table that already records every rejected
   credential across SMTP/IMAP/POP3/API. The summary endpoint is the point
   of the screen: a single account being hammered and a single IP walking a
   thousand accounts are different incidents with different responses, and
   only a per-IP *distinct account* count tells them apart.

2. /ip-rules/*      -- phase-04 SS4.3 / PRD SS5.6. CRUD over `ip_access_rules`,
   the table Postfix's policy delegation service actually consults
   (mailer/postfix/scripts/ip_access_policy.py). Because a rule here is
   enforced live on the next submission, every address is validated through
   Python's `ipaddress` module before it is stored -- a malformed CIDR is
   silently skipped by the policy server's own `except ValueError: continue`,
   so an unvalidated rule is a security control that looks configured and
   does nothing.

3. /geo-policies/*  -- PRD SS5.6. CRUD over `geo_policies`, consumed by
   security/geo_blocking.py. Same reasoning: country codes are validated as
   ISO 3166-1 alpha-2 here because the consumer compares raw strings, so
   "usa" or "United States" would simply never match and the policy would
   quietly not apply.

4. /dlp/*           -- phase-04 SS4.2. Policies (admin) and violations
   (operator).

   **The privacy rule that governs this whole group:** a DLP violation row
   records the data the policy exists to keep from leaving -- a card number,
   a national ID, a patient record -- inside `matched_pattern` and `details`.
   Phase-04 SS4.2 is explicit: "Show the matched rule, never the matched
   content", and ADR-002 SS5 puts message content outside platform scope
   entirely. So neither column is ever named in a SELECT in this file. Not
   redacted after fetching -- never fetched. A column that is not in the
   result set cannot leak through a log line, an exception repr, or a future
   refactor that spreads `**row` into a response.

Every route carries its own server-side scope+role check via
Depends(require_scope(...)). ADR-002 SS7: "Client-side gating is
presentation only. Hiding a button is not a security control."
"""

import ipaddress
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from utils.auth import AuthContext, create_api_response, org_filter, require_scope
from utils.database import get_db_connection

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)
router = APIRouter()


# `failed_auth_attempts.service` as the LIVE schema has it, not as
# database/models/security.py has it -- the model still lists only the
# original five. Alembic 0004 added 'api_key' (phase-07 H3) and 0007 added
# 'operator' (phase-06 task 6.7); filtering on a value the enum does not
# hold returns nothing, and omitting one that it does hides real failures
# from the screen whose entire job is to show them.
_AUTH_SERVICES = ("smtp", "imap", "pop3", "api", "sieve", "api_key", "operator")

_IP_RULE_TYPES = ("whitelist", "blacklist")

_GEO_ACTIONS = ("block", "challenge", "log_only")

# dlp_policies' four ENUM columns, verified against alembic 0001_baseline's
# CREATE TABLE rather than the ORM model (the two agree here, but the
# baseline is what the database enforces).
_DLP_POLICY_TYPES = ("pii", "keyword", "regex", "file_type")
_DLP_ACTIONS = ("block", "quarantine", "encrypt", "notify", "log_only")
_DLP_SEVERITIES = ("low", "medium", "high", "critical")
_DLP_APPLY_TO = ("inbound", "outbound", "both")

# ISO 3166-1 alpha-2. Validated by shape, not against a country list: the
# list changes (Kosovo, South Sudan) and a stale allowlist would reject a
# legitimate code, which fails in the more dangerous direction for a
# *blocked_countries* entry.
_ISO_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

_MAX_BLOCK_MINUTES = 60 * 24 * 30  # 30 days -- see BlockIpRequest
_SUMMARY_MAX_HOURS = 168  # 7 days
_SUMMARY_TOP_N = 25


# ---------------------------------------------------------------------------
# Serialisation / query helpers
#
# Deliberately duplicated from routes/platform.py rather than imported from
# it. Route modules are loaded by name in app.py's include loop and are not
# a library layer; importing one route module from another would make the
# import order of two independent routers load-bearing.
# ---------------------------------------------------------------------------


def _iso(value):
    """DATETIME -> ISO8601 string, passing None through, so the console never
    has to special-case one endpoint's date format against another's."""
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
    """JSON columns come back as str or bytes depending on connector version
    -- decode here so the console gets a real object, not a string of JSON."""
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


def _paginate(page: int, per_page: int, total: int) -> dict:
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": (total + per_page - 1) // per_page if per_page else 0,
    }


def _parse_iso_datetime(raw: str, *, end_of_day: bool = False) -> datetime:
    """Accept a bare ISO date ('2026-08-20') or a full ISO datetime.

    A bare date used as an upper bound means "through the end of that day".
    Otherwise `date_from=X&date_to=X` -- the single most likely filter a
    human types -- returns nothing at all.
    """
    parsed = datetime.fromisoformat(raw)
    if end_of_day and len(raw.strip()) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(content=create_api_response("error", message), status_code=status_code)


def _db_error() -> JSONResponse:
    return _error("Database connection failed", 500)


def _sort_clause(sort_by: str | None, sort_dir: str | None, allowed: dict, default: str) -> str:
    """Build an ORDER BY from a whitelist, never from caller text.

    `allowed` maps the caller-visible field name to the SQL expression it is
    permitted to sort by. Anything not in the mapping falls back to
    `default` rather than raising: a bad sort key is a cosmetic mistake, and
    silently ignoring it is safer than the alternative of interpolating it.
    """
    column = allowed.get(sort_by or "", allowed[default])
    direction = "ASC" if (sort_dir or "").lower() == "asc" else "DESC"
    return f"ORDER BY {column} {direction}"


def _scoped_org_clause(ctx: AuthContext, requested_org: str | None, alias: str = "") -> tuple:
    """org_filter() with a table alias applied to its column reference.

    org_filter stays the single source of truth for the rule that matters --
    a caller-supplied organization_id is honoured for platform scope and
    IGNORED for organization scope -- and this only fixes up the column
    prefix for queries that join more than one table.
    """
    fragment, params = org_filter(ctx, requested_org)
    if alias and fragment != "1=1":
        fragment = f"{alias}.{fragment}"
    return fragment, params


def _hour_buckets(now: datetime, hours: int) -> list:
    """`hours` hour-truncated buckets, oldest first, ending at the current
    hour. Built in Python, not generated in SQL, so an hour with no rows
    still appears as an explicit zero -- a series with holes in it misreads
    as "nothing happened" when it means "no data"."""
    base = now.replace(minute=0, second=0, microsecond=0)
    return [base - timedelta(hours=offset) for offset in range(hours - 1, -1, -1)]


# ---------------------------------------------------------------------------
# A -- Failed authentication (phase-04 SS4.4)
#
# One row in `failed_auth_attempts` is not one failure: utils/auth.py's
# _bump_failure_row collapses repeated failures from the same
# (ip, username, service) into a single row and increments attempt_count.
# Every aggregate below therefore SUMs attempt_count; COUNT(*) would
# understate a sustained brute-force run by orders of magnitude.
#
# The table is also written in two shapes. The mail-protocol writers
# (mailer/dovecot/scripts/dovecot-auth-policy.py) record real
# (client_ip, username) pairs. The API-side writers in utils/auth.py record
# two *dimension* rows per failure instead -- one with username IS NULL
# (the IP dimension) and one with client_ip = '' (the account dimension) --
# because they rate-limit the two independently. Hence NULLIF(client_ip,'')
# in every distinct-IP count: a synthetic account-dimension row must not be
# counted as an IP that attacked something.
# ---------------------------------------------------------------------------

_FAILED_AUTH_SORTS = {
    "last_attempt_at": "last_attempt_at",
    "attempt_count": "attempt_count",
    "first_attempt_at": "first_attempt_at",
}


def _failed_auth_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "client_ip": row["client_ip"],
        "username": row["username"],
        "service": row["service"],
        "failure_reason": row["failure_reason"],
        "attempt_count": _as_int(row["attempt_count"]),
        "blocked_until": _iso(row["blocked_until"]),
        # Computed in SQL against the database's clock, not Python's: the
        # API container and MySQL can disagree, and "is this IP blocked
        # right now" must match what the auth path itself will decide.
        "blocked": bool(row["blocked"]),
        "first_attempt_at": _iso(row["first_attempt_at"]),
        "last_attempt_at": _iso(row["last_attempt_at"]),
    }


@router.get(
    "/failed-auth",
    summary="List failed authentication attempts",
    description="Paginated view over `failed_auth_attempts` (phase-04 SS4.4), filterable by IP, "
    "account, service, block state and date range. Each row is a collapsed counter, not a single "
    "failure -- `attempt_count` is how many failures it represents.",
)
async def list_failed_auth(
    client_ip: str | None = Query(None, description="Exact client IP"),
    username: str | None = Query(None, description="Exact account/username"),
    service: str | None = Query(None, description=" | ".join(_AUTH_SERVICES)),
    blocked: bool | None = Query(
        None, description="true = currently blocked (blocked_until > now)"
    ),
    date_from: str | None = Query(None, description="ISO date or datetime, on last_attempt_at"),
    date_to: str | None = Query(None, description="ISO date or datetime, on last_attempt_at"),
    q: str | None = Query(None, description="Free text over client_ip and username"),
    sort_by: str | None = Query("last_attempt_at", description=" | ".join(_FAILED_AUTH_SORTS)),
    sort_dir: str | None = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    if service and service not in _AUTH_SERVICES:
        return _error(f"service must be one of {', '.join(_AUTH_SERVICES)}", 422)

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conditions: list = []
    params: list = []
    if client_ip:
        conditions.append("client_ip = %s")
        params.append(client_ip)
    if username:
        conditions.append("username = %s")
        params.append(username)
    if service:
        conditions.append("service = %s")
        params.append(service)
    if blocked is not None:
        if blocked:
            conditions.append("blocked_until IS NOT NULL AND blocked_until > NOW()")
        else:
            conditions.append("(blocked_until IS NULL OR blocked_until <= NOW())")
    try:
        if date_from:
            conditions.append("last_attempt_at >= %s")
            params.append(_parse_iso_datetime(date_from))
        if date_to:
            conditions.append("last_attempt_at <= %s")
            params.append(_parse_iso_datetime(date_to, end_of_day=True))
    except ValueError:
        return _error("date_from/date_to must be ISO dates or datetimes", 422)

    if q:
        # The % wildcards live in the bound *value*, which the driver
        # escapes -- never in the query text.
        conditions.append("(client_ip LIKE %s OR username LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_by = _sort_clause(sort_by, sort_dir, _FAILED_AUTH_SORTS, "last_attempt_at")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM failed_auth_attempts {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"""
            SELECT id, client_ip, username, service, failure_reason, attempt_count,
                   blocked_until, first_attempt_at, last_attempt_at,
                   (blocked_until IS NOT NULL AND blocked_until > NOW()) AS blocked
            FROM failed_auth_attempts
            {where}
            {order_by}, id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "Failed authentication attempts retrieved",
            {
                "items": [_failed_auth_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List failed auth failed: {exc}")
        return _error("Failed to retrieve failed authentication attempts", 500)
    finally:
        conn.close()


@router.get(
    "/failed-auth/summary",
    summary="Brute-force and distributed-attack summary",
    description="The shape of the attack rather than a list of its rows (phase-04 SS4.4). "
    "`top_ips[].distinct_accounts` is the distributed-attack signal: one IP touching many "
    "accounts is credential stuffing, one account touched by many IPs is a targeted account "
    "takeover, and the two need different responses -- so it is a real COUNT(DISTINCT username), "
    "never a copy of attempt_count. Window defaults to 24h, capped at 168h (7 days).",
)
async def failed_auth_summary(
    hours: int = Query(
        24, ge=1, description=f"Lookback window in hours (max {_SUMMARY_MAX_HOURS})"
    ),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    hours = min(hours, _SUMMARY_MAX_HOURS)
    now = datetime.now()
    buckets = _hour_buckets(now, hours)
    window_start = buckets[0]

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT COALESCE(SUM(attempt_count), 0) AS total_attempts,
                   COUNT(DISTINCT NULLIF(client_ip, '')) AS distinct_ips,
                   COUNT(DISTINCT username) AS distinct_accounts
            FROM failed_auth_attempts
            WHERE last_attempt_at >= %s
            """,
            (window_start,),
        )
        totals = cursor.fetchone() or {}

        # GROUP_CONCAT rather than a second query per IP: the services an IP
        # touched is what distinguishes a misconfigured mail client (one
        # service, one account) from a scanner (several services).
        cursor.execute(
            """
            SELECT client_ip,
                   COALESCE(SUM(attempt_count), 0) AS attempt_count,
                   COUNT(DISTINCT username) AS distinct_accounts,
                   GROUP_CONCAT(DISTINCT service ORDER BY service) AS services,
                   MAX(last_attempt_at) AS last_attempt_at,
                   (MAX(blocked_until) IS NOT NULL AND MAX(blocked_until) > NOW()) AS blocked
            FROM failed_auth_attempts
            WHERE last_attempt_at >= %s AND client_ip <> ''
            GROUP BY client_ip
            ORDER BY attempt_count DESC
            LIMIT %s
            """,
            (window_start, _SUMMARY_TOP_N),
        )
        top_ips = [
            {
                "client_ip": row["client_ip"],
                "attempt_count": _as_int(row["attempt_count"]),
                "distinct_accounts": _as_int(row["distinct_accounts"]),
                "services": (row["services"] or "").split(",") if row["services"] else [],
                "blocked": bool(row["blocked"]),
                "last_attempt_at": _iso(row["last_attempt_at"]),
            }
            for row in (cursor.fetchall() or [])
        ]

        cursor.execute(
            """
            SELECT username,
                   COALESCE(SUM(attempt_count), 0) AS attempt_count,
                   COUNT(DISTINCT NULLIF(client_ip, '')) AS distinct_ips,
                   MAX(last_attempt_at) AS last_attempt_at
            FROM failed_auth_attempts
            WHERE last_attempt_at >= %s AND username IS NOT NULL
            GROUP BY username
            ORDER BY attempt_count DESC
            LIMIT %s
            """,
            (window_start, _SUMMARY_TOP_N),
        )
        top_accounts = [
            {
                "username": row["username"],
                "attempt_count": _as_int(row["attempt_count"]),
                "distinct_ips": _as_int(row["distinct_ips"]),
                "last_attempt_at": _iso(row["last_attempt_at"]),
            }
            for row in (cursor.fetchall() or [])
        ]

        cursor.execute(
            """
            SELECT service, COALESCE(SUM(attempt_count), 0) AS attempt_count
            FROM failed_auth_attempts
            WHERE last_attempt_at >= %s
            GROUP BY service
            ORDER BY attempt_count DESC
            """,
            (window_start,),
        )
        by_service = [
            {"service": row["service"], "attempt_count": _as_int(row["attempt_count"])}
            for row in (cursor.fetchall() or [])
        ]

        # DATE()/HOUR() rather than DATE_FORMAT(): mysql-connector
        # %-formats the query text whenever params are supplied, so a
        # literal '%Y' in the SQL blows up on substitution.
        cursor.execute(
            """
            SELECT DATE(last_attempt_at) AS bucket_date,
                   HOUR(last_attempt_at) AS bucket_hour,
                   COALESCE(SUM(attempt_count), 0) AS count
            FROM failed_auth_attempts
            WHERE last_attempt_at >= %s
            GROUP BY bucket_date, bucket_hour
            """,
            (window_start,),
        )
        by_hour = {
            (row["bucket_date"], int(row["bucket_hour"])): _as_int(row["count"])
            for row in (cursor.fetchall() or [])
        }
        cursor.close()

        return create_api_response(
            "success",
            "Failed authentication summary retrieved",
            {
                "window_hours": hours,
                "total_attempts": _as_int(totals.get("total_attempts")),
                "distinct_ips": _as_int(totals.get("distinct_ips")),
                "distinct_accounts": _as_int(totals.get("distinct_accounts")),
                "top_ips": top_ips,
                "top_accounts": top_accounts,
                "by_service": by_service,
                "series": [
                    {
                        "hour": bucket.isoformat(),
                        "count": by_hour.get((bucket.date(), bucket.hour), 0),
                    }
                    for bucket in buckets
                ],
            },
        )
    except Exception as exc:
        logger.error(f"Failed auth summary failed: {exc}")
        return _error("Failed to retrieve failed authentication summary", 500)
    finally:
        conn.close()


class BlockIpRequest(BaseModel):
    minutes: int = Field(
        ...,
        ge=1,
        le=_MAX_BLOCK_MINUTES,
        description="How long to hold the block, in minutes. Capped at 30 days: "
        "`blocked_until` is a lockout timer that the auth path re-reads on every attempt, not a "
        "permanent ban list -- an unbounded value here would be a permanent block recorded in a "
        "table that is expected to be prunable.",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Why this IP is being blocked. Recorded by the operator audit middleware "
        "(phase-04 cross-cutting: auditors ask why, not just what).",
    )


class UnblockIpRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Why this block is being lifted. Audited.")


@router.post(
    "/failed-auth/{client_ip}/block",
    summary="Block an IP at the authentication layer",
    description="Sets `blocked_until` on every `failed_auth_attempts` row for this IP, which is "
    "what utils/auth.py's `_login_attempt_blocked` consults before it will consider a credential "
    "at all -- so the block takes effect on the next attempt, on every service.\n\n"
    "**It deliberately does NOT write an `ip_access_rules` blacklist entry.** That table is "
    "consumed by exactly one thing (mailer/postfix/scripts/ip_access_policy.py), which looks it "
    "up as `WHERE organization_id = <the sender domain's org> AND active = 1`, only for "
    "SASL-authenticated submission. A cross-protocol, cross-tenant auth block has no organization "
    "to attach to; a row with a NULL organization_id can never match that query, and a row "
    "attached to one arbitrary tenant would block that tenant's submissions only. Either way the "
    "console would show a block that blocks nothing, which is worse than no block at all. See the "
    "IP rules endpoints below to manage that table on its own terms.",
)
async def block_failed_auth_ip(
    client_ip: str,
    body: BlockIpRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    # Validated even though it is only ever bound as a parameter: an IP that
    # is not an IP cannot match anything the auth path will look up, so
    # accepting it would silently create a block that never fires.
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        return _error("client_ip must be a valid IPv4 or IPv6 address", 422)

    blocked_until = datetime.now() + timedelta(minutes=body.minutes)

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "UPDATE failed_auth_attempts SET blocked_until = %s, failure_reason = %s "
            "WHERE client_ip = %s",
            (blocked_until, "operator_block", client_ip),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()

        if not affected:
            # Nothing to block: `blocked_until` only exists on rows this IP
            # already produced. 404 rather than inventing a row -- a
            # fabricated failure record would corrupt every count on the
            # summary screen above.
            return _error(
                "No failed authentication attempts recorded for this IP, so there is nothing to "
                "block. Add an ip_access_rules blacklist entry instead if the intent is a "
                "pre-emptive block.",
                404,
            )

        logger.warning(
            f"IP blocked at auth layer: ip={client_ip} minutes={body.minutes} rows={affected} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            "IP blocked",
            {
                "client_ip": client_ip,
                "blocked_until": blocked_until.isoformat(),
                "rows_affected": affected,
            },
        )
    except Exception as exc:
        logger.error(f"Block IP failed: {exc}")
        return _error("Failed to block IP", 500)
    finally:
        conn.close()


@router.delete(
    "/failed-auth/{client_ip}/block",
    summary="Lift an authentication-layer block on an IP",
    description="Clears `blocked_until` for this IP. The attempt counters are left intact on "
    "purpose: unblocking is a decision about the future, and erasing the history that justified "
    "the block would erase the evidence with it.",
)
async def unblock_failed_auth_ip(
    client_ip: str,
    body: UnblockIpRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        return _error("client_ip must be a valid IPv4 or IPv6 address", 422)

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "UPDATE failed_auth_attempts SET blocked_until = NULL "
            "WHERE client_ip = %s AND blocked_until IS NOT NULL",
            (client_ip,),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()

        if not affected:
            return _error("No active block found for this IP", 404)

        logger.warning(
            f"IP block lifted: ip={client_ip} rows={affected} by={ctx['operator_id']} "
            f"reason={body.reason!r}"
        )
        return create_api_response(
            "success", "IP block lifted", {"client_ip": client_ip, "rows_affected": affected}
        )
    except Exception as exc:
        logger.error(f"Unblock IP failed: {exc}")
        return _error("Failed to lift IP block", 500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# B -- IP access rules (phase-04 SS4.3, PRD SS5.6)
# ---------------------------------------------------------------------------


def _validate_ip_entry(value: str) -> str | None:
    """None if `value` is a bare address or a CIDR network, else an error.

    Both forms are accepted because the consumer accepts both: the policy
    server calls `ipaddress.ip_network(cidr, strict=False)` on whatever is
    stored, which happily takes '203.0.113.7' as a /32. `strict=False` is
    mirrored here so '10.0.0.5/24' -- host bits set -- is accepted with the
    same meaning it will have at enforcement time, rather than being
    rejected here and accepted there.
    """
    candidate = (value or "").strip()
    if not candidate:
        return "ip_address is required"
    try:
        ipaddress.ip_address(candidate)
        return None
    except ValueError:
        pass
    try:
        ipaddress.ip_network(candidate, strict=False)
        return None
    except ValueError:
        return (
            f"'{value}' is not a valid IP address or CIDR network. The Postfix policy server "
            "skips entries it cannot parse, so an invalid rule would be stored and never enforced."
        )


class IpRuleCreateRequest(BaseModel):
    rule_type: str = Field(..., description="whitelist | blacklist")
    ip_address: str = Field(..., description="IPv4/IPv6 address or CIDR network")
    description: str | None = Field(None, max_length=255)
    organization_id: str | None = Field(
        None,
        description="Owning tenant. Required in practice -- see the endpoint description for why "
        "a rule with no organization is never evaluated.",
    )
    reason: str = Field(..., min_length=1, description="Why this rule is being created. Audited.")


class IpRuleUpdateRequest(BaseModel):
    description: str | None = Field(None, max_length=255)
    active: bool | None = None
    rule_type: str | None = Field(None, description="whitelist | blacklist")
    reason: str = Field(..., min_length=1, description="Why this rule is being changed. Audited.")


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Why this is being deleted. Audited.")


_IP_RULE_SORTS = {
    "created_at": "r.created_at",
    "updated_at": "r.updated_at",
    "ip_address": "r.ip_address",
    "rule_type": "r.rule_type",
}


def _ip_rule_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "organization_name": row.get("organization_name"),
        "rule_type": row["rule_type"],
        "ip_address": row["ip_address"],
        "description": row["description"],
        "active": bool(row["active"]),
        "created_by": row["created_by"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


@router.get(
    "/ip-rules",
    summary="List IP access rules",
    description="Paginated view over `ip_access_rules`, the table Postfix's policy delegation "
    "service enforces on authenticated submission. Platform scope sees every tenant's rules; "
    "organization scope is confined to its own and a caller-supplied organization_id is ignored "
    "(org_filter, ADR-002 SS8).",
)
async def list_ip_rules(
    rule_type: str | None = Query(None, description="whitelist | blacklist"),
    active: bool | None = Query(None),
    organization_id: str | None = Query(None, description="Platform scope only; ignored otherwise"),
    q: str | None = Query(None, description="Free text over ip_address and description"),
    sort_by: str | None = Query("created_at", description=" | ".join(_IP_RULE_SORTS)),
    sort_dir: str | None = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    if rule_type and rule_type not in _IP_RULE_TYPES:
        return _error(f"rule_type must be one of {', '.join(_IP_RULE_TYPES)}", 422)

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    org_sql, org_params = _scoped_org_clause(ctx, organization_id, "r")
    conditions = [org_sql]
    params = list(org_params)
    if rule_type:
        conditions.append("r.rule_type = %s")
        params.append(rule_type)
    if active is not None:
        conditions.append("r.active = %s")
        params.append(1 if active else 0)
    if q:
        conditions.append("(r.ip_address LIKE %s OR r.description LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    where = "WHERE " + " AND ".join(conditions)
    order_by = _sort_clause(sort_by, sort_dir, _IP_RULE_SORTS, "created_at")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM ip_access_rules r {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"""
            SELECT r.id, r.organization_id, o.name AS organization_name, r.rule_type,
                   r.ip_address, r.description, r.active, r.created_by, r.created_at, r.updated_at
            FROM ip_access_rules r
            LEFT JOIN organizations o ON o.id = r.organization_id
            {where}
            {order_by}, r.id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "IP access rules retrieved",
            {
                "items": [_ip_rule_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List IP rules failed: {exc}")
        return _error("Failed to retrieve IP access rules", 500)
    finally:
        conn.close()


@router.post(
    "/ip-rules",
    summary="Create an IP access rule",
    description="Creates a whitelist or blacklist entry enforced live by the Postfix policy "
    "server on the next authenticated submission.\n\n"
    "`organization_id` is effectively required even though the column is nullable: the policy "
    "server resolves the sender's domain to an organization and then queries "
    "`WHERE organization_id = <that org>`, so a rule with no organization is never in any result "
    "set and enforces nothing. A 422 that says so beats storing a rule that looks configured and "
    "is inert.\n\n"
    "Note the semantics the policy server gives a whitelist: an organization with **any** active "
    "whitelist rule rejects every IP that does not match one. The first whitelist rule for a "
    "tenant is therefore a lockout of everything else, not an addition to a default-allow list.",
)
async def create_ip_rule(
    request: Request,
    body: IpRuleCreateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    if body.rule_type not in _IP_RULE_TYPES:
        return _error(f"rule_type must be one of {', '.join(_IP_RULE_TYPES)}", 422)

    ip_error = _validate_ip_entry(body.ip_address)
    if ip_error:
        return _error(ip_error, 422)
    ip_value = body.ip_address.strip()

    # A tenant-scope caller can never name someone else's organization --
    # the same rule org_filter applies to reads, applied to a write.
    organization_id = (
        ctx["organization_id"] if ctx["scope"] == "organization" else body.organization_id
    )
    if not organization_id:
        return _error(
            "organization_id is required: ip_access_rules is evaluated per-organization by the "
            "Postfix policy server, so a rule with no organization would never be enforced.",
            422,
        )

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM organizations WHERE id = %s", (organization_id,))
        if not cursor.fetchone():
            cursor.close()
            return _error("Organization not found", 404)

        # Duplicate rules are not merely untidy: two identical whitelist
        # entries make the "does this tenant have a whitelist at all"
        # question, which is what flips the tenant to default-deny, harder
        # for an operator to reason about when removing one of them.
        cursor.execute(
            "SELECT id FROM ip_access_rules "
            "WHERE organization_id = %s AND rule_type = %s AND ip_address = %s",
            (organization_id, body.rule_type, ip_value),
        )
        if cursor.fetchone():
            cursor.close()
            return _error("An identical rule already exists for this organization", 409)

        # id is CHAR(26) with no DB-side default -- migration 009_ulid_safe
        # converted this table's PK away from AUTO_INCREMENT, so the ULID
        # must be supplied by the caller.
        rule_id = generate_ulid()
        cursor.execute(
            "INSERT INTO ip_access_rules "
            "(id, organization_id, rule_type, ip_address, description, active, created_by) "
            "VALUES (%s, %s, %s, %s, %s, 1, %s)",
            (
                rule_id,
                organization_id,
                body.rule_type,
                ip_value,
                body.description,
                # The operator's own email, not a shared service account:
                # ADR-002 SS3's entire point is that a privileged change is
                # attributable to a person.
                getattr(request.state, "operator_email", None),
            ),
        )
        conn.commit()

        cursor.execute(
            """
            SELECT r.id, r.organization_id, o.name AS organization_name, r.rule_type,
                   r.ip_address, r.description, r.active, r.created_by, r.created_at, r.updated_at
            FROM ip_access_rules r
            LEFT JOIN organizations o ON o.id = r.organization_id
            WHERE r.id = %s
            """,
            (rule_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        logger.warning(
            f"IP access rule created: id={rule_id} org={organization_id} "
            f"type={body.rule_type} ip={ip_value} by={ctx['operator_id']} reason={body.reason!r}"
        )
        return JSONResponse(
            content=create_api_response("success", "IP access rule created", _ip_rule_row(row)),
            status_code=201,
        )
    except Exception as exc:
        logger.error(f"Create IP rule failed: {exc}")
        return _error("Failed to create IP access rule", 500)
    finally:
        conn.close()


@router.put(
    "/ip-rules/{rule_id}",
    summary="Update an IP access rule",
    description="Changes description, active flag, or rule type. `ip_address` is deliberately "
    "immutable: editing the address in place would silently repoint an existing audit trail at a "
    "different network. Delete and recreate instead, so both actions are recorded.",
)
async def update_ip_rule(
    rule_id: str,
    body: IpRuleUpdateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    if body.rule_type is not None and body.rule_type not in _IP_RULE_TYPES:
        return _error(f"rule_type must be one of {', '.join(_IP_RULE_TYPES)}", 422)

    assignments: list = []
    params: list = []
    if body.description is not None:
        assignments.append("description = %s")
        params.append(body.description)
    if body.active is not None:
        assignments.append("active = %s")
        params.append(1 if body.active else 0)
    if body.rule_type is not None:
        assignments.append("rule_type = %s")
        params.append(body.rule_type)

    if not assignments:
        return _error("No updatable fields provided", 400)

    org_sql, org_params = _scoped_org_clause(ctx, None, "r")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        # Ownership is checked in the same statement that finds the row, and
        # a cross-org miss is a 404, never a 403 (conventions SS8: a 403
        # confirms the resource exists).
        cursor.execute(
            f"SELECT r.id FROM ip_access_rules r WHERE r.id = %s AND {org_sql}",
            [rule_id] + org_params,
        )
        if not cursor.fetchone():
            cursor.close()
            return _error("IP access rule not found", 404)

        cursor.execute(
            f"UPDATE ip_access_rules SET {', '.join(assignments)} WHERE id = %s",
            params + [rule_id],
        )
        conn.commit()

        cursor.execute(
            """
            SELECT r.id, r.organization_id, o.name AS organization_name, r.rule_type,
                   r.ip_address, r.description, r.active, r.created_by, r.created_at, r.updated_at
            FROM ip_access_rules r
            LEFT JOIN organizations o ON o.id = r.organization_id
            WHERE r.id = %s
            """,
            (rule_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        logger.warning(
            f"IP access rule updated: id={rule_id} by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response("success", "IP access rule updated", _ip_rule_row(row))
    except Exception as exc:
        logger.error(f"Update IP rule failed: {exc}")
        return _error("Failed to update IP access rule", 500)
    finally:
        conn.close()


@router.delete(
    "/ip-rules/{rule_id}",
    summary="Delete an IP access rule",
    description="Removes the rule outright. Deleting a tenant's LAST active whitelist rule "
    "returns that tenant from default-deny to default-allow -- the response reports how many "
    "active whitelist rules remain so the console can warn before that happens.",
)
async def delete_ip_rule(
    rule_id: str,
    body: ReasonRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    org_sql, org_params = _scoped_org_clause(ctx, None, "r")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT r.id, r.organization_id, r.rule_type, r.ip_address FROM ip_access_rules r "
            f"WHERE r.id = %s AND {org_sql}",
            [rule_id] + org_params,
        )
        rule = cursor.fetchone()
        if not rule:
            cursor.close()
            return _error("IP access rule not found", 404)

        cursor.execute("DELETE FROM ip_access_rules WHERE id = %s", (rule_id,))
        conn.commit()

        cursor.execute(
            "SELECT COUNT(*) AS remaining FROM ip_access_rules "
            "WHERE organization_id = %s AND rule_type = 'whitelist' AND active = 1",
            (rule["organization_id"],),
        )
        remaining = _as_int((cursor.fetchone() or {}).get("remaining"))
        cursor.close()

        logger.warning(
            f"IP access rule deleted: id={rule_id} org={rule['organization_id']} "
            f"type={rule['rule_type']} ip={rule['ip_address']} by={ctx['operator_id']} "
            f"reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            "IP access rule deleted",
            {
                "id": rule_id,
                "organization_id": rule["organization_id"],
                "active_whitelist_rules_remaining": remaining,
            },
        )
    except Exception as exc:
        logger.error(f"Delete IP rule failed: {exc}")
        return _error("Failed to delete IP access rule", 500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# C -- Geo policies (PRD SS5.6)
#
# `geo_policies` carries a UNIQUE key on organization_id (one policy per
# tenant, verified against alembic 0001_baseline), and its id is an INT
# AUTO_INCREMENT rather than a ULID -- this table predates 009_ulid_safe's
# conversions and was not one of them. Both facts shape the routes below:
# POST is a 409 when the tenant already has a policy, and {policy_id} is an
# int path parameter.
# ---------------------------------------------------------------------------


def _validate_countries(value, field: str) -> tuple:
    """(normalised list | None, error message | None).

    Codes are upper-cased before the shape check so 'us' is accepted and
    stored as 'US'. That is not cosmetic: security/geo_blocking.py compares
    the stored strings against a GeoIP lookup that returns upper-case
    codes, so a lower-case entry would silently never match -- a blocked
    country that is not blocked.
    """
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, f"{field} must be an array of ISO 3166-1 alpha-2 country codes"
    normalised: list = []
    for entry in value:
        if not isinstance(entry, str):
            return None, f"{field} must contain only strings"
        code = entry.strip().upper()
        if not _ISO_COUNTRY_RE.match(code):
            return None, (
                f"'{entry}' is not a valid ISO 3166-1 alpha-2 country code in {field} "
                "(two letters, e.g. US, GB, NG)"
            )
        if code not in normalised:
            normalised.append(code)
    return normalised, None


def _conflicting_country(allowed, blocked) -> str | None:
    """The first country listed in both lists, if any.

    geo_blocking.py checks the allow list first and returns early, so a
    country in both is silently allowed -- the operator who added it to
    `blocked_countries` would believe it was blocked. Rejecting the
    combination is the only way that intent cannot be lost.
    """
    if not allowed or not blocked:
        return None
    for code in allowed:
        if code in blocked:
            return code
    return None


class GeoPolicyCreateRequest(BaseModel):
    organization_id: str | None = Field(
        None, description="Owning tenant. Platform scope only; tenant scope always uses its own."
    )
    allowed_countries: list | None = Field(None, description="ISO 3166-1 alpha-2 codes")
    blocked_countries: list | None = Field(None, description="ISO 3166-1 alpha-2 codes")
    time_restrictions: dict | None = Field(
        None,
        description='Time-of-day rules, e.g. {"allowed_hours": {"start": 8, "end": 18}, '
        '"timezone_offset": 0, "applies_to_countries": ["US"]} -- the shape '
        "security/geo_blocking.py's _check_time_restrictions reads.",
    )
    action: str = Field("block", description="block | challenge | log_only")
    enabled: bool = True
    reason: str = Field(..., min_length=1, description="Why this policy is being created. Audited.")


class GeoPolicyUpdateRequest(BaseModel):
    allowed_countries: list | None = None
    blocked_countries: list | None = None
    time_restrictions: dict | None = None
    action: str | None = Field(None, description="block | challenge | log_only")
    enabled: bool | None = None
    reason: str = Field(..., min_length=1, description="Why this policy is being changed. Audited.")


def _geo_policy_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "organization_name": row.get("organization_name"),
        "allowed_countries": _json_field(row["allowed_countries"]) or [],
        "blocked_countries": _json_field(row["blocked_countries"]) or [],
        "time_restrictions": _json_field(row["time_restrictions"]),
        "action": row["action"],
        "enabled": bool(row["enabled"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


_GEO_POLICY_SELECT = """
    SELECT g.id, g.organization_id, o.name AS organization_name, g.allowed_countries,
           g.blocked_countries, g.time_restrictions, g.action, g.enabled,
           g.created_at, g.updated_at
    FROM geo_policies g
    LEFT JOIN organizations o ON o.id = g.organization_id
"""


@router.get(
    "/geo-policies",
    summary="List geo access policies",
    description="Paginated view over `geo_policies` (PRD SS5.6), one per tenant, as enforced by "
    "security/geo_blocking.py. org_filter applied.",
)
async def list_geo_policies(
    organization_id: str | None = Query(None, description="Platform scope only; ignored otherwise"),
    action: str | None = Query(None, description=" | ".join(_GEO_ACTIONS)),
    enabled: bool | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    if action and action not in _GEO_ACTIONS:
        return _error(f"action must be one of {', '.join(_GEO_ACTIONS)}", 422)

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    org_sql, org_params = _scoped_org_clause(ctx, organization_id, "g")
    conditions = [org_sql]
    params = list(org_params)
    if action:
        conditions.append("g.action = %s")
        params.append(action)
    if enabled is not None:
        conditions.append("g.enabled = %s")
        params.append(1 if enabled else 0)

    where = "WHERE " + " AND ".join(conditions)

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM geo_policies g {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"{_GEO_POLICY_SELECT} {where} ORDER BY g.created_at DESC, g.id DESC "
            f"LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "Geo policies retrieved",
            {
                "items": [_geo_policy_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List geo policies failed: {exc}")
        return _error("Failed to retrieve geo policies", 500)
    finally:
        conn.close()


@router.post(
    "/geo-policies",
    summary="Create a geo access policy",
    description="One policy per tenant -- `geo_policies` has a UNIQUE key on organization_id, so "
    "a second create for the same tenant is a 409, not a second row. Country codes must be ISO "
    "3166-1 alpha-2 (422 otherwise), and a country may not appear in both allowed and blocked "
    "(422 naming it): geo_blocking.py evaluates the allow list first and returns early, so such "
    "a country would be silently allowed and the operator's block would be lost.",
)
async def create_geo_policy(
    body: GeoPolicyCreateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    if body.action not in _GEO_ACTIONS:
        return _error(f"action must be one of {', '.join(_GEO_ACTIONS)}", 422)

    allowed, error = _validate_countries(body.allowed_countries, "allowed_countries")
    if error:
        return _error(error, 422)
    blocked, error = _validate_countries(body.blocked_countries, "blocked_countries")
    if error:
        return _error(error, 422)

    conflict = _conflicting_country(allowed, blocked)
    if conflict:
        return _error(
            f"Country '{conflict}' appears in both allowed_countries and blocked_countries",
            422,
        )

    organization_id = (
        ctx["organization_id"] if ctx["scope"] == "organization" else body.organization_id
    )
    if not organization_id:
        return _error("organization_id is required (geo_policies.organization_id is NOT NULL)", 422)

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM organizations WHERE id = %s", (organization_id,))
        if not cursor.fetchone():
            cursor.close()
            return _error("Organization not found", 404)

        cursor.execute("SELECT id FROM geo_policies WHERE organization_id = %s", (organization_id,))
        if cursor.fetchone():
            cursor.close()
            return _error("This organization already has a geo policy -- update it instead", 409)

        # id is INT AUTO_INCREMENT here (unlike the ULID tables), so it is
        # deliberately not supplied.
        cursor.execute(
            "INSERT INTO geo_policies "
            "(organization_id, allowed_countries, blocked_countries, time_restrictions, "
            "action, enabled) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                organization_id,
                json.dumps(allowed) if allowed is not None else None,
                json.dumps(blocked) if blocked is not None else None,
                json.dumps(body.time_restrictions) if body.time_restrictions is not None else None,
                body.action,
                1 if body.enabled else 0,
            ),
        )
        policy_id = cursor.lastrowid
        conn.commit()

        cursor.execute(f"{_GEO_POLICY_SELECT} WHERE g.id = %s", (policy_id,))
        row = cursor.fetchone()
        cursor.close()

        logger.warning(
            f"Geo policy created: id={policy_id} org={organization_id} action={body.action} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return JSONResponse(
            content=create_api_response("success", "Geo policy created", _geo_policy_row(row)),
            status_code=201,
        )
    except Exception as exc:
        if "Duplicate entry" in str(exc):
            # The SELECT above loses a race against a concurrent create; the
            # UNIQUE key is the real guarantee, so a duplicate that reaches
            # the INSERT is still a 409, not a 500.
            return _error("This organization already has a geo policy", 409)
        logger.error(f"Create geo policy failed: {exc}")
        return _error("Failed to create geo policy", 500)
    finally:
        conn.close()


@router.put(
    "/geo-policies/{policy_id}",
    summary="Update a geo access policy",
    description="Partial update. The allowed/blocked conflict check runs against the MERGED "
    "policy, not just the fields in this request -- adding 'NG' to allowed_countries when it is "
    "already in the stored blocked_countries is exactly the mistake the check exists to catch, "
    "and it is invisible if only the submitted fields are compared.",
)
async def update_geo_policy(
    policy_id: int,
    body: GeoPolicyUpdateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    if body.action is not None and body.action not in _GEO_ACTIONS:
        return _error(f"action must be one of {', '.join(_GEO_ACTIONS)}", 422)

    allowed, error = _validate_countries(body.allowed_countries, "allowed_countries")
    if error:
        return _error(error, 422)
    blocked, error = _validate_countries(body.blocked_countries, "blocked_countries")
    if error:
        return _error(error, 422)

    org_sql, org_params = _scoped_org_clause(ctx, None, "g")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT g.id, g.allowed_countries, g.blocked_countries FROM geo_policies g "
            f"WHERE g.id = %s AND {org_sql}",
            [policy_id] + org_params,
        )
        existing = cursor.fetchone()
        if not existing:
            cursor.close()
            return _error("Geo policy not found", 404)

        stored_allowed = _json_field(existing["allowed_countries"])
        stored_blocked = _json_field(existing["blocked_countries"])
        merged_allowed = allowed if allowed is not None else stored_allowed
        merged_blocked = blocked if blocked is not None else stored_blocked
        conflict = _conflicting_country(merged_allowed, merged_blocked)
        if conflict:
            cursor.close()
            return _error(
                f"Country '{conflict}' would appear in both allowed_countries and "
                "blocked_countries after this change",
                422,
            )

        assignments: list = []
        params: list = []
        if allowed is not None:
            assignments.append("allowed_countries = %s")
            params.append(json.dumps(allowed))
        if blocked is not None:
            assignments.append("blocked_countries = %s")
            params.append(json.dumps(blocked))
        if body.time_restrictions is not None:
            assignments.append("time_restrictions = %s")
            params.append(json.dumps(body.time_restrictions))
        if body.action is not None:
            assignments.append("action = %s")
            params.append(body.action)
        if body.enabled is not None:
            assignments.append("enabled = %s")
            params.append(1 if body.enabled else 0)

        if not assignments:
            cursor.close()
            return _error("No updatable fields provided", 400)

        cursor.execute(
            f"UPDATE geo_policies SET {', '.join(assignments)} WHERE id = %s",
            params + [policy_id],
        )
        conn.commit()

        cursor.execute(f"{_GEO_POLICY_SELECT} WHERE g.id = %s", (policy_id,))
        row = cursor.fetchone()
        cursor.close()

        logger.warning(
            f"Geo policy updated: id={policy_id} by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response("success", "Geo policy updated", _geo_policy_row(row))
    except Exception as exc:
        logger.error(f"Update geo policy failed: {exc}")
        return _error("Failed to update geo policy", 500)
    finally:
        conn.close()


@router.delete(
    "/geo-policies/{policy_id}",
    summary="Delete a geo access policy",
    description="Removes the tenant's geo policy entirely, which returns that tenant to "
    "unrestricted country access -- geo_blocking.py treats 'no policy row' as allow-all. Prefer "
    "PUT with enabled=false when the intent is a temporary suspension, so the country lists "
    "survive to be re-enabled.",
)
async def delete_geo_policy(
    policy_id: int,
    body: ReasonRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="operator")),
):
    org_sql, org_params = _scoped_org_clause(ctx, None, "g")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT g.id, g.organization_id FROM geo_policies g WHERE g.id = %s AND {org_sql}",
            [policy_id] + org_params,
        )
        policy = cursor.fetchone()
        if not policy:
            cursor.close()
            return _error("Geo policy not found", 404)

        cursor.execute("DELETE FROM geo_policies WHERE id = %s", (policy_id,))
        conn.commit()
        cursor.close()

        logger.warning(
            f"Geo policy deleted: id={policy_id} org={policy['organization_id']} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            "Geo policy deleted",
            {"id": policy_id, "organization_id": policy["organization_id"]},
        )
    except Exception as exc:
        logger.error(f"Delete geo policy failed: {exc}")
        return _error("Failed to delete geo policy", 500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# D -- DLP (phase-04 SS4.2)
#
# Policy management is `admin` and violation review is `operator`, per
# phase-04 SS4.2's "Policy management (admin)" against the read-only
# violation list. Both are above `support`: knowing which patterns a tenant
# protects is itself a map of what that tenant considers sensitive.
#
# dlp_policies.id and dlp_violations.policy_id are INT (not ULID) and
# dlp_violations.id is BIGINT -- both AUTO_INCREMENT, verified against
# 0001_baseline. Hence int path parameters and no generate_ulid() here.
# ---------------------------------------------------------------------------

_DLP_POLICY_SORTS = {
    "created_at": "p.created_at",
    "updated_at": "p.updated_at",
    "name": "p.name",
    "severity": "p.severity",
}

_DLP_VIOLATION_SORTS = {
    "created_at": "v.created_at",
    "severity": "v.severity",
    "sender": "v.sender",
}

_DLP_POLICY_SELECT = """
    SELECT p.id, p.organization_id, o.name AS organization_name, p.name, p.description,
           p.policy_type, p.patterns, p.action, p.severity, p.enabled, p.apply_to,
           p.created_at, p.updated_at
    FROM dlp_policies p
    LEFT JOIN organizations o ON o.id = p.organization_id
"""


def _dlp_policy_row(row: dict) -> dict:
    """The policy IS the rule, so `patterns` is returned in full here.

    That is the opposite of the violation shape below, and deliberately so:
    a policy's patterns are configuration an operator must be able to read
    and correct, while a violation's `matched_pattern` is the customer data
    that matched. Same word, opposite privacy class.
    """
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "organization_name": row.get("organization_name"),
        "name": row["name"],
        "description": row["description"],
        "policy_type": row["policy_type"],
        "patterns": _json_field(row["patterns"]),
        "action": row["action"],
        "severity": row["severity"],
        "enabled": bool(row["enabled"]),
        "apply_to": row["apply_to"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


class DlpPolicyCreateRequest(BaseModel):
    organization_id: str | None = Field(
        None, description="Owning tenant. Platform scope only; tenant scope uses its own."
    )
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    policy_type: str = Field("keyword", description=" | ".join(_DLP_POLICY_TYPES))
    patterns: list = Field(..., description="Non-empty array of patterns to match")
    action: str = Field("notify", description=" | ".join(_DLP_ACTIONS))
    severity: str = Field("medium", description=" | ".join(_DLP_SEVERITIES))
    enabled: bool = True
    apply_to: str = Field("outbound", description=" | ".join(_DLP_APPLY_TO))
    reason: str = Field(..., min_length=1, description="Why this policy is being created. Audited.")


class DlpPolicyUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    policy_type: str | None = None
    patterns: list | None = None
    action: str | None = None
    severity: str | None = None
    enabled: bool | None = None
    apply_to: str | None = None
    reason: str = Field(..., min_length=1, description="Why this policy is being changed. Audited.")


def _validate_dlp_enums(policy_type, action, severity, apply_to) -> str | None:
    for value, allowed, field in (
        (policy_type, _DLP_POLICY_TYPES, "policy_type"),
        (action, _DLP_ACTIONS, "action"),
        (severity, _DLP_SEVERITIES, "severity"),
        (apply_to, _DLP_APPLY_TO, "apply_to"),
    ):
        if value is not None and value not in allowed:
            return f"{field} must be one of {', '.join(allowed)}"
    return None


@router.get(
    "/dlp/policies",
    summary="List DLP policies",
    description="Paginated view over `dlp_policies` (phase-04 SS4.2), with org_filter applied. "
    "Readable at `operator`; writes below are `admin` because a disabled DLP policy is an open "
    "exfiltration path and the tenant has no way to see that it was turned off.",
)
async def list_dlp_policies(
    organization_id: str | None = Query(None, description="Platform scope only; ignored otherwise"),
    policy_type: str | None = Query(None, description=" | ".join(_DLP_POLICY_TYPES)),
    severity: str | None = Query(None, description=" | ".join(_DLP_SEVERITIES)),
    apply_to: str | None = Query(None, description=" | ".join(_DLP_APPLY_TO)),
    enabled: bool | None = Query(None),
    q: str | None = Query(None, description="Free text over name and description"),
    sort_by: str | None = Query("created_at", description=" | ".join(_DLP_POLICY_SORTS)),
    sort_dir: str | None = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    enum_error = _validate_dlp_enums(policy_type, None, severity, apply_to)
    if enum_error:
        return _error(enum_error, 422)

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    org_sql, org_params = _scoped_org_clause(ctx, organization_id, "p")
    conditions = [org_sql]
    params = list(org_params)
    if policy_type:
        conditions.append("p.policy_type = %s")
        params.append(policy_type)
    if severity:
        conditions.append("p.severity = %s")
        params.append(severity)
    if apply_to:
        conditions.append("p.apply_to = %s")
        params.append(apply_to)
    if enabled is not None:
        conditions.append("p.enabled = %s")
        params.append(1 if enabled else 0)
    if q:
        conditions.append("(p.name LIKE %s OR p.description LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    where = "WHERE " + " AND ".join(conditions)
    order_by = _sort_clause(sort_by, sort_dir, _DLP_POLICY_SORTS, "created_at")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM dlp_policies p {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"{_DLP_POLICY_SELECT} {where} {order_by}, p.id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "DLP policies retrieved",
            {
                "items": [_dlp_policy_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List DLP policies failed: {exc}")
        return _error("Failed to retrieve DLP policies", 500)
    finally:
        conn.close()


@router.post(
    "/dlp/policies",
    summary="Create a DLP policy",
    description="`admin` (phase-04 SS4.2: policy management is admin). `patterns` must be a "
    "non-empty array -- the column is NOT NULL and a policy with nothing to match is a control "
    "that appears active and inspects nothing.",
)
async def create_dlp_policy(
    body: DlpPolicyCreateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="admin")),
):
    enum_error = _validate_dlp_enums(body.policy_type, body.action, body.severity, body.apply_to)
    if enum_error:
        return _error(enum_error, 422)
    if not body.patterns:
        return _error("patterns must be a non-empty array", 422)

    organization_id = (
        ctx["organization_id"] if ctx["scope"] == "organization" else body.organization_id
    )
    if not organization_id:
        return _error("organization_id is required (dlp_policies.organization_id is NOT NULL)", 422)

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM organizations WHERE id = %s", (organization_id,))
        if not cursor.fetchone():
            cursor.close()
            return _error("Organization not found", 404)

        # No UNIQUE key on (organization_id, name) in the schema, so this is
        # an application-level guard rather than a caught IntegrityError:
        # two policies with the same name are indistinguishable in the
        # violation list, which is where an operator has to act on them.
        cursor.execute(
            "SELECT id FROM dlp_policies WHERE organization_id = %s AND name = %s",
            (organization_id, body.name),
        )
        if cursor.fetchone():
            cursor.close()
            return _error("A DLP policy with that name already exists for this organization", 409)

        cursor.execute(
            "INSERT INTO dlp_policies "
            "(organization_id, name, description, policy_type, patterns, action, severity, "
            "enabled, apply_to) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                organization_id,
                body.name,
                body.description,
                body.policy_type,
                json.dumps(body.patterns),
                body.action,
                body.severity,
                1 if body.enabled else 0,
                body.apply_to,
            ),
        )
        policy_id = cursor.lastrowid
        conn.commit()

        cursor.execute(f"{_DLP_POLICY_SELECT} WHERE p.id = %s", (policy_id,))
        row = cursor.fetchone()
        cursor.close()

        logger.warning(
            f"DLP policy created: id={policy_id} org={organization_id} name={body.name!r} "
            f"action={body.action} by={ctx['operator_id']} reason={body.reason!r}"
        )
        return JSONResponse(
            content=create_api_response("success", "DLP policy created", _dlp_policy_row(row)),
            status_code=201,
        )
    except Exception as exc:
        logger.error(f"Create DLP policy failed: {exc}")
        return _error("Failed to create DLP policy", 500)
    finally:
        conn.close()


@router.put(
    "/dlp/policies/{policy_id}",
    summary="Update a DLP policy",
    description="`admin`. Partial update; `patterns`, when supplied, replaces the array wholesale "
    "rather than merging -- a merge would make removing a pattern impossible through this API.",
)
async def update_dlp_policy(
    policy_id: int,
    body: DlpPolicyUpdateRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="admin")),
):
    enum_error = _validate_dlp_enums(body.policy_type, body.action, body.severity, body.apply_to)
    if enum_error:
        return _error(enum_error, 422)
    if body.patterns is not None and not body.patterns:
        return _error("patterns must be a non-empty array", 422)

    assignments: list = []
    params: list = []
    for column, value in (
        ("name", body.name),
        ("description", body.description),
        ("policy_type", body.policy_type),
        ("action", body.action),
        ("severity", body.severity),
        ("apply_to", body.apply_to),
    ):
        if value is not None:
            assignments.append(f"{column} = %s")
            params.append(value)
    if body.patterns is not None:
        assignments.append("patterns = %s")
        params.append(json.dumps(body.patterns))
    if body.enabled is not None:
        assignments.append("enabled = %s")
        params.append(1 if body.enabled else 0)

    if not assignments:
        return _error("No updatable fields provided", 400)

    org_sql, org_params = _scoped_org_clause(ctx, None, "p")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT p.id FROM dlp_policies p WHERE p.id = %s AND {org_sql}",
            [policy_id] + org_params,
        )
        if not cursor.fetchone():
            cursor.close()
            return _error("DLP policy not found", 404)

        cursor.execute(
            f"UPDATE dlp_policies SET {', '.join(assignments)} WHERE id = %s",
            params + [policy_id],
        )
        conn.commit()

        cursor.execute(f"{_DLP_POLICY_SELECT} WHERE p.id = %s", (policy_id,))
        row = cursor.fetchone()
        cursor.close()

        logger.warning(
            f"DLP policy updated: id={policy_id} enabled={body.enabled} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response("success", "DLP policy updated", _dlp_policy_row(row))
    except Exception as exc:
        logger.error(f"Update DLP policy failed: {exc}")
        return _error("Failed to update DLP policy", 500)
    finally:
        conn.close()


@router.delete(
    "/dlp/policies/{policy_id}",
    summary="Delete a DLP policy",
    description="`admin`, reason required. Past violations are deliberately NOT removed with it: "
    "`dlp_violations` has no foreign key to `dlp_policies` in the shipped schema (verified "
    "against 0001_baseline -- the ORM model declares an ON DELETE CASCADE the database does not "
    "have), so the violation history survives, which is the correct outcome. A violation record "
    "is evidence that data left, and deleting the rule must not delete the evidence. The response "
    "reports how many violations were retained.",
)
async def delete_dlp_policy(
    policy_id: int,
    body: ReasonRequest,
    ctx: AuthContext = Depends(require_scope("platform", "write", role="admin")),
):
    org_sql, org_params = _scoped_org_clause(ctx, None, "p")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT p.id, p.organization_id, p.name FROM dlp_policies p "
            f"WHERE p.id = %s AND {org_sql}",
            [policy_id] + org_params,
        )
        policy = cursor.fetchone()
        if not policy:
            cursor.close()
            return _error("DLP policy not found", 404)

        cursor.execute(
            "SELECT COUNT(*) AS total FROM dlp_violations WHERE policy_id = %s", (policy_id,)
        )
        retained = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute("DELETE FROM dlp_policies WHERE id = %s", (policy_id,))
        conn.commit()
        cursor.close()

        logger.warning(
            f"DLP policy deleted: id={policy_id} org={policy['organization_id']} "
            f"name={policy['name']!r} violations_retained={retained} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )
        return create_api_response(
            "success",
            "DLP policy deleted",
            {
                "id": policy_id,
                "organization_id": policy["organization_id"],
                "violations_retained": retained,
            },
        )
    except Exception as exc:
        logger.error(f"Delete DLP policy failed: {exc}")
        return _error("Failed to delete DLP policy", 500)
    finally:
        conn.close()


# --- Violations -------------------------------------------------------------
#
# PRIVACY (phase-04 SS4.2, ADR-002 SS5). `dlp_violations.matched_pattern`
# holds the text that tripped the rule and `details` holds the milter's
# context around it -- for a policy whose whole purpose is to stop a credit
# card or a national ID leaving, those two columns contain exactly that
# value. Returning them would make the console a search interface for the
# data the policy exists to protect, and would do it across every tenant at
# once.
#
# The chosen approach is NON-RETRIEVAL, not post-hoc redaction: neither
# column is named in any SELECT below. `has_match` -- a boolean derived in
# SQL from `matched_pattern IS NOT NULL AND matched_pattern <> ''` -- plus
# the policy's own name and `violation_type` answer the operational
# question ("which rule fired, on whose mail, and what did we do about
# it?") without the value ever entering the process. Truncation was
# rejected: a truncated card number is still card digits, and a prefix is
# enough to confirm a guess.


def _dlp_violation_row(row: dict) -> dict:
    """One violation for the wire -- the rule, never the matched content.

    Note what is absent by construction: `matched_pattern` and `details`
    are not selected by the queries that feed this, so there is no key here
    to forget to strip.
    """
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "organization_name": row.get("organization_name"),
        "policy_id": row["policy_id"],
        # LEFT JOIN: a policy deleted after the violation was recorded
        # leaves the history intact but nameless (see DELETE above).
        "policy_name": row.get("policy_name"),
        "policy_type": row.get("policy_type"),
        "message_id": row["message_id"],
        "sender": row["sender"],
        "recipient": row["recipient"],
        "violation_type": row["violation_type"],
        "action_taken": row["action_taken"],
        "severity": row["severity"],
        # The redaction contract: whether the policy matched, never what it
        # matched on. `details` has no representation here at all.
        "has_match": bool(row["has_match"]),
        "matched_pattern": None,
        "details": None,
        "redacted": True,
        "created_at": _iso(row["created_at"]),
    }


_DLP_VIOLATION_SELECT = """
    SELECT v.id, v.organization_id, o.name AS organization_name, v.policy_id,
           p.name AS policy_name, p.policy_type AS policy_type, v.message_id, v.sender,
           v.recipient, v.violation_type, v.action_taken, v.severity, v.created_at,
           (v.matched_pattern IS NOT NULL AND v.matched_pattern <> '') AS has_match
    FROM dlp_violations v
    LEFT JOIN dlp_policies p ON p.id = v.policy_id
    LEFT JOIN organizations o ON o.id = v.organization_id
"""


@router.get(
    "/dlp/violations",
    summary="List DLP violations (content redacted)",
    description="Cross-tenant violation feed: which policy fired, on whose message, and what was "
    "done about it. **`matched_pattern` and `details` are never returned and are never even "
    "selected** (phase-04 SS4.2: show the matched rule, never the matched content; ADR-002 SS5). "
    "`has_match` reports that the policy matched; `redacted: true` marks the response so a client "
    "cannot mistake the absence for an empty column.",
)
async def list_dlp_violations(
    organization_id: str | None = Query(None, description="Platform scope only; ignored otherwise"),
    policy_id: int | None = Query(None),
    severity: str | None = Query(None),
    violation_type: str | None = Query(None),
    action_taken: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO date or datetime"),
    date_to: str | None = Query(None, description="ISO date or datetime"),
    q: str | None = Query(None, description="Free text over sender and recipient"),
    sort_by: str | None = Query("created_at", description=" | ".join(_DLP_VIOLATION_SORTS)),
    sort_dir: str | None = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    org_sql, org_params = _scoped_org_clause(ctx, organization_id, "v")
    conditions = [org_sql]
    params = list(org_params)
    if policy_id is not None:
        conditions.append("v.policy_id = %s")
        params.append(policy_id)
    # severity/violation_type/action_taken are free-form VARCHARs on this
    # table (not ENUMs like they are on dlp_policies), so they are matched
    # as bound parameters rather than validated against a whitelist -- the
    # milter is free to write values this API has never heard of.
    if severity:
        conditions.append("v.severity = %s")
        params.append(severity)
    if violation_type:
        conditions.append("v.violation_type = %s")
        params.append(violation_type)
    if action_taken:
        conditions.append("v.action_taken = %s")
        params.append(action_taken)
    try:
        if date_from:
            conditions.append("v.created_at >= %s")
            params.append(_parse_iso_datetime(date_from))
        if date_to:
            conditions.append("v.created_at <= %s")
            params.append(_parse_iso_datetime(date_to, end_of_day=True))
    except ValueError:
        return _error("date_from/date_to must be ISO dates or datetimes", 422)

    if q:
        conditions.append("(v.sender LIKE %s OR v.recipient LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    where = "WHERE " + " AND ".join(conditions)
    order_by = _sort_clause(sort_by, sort_dir, _DLP_VIOLATION_SORTS, "created_at")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS total FROM dlp_violations v {where}", params)
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"{_DLP_VIOLATION_SELECT} {where} {order_by}, v.id DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        rows = cursor.fetchall() or []
        cursor.close()

        return create_api_response(
            "success",
            "DLP violations retrieved",
            {
                "items": [_dlp_violation_row(row) for row in rows],
                "pagination": _paginate(page, per_page, total),
            },
        )
    except Exception as exc:
        logger.error(f"List DLP violations failed: {exc}")
        return _error("Failed to retrieve DLP violations", 500)
    finally:
        conn.close()


# NOTE: /dlp/violations/summary MUST stay declared above
# /dlp/violations/{violation_id}. FastAPI matches routes in declaration
# order, so the parameterised route would otherwise swallow "summary" and
# fail its int coercion.
@router.get(
    "/dlp/violations/summary",
    summary="DLP violation summary",
    description="Counts by severity, policy and action taken, plus a 30-day daily series -- the "
    "'is exfiltration increasing' view. Aggregates only; no violation content is involved at any "
    "point, since the columns that hold it are not read here either.",
)
async def dlp_violations_summary(
    organization_id: str | None = Query(None, description="Platform scope only; ignored otherwise"),
    days: int = Query(30, ge=1, le=365, description="Length of the daily series"),
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    org_sql, org_params = _scoped_org_clause(ctx, organization_id, "v")
    window_start = (datetime.now() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            f"SELECT COUNT(*) AS total FROM dlp_violations v WHERE {org_sql}", list(org_params)
        )
        total = _as_int((cursor.fetchone() or {}).get("total"))

        cursor.execute(
            f"SELECT v.severity, COUNT(*) AS count FROM dlp_violations v WHERE {org_sql} "
            f"GROUP BY v.severity ORDER BY count DESC",
            list(org_params),
        )
        by_severity = [
            {"severity": row["severity"], "count": _as_int(row["count"])}
            for row in (cursor.fetchall() or [])
        ]

        cursor.execute(
            f"""
            SELECT v.policy_id, p.name AS policy_name, COUNT(*) AS count
            FROM dlp_violations v
            LEFT JOIN dlp_policies p ON p.id = v.policy_id
            WHERE {org_sql}
            GROUP BY v.policy_id, p.name
            ORDER BY count DESC
            LIMIT 25
            """,
            list(org_params),
        )
        by_policy = [
            {
                "policy_id": row["policy_id"],
                "policy_name": row["policy_name"],
                "count": _as_int(row["count"]),
            }
            for row in (cursor.fetchall() or [])
        ]

        cursor.execute(
            f"SELECT v.action_taken, COUNT(*) AS count FROM dlp_violations v WHERE {org_sql} "
            f"GROUP BY v.action_taken ORDER BY count DESC",
            list(org_params),
        )
        by_action = [
            {"action_taken": row["action_taken"], "count": _as_int(row["count"])}
            for row in (cursor.fetchall() or [])
        ]

        cursor.execute(
            f"""
            SELECT DATE(v.created_at) AS bucket_date, COUNT(*) AS count
            FROM dlp_violations v
            WHERE {org_sql} AND v.created_at >= %s
            GROUP BY bucket_date
            """,
            list(org_params) + [window_start],
        )
        by_day = {row["bucket_date"]: _as_int(row["count"]) for row in (cursor.fetchall() or [])}
        cursor.close()

        # Zero-filled in Python for the same reason as the hourly series
        # above: a gap in a chart reads as "no violations", which is the
        # opposite of "we have no data for that day".
        series = []
        for offset in range(days):
            day = (window_start + timedelta(days=offset)).date()
            series.append({"date": day.isoformat(), "count": by_day.get(day, 0)})

        return create_api_response(
            "success",
            "DLP violation summary retrieved",
            {
                "total": total,
                "days": days,
                "by_severity": by_severity,
                "by_policy": by_policy,
                "by_action_taken": by_action,
                "series": series,
            },
        )
    except Exception as exc:
        logger.error(f"DLP violation summary failed: {exc}")
        return _error("Failed to retrieve DLP violation summary", 500)
    finally:
        conn.close()


@router.get(
    "/dlp/violations/{violation_id}",
    summary="Get one DLP violation (content redacted)",
    description="Same fields, and the same redaction, as the list view. There is deliberately no "
    "'reveal' variant of this endpoint: phase-04 SS4.2 and ADR-002 SS5 place the matched content "
    "outside platform scope entirely, and an endpoint that could return it would make every "
    "other guard here decorative.",
)
async def get_dlp_violation(
    violation_id: int,
    ctx: AuthContext = Depends(require_scope("platform", "read", role="operator")),
):
    org_sql, org_params = _scoped_org_clause(ctx, None, "v")

    conn = get_db_connection()
    if not conn:
        return _db_error()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"{_DLP_VIOLATION_SELECT} WHERE v.id = %s AND {org_sql}",
            [violation_id] + list(org_params),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return _error("DLP violation not found", 404)
        return create_api_response("success", "DLP violation retrieved", _dlp_violation_row(row))
    except Exception as exc:
        logger.error(f"Get DLP violation failed: {exc}")
        return _error("Failed to retrieve DLP violation", 500)
    finally:
        conn.close()
