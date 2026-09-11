#!/usr/bin/env python3
"""
SSL Certificate Status API Routes

Provides visibility into the certificate management system:
- Current status of all domain certificates (active, failed, expired)
- ACME account rotation stats (certs issued, success/failure rates, last used)
- Per-domain cert detail and renewal history
- SNI configuration summary

All endpoints require API key authentication.
Admin endpoints additionally require admin_access on the API key.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from schemas.common import ErrorResponse
from schemas.ssl import AcmeAccountsResponse
from utils.auth import create_api_response, require_api_key
from utils.database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# What `ssl_certificates` actually has (alembic/versions/0001_baseline.py --
# a mysqldump of the live DB, checked before touching any query in this file)
#
#   id CHAR(26), domain_id CHAR(26), certificate_path, private_key_path,
#   chain_path, status ENUM('active','expired','revoked','pending'),
#   issuer, valid_from DATETIME, valid_until DATETIME, auto_renew,
#   created_at DATETIME, last_renewed DATETIME, fingerprint, algorithm,
#   key_size
#
# Two things this file assumed and the table does not have:
#
#   * `error_message` -- no such column, and no error column of any name.
#     Every handler below that read row["error_message"] raised KeyError on
#     the returned dict (the SELECTs never asked for it either), so
#     GET /certificates and GET /certificates/{domain} 500'd on every call.
#     Returned as a constant null now, with _SSL_SCHEMA_NOTE saying why,
#     rather than dropped: the console renders the field, and a null with an
#     explanation beats a missing key or an invented column.
#   * `updated_at` -- no such column either. The old `ORDER BY updated_at
#     DESC` on the ssl_certificates/domains join was therefore NOT ambiguous
#     as suspected; MySQL resolved it to `domains.updated_at` and silently
#     sorted certificates by when their *domain row* was last edited. Every
#     ORDER BY below is table-qualified now, and the default sort is
#     valid_until ASC -- what an expiry screen is for.
#
# The status filter's old whitelist ('active','failed','expired') did not
# match the enum either: there is no 'failed' member, and 'revoked' and
# 'pending' were unreachable. Corrected to the real members.
_SSL_STATUSES = ("active", "expired", "revoked", "pending")

_SSL_SCHEMA_NOTE = (
    "ssl_certificates has no error column, so `error_message` is always null. The renewal "
    "error text exists only in cert_manager's own log "
    "(/var/log/cert_manager/cert_manager.log in the cert_manager container); surfacing it "
    "through this API needs a column on ssl_certificates and a writer in "
    "mailer/cert_manager/scripts/cert_manager.py::update_certificate_database()."
)

# ORDER BY takes no bound parameter, so a sort key must come from a closed
# mapping -- same reasoning as routes/domains.py's _DOMAIN_SORT_KEYS. Values
# are table-qualified so the join can never make one ambiguous.
_CERT_SORT_KEYS = {
    "domain": "d.domain",
    "valid_until": "sc.valid_until",
    "status": "sc.status",
    "issued_at": "sc.created_at",
}
_CERT_SORT_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}


@router.get(
    "/status",
    summary="Get SSL certificate status",
    description="Returns the health and expiry status of all TLS certificates managed by the system. Includes aggregate counts of active, failed, and expired certificates, ACME account health, the most recently issued certificate, and certificates expiring within 30 days.",
)
@require_api_key("read", scope="platform", role="support")
async def get_ssl_status(include_accounts: str = Query("true")):
    """
    Overall SSL certificate system status.

    Returns aggregate counts (active, failed, expired certs), ACME account
    health, and the timestamp of the last successful issuance.

    Query params:
        include_accounts (bool): include per-account breakdown (default true)
    """
    include_accounts_bool = include_accounts.lower() != "false"

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        # Aggregate cert counts by status. No join needed -- nothing here
        # reads a `domains` column, and the LEFT JOIN that used to be here
        # only widened the scan.
        cursor.execute("""
            SELECT sc.status, COUNT(*) AS count
            FROM ssl_certificates sc
            GROUP BY sc.status
        """)
        status_rows = cursor.fetchall()
        cert_counts = {row["status"]: row["count"] for row in status_rows}

        # Most recent successful issuance. Ordered by COALESCE(last_renewed,
        # created_at): a certificate issued once and never renewed has a
        # NULL last_renewed, and ordering on that alone would rank every
        # never-renewed cert as oldest. The old query ordered by an
        # `updated_at` that ssl_certificates does not have -- MySQL resolved
        # it to domains.updated_at -- and then read last_issued["updated_at"]
        # off a row whose SELECT list never included it, raising KeyError.
        cursor.execute("""
            SELECT d.domain, COALESCE(sc.last_renewed, sc.created_at) AS issued_at
            FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id
            WHERE sc.status = 'active'
            ORDER BY issued_at DESC
            LIMIT 1
        """)
        last_issued = cursor.fetchone()

        # Certs expiring in the next 30 days. The old version approximated
        # this as "updated_at is more than 60 days old", which measured the
        # wrong column on the wrong table entirely. valid_until is the real
        # expiry, and it is indexed (idx_ssl_valid_until).
        cursor.execute("""
            SELECT COUNT(*) AS expiring_soon
            FROM ssl_certificates sc
            WHERE sc.status = 'active'
              AND sc.valid_until IS NOT NULL
              AND sc.valid_until > NOW()
              AND sc.valid_until <= NOW() + INTERVAL 30 DAY
        """)
        expiring_row = cursor.fetchone()

        payload = {
            "certificates": {
                "active": cert_counts.get("active", 0),
                "expired": cert_counts.get("expired", 0),
                "revoked": cert_counts.get("revoked", 0),
                "pending": cert_counts.get("pending", 0),
                # Kept as a constant 0 rather than removed: the enum has no
                # 'failed' member (see _SSL_STATUSES), so the old
                # cert_counts.get("failed") could only ever be 0 -- but the
                # console renders the tile, and a documented zero beats a
                # key that vanishes.
                "failed": 0,
                "total": sum(cert_counts.values()),
                "expiring_soon": expiring_row["expiring_soon"] if expiring_row else 0,
            },
            "last_issued": {
                "domain": (last_issued.get("domain") or "unknown") if last_issued else None,
                "issued_at": last_issued["issued_at"].isoformat()
                if last_issued and last_issued.get("issued_at")
                else None,
            },
            "schema_note": _SSL_SCHEMA_NOTE,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # ACME account breakdown
        if include_accounts_bool:
            cursor.execute("""
                SELECT
                    email,
                    certs_issued,
                    success_count,
                    failure_count,
                    last_used,
                    created_at
                FROM acme_account_stats
                ORDER BY certs_issued DESC
            """)
            accounts = cursor.fetchall()
            payload["acme_accounts"] = [
                {
                    "email": row["email"],
                    "certs_issued": row["certs_issued"],
                    "success_count": row["success_count"],
                    "failure_count": row["failure_count"],
                    "success_rate": (
                        round(row["success_count"] / row["certs_issued"] * 100, 1)
                        if row["certs_issued"] > 0
                        else None
                    ),
                    "last_used": row["last_used"].isoformat() if row["last_used"] else None,
                    "registered_at": row.get("created_at").isoformat()
                    if row.get("created_at")
                    else None,
                }
                for row in accounts
            ]
            payload["acme_accounts_total"] = len(accounts)

        cursor.close()
        conn.close()
        return create_api_response("success", "SSL status retrieved", payload)

    except Exception as e:
        logger.error(f"SSL status query failed: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve SSL status"), status_code=500
        )


def _certificate_item(row: dict) -> dict:
    """One certificate as the console's expiry screen needs it.

    `days_until_expiry` is computed here rather than left to the client so
    the "highlight anything under 30 days" rule (phase-03 SS3.3) is decided
    against the server's clock, not a browser's -- a workstation with a
    skewed clock must not be able to hide an expiring certificate.
    """
    valid_until = row.get("valid_until")
    days_until_expiry = None
    if valid_until:
        days_until_expiry = (valid_until - datetime.now()).days

    return {
        "id": row["id"],
        "domain": row.get("domain") or "unknown",
        "organization_id": row.get("organization_id"),
        "organization_name": row.get("organization_name"),
        "status": row["status"],
        "issuer": row.get("issuer"),
        "algorithm": row.get("algorithm"),
        "key_size": row.get("key_size"),
        "auto_renew": bool(row["auto_renew"]) if row.get("auto_renew") is not None else None,
        # No error column on this table -- see _SSL_SCHEMA_NOTE.
        "error_message": None,
        "valid_from": row["valid_from"].isoformat() if row.get("valid_from") else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "days_until_expiry": days_until_expiry,
        "issued_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "last_renewed": row["last_renewed"].isoformat() if row.get("last_renewed") else None,
    }


# The one SELECT list both the list and the single-domain read use, so the
# two views cannot drift into disagreeing about a certificate.
_CERT_SELECT = """
    SELECT
        sc.id, d.domain, sc.status, sc.issuer, sc.algorithm, sc.key_size,
        sc.auto_renew, sc.valid_from, sc.valid_until, sc.created_at, sc.last_renewed,
        d.organization_id AS organization_id, o.name AS organization_name
    FROM ssl_certificates sc
    LEFT JOIN domains d ON sc.domain_id = d.id
    LEFT JOIN organizations o ON o.id = d.organization_id
"""


@router.get(
    "/certificates",
    summary="List all SSL certificates",
    description="Paginated list of every domain certificate with its expiry, issuer and owning "
    "organization -- the read behind the console's certificate screen (phase-03 SS3.3), which "
    "sorts by expiry ascending and highlights anything inside 30 days. Filterable by status, "
    "domain substring, and days-until-expiry.",
)
@require_api_key("read", scope="platform", role="support")
async def list_certificates(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
    status: str = Query("", description=f"One of {', '.join(_SSL_STATUSES)}"),
    domain: str = Query("", description="Substring match on the domain name"),
    expiring_days: int | None = Query(
        None, ge=0, description="Only certificates expiring within this many days"
    ),
    sort_by: str = Query("valid_until", description="domain | valid_until | status | issued_at"),
    sort_dir: str = Query("asc", description="asc | desc"),
):
    if status and status not in _SSL_STATUSES:
        return JSONResponse(
            content=create_api_response(
                "error", f"status must be one of {', '.join(_SSL_STATUSES)}"
            ),
            status_code=422,
        )
    if sort_by not in _CERT_SORT_KEYS:
        return JSONResponse(
            content=create_api_response(
                "error", f"sort_by must be one of {', '.join(sorted(_CERT_SORT_KEYS))}"
            ),
            status_code=422,
        )
    if sort_dir.lower() not in _CERT_SORT_DIRECTIONS:
        return JSONResponse(
            content=create_api_response("error", "sort_dir must be 'asc' or 'desc'"),
            status_code=422,
        )

    per_page = min(per_page, 200)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        where_clauses = []
        params = []

        if status:
            where_clauses.append("sc.status = %s")
            params.append(status)

        if domain:
            where_clauses.append("d.domain LIKE %s")
            params.append(f"%{domain}%")

        if expiring_days is not None:
            # Rows with a NULL valid_until are excluded rather than treated
            # as expiring: "we don't know when this expires" is a different
            # problem from "this expires on Tuesday", and mixing them into
            # the expiry queue buries the real ones.
            where_clauses.append(
                "sc.valid_until IS NOT NULL AND sc.valid_until <= NOW() + INTERVAL %s DAY"
            )
            params.append(expiring_days)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        # Ascending expiry puts NULLs first in MySQL; `IS NULL` as the lead
        # key pushes unknown-expiry rows to the end so the soonest real
        # expiry is the first row on the screen.
        order_column = _CERT_SORT_KEYS[sort_by]
        direction = _CERT_SORT_DIRECTIONS[sort_dir.lower()]
        order_sql = (
            f"{order_column} IS NULL, {order_column} {direction}"
            if sort_by == "valid_until"
            else f"{order_column} {direction}"
        )

        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM ssl_certificates sc
            LEFT JOIN domains d ON sc.domain_id = d.id
            {where_sql}
            """,
            params,
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            f"{_CERT_SELECT} {where_sql} ORDER BY {order_sql}, sc.id DESC LIMIT %s OFFSET %s",
            params + [per_page, (page - 1) * per_page],
        )

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        return create_api_response(
            "success",
            "Certificates retrieved",
            {
                "items": [_certificate_item(row) for row in rows],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": max(1, -(-total // per_page)),
                },
                "schema_note": _SSL_SCHEMA_NOTE,
            },
        )

    except Exception as e:
        logger.error(f"Certificate list query failed: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to list certificates"), status_code=500
        )


@router.get(
    "/certificates/{domain}",
    summary="Get certificate for a domain",
    description="The certificate record for one domain: status, issuer, validity window, "
    "days until expiry and owning organization. Carries the same fields as the list view so "
    "the console's detail pane and table cannot disagree.",
)
@require_api_key("read", scope="platform", role="support")
async def get_certificate(domain: str):
    """Get the certificate record for a specific domain.

    Carried the same `row["error_message"]` KeyError the list view did (that
    column does not exist -- see _SSL_SCHEMA_NOTE), so this 500'd on every
    call too. Fixed here as well rather than left next door to the fix: it
    is the same defect, one line apart, on the endpoint the console's
    certificate detail pane calls.
    """
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            {_CERT_SELECT}
            WHERE sc.domain_id = (SELECT id FROM domains WHERE domain = %s LIMIT 1)
            ORDER BY sc.created_at DESC
            LIMIT 1
            """,
            (domain,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return JSONResponse(
                content=create_api_response("error", f"No certificate record for '{domain}'"),
                status_code=404,
            )

        return create_api_response(
            "success",
            "Certificate retrieved",
            {**_certificate_item(row), "schema_note": _SSL_SCHEMA_NOTE},
        )

    except Exception as e:
        logger.error(f"Certificate lookup failed for {domain}: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve certificate"), status_code=500
        )


class CertificateRenewRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        description="Why this certificate is being renewed out of band. Recorded by the audit "
        "middleware (phase-02 cross-cutting: corrective actions carry a reason).",
    )


@router.post(
    "/certificates/{domain}/renew",
    summary="Trigger certificate renewal for a domain",
    description="Requests an out-of-band renewal from the certificate manager. Returns 501 on "
    "this deployment: cert_manager has no trigger interface of any kind -- see the response "
    "message for exactly what is missing.",
)
@require_api_key("write", scope="platform", role="operator")
async def renew_certificate(domain: str, body: CertificateRenewRequest, request: Request):
    """501, and deliberately so.

    Traced every way a renewal could be asked for before writing this:

      * cert_manager exposes NO HTTP interface. It is a supervisord-run
        script (mailer/cert_manager/supervisord.conf ->
        mailer/cert_manager/scripts/cert_manager.py) whose main() ends in a
        `while True: process_domains(); time.sleep(self.check_interval)`
        loop. It binds no port, and docker-compose.yml's cert_manager
        service publishes none -- there is no CERT_MANAGER_URL to call
        because there is nothing listening.
      * It takes no work from the database either. process_domains() reads
        `SELECT domain FROM domains WHERE active = 1` and then decides
        renewal purely from the certificate FILE on disk
        (check_certificate_expiry() shells out to `openssl x509 -enddate`).
        It never reads ssl_certificates.status or valid_until -- so there is
        no row this API could write to make the next cycle pick the domain
        up. Writing one would produce a button that reports success and
        changes nothing, which is worse than this 501.
      * There is no renewal-request/job table in the schema for the API to
        enqueue into (verified against 0001_baseline).

    The check interval is CERT_CHECK_INTERVAL, default 21600s (6h), so an
    expiring certificate is picked up automatically within that window;
    what is missing is only the ability to force it sooner.
    """
    ctx = request.state.auth_context
    logger.warning(
        f"Certificate renewal requested but unavailable: domain={domain} "
        f"by={ctx['operator_id']} reason={body.reason!r}"
    )
    return JSONResponse(
        content=create_api_response(
            "error",
            "Certificate renewal cannot be triggered from this API. cert_manager runs as a "
            "polling loop with no HTTP listener, no published port, and no queue table -- it "
            "decides what to renew by reading certificate files on disk "
            "(cert_manager.py::check_certificate_expiry), not by reading ssl_certificates, so "
            "there is nothing this service can write or call to force a renewal. It renews "
            "expiring certificates on its own every CERT_CHECK_INTERVAL seconds (default "
            "21600 = 6h). To force one now, run "
            "`docker compose exec cert_manager python3 /usr/local/bin/cert_manager.py` or "
            "restart the cert_manager container. Closing this gap needs a trigger interface on "
            "cert_manager (an HTTP endpoint or a renewal-request table it polls).",
            error_code="CERT_RENEW_UNAVAILABLE",
        ),
        status_code=501,
    )


@router.get(
    "/accounts",
    summary="List ACME accounts",
    description="Returns all ACME accounts registered with certificate authorities, including per-account issuance statistics, success and failure rates, and last-used timestamps. Requires an admin API key.",
    response_model=AcmeAccountsResponse,
    responses={
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / Failed to retrieve ACME accounts",
        },
    },
)
@require_api_key("admin", scope="platform", role="admin")
async def list_acme_accounts():
    """
    List all ACME accounts with full issuance stats.
    Requires admin API key (admin_access = 1).
    """
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        # `sc.created_at, sc.last_renewed` used to appear in this SELECT
        # against a single-table query with no `sc` alias -- MySQL error
        # 1054, so this endpoint failed on every call. acme_account_stats
        # (defined in alembic/versions/0002_adhoc_table_tracking.py, not the
        # baseline) has created_at/updated_at and no last_renewed at all.
        cursor.execute("""
            SELECT
                id, email, certs_issued, success_count, failure_count,
                last_used, created_at, updated_at
            FROM acme_account_stats
            ORDER BY certs_issued DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        accounts = [
            {
                "id": row["id"],
                "email": row["email"],
                "certs_issued": row["certs_issued"],
                "success_count": row["success_count"],
                "failure_count": row["failure_count"],
                "success_rate": (
                    round(row["success_count"] / row["certs_issued"] * 100, 1)
                    if row["certs_issued"] > 0
                    else None
                ),
                "last_used": row["last_used"].isoformat() if row["last_used"] else None,
                "registered_at": row.get("created_at").isoformat()
                if row.get("created_at")
                else None,
            }
            for row in rows
        ]

        return create_api_response(
            "success",
            "ACME accounts retrieved",
            {
                "accounts": accounts,
                "total": len(accounts),
            },
        )

    except Exception as e:
        logger.error(f"ACME accounts query failed: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve ACME accounts"),
            status_code=500,
        )
