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

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import logging
from utils.auth import require_api_key, require_admin, create_api_response
from utils.database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/status",
    summary="Get SSL certificate status",
    description="Returns the health and expiry status of all TLS certificates managed by the system. Includes aggregate counts of active, failed, and expired certificates, ACME account health, the most recently issued certificate, and certificates expiring within 30 days.",
)
@require_api_key("read")
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

        # Aggregate cert counts by status
        cursor.execute("""
            SELECT
                status,
                COUNT(*) AS count
            FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id
            GROUP BY status
        """)
        status_rows = cursor.fetchall()
        cert_counts = {row["status"]: row["count"] for row in status_rows}

        # Most recent successful issuance
        cursor.execute("""
            SELECT d.domain, sc.last_renewed
            FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
        """)
        last_issued = cursor.fetchone()

        # Certs expiring in the next 30 days
        cursor.execute("""
            SELECT COUNT(*) AS expiring_soon
            FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id
            WHERE status = 'active'
              AND updated_at <= NOW() - INTERVAL 60 DAY
        """)
        expiring_row = cursor.fetchone()

        payload = {
            "certificates": {
                "active": cert_counts.get("active", 0),
                "failed": cert_counts.get("failed", 0),
                "expired": cert_counts.get("expired", 0),
                "total": sum(cert_counts.values()),
                "expiring_soon": expiring_row["expiring_soon"] if expiring_row else 0,
            },
            "last_issued": {
                "domain": last_issued.get("domain", "unknown") if last_issued else None,
                "issued_at": last_issued["updated_at"].isoformat() if last_issued else None,
            },
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


@router.get(
    "/certificates",
    summary="List all SSL certificates",
    description="Returns a paginated list of all domain certificates with their current status. Supports filtering by certificate status (active, failed, expired) and domain name substring search.",
)
@require_api_key("read")
async def list_certificates(
    page: int = Query(1),
    per_page: int = Query(50),
    status: str = Query(""),
    domain: str = Query(""),
):
    """
    Paginated list of all domain certificates with their current status.

    Query params:
        page (int):     page number, default 1
        per_page (int): results per page, max 200, default 50
        status (str):   filter by status (active | failed | expired)
        domain (str):   search by domain name (substring match)
    """
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

        if status in ("active", "failed", "expired"):
            where_clauses.append("status = %s")
            params.append(status)

        if domain:
            where_clauses.append("d.domain LIKE %s")
            params.append(f"%{domain}%")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cursor.execute(
            f"SELECT COUNT(*) AS total FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id {where_sql}",
            params,
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT
                sc.id, d.domain, sc.status,
                sc.created_at, sc.last_renewed
            FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
        """,
            params + [per_page, (page - 1) * per_page],
        )

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        items = [
            {
                "id": row["id"],
                "domain": row.get("domain", "unknown"),
                "status": row["status"],
                "error_message": row["error_message"],
                "issued_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                "updated_at": row.get("last_renewed").isoformat()
                if row.get("last_renewed")
                else None,
            }
            for row in rows
        ]

        return create_api_response(
            "success",
            "Certificates retrieved",
            {
                "items": items,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": max(1, -(-total // per_page)),
                },
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
    description="Returns the certificate record for a specific domain, including its current status, issuance date, last update, and any error messages from the most recent renewal attempt.",
)
@require_api_key("read")
async def get_certificate(domain: str):
    """Get the certificate record for a specific domain."""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT sc.id, d.domain, sc.status, sc.created_at, sc.last_renewed
            FROM ssl_certificates sc LEFT JOIN domains d ON sc.domain_id = d.id
            WHERE sc.domain_id = (SELECT id FROM domains WHERE domain = %s LIMIT 1)
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
            {
                "id": row["id"],
                "domain": row.get("domain", "unknown"),
                "status": row["status"],
                "error_message": row["error_message"],
                "issued_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                "updated_at": row.get("last_renewed").isoformat()
                if row.get("last_renewed")
                else None,
            },
        )

    except Exception as e:
        logger.error(f"Certificate lookup failed for {domain}: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve certificate"), status_code=500
        )


@router.get(
    "/accounts",
    summary="List ACME accounts",
    description="Returns all ACME accounts registered with certificate authorities, including per-account issuance statistics, success and failure rates, and last-used timestamps. Requires an admin API key.",
)
@require_api_key("admin")
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
        cursor.execute("""
            SELECT
                id, email, certs_issued, success_count, failure_count,
                last_used, sc.created_at, sc.last_renewed
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
