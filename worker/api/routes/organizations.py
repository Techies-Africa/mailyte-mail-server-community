#!/usr/bin/env python3
"""
Organization Management API Routes
Comprehensive organization management with validation and relationships.

This module provides API endpoints for:
- Organization CRUD operations with proper validation
- Organization settings and configuration management
- Quota and rate limit management
- Usage statistics and monitoring
"""

import html as html_module
import logging
import re
from datetime import datetime
from typing import Any

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


import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import sessionmaker
from utils.auth import create_api_response, require_api_key

from database.models.core import Domain, EmailAccount, Organization

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from schemas.common import ErrorResponse, SimpleMessageResponse
from schemas.organization import (
    OrganizationDetailResponse,
    OrganizationListResponse,
    OrganizationQuotaOverrideClearResponse,
    OrganizationQuotaResponse,
    OrganizationQuotaUpdateResponse,
    OrganizationWriteResponse,
)

from shared.webhook_dispatcher import Events, dispatch_event

# ---------------------------------------------------------------------------
# Pydantic request/response models for OpenAPI documentation
# ---------------------------------------------------------------------------


class OrganizationCreate(BaseModel):
    """Request body for creating a new organization."""

    id: str = Field(
        ...,
        description="Unique organization identifier (alphanumeric, hyphens, underscores)",
        example="acme-corp",
    )
    name: str = Field(..., description="Organization display name", example="Acme Corp")
    external_id: str | None = Field(
        None, description="External system ID for integration", example="acme-123"
    )
    admin_email: str | None = Field(
        None, description="Admin contact email", example="admin@acme.com"
    )
    admin_name: str | None = Field(None, description="Admin contact name", example="Jane Doe")
    description: str | None = Field(
        None, description="Organization description", example="Primary business unit"
    )
    settings: dict[str, Any] | None = Field(
        None, description="Arbitrary organization-level settings"
    )
    rate_limits: dict[str, Any] | None = Field(
        None, description="Rate-limiting rules applied across all domains"
    )
    storage_quotas: dict[str, Any] | None = Field(
        None, description="Organization-wide storage quota overrides"
    )
    webhook_urls: list[str] | None = Field(
        None,
        description="Webhook endpoints for event notifications",
        example=["https://hooks.example.com/mailyte"],
    )
    webhook_secret: str | None = Field(
        None, description="Shared secret for signing webhook payloads"
    )
    sending_profile: str | None = Field(
        None,
        description="Outbound stream class: transactional|marketing|both. Routes marketing orgs out the marketing IP.",
    )
    active: bool = Field(True, description="Whether the organization is active")


class OrganizationUpdate(BaseModel):
    """Request body for updating an existing organization."""

    name: str | None = Field(None, description="Updated organization display name")
    external_id: str | None = Field(None, description="Updated external system ID")
    admin_email: str | None = Field(None, description="Updated admin contact email")
    admin_name: str | None = Field(None, description="Updated admin contact name")
    description: str | None = Field(None, description="Updated organization description")
    settings: dict[str, Any] | None = Field(None, description="Updated organization settings")
    rate_limits: dict[str, Any] | None = Field(None, description="Updated rate-limiting rules")
    storage_quotas: dict[str, Any] | None = Field(
        None, description="Updated storage quota overrides"
    )
    webhook_urls: list[str] | None = Field(None, description="Updated webhook endpoint list")
    webhook_secret: str | None = Field(None, description="Updated webhook secret")
    sending_profile: str | None = Field(
        None, description="Outbound stream class: transactional|marketing|both"
    )
    active: bool | None = Field(None, description="Enable or disable the organization")


class OrganizationQuotaUpdate(BaseModel):
    """Request body for updating organization quota settings."""

    storage_quotas: dict[str, Any] | None = Field(
        None, description="Organization-wide storage quota overrides"
    )
    rate_limits: dict[str, Any] | None = Field(
        None, description="Organization-wide rate-limiting rules"
    )


logger = logging.getLogger(__name__)
router = APIRouter()

# Console phase-02 SS2.1 asks for "sort by storage, mailbox count, creation
# date". ORDER BY cannot take a bound parameter, so a caller-supplied sort
# column would have to be interpolated straight into the SQL -- which is
# exactly the injection this closed mapping exists to prevent. Anything not
# a key here is rejected with 422; the raw string never reaches the query.
_ORG_SORT_KEYS = (
    "name",
    "created_at",
    "domain_count",
    "email_account_count",
    "total_storage_used",
)
_SORT_DIRECTIONS = ("asc", "desc")


def _parse_bool_param(raw: str | None) -> tuple[bool, bool | None]:
    """Parse a "true"/"false" query string into (valid, value).

    Anything unrecognised is reported invalid rather than coerced, so
    `?active=yes` 422s instead of silently filtering to inactive orgs -- a
    filter that quietly means the opposite of what was asked is worse than
    an error, and support reads these lists to make decisions about tenants.
    """
    if raw is None:
        return True, None
    normalised = raw.strip().lower()
    if normalised in ("true", "1"):
        return True, True
    if normalised in ("false", "0"):
        return True, False
    return False, None


def get_db_session():
    """Get SQLAlchemy session"""
    engine = create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    Session = sessionmaker(bind=engine)
    return Session()


def validate_organization_data(data, is_update=False):
    """Validate organization data"""
    errors = []

    if not is_update and ("id" not in data or not data["id"]):
        errors.append("Organization ID is required")
    elif "id" in data and not re.match(r"^[a-zA-Z0-9_-]+$", data["id"]):
        errors.append(
            "Organization ID must contain only alphanumeric characters, hyphens, and underscores"
        )

    if not is_update and ("name" not in data or not data["name"]):
        errors.append("Organization name is required")
    elif "name" in data and len(data["name"]) > 255:
        errors.append("Organization name must be 255 characters or less")

    if "external_id" in data and data["external_id"] and len(data["external_id"]) > 255:
        errors.append("External ID must be 255 characters or less")

    if "admin_email" in data and data["admin_email"]:
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, data["admin_email"]):
            errors.append("Invalid admin email format")

    # The transport map matches this value literally; a typo would route mail
    # to a nonexistent transport, so only the three known classes are allowed.
    if data.get("sending_profile") is not None and data["sending_profile"] not in (
        "transactional",
        "marketing",
        "both",
    ):
        errors.append("sending_profile must be one of: transactional, marketing, both")

    return errors


@router.get(
    "/",
    summary="List all organizations",
    description="Retrieve a paginated list of all organizations with domain counts, email account "
    "counts, storage used and storage quota. Searchable (`q` matches name, exact id, external id, "
    "an owned domain, or an owned mailbox address), filterable (`active`, `over_quota`) and "
    "sortable. Requires an admin-scoped API key -- this is a full cross-tenant directory "
    "(deep-audit.md §2.1), not something any single tenant should ever see.",
    response_model=OrganizationListResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Unknown sort key/direction or bad boolean"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve organizations"},
    },
)
@require_api_key("admin", scope="platform", role="support")
async def list_organizations(
    request: Request,
    q: str | None = Query(
        None,
        description="Search: organization name (LIKE), exact organization id, external id (LIKE), "
        "or the owner of a matching domain or mailbox address.",
    ),
    active: str | None = Query(None, description="true|false -- filter on organizations.active"),
    over_quota: str | None = Query(
        None,
        description="true -- only organizations whose summed domain storage exceeds their summed "
        "domain quota",
    ),
    sort_by: str = Query(
        "name",
        description="name | created_at | domain_count | email_account_count | total_storage_used",
    ),
    sort_dir: str = Query("asc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
):
    """List all organizations with usage statistics.

    Console phase-02 SS2.1: support is handed an email address, not an org
    id, so `q` deliberately searches *through* the child tables -- an
    address that matches a mailbox lands on the owning tenant. The domain
    and mailbox arms are correlated EXISTS subqueries rather than joins
    precisely so an org owning fifty matching mailboxes still appears once.
    """
    per_page = min(per_page, 200)

    if sort_by not in _ORG_SORT_KEYS:
        return JSONResponse(
            content=create_api_response(
                "error", f"sort_by must be one of {', '.join(_ORG_SORT_KEYS)}"
            ),
            status_code=422,
        )
    if sort_dir not in _SORT_DIRECTIONS:
        return JSONResponse(
            content=create_api_response("error", "sort_dir must be 'asc' or 'desc'"),
            status_code=422,
        )

    active_valid, active_flag = _parse_bool_param(active)
    over_quota_valid, over_quota_flag = _parse_bool_param(over_quota)
    if not active_valid or not over_quota_valid:
        return JSONResponse(
            content=create_api_response("error", "active/over_quota must be 'true' or 'false'"),
            status_code=422,
        )

    session = get_db_session()
    try:
        # One grouped aggregate per child table, joined once. This handler
        # previously ran three extra queries INSIDE the per-org loop (domain
        # count, account count, storage sum) -- 2 + 3N queries, i.e. 602 for
        # a full 200-org page, all of them serial round trips. It also could
        # not sort or filter on any of those numbers, because they did not
        # exist until after the page had already been chosen.
        domain_agg = (
            session.query(
                Domain.organization_id.label("organization_id"),
                func.count(Domain.id).label("domain_count"),
                func.coalesce(func.sum(Domain.total_storage_used), 0).label("total_storage_used"),
                func.coalesce(func.sum(Domain.max_quota), 0).label("storage_quota"),
            )
            .group_by(Domain.organization_id)
            .subquery()
        )
        account_agg = (
            session.query(
                EmailAccount.organization_id.label("organization_id"),
                func.count(EmailAccount.id).label("email_account_count"),
            )
            .group_by(EmailAccount.organization_id)
            .subquery()
        )

        # COALESCE at the join, not in Python: an org with no domains has no
        # aggregate row at all, and both ORDER BY and the over_quota
        # comparison would then see NULL rather than the zero that "this
        # tenant has nothing yet" actually means.
        domain_count_col = func.coalesce(domain_agg.c.domain_count, 0)
        storage_used_col = func.coalesce(domain_agg.c.total_storage_used, 0)
        storage_quota_col = func.coalesce(domain_agg.c.storage_quota, 0)
        account_count_col = func.coalesce(account_agg.c.email_account_count, 0)

        query = (
            session.query(
                Organization,
                domain_count_col,
                account_count_col,
                storage_used_col,
                storage_quota_col,
            )
            .outerjoin(domain_agg, domain_agg.c.organization_id == Organization.id)
            .outerjoin(account_agg, account_agg.c.organization_id == Organization.id)
        )

        if q:
            like = f"%{q}%"
            # id is matched exactly, not LIKE: it is a 26-char ULID, so a
            # substring match on it is noise, and support pasting a full id
            # should land on exactly one row.
            query = query.filter(
                or_(
                    Organization.name.like(like),
                    Organization.id == q,
                    Organization.external_id.like(like),
                    session.query(Domain.id)
                    .filter(
                        Domain.organization_id == Organization.id,
                        Domain.domain.like(like),
                    )
                    .exists(),
                    session.query(EmailAccount.id)
                    .filter(
                        EmailAccount.organization_id == Organization.id,
                        EmailAccount.email.like(like),
                    )
                    .exists(),
                )
            )

        if active_flag is not None:
            query = query.filter(Organization.active.is_(active_flag))

        if over_quota_flag:
            # Strictly greater: at-quota is not over-quota, and an org with
            # no domains (0 > 0) is correctly excluded rather than counted
            # as breaching a quota it does not have.
            query = query.filter(storage_used_col > storage_quota_col)

        sort_columns = {
            "name": Organization.name,
            "created_at": Organization.created_at,
            "domain_count": domain_count_col,
            "email_account_count": account_count_col,
            "total_storage_used": storage_used_col,
        }
        sort_column = sort_columns[sort_by]
        ordering = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
        # id as the tiebreaker so paging is stable: name and created_at are
        # both non-unique, and without it MySQL may return the same row on
        # two consecutive pages and drop another entirely.
        query = query.order_by(ordering, Organization.id.asc())

        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        result = []
        for org, domain_count, account_count, storage_used, storage_quota in rows:
            org_data = org.to_dict()
            org_data["domain_count"] = int(domain_count or 0)
            org_data["email_account_count"] = int(account_count or 0)
            org_data["total_storage_used"] = int(storage_used or 0)
            # phase-02 SS2.1's "Storage used / quota" column needs both
            # halves; only the numerator was ever returned.
            org_data["storage_quota"] = int(storage_quota or 0)
            result.append(org_data)

        return create_api_response(
            "success",
            "Organizations retrieved successfully",
            {
                "items": result,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": (total + per_page - 1) // per_page,
                },
            },
        )

    except Exception as e:
        logger.error(f"List organizations error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve organizations"),
            status_code=500,
        )
    finally:
        session.close()


@router.get(
    "/{organization_id}",
    summary="Get organization details",
    description="Retrieve detailed information about a specific organization, including all its domains, email account counts, and aggregated storage statistics.",
    response_model=OrganizationDetailResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Organization not found (or belongs to another org -- tenants may only read their own)",
        },
        500: {"model": ErrorResponse, "description": "Failed to retrieve organization"},
    },
)
@require_api_key("read")
async def get_organization(organization_id: str, request: Request):
    """Get specific organization with detailed statistics"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        # Tenants may only read their own organization -- this once returned
        # ANY organization's full record (admin_email, settings, ...) to ANY
        # authenticated tenant by ID. The fix for that compared against
        # get_org_context(), which is None for an operator session, so it
        # then locked platform scope out of EVERY organization and with it
        # the console's whole org-detail screen. ADR-002 SS8 is explicit
        # that a PLATFORM credential returns data across all organizations
        # and that this is intentional; the ownership test therefore only
        # applies to organization scope (same idiom as domains.py::get_domain
        # and mailboxes.py::get_email_account).
        if not org or (ctx["scope"] == "organization" and org.id != ctx["organization_id"]):
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        org_data = org.to_dict()

        # Add detailed statistics
        domains = session.query(Domain).filter_by(organization_id=organization_id).all()
        org_data["domains"] = [domain.to_dict() for domain in domains]
        org_data["domain_count"] = len(domains)

        # Email account statistics
        email_accounts = (
            session.query(EmailAccount).filter_by(organization_id=organization_id).all()
        )
        org_data["email_account_count"] = len(email_accounts)

        # Storage statistics
        total_storage = sum(domain.total_storage_used for domain in domains)
        total_quota = sum(domain.max_quota for domain in domains)
        org_data["storage_statistics"] = {
            "total_storage_used": total_storage,
            "total_quota": total_quota,
            "usage_percentage": (total_storage / total_quota * 100) if total_quota > 0 else 0,
        }

        return create_api_response("success", "Organization retrieved successfully", org_data)

    except Exception as e:
        logger.error(f"Get organization error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve organization"), status_code=500
        )
    finally:
        session.close()


@router.post(
    "/",
    summary="Create a new organization",
    description="Register a new organization that can own domains and email accounts. The organization ID must be unique and is used as the primary identifier. Requires a platform-scope credential -- creating tenants is a platform-level action (deep-audit.md §2.1) that no tenant credential can reach; resellers should use POST /reseller/sub-organizations instead.",
    response_model=OrganizationWriteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No data provided / validation failed"},
        409: {
            "model": ErrorResponse,
            "description": "Organization ID or external_id already exists",
        },
        500: {"model": ErrorResponse, "description": "Failed to create organization"},
    },
)
# Platform scope, but deliberately NOT role-gated. Tenant signup is
# self-serve: the control plane (mailyte-api) creates the organization the
# instant someone completes onboarding, with no human in the loop. A
# role="admin" gate here requires an *operator session*, which an API key can
# never have (_role_satisfies: a bare platform key has no role at all), so it
# made automated tenant creation impossible -- every org created after
# 2026-08-19 existed only in the control plane, and the first domain any of
# them added failed provisioning forever with "Organization not found".
#
# scope="platform" is the boundary that actually matters and it still holds:
# it is enforced by the api_keys.scope column, so no tenant credential can
# reach this route regardless of its permission flags (ADR-002 §8). "write"
# matches PUT /organizations/{id}, which the control plane already uses --
# creating and updating a tenant now sit in the same tier. DELETE stays
# role-gated: destroying a tenant is still console/human territory.
@require_api_key("write", scope="platform")
async def create_organization(request: Request):
    """Create new organization"""
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    # Validate data
    errors = validate_organization_data(data)
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    session = get_db_session()
    try:
        # Check if organization already exists by ID
        existing = session.query(Organization).filter_by(id=data["id"]).first()
        if existing:
            return JSONResponse(
                content=create_api_response("error", "Organization with this ID already exists"),
                status_code=409,
            )

        # Check if external_id already exists (if provided)
        if data.get("external_id"):
            existing_external = (
                session.query(Organization).filter_by(external_id=data["external_id"]).first()
            )
            if existing_external:
                return JSONResponse(
                    content=create_api_response(
                        "error", "Organization with this external ID already exists"
                    ),
                    status_code=409,
                )

        # Create organization
        org = Organization(
            id=data["id"],
            external_id=data.get("external_id"),
            name=sanitize_text(data["name"]),
            description=sanitize_text(data.get("description")),
            admin_email=data.get("admin_email"),
            admin_name=data.get("admin_name"),
            settings=data.get("settings", {}),
            rate_limits=data.get("rate_limits", {}),
            storage_quotas=data.get("storage_quotas", {}),
            webhook_urls=data.get("webhook_urls", []),
            webhook_secret=data.get("webhook_secret"),
            sending_profile=data.get("sending_profile") or "transactional",
            active=data.get("active", True),
        )

        session.add(org)
        session.commit()

        dispatch_event(
            Events.ORG_CREATED,
            data={"organization_id": org.id, "name": org.name, "admin_email": org.admin_email},
            org_id=org.id,
            source_service="api",
        )

        return JSONResponse(
            content=create_api_response(
                "success", "Organization created successfully", org.to_dict()
            ),
            status_code=201,
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Create organization error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to create organization"), status_code=500
        )
    finally:
        session.close()


@router.put(
    "/{organization_id}",
    summary="Update an organization",
    description="Update the settings of an existing organization such as name, admin contact, webhooks, and rate limits.",
    response_model=OrganizationWriteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No data provided / validation failed"},
        404: {
            "model": ErrorResponse,
            "description": "Organization not found (or belongs to another org -- tenants may only update their own)",
        },
        409: {"model": ErrorResponse, "description": "external_id already exists"},
        500: {"model": ErrorResponse, "description": "Failed to update organization"},
    },
)
@require_api_key("write")
async def update_organization(organization_id: str, request: Request):
    """Update organization"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    # Validate data
    errors = validate_organization_data(data, is_update=True)
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        # Tenants may only update their own organization -- previously any
        # tenant could rewrite ANY other org's admin_email, webhook_secret,
        # settings, or active flag by ID. Scoped to organization callers
        # only: the earlier get_org_context() comparison returned None for
        # an operator session and so 404'd the console out of editing every
        # tenant, contradicting ADR-002 SS8. Still 404, never 403, for a
        # cross-org tenant request (conventions SS8: do not leak existence).
        if not org or (ctx["scope"] == "organization" and org.id != ctx["organization_id"]):
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        # Check if external_id is being updated and already exists
        if "external_id" in data and data["external_id"] != org.external_id:
            existing_external = (
                session.query(Organization).filter_by(external_id=data["external_id"]).first()
            )
            if existing_external:
                return JSONResponse(
                    content=create_api_response(
                        "error", "Organization with this external ID already exists"
                    ),
                    status_code=409,
                )

        # Update fields
        if "external_id" in data:
            org.external_id = data["external_id"]
        if "name" in data:
            org.name = sanitize_text(data["name"])
        if "description" in data:
            org.description = data["description"]
        if "admin_email" in data:
            org.admin_email = data["admin_email"]
        if "admin_name" in data:
            org.admin_name = data["admin_name"]
        if "settings" in data:
            org.settings = data["settings"]
        if "rate_limits" in data:
            org.rate_limits = data["rate_limits"]
        if "storage_quotas" in data:
            org.storage_quotas = data["storage_quotas"]
        if "webhook_urls" in data:
            org.webhook_urls = data["webhook_urls"]
        if "webhook_secret" in data:
            org.webhook_secret = data["webhook_secret"]
        if "sending_profile" in data:
            org.sending_profile = data["sending_profile"]
        if "active" in data:
            org.active = data["active"]

        org.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.ORG_UPDATED,
            data={"organization_id": org.id, "name": org.name, "updated_fields": list(data.keys())},
            org_id=org.id,
            source_service="api",
        )

        return create_api_response("success", "Organization updated successfully", org.to_dict())

    except Exception as e:
        session.rollback()
        logger.error(f"Update organization error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update organization"), status_code=500
        )
    finally:
        session.close()


@router.delete(
    "/{organization_id}",
    summary="Delete an organization",
    description="Permanently remove an organization. The organization must have no remaining domains or email accounts; delete those first. Requires an admin-scoped API key -- deleting a tenant is a platform-level action (deep-audit.md §2.1), not tenant self-service.",
    response_model=SimpleMessageResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Organization still has domains and/or email accounts",
        },
        404: {"model": ErrorResponse, "description": "Organization not found"},
        500: {"model": ErrorResponse, "description": "Failed to delete organization"},
    },
)
@require_api_key("admin", scope="platform", role="admin")
async def delete_organization(organization_id: str, request: Request):
    """Delete organization and all related data"""
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        # Check for existing domains/accounts
        domain_count = session.query(Domain).filter_by(organization_id=organization_id).count()
        account_count = (
            session.query(EmailAccount).filter_by(organization_id=organization_id).count()
        )

        if domain_count > 0 or account_count > 0:
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Cannot delete organization with {domain_count} domains and {account_count} email accounts. Delete them first.",
                ),
                status_code=400,
            )

        org_id_val = org.id
        org_name_val = org.name
        session.delete(org)
        session.commit()

        dispatch_event(
            Events.ORG_DELETED,
            data={"organization_id": org_id_val, "name": org_name_val},
            org_id=org_id_val,
            source_service="api",
        )

        return create_api_response("success", "Organization deleted successfully")

    except Exception as e:
        session.rollback()
        logger.error(f"Delete organization error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete organization"), status_code=500
        )
    finally:
        session.close()


@router.get(
    "/{organization_id}/quotas",
    summary="Get organization quotas",
    description="Retrieve quota limits and current storage usage for an organization, broken down by domain with per-domain account counts.",
    response_model=OrganizationQuotaResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Organization not found (or belongs to another org -- tenants may only read their own)",
        },
        500: {"model": ErrorResponse, "description": "Failed to retrieve quota information"},
    },
)
@require_api_key("read")
async def get_organization_quotas(organization_id: str, request: Request):
    """Get organization quota and usage information"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        # Ownership check applies to organization scope only -- platform
        # scope reads every tenant's quotas by design (ADR-002 SS8), which
        # is precisely what the console's Quotas tab (phase-02 SS2.2) is.
        if not org or (ctx["scope"] == "organization" and org.id != ctx["organization_id"]):
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        domains = session.query(Domain).filter_by(organization_id=organization_id).all()
        email_accounts = (
            session.query(EmailAccount).filter_by(organization_id=organization_id).all()
        )

        quota_info = {
            "organization_id": organization_id,
            "organization_quotas": org.storage_quotas or {},
            "total_domains": len(domains),
            "total_email_accounts": len(email_accounts),
            # phase-02 SS2.6: the console must be able to tell a
            # plan-derived quota from one an operator set by hand before it
            # offers an edit, and must name who to ask about it.
            "quota_override": bool(org.quota_override),
            "quota_override_at": org.quota_override_at.isoformat()
            if org.quota_override_at
            else None,
            "quota_override_by": org.quota_override_by,
            "storage_summary": {
                "total_storage_used": sum(domain.total_storage_used for domain in domains),
                "total_quota": sum(domain.max_quota for domain in domains),
                "domains": [],
            },
        }

        for domain in domains:
            domain_accounts = [acc for acc in email_accounts if acc.domain_id == domain.id]
            quota_info["storage_summary"]["domains"].append(
                {
                    "domain": domain.domain,
                    "storage_used": domain.total_storage_used,
                    "quota": domain.max_quota,
                    "usage_percentage": domain.get_storage_usage_percentage(),
                    "email_accounts": len(domain_accounts),
                    "max_users": domain.max_users,
                }
            )

        return create_api_response(
            "success", "Organization quota information retrieved successfully", quota_info
        )

    except Exception as e:
        logger.error(f"Get organization quotas error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve quota information"),
            status_code=500,
        )
    finally:
        session.close()


@router.get(
    "/by-external-id/{external_id}",
    summary="Look up organization by external ID",
    description="Find an organization using its external system identifier. Returns the same detailed view as the primary get-organization endpoint.",
    response_model=OrganizationDetailResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Organization not found (or belongs to another org -- tenants may only look up their own)",
        },
        500: {"model": ErrorResponse, "description": "Failed to retrieve organization"},
    },
)
@require_api_key("read")
async def get_organization_by_external_id(external_id: str, request: Request):
    """Get organization by external ID"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(external_id=external_id).first()
        # external_id is the Laravel-side key, so this lookup is exactly how
        # the console (and mailyte-api) resolves a billing record to a
        # tenant -- with the old unconditional `org.id != caller_org_id` it
        # was unusable from platform scope, where caller_org_id is None.
        if not org or (ctx["scope"] == "organization" and org.id != ctx["organization_id"]):
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        org_data = org.to_dict()

        # Add detailed statistics
        domains = session.query(Domain).filter_by(organization_id=org.id).all()
        org_data["domains"] = [domain.to_dict() for domain in domains]
        org_data["domain_count"] = len(domains)

        # Email account statistics
        email_accounts = session.query(EmailAccount).filter_by(organization_id=org.id).all()
        org_data["email_account_count"] = len(email_accounts)

        # Storage statistics
        total_storage = sum(domain.total_storage_used for domain in domains)
        total_quota = sum(domain.max_quota for domain in domains)
        org_data["storage_statistics"] = {
            "total_storage_used": total_storage,
            "total_quota": total_quota,
            "usage_percentage": (total_storage / total_quota * 100) if total_quota > 0 else 0,
        }

        return create_api_response("success", "Organization retrieved successfully", org_data)

    except Exception as e:
        logger.error(f"Get organization by external ID error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve organization"), status_code=500
        )
    finally:
        session.close()


@router.put(
    "/{organization_id}/quotas",
    summary="Update organization quotas",
    description="Adjust the storage quotas and rate limits for an organization. These settings "
    "apply across all domains owned by the organization. Requires an admin-scoped API key -- "
    "raising your own quota is a commercial/plan decision (ADR-001's invariant split), not "
    "tenant self-service. A write by a human operator sets an override flag; a write by an "
    "automated caller (no operator identity) is refused with 409 while that flag stands, unless "
    "it passes `force=true`. See console phase-02 SS2.6 decision (a).",
    response_model=OrganizationQuotaUpdateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No quota data provided"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
        409: {
            "model": ErrorResponse,
            "description": "An operator override is in place and force=true was not passed",
        },
        500: {"model": ErrorResponse, "description": "Failed to update quotas"},
    },
)
@require_api_key("admin", scope="platform", role="admin")
async def update_organization_quotas(
    organization_id: str,
    request: Request,
    force: bool = Query(
        False,
        description="Automated callers only -- overwrite a standing operator override and clear "
        "the flag. Ignored for operator writes, which always (re)assert the override.",
    ),
):
    """Update organization quota settings.

    console phase-02 SS2.6 recorded decision (a): console edits are
    overrides that Laravel's plan sync respects. The discriminator between
    "a human operator" and "Laravel's automated plan sync" is
    ctx["operator_id"] -- an operator session carries one, a bare
    platform-scope API key does not. That is the only distinction available
    at this layer, and it is the right one: the flag exists to protect a
    *deliberate human decision* from an unattended job, not to protect one
    credential from another.

    KNOWN GAP, deliberately not papered over here: this route is gated
    role="admin", and _role_satisfies() gives a bare platform-scope API key
    no role at all, so mailyte-api's sync currently gets a 403 from this
    endpoint and never reaches the 409 branch below. The sync's actual
    write path today is rate_limiter.py's POST /quotas/domain/{domain} and
    POST /rate-limits/domain/{domain}, both `@require_api_key("write")`
    with no role gate and both at DOMAIN granularity, so neither consults
    this org-level flag. Closing that requires either giving the sync an
    operator identity or extending the override check down to the
    domain-level writes -- a decision for the phase that owns those routes,
    not something to smuggle in by loosening an auth gate the phase doc
    (SS2.6: "Role: admin") explicitly specifies.
    """
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No quota data provided"), status_code=400
        )

    ctx = request.state.auth_context
    operator_id = ctx.get("operator_id")

    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        if not operator_id and org.quota_override and not force:
            # 409, not a silent no-op and not a 403: the caller is
            # authorised, the request is well-formed, and the state of the
            # resource is what forbids it -- which is exactly what 409
            # means (conventions SS8). The message names WHO and WHEN
            # because the sync's operator on the other end has to be able
            # to find the human to ask, and states the escape hatch so
            # nobody has to read this file to discover it.
            who = org.quota_override_by or "an operator"
            when = org.quota_override_at.isoformat() if org.quota_override_at else "an unknown time"
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Quota override in place: set by {who} at {when}. Automated plan sync will "
                    "not overwrite an operator's deliberate change. Pass ?force=true to override "
                    "it (this also clears the flag), or clear it first via "
                    f"POST /api/v1/organizations/{organization_id}/quotas/clear-override.",
                ),
                status_code=409,
            )

        # Update storage quotas
        if "storage_quotas" in data:
            org.storage_quotas = data["storage_quotas"]

        # Update rate limits
        if "rate_limits" in data:
            org.rate_limits = data["rate_limits"]

        if operator_id:
            # A human edited this. Re-stamped on every operator write, not
            # only the first, so quota_override_at answers "when was this
            # value last decided by a person" rather than "when did the
            # override era begin" -- the former is what a colleague reading
            # the Quotas tab six weeks later actually needs.
            org.quota_override = True
            org.quota_override_at = datetime.now()
            # request.state.operator_email is set alongside operator_id by
            # utils/auth.py's operator session resolver.
            org.quota_override_by = getattr(request.state, "operator_email", None)
        elif force:
            # An automated caller deliberately overrode a standing flag.
            # Clearing it is the honest bookkeeping: the current value is
            # no longer the operator's, so leaving the flag set would
            # attribute a machine's number to a human.
            org.quota_override = False
            org.quota_override_at = None
            org.quota_override_by = None

        org.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.ORG_UPDATED,
            data={
                "organization_id": organization_id,
                "update_type": "quotas",
                "storage_quotas": org.storage_quotas,
                "rate_limits": org.rate_limits,
                "quota_override": bool(org.quota_override),
            },
            org_id=organization_id,
            source_service="api",
        )

        return create_api_response(
            "success",
            "Organization quotas updated successfully",
            {
                "organization_id": organization_id,
                "storage_quotas": org.storage_quotas,
                "rate_limits": org.rate_limits,
                "quota_override": bool(org.quota_override),
                "quota_override_at": org.quota_override_at.isoformat()
                if org.quota_override_at
                else None,
                "quota_override_by": org.quota_override_by,
            },
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update organization quotas error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update quotas"), status_code=500
        )
    finally:
        session.close()


class QuotaOverrideClearRequest(BaseModel):
    """Body for clearing a quota override."""

    reason: str = Field(
        ...,
        min_length=1,
        description="Why the override is being dropped. Recorded by the audit middleware -- "
        "phase-02's cross-cutting rule that corrective actions carry a reason.",
    )


@router.post(
    "/{organization_id}/quotas/clear-override",
    summary="Clear an operator quota override",
    description="Drops the quota override flag so Laravel's plan sync resumes managing this "
    "organization's quotas (console phase-02 SS2.6 decision (a)). The quota values themselves "
    "are left exactly as they are -- this hands control back, it does not roll anything back; "
    "the next plan sync is what restores the plan-derived numbers.",
    response_model=OrganizationQuotaOverrideClearResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Organization not found"},
        500: {"model": ErrorResponse, "description": "Failed to clear quota override"},
    },
)
@require_api_key("admin", scope="platform", role="admin")
async def clear_organization_quota_override(
    organization_id: str, body: QuotaOverrideClearRequest, request: Request
):
    """Clear the quota override so automated plan sync resumes.

    Deliberately idempotent: clearing an org that is not overridden
    succeeds rather than 404/409-ing. The caller's intent is "make sure
    sync owns this", and that is already true -- failing would only invite
    a retry loop against a state that is already correct.
    """
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        was_overridden = bool(org.quota_override)
        org.quota_override = False
        org.quota_override_at = None
        org.quota_override_by = None
        org.updated_at = datetime.now()
        session.commit()

        logger.warning(
            f"Quota override cleared: org={organization_id} was_overridden={was_overridden} "
            f"by={getattr(request.state, 'operator_email', None)} reason={body.reason!r}"
        )

        return create_api_response(
            "success",
            "Quota override cleared; plan sync will resume managing this organization",
            {"organization_id": organization_id, "quota_override": False},
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Clear quota override error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to clear quota override"), status_code=500
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# AI assistant (Maya) settings -- entitlement + org policy (mobile v1 SS13a)
# ---------------------------------------------------------------------------

_AI_POLICY_VALUES = ("unset", "allowed", "blocked", "accepted_for_all")
_AI_ENTITLEMENT_FIELDS = ("ai_entitled", "ai_plan", "ai_monthly_quota")
_AI_SETTINGS_FIELDS = _AI_ENTITLEMENT_FIELDS + ("ai_org_policy",)


def _ai_settings_payload(org) -> dict:
    return {
        "organization_id": org.id,
        "ai_entitled": bool(org.ai_entitled),
        "ai_plan": org.ai_plan,
        "ai_org_policy": org.ai_org_policy or "unset",
        "ai_monthly_quota": org.ai_monthly_quota,
        "ai_policy_updated_at": org.ai_policy_updated_at.isoformat()
        if org.ai_policy_updated_at
        else None,
        "ai_policy_updated_by": org.ai_policy_updated_by,
    }


def _ai_settings_actor(request: Request) -> str:
    """Who is making this change, as a display string. Operator sessions carry
    an email; API keys a name/prefix. Recorded verbatim into any
    revoked_by_organisation ledger entries this write produces, so a holder
    reading their consent history can see WHO blocked Maya for them."""
    operator_email = getattr(request.state, "operator_email", None)
    if operator_email:
        return operator_email
    api_key_data = getattr(request.state, "api_key_data", None) or {}
    label = api_key_data.get("name") or api_key_data.get("key_id") or api_key_data.get("id")
    return f"api-key:{label}" if label else "platform"


@router.get(
    "/{organization_id}/ai-settings",
    summary="Read an organization's AI assistant settings",
    description="Entitlement (plan), org policy, monthly quota and the policy audit stamp. "
    "Tenants read only their own organization; cross-org ids are 404, never 403.",
)
@require_api_key("read")
async def get_organization_ai_settings(organization_id: str, request: Request):
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org or (ctx["scope"] == "organization" and org.id != ctx["organization_id"]):
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )
        return create_api_response("success", "AI settings", _ai_settings_payload(org))
    except Exception as e:
        logger.error(f"Get AI settings error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to read AI settings"), status_code=500
        )
    finally:
        session.close()


@router.put(
    "/{organization_id}/ai-settings",
    summary="Update an organization's AI assistant settings",
    description="Sets the Maya entitlement (`ai_entitled`, `ai_plan`, `ai_monthly_quota`) "
    "and/or the org policy (`ai_org_policy`: unset | allowed | blocked | accepted_for_all). "
    "Entitlement fields are how Laravel's billing (or staff) flip a paying organization on -- "
    "platform credentials only; an organization credential may set only its own "
    "`ai_org_policy`. Setting the policy to `blocked` revokes every holder's standing "
    "consent and writes a revoked_by_organisation entry to each of their consent histories, "
    "attributed to the caller (mobile v1 13a-2).",
)
@require_api_key("admin")
async def update_organization_ai_settings(organization_id: str, request: Request):
    """Entitlement is a commercial fact; policy is the org's own governance.

    Unknown fields are rejected rather than ignored: a typo like
    `ai_org_polcy` silently doing nothing would leave a paying org's admin
    convinced they had blocked AI when they had not. Local import of the
    consent helpers keeps this file's shared import block untouched.
    """
    from utils.ai_consent import SessionCursor, revoke_consents_for_organization

    ctx = request.state.auth_context
    data = await request.json()
    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    unknown = sorted(set(data) - set(_AI_SETTINGS_FIELDS))
    if unknown:
        return JSONResponse(
            content=create_api_response("error", f"Unknown fields: {', '.join(unknown)}"),
            status_code=400,
        )

    errors = []
    if "ai_entitled" in data and not isinstance(data["ai_entitled"], bool):
        errors.append("ai_entitled must be a boolean")
    if "ai_plan" in data and data["ai_plan"] is not None:
        if not isinstance(data["ai_plan"], str) or not (1 <= len(data["ai_plan"]) <= 64):
            errors.append("ai_plan must be a string of at most 64 characters, or null")
    if "ai_org_policy" in data and data["ai_org_policy"] not in _AI_POLICY_VALUES:
        errors.append("ai_org_policy must be one of: " + ", ".join(_AI_POLICY_VALUES))
    if "ai_monthly_quota" in data and data["ai_monthly_quota"] is not None:
        quota = data["ai_monthly_quota"]
        if isinstance(quota, bool) or not isinstance(quota, int) or not 0 <= quota <= 1_000_000:
            errors.append("ai_monthly_quota must be an integer between 0 and 1000000, or null")
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    tenant = ctx["scope"] == "organization"
    if tenant and any(field in data for field in _AI_ENTITLEMENT_FIELDS):
        # 403, not 404: the organization is the caller's own; what is refused
        # is the class of field, which is a platform/billing decision
        # (ADR-001's invariant split, same reasoning as the quotas route).
        return JSONResponse(
            content=create_api_response(
                "error",
                "Entitlement fields (ai_entitled, ai_plan, ai_monthly_quota) are set by "
                "the platform; an organization may set only ai_org_policy",
            ),
            status_code=403,
        )

    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org or (tenant and org.id != ctx["organization_id"]):
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        actor = _ai_settings_actor(request)
        previous_policy = org.ai_org_policy or "unset"
        revoked = 0

        if "ai_entitled" in data:
            org.ai_entitled = data["ai_entitled"]
        if "ai_plan" in data:
            org.ai_plan = data["ai_plan"]
        if "ai_monthly_quota" in data:
            org.ai_monthly_quota = data["ai_monthly_quota"]
        if "ai_org_policy" in data:
            org.ai_org_policy = data["ai_org_policy"]
            org.ai_policy_updated_at = datetime.now()
            org.ai_policy_updated_by = actor
            if data["ai_org_policy"] == "blocked" and previous_policy != "blocked":
                # 13a-2: blocking is a hard veto that also clears standing
                # individual consents -- each holder gets a
                # revoked_by_organisation ledger entry naming the actor.
                # Same session/transaction as the policy write: either both
                # land or neither does.
                revoked = revoke_consents_for_organization(
                    SessionCursor(session.connection()), org.id, actor
                )

        org.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.ORG_UPDATED,
            data={
                "organization_id": org.id,
                "update_type": "ai_settings",
                "updated_fields": sorted(data.keys()),
                "revoked_consents": revoked,
            },
            org_id=org.id,
            source_service="api",
        )

        payload = _ai_settings_payload(org)
        payload["revoked_consents"] = revoked
        return create_api_response("success", "AI settings updated successfully", payload)

    except Exception as e:
        session.rollback()
        logger.error(f"Update AI settings error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update AI settings"), status_code=500
        )
    finally:
        session.close()
