#!/usr/bin/env python3
"""
Alias Management API Routes
Email alias and forwarding management with production features
"""

import html as html_module
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


def sanitize_text(value):
    """Sanitize free-text input to prevent stored XSS. Strips all HTML tags."""
    if not value or not isinstance(value, str):
        return value
    import re as _re

    value = _re.sub(r"<[^>]+>", "", value)  # Strip HTML tags
    return html_module.escape(value, quote=True)


import sys
from pathlib import Path

from utils.auth import create_api_response, org_filter, require_api_key
from utils.database import get_db_connection

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from schemas.alias import (
    AliasAddResponse,
    AliasBulkResponse,
    AliasEditResponse,
    AliasGetResponse,
    AliasStatsResponse,
)

# No aliases.py endpoint returns a bare (data-less) message envelope, so
# SimpleMessageResponse isn't needed here -- every response below carries a
# `data` payload (unlike mailboxes.py/organizations.py/domains.py, which
# import it for their delete endpoints).
from schemas.common import ErrorResponse

from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Pydantic request/response models for OpenAPI documentation ---


class AliasCreate(BaseModel):
    source: str = Field(
        ..., description="Source email address (the alias)", example="sales@example.com"
    )
    destination: str = Field(
        ...,
        description="Destination email address (where mail is forwarded)",
        example="alice@example.com",
    )


class AliasBulkCreate(BaseModel):
    aliases: list = Field(
        ..., description="List of alias objects with source and destination fields"
    )


def validate_email_list(email_list):
    """Validate comma-separated list of email addresses"""
    if not email_list:
        return False, "Email list cannot be empty"

    emails = [email.strip() for email in email_list.split(",")]
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

    for email in emails:
        if not re.match(pattern, email):
            return False, f"Invalid email format: {email}"

    return True, emails


@router.post(
    "/add",
    summary="Create a new email alias",
    description="Create a single email alias that forwards mail from the source address to one or more destination addresses. Validates both source and destination email formats, checks domain existence, and prevents duplicate aliases.",
    response_model=AliasAddResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Missing field / invalid source or destination address format / domain not found or inactive",
        },
        409: {"model": ErrorResponse, "description": "Alias already exists"},
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to add alias",
        },
    },
)
@require_api_key("write")
async def add_alias(request: Request):
    """Add a new email alias"""
    data = await request.json()

    required_fields = ["source", "destination"]
    for field in required_fields:
        if field not in data:
            return JSONResponse(
                content=create_api_response("error", f"Missing required field: {field}"),
                status_code=400,
            )

    address = data["source"].lower().strip()
    goto = data["destination"].strip()

    # Validate source address format
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", address):
        return JSONResponse(
            content=create_api_response("error", "Invalid source address format"), status_code=400
        )

    # Validate destination addresses
    valid_goto, goto_result = validate_email_list(goto)
    if not valid_goto:
        return JSONResponse(content=create_api_response("error", goto_result), status_code=400)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    ctx = request.state.auth_context

    try:
        cursor = conn.cursor(dictionary=True)

        # Extract domain from address
        domain = address.split("@")[1]

        # Check the domain exists AND belongs to the caller's org -- without
        # the organization_id filter, any tenant could create an alias on a
        # domain owned by a different organization. Platform scope drops the
        # filter and works on any org's domain (ADR-002 SS8).
        domain_sql = "SELECT id, organization_id FROM domains WHERE domain = %s AND active = 1"
        domain_params = [domain]
        if ctx["scope"] == "organization":
            domain_sql += " AND organization_id = %s"
            domain_params.append(ctx["organization_id"])
        cursor.execute(domain_sql, tuple(domain_params))
        domain_result = cursor.fetchone()
        if not domain_result:
            return JSONResponse(
                content=create_api_response("error", f"Domain {domain} not found or inactive"),
                status_code=400,
            )
        # The alias inherits the domain's org, never a caller-supplied value.
        org_id = domain_result["organization_id"]

        # Check if alias already exists (source addresses are globally unique,
        # not per-org, since they're real mailbox addresses)
        cursor.execute("SELECT id FROM aliases WHERE source = %s", (address,))
        if cursor.fetchone():
            return JSONResponse(
                content=create_api_response("error", f"Alias {address} already exists"),
                status_code=409,
            )

        # Insert alias
        from shared.ulid_utils import generate_ulid

        alias_id = generate_ulid()
        cursor.execute(
            """
            INSERT INTO aliases (
                id, source, destination, domain_id, organization_id, active,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                alias_id,
                address,
                goto,
                domain_result["id"],
                org_id,
                data.get("active", 1),
                datetime.now(),
                datetime.now(),
            ),
        )

        # aliases.id is a CHAR(26) ULID -- cursor.lastrowid only tracks
        # AUTO_INCREMENT columns, so it reads 0 here. Keep the ULID that was
        # generated and inserted above; overwriting it made every response
        # (and every dispatched event) carry alias_id: 0.

        dispatch_event(
            Events.ALIAS_CREATED,
            data={"alias_id": alias_id, "source": address, "destination": goto, "domain": domain},
            domain=domain,
            source_service="api",
        )

        return create_api_response(
            "success",
            f"Alias {address} created successfully",
            {"alias_id": alias_id, "source": address},
        )

    except Exception as e:
        logger.error(f"Add alias error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to add alias"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/get/{alias_id}",
    summary="Get alias information",
    description="Retrieve alias details by ID or source address. Pass 'all' to list all active aliases with pagination. Each alias includes destination count, parsed destination list, and 30-day forwarding statistics.",
    response_model=AliasGetResponse,
    responses={
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to retrieve aliases",
        }
    },
)
@require_api_key("read")
async def get_aliases(
    alias_id: str, request: Request, page: int = Query(1), per_page: int = Query(50)
):
    """Get alias information"""
    ctx = request.state.auth_context
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        # Platform scope reads across every organization (ADR-002 SS8);
        # organization scope stays pinned to its own org. Hand-built rather
        # than via org_filter() because these queries need the `a.` alias.
        org_clause = ""
        org_params: list = []
        if ctx["scope"] == "organization":
            org_clause = " AND a.organization_id = %s"
            org_params = [ctx["organization_id"]]

        if alias_id == "all":
            per_page = min(per_page, 200)
            offset = (page - 1) * per_page
            # Previously WHERE a.active = 1 with no org filter at all -- any
            # authenticated tenant could list every alias on the platform.
            cursor.execute(
                f"""
                SELECT a.*, d.description as domain_description
                FROM aliases a
                LEFT JOIN domains d ON a.domain_id = d.id
                WHERE a.active = 1{org_clause}
                ORDER BY a.source
                LIMIT %s OFFSET %s
            """,
                tuple(org_params + [per_page, offset]),
            )
            aliases = cursor.fetchall()
        else:
            # Previously matched by id/source alone -- any tenant could read
            # another org's alias by guessing its ID or address.
            cursor.execute(
                f"""
                SELECT a.*, d.description as domain_description
                FROM aliases a
                LEFT JOIN domains d ON a.domain_id = d.id
                WHERE (a.id = %s OR a.source = %s){org_clause}
            """,
                tuple([alias_id, alias_id] + org_params),
            )
            aliases = cursor.fetchall()

        # Add statistics for each alias
        for alias in aliases:
            # Count destination addresses
            destinations = alias["destination"].split(",")
            alias["destination_count"] = len(destinations)
            alias["destinations"] = [dest.strip() for dest in destinations]

            # Get forwarding statistics if available. Best-effort: the
            # message_forwards table is referenced here but not created by
            # any migration (pre-existing gap, unrelated to auth/org-scoping
            # -- schema fixes belong to phase-08). Without this guard, every
            # call to this endpoint 500s regardless of org-scoping.
            try:
                cursor.execute(
                    """
                    SELECT COUNT(*) as forwarded_count
                    FROM message_forwards
                    WHERE alias_address = %s
                    AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                """,
                    (alias["source"],),
                )
                stats = cursor.fetchone()
                alias["monthly_forwards"] = stats["forwarded_count"] if stats else 0
            except Exception as stats_exc:
                logger.warning(f"Forwarding stats unavailable for {alias['source']}: {stats_exc}")
                alias["monthly_forwards"] = None

        return create_api_response("success", "Aliases retrieved successfully", aliases)

    except Exception as e:
        logger.error(f"Get aliases error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve aliases"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/edit",
    summary="Edit alias settings",
    description="Batch-update one or more aliases. Accepts an items array of alias IDs or source addresses and an attr object with fields to update (destination, active status). Destination addresses are validated before applying changes.",
    response_model=AliasEditResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request format -- items/attr expected",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to edit alias",
        },
    },
)
@require_api_key("write")
async def edit_alias(request: Request):
    """Edit alias settings"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data or "items" not in data or "attr" not in data:
        return JSONResponse(
            content=create_api_response("error", "Invalid request format"), status_code=400
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        results = []

        for alias_id in data["items"]:
            # Previously updated by id/source alone -- any tenant could edit
            # another org's alias. Verify ownership before touching it.
            cursor.execute(
                "SELECT organization_id FROM aliases WHERE id = %s OR source = %s",
                (alias_id, alias_id),
            )
            owner = cursor.fetchone()
            # Platform scope may edit any org's alias (ADR-002 SS8);
            # organization scope only its own.
            if not owner or (
                ctx["scope"] == "organization"
                and owner["organization_id"] != ctx["organization_id"]
            ):
                results.append(
                    {"alias": alias_id, "status": "error", "msg": f"Alias {alias_id} not found"}
                )
                continue
            # Re-pin the UPDATE to the row's own org rather than the caller's:
            # a platform caller has none, and this keeps the write narrowed to
            # exactly the alias whose ownership was just verified.
            alias_org_id = owner["organization_id"]

            update_fields = []
            update_values = []

            for key, value in data["attr"].items():
                if key == "destination":
                    valid_goto, goto_result = validate_email_list(value)
                    if not valid_goto:
                        results.append({"alias": alias_id, "status": "error", "msg": goto_result})
                        continue
                    update_fields.append(f"{key} = %s")
                    update_values.append(value.strip())
                elif key in ["active"]:
                    update_fields.append(f"{key} = %s")
                    update_values.append(value)

            if update_fields:
                update_fields.append("updated_at = %s")
                update_values.append(datetime.now())

                cursor.execute(
                    f"""
                    UPDATE aliases
                    SET {", ".join(update_fields)}
                    WHERE (id = %s OR source = %s) AND organization_id = %s
                """,
                    update_values + [alias_id, alias_id, alias_org_id],
                )

                if cursor.rowcount > 0:
                    dispatch_event(
                        Events.ALIAS_UPDATED,
                        data={"alias_id": alias_id, "updated_fields": list(data["attr"].keys())},
                        source_service="api",
                    )
                    results.append(
                        {
                            "alias": alias_id,
                            "status": "success",
                            "msg": f"Alias {alias_id} updated successfully",
                        }
                    )
                else:
                    results.append(
                        {"alias": alias_id, "status": "error", "msg": f"Alias {alias_id} not found"}
                    )

        return create_api_response("success", "Alias update completed", results)

    except Exception as e:
        logger.error(f"Edit alias error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to edit alias"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/delete",
    summary="Delete alias(es)",
    description="Delete one or more email aliases. Accepts an array of alias IDs or source addresses. Returns per-alias success/error results. Deleted aliases stop forwarding immediately.",
    response_model=AliasEditResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request format -- array of alias IDs expected",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to delete aliases",
        },
    },
)
@require_api_key("write")
async def delete_alias(request: Request):
    """Delete alias(es)"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data or not isinstance(data, list):
        return JSONResponse(
            content=create_api_response(
                "error", "Invalid request format - array of alias IDs expected"
            ),
            status_code=400,
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        results = []

        for alias_id in data:
            # Get alias info before deletion -- also verifies ownership.
            # Previously matched by id/source alone with no org check at
            # all, so any tenant could delete another org's alias.
            cursor.execute(
                "SELECT source, organization_id FROM aliases WHERE id = %s OR source = %s",
                (alias_id, alias_id),
            )
            alias_info = cursor.fetchone()

            # Platform scope may delete any org's alias (ADR-002 SS8);
            # organization scope only its own.
            if not alias_info or (
                ctx["scope"] == "organization"
                and alias_info["organization_id"] != ctx["organization_id"]
            ):
                results.append(
                    {"alias": alias_id, "status": "error", "msg": f"Alias {alias_id} not found"}
                )
                continue

            cursor.execute(
                "DELETE FROM aliases WHERE (id = %s OR source = %s) AND organization_id = %s",
                # Re-pin to the row's own org (see edit_alias).
                (alias_id, alias_id, alias_info["organization_id"]),
            )

            if cursor.rowcount > 0:
                address = alias_info["source"] if alias_info else alias_id
                dispatch_event(
                    Events.ALIAS_DELETED,
                    data={"alias_id": alias_id, "source": address},
                    source_service="api",
                )
                results.append(
                    {
                        "alias": alias_id,
                        "source": address,
                        "status": "success",
                        "msg": f"Alias {address} deleted successfully",
                    }
                )
            else:
                results.append(
                    {"alias": alias_id, "status": "error", "msg": f"Alias {alias_id} not found"}
                )

        return create_api_response("success", "Alias deletion completed", results)

    except Exception as e:
        logger.error(f"Delete alias error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete aliases"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/get/stats/{domain}",
    summary="Get alias statistics for a domain",
    description="Retrieve aggregate alias statistics for a domain, including total/active/inactive counts, top 10 forwarding destinations by usage, and daily forwarding volume over the last 30 days.",
    response_model=AliasStatsResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to retrieve alias statistics",
        },
    },
)
@require_api_key("read")
async def get_alias_stats(domain: str, request: Request):
    """Get alias statistics for a domain"""
    ctx = request.state.auth_context
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        # Look up domain_id from domain name, scoped to the caller's org --
        # previously matched by domain name alone, so any tenant could pull
        # alias stats for a domain owned by a different organization.
        # org_filter() degrades to "1=1" for platform scope, which reads any
        # org's stats by design (ADR-002 SS8).
        org_sql, org_params = org_filter(ctx)
        cursor.execute(
            f"SELECT id FROM domains WHERE domain = %s AND {org_sql}",
            tuple([domain] + org_params),
        )
        domain_row = cursor.fetchone()
        if not domain_row:
            return JSONResponse(
                content=create_api_response("error", f"Domain {domain} not found"), status_code=404
            )
        domain_id = domain_row["id"]

        # Get basic alias statistics
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_aliases,
                SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) as active_aliases,
                SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) as inactive_aliases
            FROM aliases
            WHERE domain_id = %s
        """,
            (domain_id,),
        )

        basic_stats = cursor.fetchone()

        # Get top forwarding destinations
        cursor.execute(
            """
            SELECT
                destination,
                COUNT(*) as usage_count
            FROM aliases
            WHERE domain_id = %s AND active = 1
            GROUP BY destination
            ORDER BY usage_count DESC
            LIMIT 10
        """,
            (domain_id,),
        )

        top_destinations = cursor.fetchall()

        # Get monthly forwarding volume. Best-effort -- see the same
        # message_forwards note in get_aliases() above: the table is
        # referenced but never created by any migration.
        try:
            cursor.execute(
                """
                SELECT
                    DATE(mf.created_at) as date,
                    COUNT(*) as forwards_count
                FROM message_forwards mf
                JOIN aliases a ON mf.alias_address = a.source
                WHERE a.domain_id = %s
                AND mf.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY DATE(mf.created_at)
                ORDER BY date DESC
            """,
                (domain_id,),
            )
            monthly_volume = cursor.fetchall()
        except Exception as volume_exc:
            logger.warning(
                f"Monthly forwarding volume unavailable for domain {domain}: {volume_exc}"
            )
            monthly_volume = []

        stats = {
            "basic_stats": basic_stats,
            "top_destinations": top_destinations,
            "monthly_volume": monthly_volume,
        }

        return create_api_response(
            "success", f"Alias statistics for {domain} retrieved successfully", stats
        )

    except Exception as e:
        logger.error(f"Get alias stats error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve alias statistics"),
            status_code=500,
        )
    finally:
        conn.close()


@router.post(
    "/add/bulk",
    summary="Bulk create aliases",
    description="Create multiple email aliases in a single request. Each alias in the array is validated independently -- invalid entries are skipped and reported while valid ones are created. Returns a summary with success/error counts and per-alias results.",
    response_model=AliasBulkResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request format -- aliases array expected",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to add bulk aliases",
        },
    },
)
@require_api_key("write")
async def add_bulk_aliases(request: Request):
    """Add multiple aliases in bulk"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data or "aliases" not in data or not isinstance(data["aliases"], list):
        return JSONResponse(
            content=create_api_response("error", "Invalid request format - aliases array expected"),
            status_code=400,
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor()
        results = []
        success_count = 0
        error_count = 0

        for alias_data in data["aliases"]:
            try:
                address = alias_data["source"].lower().strip()
                goto = alias_data["destination"].strip()
                domain = address.split("@")[1]

                # Validate source address
                if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", address):
                    results.append(
                        {
                            "source": address,
                            "status": "error",
                            "msg": "Invalid source address format",
                        }
                    )
                    error_count += 1
                    continue

                # Validate destination
                valid_goto, goto_result = validate_email_list(goto)
                if not valid_goto:
                    results.append({"source": address, "status": "error", "msg": goto_result})
                    error_count += 1
                    continue

                # Look up domain_id, scoped to the caller's org -- previously
                # matched by domain name alone, so any tenant could bulk-add
                # aliases on a domain owned by a different organization.
                # Platform scope drops the filter (ADR-002 SS8) and takes each
                # alias's org from the domain it lands on.
                domain_sql = (
                    "SELECT id, organization_id FROM domains WHERE domain = %s AND active = 1"
                )
                domain_params = [domain]
                if ctx["scope"] == "organization":
                    domain_sql += " AND organization_id = %s"
                    domain_params.append(ctx["organization_id"])
                cursor.execute(domain_sql, tuple(domain_params))
                domain_row = cursor.fetchone()
                if not domain_row:
                    results.append(
                        {
                            "source": address,
                            "status": "error",
                            "msg": f"Domain {domain} not found or inactive",
                        }
                    )
                    error_count += 1
                    continue

                # Check if already exists
                cursor.execute("SELECT id FROM aliases WHERE source = %s", (address,))
                if cursor.fetchone():
                    results.append(
                        {"source": address, "status": "error", "msg": "Alias already exists"}
                    )
                    error_count += 1
                    continue

                # Insert alias
                try:
                    from shared.ulid_utils import generate_ulid

                    bulk_alias_id = generate_ulid()
                except ImportError:
                    import uuid

                    bulk_alias_id = str(uuid.uuid4()).replace("-", "")[:26]
                cursor.execute(
                    """
                    -- `id` was missing from the column list while its value
                    -- was still supplied: 7 columns, 8 placeholders, so every
                    -- bulk alias insert raised. aliases.id is CHAR(26) with no
                    -- DB-side default (009_ulid_safe.sql), so it must be named.
                    INSERT INTO aliases (
                        id, source, destination, domain_id, organization_id, active, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        bulk_alias_id,
                        address,
                        goto,
                        domain_row[0],
                        domain_row[1],
                        alias_data.get("active", 1),
                        datetime.now(),
                        datetime.now(),
                    ),
                )

                results.append(
                    {"source": address, "status": "success", "msg": "Alias created successfully"}
                )
                success_count += 1

            except Exception as e:
                results.append(
                    {
                        "source": alias_data.get("source", "unknown"),
                        "status": "error",
                        "msg": f"Failed to create alias: {str(e)}",
                    }
                )
                error_count += 1

        return create_api_response(
            "success",
            "Bulk alias creation completed",
            {
                "summary": {
                    "total": len(data["aliases"]),
                    "success": success_count,
                    "errors": error_count,
                },
                "results": results,
            },
        )

    except Exception as e:
        logger.error(f"Bulk add aliases error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to add bulk aliases"), status_code=500
        )
    finally:
        conn.close()
