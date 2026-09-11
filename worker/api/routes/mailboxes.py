#!/usr/bin/env python3
"""
Email Account Management API Routes
Comprehensive email account management with production features and organization support.

This module provides API endpoints for:
- Email account CRUD operations with proper validation and relationships
- Account quota management and monitoring
- User authentication and security settings
- Integration with the new organization/domain/email account structure

Key Features:
- Organization and domain-aware account management
- Comprehensive validation and error handling
- Quota and usage management
- Enhanced security features
"""

import html as html_module
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
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

from schemas.common import ErrorResponse, SimpleMessageResponse
from schemas.mailbox import (
    MailboxAddResponse,
    MailboxDetailResponse,
    MailboxEditResponse,
    MailboxGetLegacyResponse,
    MailboxLegacyQuotaResponse,
    MailboxLegacyQuotaUpdateResponse,
    MailboxListResponse,
    MailboxQuotaResponse,
    MailboxQuotaUpdateResponse,
    MailboxStatsResponse,
    MailboxWriteResponse,
)
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker
from utils.auth import (
    create_api_response,
    hash_password,
    require_api_key,
    validate_password_strength,
    verify_mailbox_scope,
)
from utils.database import get_db_connection

# The doveadm auth-cache flush the SMTP-credential routes already use.
# Dovecot caches successful auth for up to auth_cache_ttl (1 hour), so a
# suspended/deleted/password-changed mailbox keeps authenticating from cache
# unless the entry is flushed (see utils/smtp_credentials.flush_auth_cache).
from utils.smtp_credentials import flush_auth_cache

from database.models.authentication import MailboxSession
from database.models.core import Domain, EmailAccount, Organization
from database.models.enums import AccountStatus

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Pydantic request/response models for OpenAPI documentation ---


class MailboxCreate(BaseModel):
    email: str = Field(
        ..., description="Full email address for the new mailbox", example="user@example.com"
    )
    password: str = Field(
        ...,
        description="Password for IMAP/SMTP authentication (will be bcrypt-hashed)",
        example="SecureP@ss123!",
    )
    name: str | None = Field(
        None, description="Display name of the mailbox owner", example="John Doe"
    )
    domain_id: str = Field(
        ...,
        description="ULID of the domain this mailbox belongs to",
        example="01ARZ3NDEKTSV4RRFFQ69G5FAV",
    )
    storage_quota: int = Field(
        5368709120, description="Storage quota in bytes (default 5GB)", example=5368709120
    )


class MailboxUpdate(BaseModel):
    name: str | None = Field(None, description="Updated display name")
    status: str | None = Field(
        None, description="Account status: active, inactive, or suspended", example="active"
    )
    storage_quota: int | None = Field(None, description="Updated storage quota in bytes")
    forward_enabled: bool | None = Field(None, description="Enable email forwarding")
    forward_destination: str | None = Field(
        None, description="Forwarding destination email address"
    )
    vacation_enabled: bool | None = Field(None, description="Enable vacation auto-responder")
    vacation_message: str | None = Field(None, description="Vacation auto-reply message text")


class QuotaUpdate(BaseModel):
    storage_quota: int = Field(..., description="New storage quota in bytes", example=10737418240)


def get_db_session():
    """Get SQLAlchemy session"""
    engine = create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    Session = sessionmaker(bind=engine)
    return Session()


def validate_email(email):
    """Validate email address format"""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


# phase-07 H7: password strength is validated by utils.auth's
# validate_password_strength() (single source of truth, see that module) --
# this used to be its own, weaker (8-char) copy here.
validate_password = validate_password_strength


def validate_email_account_data(data, is_update=False):
    """Validate email account data"""
    errors = []

    if not is_update and ("email" not in data or not data["email"]):
        errors.append("Email address is required")
    elif "email" in data and not validate_email(data["email"]):
        errors.append("Invalid email format")

    if not is_update and ("password" not in data or not data["password"]):
        errors.append("Password is required")
    elif "password" in data and data["password"]:
        valid_password, password_msg = validate_password(data["password"])
        if not valid_password:
            errors.append(password_msg)

    if "storage_quota" in data:
        try:
            quota = int(data["storage_quota"])
            if quota < 0:
                errors.append("Storage quota must be non-negative")
        except (ValueError, TypeError):
            errors.append("Storage quota must be a valid number")

    if "status" in data and data["status"] not in [status.value for status in AccountStatus]:
        errors.append("Invalid account status")

    return errors


# Whitelisted ORDER BY targets for the cross-tenant mailbox search
# (console phase-02 SS2.4). Closed mapping: ORDER BY takes no bound
# parameter, so anything not listed here would have to be interpolated.
#
# The activity column is `last_login`, not `last_login_at` -- verified
# against database/models/core.py's EmailAccount and 001_init_schema.sql
# rather than assumed from the phase doc's prose.
_MAILBOX_SORT_KEYS = ("email", "created_at", "storage_used", "last_login")
_SORT_DIRECTIONS = ("asc", "desc")


@router.get(
    "/email-accounts",
    summary="List all email accounts",
    description="Retrieve a paginated list of email accounts, searchable by address or display "
    "name and filterable by domain, organization, or status. Sortable by email, creation date, "
    "storage used or last login. Includes domain and organization context for each account.",
    response_model=MailboxListResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Unknown sort key/direction or status"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve email accounts"},
    },
)
@require_api_key("read")
async def list_email_accounts(
    request: Request,
    q: str = Query(
        None,
        description="Search the email address or the mailbox display name (`name`), both LIKE.",
    ),
    organization_id: str = Query(
        None,
        description="Platform scope only -- filter to a single organization. Ignored for tenant credentials, which always see only their own org.",
    ),
    domain_id: str = Query(None),
    status: str = Query(None),
    sort_by: str = Query("email", description="email | created_at | storage_used | last_login"),
    sort_dir: str = Query("asc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
):
    """List all email accounts with domain and organization context.

    Tenant scope: always the caller's own org -- a client-supplied
    organization_id is ignored, never honoured (task 6.4's org_filter
    principle; this previously took organization_id as an optional
    client-supplied query param, honoured verbatim). Platform scope: sees
    every organization unless organization_id narrows it (ADR-002 SS8
    "sudo sees all").

    `q` is what makes phase-02 SS2.4 ("customer says mail to x@y.com is
    bouncing") a single request rather than an operator paging through
    every tenant looking for one address.
    """
    per_page = min(per_page, 200)
    ctx = request.state.auth_context

    if sort_by not in _MAILBOX_SORT_KEYS:
        return JSONResponse(
            content=create_api_response(
                "error", f"sort_by must be one of {', '.join(_MAILBOX_SORT_KEYS)}"
            ),
            status_code=422,
        )
    if sort_dir not in _SORT_DIRECTIONS:
        return JSONResponse(
            content=create_api_response("error", "sort_dir must be 'asc' or 'desc'"),
            status_code=422,
        )
    if status:
        # AccountStatus(status) raises ValueError on an unknown value, which
        # the bare `except` below would otherwise turn into a 500. A bad
        # filter value is the caller's mistake, not the server's (SS8).
        try:
            status_filter = AccountStatus(status)
        except ValueError:
            valid = ", ".join(member.value for member in AccountStatus)
            return JSONResponse(
                content=create_api_response("error", f"status must be one of {valid}"),
                status_code=422,
            )

    session = get_db_session()
    try:
        query = session.query(EmailAccount)
        if ctx["scope"] == "organization":
            query = query.filter_by(organization_id=ctx["organization_id"])
        elif organization_id:
            query = query.filter_by(organization_id=organization_id)

        if domain_id:
            query = query.filter_by(domain_id=domain_id)
        if status:
            query = query.filter_by(status=status_filter)
        if q:
            like = f"%{q}%"
            query = query.filter(or_(EmailAccount.email.like(like), EmailAccount.name.like(like)))

        sort_columns = {
            "email": EmailAccount.email,
            "created_at": EmailAccount.created_at,
            "storage_used": EmailAccount.storage_used,
            "last_login": EmailAccount.last_login,
        }
        sort_column = sort_columns[sort_by]
        ordering = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
        # id tiebreaker: last_login is nullable and storage_used is heavily
        # tied, so without it consecutive pages can repeat and skip rows.
        query = query.order_by(ordering, EmailAccount.id.asc())

        total = query.count()
        accounts = query.offset((page - 1) * per_page).limit(per_page).all()

        # Two grouped lookups for the page rather than two per row (2 + 2N
        # before -- 402 queries on a full 200-row page). Cross-tenant pages
        # made this worse, not better: consecutive rows rarely shared an
        # organization, so nothing was served from the identity map.
        domain_ids = {account.domain_id for account in accounts}
        org_ids = {account.organization_id for account in accounts}
        domain_names: dict = {}
        org_names: dict = {}
        if accounts:
            domain_names = dict(
                session.query(Domain.id, Domain.domain).filter(Domain.id.in_(domain_ids)).all()
            )
            org_names = dict(
                session.query(Organization.id, Organization.name)
                .filter(Organization.id.in_(org_ids))
                .all()
            )

        result = []
        for account in accounts:
            account_data = account.to_dict()

            account_data["domain_name"] = domain_names.get(account.domain_id, "Unknown")
            account_data["organization_name"] = org_names.get(account.organization_id, "Unknown")

            # Remove sensitive information
            if "password" in account_data:
                del account_data["password"]

            result.append(account_data)

        return create_api_response(
            "success",
            "Email accounts retrieved successfully",
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
        logger.error(f"List email accounts error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve email accounts"),
            status_code=500,
        )
    finally:
        session.close()


@router.get(
    "/email-accounts/{account_id}",
    summary="Get email account details",
    description="Retrieve detailed information for a specific email account by ID, including its associated domain and organization data. Sensitive fields like password are excluded.",
    response_model=MailboxDetailResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Email account not found"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve email account"},
    },
)
@require_api_key("read")
async def get_email_account(account_id: str, request: Request):
    """Get specific email account with detailed information"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        # Platform scope may fetch any org's mailbox by id; organization
        # scope only its own (task 6.4).
        if not account or (
            ctx["scope"] == "organization" and account.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Email account not found"), status_code=404
            )

        account_data = account.to_dict()

        # Add domain and organization information
        domain = session.query(Domain).filter_by(id=account.domain_id).first()
        org = session.query(Organization).filter_by(id=account.organization_id).first()

        account_data["domain"] = domain.to_dict() if domain else None
        account_data["organization"] = org.to_dict() if org else None

        # Remove sensitive information
        if "password" in account_data:
            del account_data["password"]

        return create_api_response("success", "Email account retrieved successfully", account_data)

    except Exception as e:
        logger.error(f"Get email account error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve email account"),
            status_code=500,
        )
    finally:
        session.close()


@router.post(
    "/email-accounts",
    summary="Create a new email account",
    description="Provision a new email account. The password is bcrypt-hashed and the mailbox is immediately usable via IMAP/POP3/SMTP. Storage quota defaults to 1GB. Validates email format, password strength, and domain existence.",
    response_model=MailboxWriteResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "No data provided / validation failed / domain user limit reached",
        },
        404: {"model": ErrorResponse, "description": "Domain not found"},
        409: {"model": ErrorResponse, "description": "Email account or external_id already exists"},
        500: {"model": ErrorResponse, "description": "Failed to create email account"},
    },
)
@require_api_key("write")
async def create_email_account(request: Request):
    """Create new email account"""
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    # Validate data
    errors = validate_email_account_data(data)
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    session = get_db_session()
    try:
        # Extract domain from email
        email_parts = data["email"].split("@")
        local_part = email_parts[0]
        domain_name = email_parts[1]

        # Find domain.
        #
        # The org filter is load-bearing, not defensive: organization_id is
        # inherited from whatever domain this lookup returns, so an unfiltered
        # lookup let a tenant create a mailbox on ANOTHER tenant's domain and
        # have it silently adopted into that tenant's organization. 404 rather
        # than 403 for a domain the caller does not own -- conventions SS8
        # forbids leaking that another tenant holds it.
        ctx = request.state.auth_context
        domain_query = session.query(Domain).filter_by(domain=domain_name)
        if ctx["scope"] == "organization":
            domain_query = domain_query.filter_by(organization_id=ctx["organization_id"])
        domain = domain_query.first()
        if not domain:
            return JSONResponse(
                content=create_api_response("error", f"Domain {domain_name} not found"),
                status_code=404,
            )

        # Check if email already exists
        existing_account = (
            session.query(EmailAccount).filter(EmailAccount.email.ilike(data["email"])).first()
        )
        if existing_account:
            return JSONResponse(
                content=create_api_response(
                    "error", f"Email account {data['email']} already exists"
                ),
                status_code=409,
            )

        # Check if external_id already exists (if provided)
        if data.get("external_id"):
            existing_external = (
                session.query(EmailAccount).filter_by(external_id=data["external_id"]).first()
            )
            if existing_external:
                return JSONResponse(
                    content=create_api_response(
                        "error", "Email account with this external_id already exists"
                    ),
                    status_code=409,
                )

        # Check domain user limit
        account_count = session.query(EmailAccount).filter_by(domain_id=domain.id).count()
        if account_count >= domain.max_users:
            return JSONResponse(
                content=create_api_response(
                    "error", f"Domain has reached maximum user limit ({domain.max_users})"
                ),
                status_code=400,
            )

        # Hash password
        hashed_password = hash_password(data["password"])

        # Create email account
        account = EmailAccount(
            email=data["email"],
            local_part=local_part,
            domain_id=domain.id,
            organization_id=domain.organization_id,
            password=hashed_password,
            name=sanitize_text(data.get("name")),
            # AccountStatus's values are lowercase ("active"/"inactive"/
            # "suspended", see database/models/enums.py) -- the default here
            # was "ACTIVE" (uppercase), which doesn't match any member and
            # made every create_email_account call with no explicit status
            # fail with "'ACTIVE' is not a valid AccountStatus". Callers
            # (e.g. mailyte-api) never send this field, so this default was
            # hit on every real mailbox creation.
            status=AccountStatus(data.get("status", AccountStatus.ACTIVE.value)),
            storage_quota=data.get("storage_quota", 1073741824),  # 1GB default
            rate_limits=data.get("rate_limits", {}),
            storage_quotas=data.get("storage_quotas", {}),
            forward_enabled=data.get("forward_enabled", False),
            forward_destination=data.get("forward_destination"),
            vacation_enabled=data.get("vacation_enabled", False),
            vacation_message=data.get("vacation_message"),
            external_id=data.get("external_id"),
        )

        session.add(account)

        # Update domain counters
        domain.total_email_accounts += 1

        session.commit()

        dispatch_event(
            Events.MAILBOX_CREATED,
            data={
                "account_id": account.id,
                "email": account.email,
                "domain_id": account.domain_id,
                "organization_id": account.organization_id,
            },
            org_id=account.organization_id,
            domain=domain_name,
            source_service="api",
        )

        result = account.to_dict()
        if "password" in result:
            del result["password"]

        return JSONResponse(
            content=create_api_response("success", "Email account created successfully", result),
            status_code=201,
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Create email account error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to create email account"), status_code=500
        )
    finally:
        session.close()


@router.put(
    "/email-accounts/{account_id}",
    summary="Update an email account",
    description="Update properties of an existing email account such as name, status, storage quota, forwarding settings, and vacation auto-responder. Password changes are re-hashed automatically.",
    response_model=MailboxWriteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No data provided / validation failed"},
        404: {"model": ErrorResponse, "description": "Email account not found"},
        409: {"model": ErrorResponse, "description": "external_id already exists"},
        500: {"model": ErrorResponse, "description": "Failed to update email account"},
    },
)
@require_api_key("write")
async def update_email_account(account_id: str, request: Request):
    """Update email account"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    # Validate data
    errors = validate_email_account_data(data, is_update=True)
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        # Platform scope may update any org's mailbox (ADR-002 SS8); organization
        # scope only its own, and cross-org stays 404 (conventions SS8).
        if not account or (
            ctx["scope"] == "organization" and account.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Email account not found"), status_code=404
            )

        # Update fields
        if "external_id" in data:
            # Check if external_id already exists (if provided and different)
            if data["external_id"] and data["external_id"] != account.external_id:
                existing_external = (
                    session.query(EmailAccount).filter_by(external_id=data["external_id"]).first()
                )
                if existing_external:
                    return JSONResponse(
                        content=create_api_response(
                            "error", "Email account with this external_id already exists"
                        ),
                        status_code=409,
                    )
            account.external_id = data["external_id"]
        if "password" in data:
            account.password = hash_password(data["password"])
        if "name" in data:
            account.name = sanitize_text(data["name"])
        if "status" in data:
            account.status = AccountStatus(data["status"])
        if "storage_quota" in data:
            account.storage_quota = data["storage_quota"]
        if "rate_limits" in data:
            account.rate_limits = data["rate_limits"]
        if "storage_quotas" in data:
            account.storage_quotas = data["storage_quotas"]
        if "forward_enabled" in data:
            account.forward_enabled = data["forward_enabled"]
        if "forward_destination" in data:
            account.forward_destination = data["forward_destination"]
        if "vacation_enabled" in data:
            account.vacation_enabled = data["vacation_enabled"]
        if "vacation_message" in data:
            account.vacation_message = data["vacation_message"]

        # Billing-owned fields. Laravel decides these from what the customer
        # bought; the mail server stores and enforces them and never derives
        # them itself (ADR-001). `ai_monthly_quota` NULL means "fall back to
        # the organization's value", which is what every mailbox billing has
        # not touched should keep doing.
        if "ai_monthly_quota" in data:
            quota = data["ai_monthly_quota"]
            account.ai_monthly_quota = int(quota) if quota is not None else None
        if "billing_tier" in data:
            account.billing_tier = data["billing_tier"] or None

        account.updated_at = datetime.now()
        session.commit()

        # A suspend/deactivate or password change must bite on the NEXT
        # IMAP/SMTP AUTH attempt, not after Dovecot's 1h auth-cache TTL --
        # same doveadm flush the SMTP-credential routes do. Runs after the
        # commit: the mutation is already durable, so a flush failure only
        # degrades to the TTL window (the helper logs it) and never fails
        # the request.
        if "password" in data or "status" in data:
            flush_auth_cache(account.email)

        dispatch_event(
            Events.MAILBOX_UPDATED,
            data={
                "account_id": account.id,
                "email": account.email,
                "organization_id": account.organization_id,
                "updated_fields": [k for k in data.keys() if k != "password"],
            },
            org_id=account.organization_id,
            source_service="api",
        )

        result = account.to_dict()
        if "password" in result:
            del result["password"]

        return create_api_response("success", "Email account updated successfully", result)

    except Exception as e:
        session.rollback()
        logger.error(f"Update email account error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update email account"), status_code=500
        )
    finally:
        session.close()


@router.delete(
    "/email-accounts/{account_id}",
    summary="Delete an email account",
    description="Permanently delete an email account and update the associated domain counters (total accounts and storage used). This action cannot be undone.",
    response_model=SimpleMessageResponse,
    responses={
        204: {
            "description": "Already absent (never existed, or belongs to another org) -- idempotent delete-retry-safe (phase-04 task 4.3)"
        },
        500: {"model": ErrorResponse, "description": "Failed to delete email account"},
    },
)
@require_api_key("write")
async def delete_email_account(account_id: str, request: Request):
    """Delete email account"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        # Platform scope may delete any org's mailbox (ADR-002 SS8); organization
        # scope only its own.
        if not account or (
            ctx["scope"] == "organization" and account.organization_id != ctx["organization_id"]
        ):
            # Already absent (from the caller's org-scoped view) is the
            # desired end state of a delete -- 204, not 404, so a retried
            # delete is naturally safe (phase-04 task 4.3). Indistinguishable
            # from "belongs to another org", which is intentional: 404 already
            # forbade telling those two cases apart (conventions §8).
            return Response(status_code=204)

        account_id_val = account.id
        account_email_val = account.email
        account_org_id_val = account.organization_id

        # Update domain counters
        domain = session.query(Domain).filter_by(id=account.domain_id).first()
        if domain:
            domain.total_email_accounts = max(0, domain.total_email_accounts - 1)
            domain.total_storage_used -= account.storage_used

        session.delete(account)
        session.commit()

        # The deleted mailbox must stop authenticating now, not after the
        # auth-cache TTL (see update_email_account above). Best-effort after
        # the commit -- a flush failure is logged, never a request failure.
        flush_auth_cache(account_email_val)

        dispatch_event(
            Events.MAILBOX_DELETED,
            data={
                "account_id": account_id_val,
                "email": account_email_val,
                "organization_id": account_org_id_val,
            },
            org_id=account_org_id_val,
            source_service="api",
        )

        return create_api_response("success", "Email account deleted successfully")

    except Exception as e:
        session.rollback()
        logger.error(f"Delete email account error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete email account"), status_code=500
        )
    finally:
        session.close()


@router.get(
    "/email-accounts/{account_id}/quotas",
    summary="Get account quota and usage",
    description="Retrieve detailed storage quota and usage information for an email account, including attachment and email storage breakdown, usage percentage, rate limits, and whether the account is over threshold.",
    response_model=MailboxQuotaResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Email account not found"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve quota information"},
    },
)
@require_api_key("read")
async def get_account_quotas(account_id: str, request: Request):
    """Get email account quota and usage information"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        # Platform scope may read any org's quota (ADR-002 SS8); organization
        # scope only its own.
        if not account or (
            ctx["scope"] == "organization" and account.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Email account not found"), status_code=404
            )

        quota_info = {
            "account_id": account_id,
            "email": account.email,
            "storage_quota": account.storage_quota,
            "storage_used": account.storage_used,
            "attachment_storage_used": account.attachment_storage_used,
            "email_storage_used": account.email_storage_used,
            "usage_percentage": account.get_storage_usage_percentage(),
            "total_files": account.total_files,
            "total_attachments": account.total_attachments,
            "total_emails": account.total_emails,
            "rate_limits": account.rate_limits or {},
            "storage_quotas": account.storage_quotas or {},
            "last_storage_calculation": account.last_storage_calculation.isoformat()
            if account.last_storage_calculation
            else None,
            "over_threshold": account.is_storage_over_threshold(),
        }

        return create_api_response(
            "success", "Account quota information retrieved successfully", quota_info
        )

    except Exception as e:
        logger.error(f"Get account quotas error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve quota information"),
            status_code=500,
        )
    finally:
        session.close()


@router.put(
    "/email-accounts/{account_id}/quotas",
    summary="Update account quota settings",
    description="Update storage quota, rate limits, and storage quota sub-settings for an email account. Allows fine-grained control over account resource allocation.",
    response_model=MailboxQuotaUpdateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No quota data provided"},
        404: {"model": ErrorResponse, "description": "Email account not found"},
        500: {"model": ErrorResponse, "description": "Failed to update quotas"},
    },
)
@require_api_key("write")
async def update_account_quotas(account_id: str, request: Request):
    """Update email account quota settings"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No quota data provided"), status_code=400
        )

    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        # Platform scope may adjust any org's quota (ADR-002 SS8); organization
        # scope only its own.
        if not account or (
            ctx["scope"] == "organization" and account.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Email account not found"), status_code=404
            )

        # Update quotas
        if "storage_quota" in data:
            account.storage_quota = data["storage_quota"]
        if "rate_limits" in data:
            account.rate_limits = data["rate_limits"]
        if "storage_quotas" in data:
            account.storage_quotas = data["storage_quotas"]

        account.updated_at = datetime.now()
        session.commit()

        return create_api_response(
            "success",
            "Account quotas updated successfully",
            {
                "account_id": account_id,
                "storage_quota": account.storage_quota,
                "rate_limits": account.rate_limits,
                "storage_quotas": account.storage_quotas,
            },
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update account quotas error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update quotas"), status_code=500
        )
    finally:
        session.close()


class MailboxPasswordReset(BaseModel):
    new_password: str = Field(
        ...,
        description="New password (utils.auth.validate_password_strength's policy: 12+ chars, "
        "letters and numbers, not on the common list). bcrypt-hashed, never echoed back.",
        example="Welcome-Temp-2026",
    )
    temporary: bool = Field(
        False,
        description="When true the holder must set their own password on next sign-in: the "
        "webmail/mobile session is issued but every /api/v1/mailbox/* route except "
        "POST /security/password and logout answers 403 password_change_required.",
    )
    reason: str = Field(
        "temporary",
        description="Recorded as password_change_reason when temporary=true: "
        "`temporary` (onboarding) or `admin_reset` (support-driven reset). Ignored otherwise.",
    )


# What an admin may record as the reason for a forced change. `expired` is
# also a valid column value but is reserved for a future expiry sweep -- an
# operator resetting a password by hand is never that.
_ADMIN_PASSWORD_CHANGE_REASONS = ("temporary", "admin_reset")


@router.post(
    "/email-accounts/{account_id}/reset-password",
    summary="Reset an email account's password",
    description="Set a new password for a mailbox on the holder's behalf. bcrypt-hashes it, "
    "flushes Dovecot's auth cache so IMAP/SMTP honour the change immediately, and signs out "
    "every webmail/mobile session of the mailbox. With `temporary: true` the holder is forced "
    "to choose their own password at next sign-in (see POST /api/v1/mailbox/security/password). "
    "Organization scope may only reset its own mailboxes; cross-org is 404.",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Weak password (`error_code: weak_password`, `msg` carries the "
            "failed rule) or unknown reason",
        },
        404: {"model": ErrorResponse, "description": "Email account not found"},
        500: {"model": ErrorResponse, "description": "Failed to reset password"},
    },
)
@require_api_key("write")
async def reset_email_account_password(
    account_id: str, body: MailboxPasswordReset, request: Request
):
    """Admin-side counterpart of the holder's own POST /api/v1/mailbox/security/password."""
    ctx = request.state.auth_context

    # Same policy as every other password in the system (phase-07 H7). 400
    # rather than 422 to match this module's neighbours, but with the same
    # error_code the holder-facing endpoint uses so clients branch once.
    valid_password, password_msg = validate_password(body.new_password)
    if not valid_password:
        return JSONResponse(
            content=create_api_response("error", password_msg, error_code="weak_password"),
            status_code=400,
        )

    reason = (body.reason or "temporary").strip().lower()
    if reason not in _ADMIN_PASSWORD_CHANGE_REASONS:
        return JSONResponse(
            content=create_api_response(
                "error", f"reason must be one of {', '.join(_ADMIN_PASSWORD_CHANGE_REASONS)}"
            ),
            status_code=400,
        )

    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        # Platform scope may reset any org's mailbox (ADR-002 SS8); organization
        # scope only its own, and cross-org stays 404 (conventions SS8).
        if not account or (
            ctx["scope"] == "organization" and account.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Email account not found"), status_code=404
            )

        now = datetime.now()
        account.password = hash_password(body.new_password)
        account.must_change_password = bool(body.temporary)
        account.password_change_reason = reason if body.temporary else None
        account.password_changed_at = now
        account.updated_at = now

        # An admin reset means the old credential is no longer trusted -- a
        # compromised account, a departed employee, an onboarding handover.
        # Every existing webmail/mobile session goes with it; the holder
        # signs in again with the password the admin just set.
        sessions_revoked = (
            session.query(MailboxSession)
            .filter(
                MailboxSession.email_account_id == account.id,
                MailboxSession.revoked_at.is_(None),
            )
            .update({MailboxSession.revoked_at: now}, synchronize_session=False)
        )
        session.commit()

        account_email = account.email
        account_org_id = account.organization_id

        # After the commit, same as update_email_account: the new hash is
        # durable, so a flush failure only degrades to the 1h cache TTL and
        # is reported, never turned into a failed request.
        cache_flushed = flush_auth_cache(account_email)

        dispatch_event(
            Events.MAILBOX_PASSWORD_CHANGED,
            data={
                "account_id": account.id,
                "email": account_email,
                "organization_id": account_org_id,
                "changed_by": "admin",
                "temporary": bool(body.temporary),
                "sessions_revoked": int(sessions_revoked or 0),
            },
            org_id=account_org_id,
            source_service="api",
        )

        return create_api_response(
            "success",
            "Password reset",
            {
                "account_id": account.id,
                "email": account_email,
                "must_change_password": bool(body.temporary),
                "password_change_reason": reason if body.temporary else None,
                "password_changed_at": now.isoformat(),
                "sessions_revoked": int(sessions_revoked or 0),
                "cache_flushed": cache_flushed,
            },
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Reset email account password error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to reset password"), status_code=500
        )
    finally:
        session.close()


@router.post(
    "/add",
    summary="Add a new mailbox (legacy)",
    description="Provision a new mailbox using the legacy raw-SQL path. Requires local_part, domain, and password. Validates email format, password strength, and domain existence. Supports TLS enforcement, quarantine settings, and rate limiting.",
    response_model=MailboxAddResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Missing/invalid field, invalid local_part, email/domain mismatch, invalid email, weak password, or domain not found/inactive",
        },
        409: {"model": ErrorResponse, "description": "MAILBOX_ALREADY_EXISTS"},
        500: {"model": ErrorResponse, "description": "Failed to add mailbox"},
    },
)
@require_api_key("write")
async def add_mailbox(request: Request):
    """Add a new mailbox"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            content=create_api_response("error", "Invalid or missing JSON body"), status_code=400
        )

    if not data or not isinstance(data, dict):
        return JSONResponse(
            content=create_api_response("error", "Request body must be a JSON object"),
            status_code=400,
        )

    required_fields = ["local_part", "domain", "password"]
    for field in required_fields:
        if field not in data or not data[field]:
            return JSONResponse(
                content=create_api_response("error", f"Missing required field: {field}"),
                status_code=400,
            )

    # Validate local_part — no special chars that could cause issues
    local_part = data["local_part"].strip()
    if not re.match(r"^[a-zA-Z0-9._%+-]+$", local_part):
        return JSONResponse(
            content=create_api_response(
                "error",
                "Invalid local_part: only letters, numbers, dots, hyphens, underscores allowed",
            ),
            status_code=400,
        )

    email = f"{local_part}@{data['domain']}".lower()

    # If email field was provided, validate it matches local_part@domain
    if "email" in data and data["email"]:
        provided_email = data["email"].strip().lower()
        if provided_email != email:
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Email field ({provided_email}) must match local_part@domain ({email})",
                ),
                status_code=400,
            )

    if not validate_email(email):
        return JSONResponse(
            content=create_api_response("error", "Invalid email format"), status_code=400
        )

    valid_password, password_msg = validate_password(data["password"])
    if not valid_password:
        return JSONResponse(content=create_api_response("error", password_msg), status_code=400)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    ctx = request.state.auth_context

    try:
        cursor = conn.cursor(dictionary=True)

        # Check the domain exists, is active, AND belongs to the caller's
        # org -- without the organization_id filter, any tenant could
        # provision a mailbox on a domain owned by a different organization.
        # Platform scope provisions on any org's domain (ADR-002 SS8) and so
        # skips that filter.
        domain_sql = "SELECT id, organization_id FROM domains WHERE domain = %s AND active = 1"
        domain_params = [data["domain"]]
        if ctx["scope"] == "organization":
            domain_sql += " AND organization_id = %s"
            domain_params.append(ctx["organization_id"])
        cursor.execute(domain_sql, tuple(domain_params))
        domain_row = cursor.fetchone()
        if not domain_row:
            return JSONResponse(
                content=create_api_response(
                    "error", f"Domain {data['domain']} not found or inactive"
                ),
                status_code=400,
            )
        domain_id = domain_row["id"] if isinstance(domain_row, dict) else domain_row[0]
        # The new mailbox's org is the domain's org, never a caller-supplied
        # value -- a platform caller has no org of its own to fall back on.
        org_id = domain_row["organization_id"] if isinstance(domain_row, dict) else domain_row[1]

        # Check if mailbox already exists (email addresses are globally
        # unique, not per-org)
        cursor.execute("SELECT id FROM email_accounts WHERE email = %s", (email,))
        existing = cursor.fetchone()
        if existing:
            existing_id = existing["id"] if isinstance(existing, dict) else existing[0]
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Mailbox {email} already exists",
                    {"existing_id": existing_id},
                    error_code="MAILBOX_ALREADY_EXISTS",
                ),
                status_code=409,
            )

        # Hash password
        hashed_password = hash_password(data["password"])

        # Insert mailbox (EE uses ULID primary keys)
        try:
            from shared.ulid_utils import generate_ulid

            mailbox_id = generate_ulid()
        except ImportError:
            import uuid

            mailbox_id = str(uuid.uuid4()).replace("-", "")[:26]

        try:
            cursor.execute(
                """
                INSERT INTO email_accounts (
                    id, email, local_part, domain_id, organization_id,
                    password, name, status, storage_quota
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    mailbox_id,
                    email,
                    local_part,
                    domain_id,
                    org_id,
                    hashed_password,
                    sanitize_text(data.get("name", "")),
                    "active" if data.get("active", 1) else "inactive",
                    data.get("quota", 5368709120),
                ),
            )
        except Exception as insert_exc:
            # Race: another request created the same address between our
            # SELECT above and this INSERT. The UNIQUE key on `email` is the
            # real guard; translate its violation to 409 instead of letting
            # a raw DB error reach the client (phase-04 task 4.4).
            if "Duplicate entry" not in str(insert_exc):
                raise
            cursor.execute("SELECT id FROM email_accounts WHERE email = %s", (email,))
            race_row = cursor.fetchone()
            race_id = (
                (race_row["id"] if isinstance(race_row, dict) else race_row[0])
                if race_row
                else None
            )
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Mailbox {email} already exists",
                    {"existing_id": race_id},
                    error_code="MAILBOX_ALREADY_EXISTS",
                ),
                status_code=409,
            )

        # email_accounts.id is a CHAR(26) ULID -- cursor.lastrowid only
        # tracks AUTO_INCREMENT columns, so it reads 0 here. Return the ULID
        # this handler generated and inserted, not a constant 0.
        user_id = mailbox_id

        dispatch_event(
            Events.MAILBOX_CREATED,
            data={"user_id": user_id, "email": email, "domain": data["domain"]},
            domain=data["domain"],
            source_service="api",
        )

        return create_api_response(
            "success", f"Mailbox {email} created successfully", {"user_id": user_id, "email": email}
        )

    except Exception as e:
        logger.error(f"Add mailbox error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to add mailbox"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/get/{mailbox_id}",
    summary="Get mailbox information (legacy)",
    description="Retrieve mailbox details by email address or ID using the legacy raw-SQL path. Pass 'all' to list all active mailboxes. Includes message statistics, login history, and quota usage percentage.",
    response_model=MailboxGetLegacyResponse,
    responses={500: {"model": ErrorResponse, "description": "Failed to retrieve mailboxes"}},
)
@require_api_key("read")
async def get_mailboxes(mailbox_id: str, request: Request):
    """Get mailbox information"""
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
        # than via org_filter() because these queries need the `ea.` alias.
        org_clause = ""
        org_params: list = []
        if ctx["scope"] == "organization":
            org_clause = " AND ea.organization_id = %s"
            org_params = [ctx["organization_id"]]

        if mailbox_id == "all":
            # Previously WHERE ea.status = 'active' with no org filter --
            # any authenticated tenant could list every mailbox on the
            # platform.
            cursor.execute(
                f"""
                SELECT ea.*, d.domain as domain_name
                FROM email_accounts ea
                LEFT JOIN domains d ON ea.domain_id = d.id
                WHERE ea.status = 'active'{org_clause}
                ORDER BY ea.email
            """,
                tuple(org_params),
            )
            mailboxes = cursor.fetchall()
        else:
            # Previously matched by email/id alone -- any tenant could read
            # another org's mailbox by guessing its email or ID.
            cursor.execute(
                f"""
                SELECT ea.*, d.domain as domain_name
                FROM email_accounts ea
                LEFT JOIN domains d ON ea.domain_id = d.id
                WHERE (ea.email = %s OR ea.id = %s){org_clause}
            """,
                tuple([mailbox_id, mailbox_id] + org_params),
            )
            mailboxes = cursor.fetchall()

        # Remove sensitive information and add stats
        for mailbox in mailboxes:
            if "password" in mailbox:
                del mailbox["password"]

            # Mailbox statistics from email_accounts columns
            mailbox["stats"] = {
                "message_count": mailbox.get("total_emails", 0),
                "total_size": mailbox.get("storage_used", 0),
            }

            # Quota usage percentage
            quota = mailbox.get("storage_quota", 0)
            used = mailbox.get("storage_used", 0)
            mailbox["quota_usage_percent"] = (used / quota * 100) if quota > 0 else 0

        return create_api_response("success", "Mailboxes retrieved successfully", mailboxes)

    except Exception as e:
        logger.error(f"Get mailboxes error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve mailboxes"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/edit",
    summary="Edit mailbox settings (legacy)",
    description="Batch-update one or more mailboxes using the legacy raw-SQL path. Accepts an items array of mailbox emails and an attr object with fields to update (name, storage_quota, password, TLS, quarantine, rate limits, etc.).",
    response_model=MailboxEditResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request format"},
        500: {"model": ErrorResponse, "description": "Failed to edit mailbox"},
    },
)
@require_api_key("write")
async def edit_mailbox(request: Request):
    """Edit mailbox settings"""
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

        for mailbox in data["items"]:
            # Previously updated by email alone -- any tenant could edit
            # another org's mailbox. Verify ownership before touching it.
            cursor.execute(
                "SELECT organization_id FROM email_accounts WHERE email = %s",
                (mailbox,),
            )
            owner = cursor.fetchone()
            # Platform scope may edit any org's mailbox (ADR-002 SS8);
            # organization scope only its own.
            if not owner or (
                ctx["scope"] == "organization"
                and owner["organization_id"] != ctx["organization_id"]
            ):
                results.append(
                    {"mailbox": mailbox, "status": "error", "msg": f"Mailbox {mailbox} not found"}
                )
                continue
            # Re-pin the UPDATE to the row's own org rather than the caller's:
            # a platform caller has none, and this keeps the write narrowed to
            # exactly the mailbox whose ownership was just verified.
            mailbox_org_id = owner["organization_id"]

            update_fields = []
            update_values = []

            for key, value in data["attr"].items():
                if key == "password" and value:
                    valid_password, password_msg = validate_password(value)
                    if not valid_password:
                        results.append({"mailbox": mailbox, "status": "error", "msg": password_msg})
                        continue
                    update_fields.append(f"{key} = %s")
                    update_values.append(hash_password(value))
                elif key == "name":
                    update_fields.append("name = %s")
                    update_values.append(sanitize_text(value))
                elif key == "quota":
                    # email_accounts has no `quota` column -- the storage
                    # quota lives in storage_quota (bytes). Writing `quota`
                    # raised "Unknown column" and 500'd the whole batch.
                    update_fields.append("storage_quota = %s")
                    update_values.append(value)
                elif key == "active":
                    # No `active` column either: it maps onto the status
                    # enum (truthy -> 'active', falsy -> 'inactive').
                    update_fields.append("status = %s")
                    update_values.append("active" if value in (1, True, "1") else "inactive")
                # The remaining mailcow-era attrs this endpoint used to
                # accept (force_pw_update, tls_enforce_*, quarantine_*,
                # rl_value/rl_frame) exist on no email_accounts column --
                # writing them raised "Unknown column" and failed the whole
                # batch, so they are ignored rather than half-applied.

            if update_fields:
                # The audit stamp is `updated_at` (database/models/core.py);
                # `modified` exists on no table in this schema, so the
                # unconditional write below made EVERY edit 500.
                update_fields.append("updated_at = %s")
                update_values.extend([datetime.now(), mailbox, mailbox_org_id])

                cursor.execute(
                    f"""
                    UPDATE email_accounts
                    SET {", ".join(update_fields)}
                    WHERE email = %s AND organization_id = %s
                """,
                    update_values,
                )

                if cursor.rowcount > 0:
                    # Password / active changes must bite on the next AUTH
                    # attempt, not after Dovecot's 1h auth-cache TTL. Flush
                    # failures are logged by the helper, never fatal.
                    if "password" in data["attr"] or "active" in data["attr"]:
                        flush_auth_cache(mailbox)
                    dispatch_event(
                        Events.MAILBOX_UPDATED,
                        data={
                            "email": mailbox,
                            "updated_fields": [k for k in data["attr"].keys() if k != "password"],
                        },
                        source_service="api",
                    )
                    results.append(
                        {
                            "mailbox": mailbox,
                            "status": "success",
                            "msg": f"Mailbox {mailbox} updated successfully",
                        }
                    )
                else:
                    results.append(
                        {
                            "mailbox": mailbox,
                            "status": "error",
                            "msg": f"Mailbox {mailbox} not found",
                        }
                    )

        return create_api_response("success", "Mailbox update completed", results)

    except Exception as e:
        logger.error(f"Edit mailbox error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to edit mailbox"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/delete",
    summary="Delete mailbox(es) (legacy)",
    description="Delete one or more mailboxes and all associated data (aliases, sender restrictions, login records, messages) using the legacy raw-SQL path. Accepts an array of mailbox email addresses. Each deletion is wrapped in a transaction.",
    response_model=MailboxEditResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request format -- array of mailboxes expected",
        },
        500: {"model": ErrorResponse, "description": "Failed to delete mailboxes"},
    },
)
@require_api_key("write")
async def delete_mailbox(request: Request):
    """Delete mailbox(es) and associated data"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data or not isinstance(data, list):
        return JSONResponse(
            content=create_api_response(
                "error", "Invalid request format - array of mailboxes expected"
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

        for mailbox in data:
            try:
                # Previously deleted by email alone -- any tenant could
                # delete another org's mailbox. Verify ownership first.
                cursor.execute(
                    "SELECT organization_id FROM email_accounts WHERE email = %s",
                    (mailbox,),
                )
                owner = cursor.fetchone()
                # Platform scope may delete any org's mailbox (ADR-002 SS8);
                # organization scope only its own.
                if not owner or (
                    ctx["scope"] == "organization"
                    and owner["organization_id"] != ctx["organization_id"]
                ):
                    results.append(
                        {
                            "mailbox": mailbox,
                            "status": "error",
                            "msg": f"Mailbox {mailbox} not found",
                        }
                    )
                    continue
                # Re-pin the DELETE to the row's own org (see edit_mailbox).
                mailbox_org_id = owner["organization_id"]

                cursor.execute("START TRANSACTION")

                # Delete associated data
                cursor.execute(
                    "DELETE FROM aliases WHERE destination LIKE %s OR source = %s",
                    (f"%{mailbox}%", mailbox),
                )
                aliases_deleted = cursor.rowcount

                # Messages are not tracked/deleted by this endpoint (no
                # messages table is touched here) -- pre-existing: the
                # dispatch/response below referenced an undefined
                # `messages_deleted` variable, which would NameError on
                # every successful deletion.
                messages_deleted = 0

                cursor.execute(
                    "DELETE FROM email_accounts WHERE email = %s AND organization_id = %s",
                    (mailbox, mailbox_org_id),
                )
                user_deleted = cursor.rowcount

                if user_deleted > 0:
                    cursor.execute("COMMIT")
                    # The deleted mailbox must stop authenticating now, not
                    # after Dovecot's 1h auth-cache TTL. Best-effort after
                    # the commit; the helper logs failures.
                    flush_auth_cache(mailbox)
                    dispatch_event(
                        Events.MAILBOX_DELETED,
                        data={
                            "email": mailbox,
                            "aliases_deleted": aliases_deleted,
                            "messages_deleted": messages_deleted,
                        },
                        source_service="api",
                    )
                    results.append(
                        {
                            "mailbox": mailbox,
                            "status": "success",
                            "msg": f"Mailbox {mailbox} deleted successfully",
                            "stats": {
                                "aliases_deleted": aliases_deleted,
                                "messages_deleted": messages_deleted,
                            },
                        }
                    )
                else:
                    cursor.execute("ROLLBACK")
                    results.append(
                        {
                            "mailbox": mailbox,
                            "status": "error",
                            "msg": f"Mailbox {mailbox} not found",
                        }
                    )

            except Exception as e:
                cursor.execute("ROLLBACK")
                results.append(
                    {
                        "mailbox": mailbox,
                        "status": "error",
                        "msg": f"Failed to delete mailbox {mailbox}: {str(e)}",
                    }
                )

        return create_api_response("success", "Mailbox deletion completed", results)

    except Exception as e:
        logger.error(f"Delete mailbox error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete mailboxes"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/get/quota/{mailbox}",
    summary="Get mailbox quota details (legacy)",
    description=(
        "Retrieve detailed quota information for a mailbox using the legacy raw-SQL path, "
        "including total quota, usage, available space, usage percentage, and a per-folder size breakdown."
    ),
    response_model=MailboxLegacyQuotaResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Mailbox not found"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve quota information"},
    },
)
@require_api_key("read")
async def get_mailbox_quota(mailbox: str, request: Request):
    """Get detailed quota information for a mailbox"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        # This legacy raw-SQL route had NO org scoping at all: it keyed
        # straight off the mailbox address, so any authenticated tenant could
        # reach any other tenant's mailbox by guessing an address.
        # verify_mailbox_scope 404s for a foreign mailbox and passes platform
        # scope through unchanged (ADR-002 SS8).
        verify_mailbox_scope(cursor, mailbox, request.state.auth_context)

        # `quota` is not a column on email_accounts -- it is `storage_quota`.
        # Both derived expressions referenced the wrong name, so this query
        # raised before it could return a row.
        cursor.execute(
            """
            SELECT email, storage_quota, storage_used,
                   CASE WHEN storage_quota > 0
                        THEN (storage_used / storage_quota * 100)
                        ELSE 0 END AS usage_percent,
                   (storage_quota - storage_used) AS available
            FROM email_accounts
            WHERE email = %s
        """,
            (mailbox,),
        )

        quota_info = cursor.fetchone()
        if not quota_info:
            return JSONResponse(
                content=create_api_response("error", "Mailbox not found"), status_code=404
            )

        # Per-folder sizes live in the Maildir on disk (Dovecot owns them);
        # no table in this schema tracks folder-level usage. The previous
        # query here had no FROM clause at all -- it selected columns
        # (folder, size, mailbox) that exist on no table, so this endpoint
        # 500'd on every call. An empty list is the honest answer the
        # database can give (cf. get_mailbox_stats's unread_messages).
        quota_info["folder_breakdown"] = []

        return create_api_response(
            "success", "Quota information retrieved successfully", quota_info
        )

    except Exception as e:
        logger.error(f"Get mailbox quota error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve quota information"),
            status_code=500,
        )
    finally:
        conn.close()


@router.post(
    "/edit/quota",
    summary="Update mailbox quota (legacy)",
    description=(
        "Update the storage quota for a mailbox using the legacy raw-SQL "
        "path. Requires the mailbox email address and the new quota value."
    ),
    response_model=MailboxLegacyQuotaUpdateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Missing required fields: mailbox, quota"},
        404: {"model": ErrorResponse, "description": "Mailbox not found"},
        500: {"model": ErrorResponse, "description": "Failed to update quota"},
    },
)
@require_api_key("write")
async def edit_mailbox_quota(request: Request):
    """Update mailbox quota"""
    data = await request.json()

    if not data or "mailbox" not in data or "quota" not in data:
        return JSONResponse(
            content=create_api_response("error", "Missing required fields: mailbox, quota"),
            status_code=400,
        )

    # Quota is a byte count on email_accounts.storage_quota (BIGINT) -- a
    # non-numeric or negative value is the caller's mistake, not a 500.
    try:
        quota_bytes = int(data["quota"])
    except (TypeError, ValueError):
        return JSONResponse(
            content=create_api_response("error", "quota must be an integer number of bytes"),
            status_code=400,
        )
    if quota_bytes < 0:
        return JSONResponse(
            content=create_api_response("error", "quota must be non-negative"),
            status_code=400,
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        # This legacy raw-SQL route had NO org scoping at all: it keyed
        # straight off the mailbox address, so any authenticated tenant could
        # reach any other tenant's mailbox by guessing an address.
        # verify_mailbox_scope 404s for a foreign mailbox and passes platform
        # scope through unchanged (ADR-002 SS8).
        verify_mailbox_scope(cursor, data["mailbox"], request.state.auth_context)

        cursor.close()
        cursor = conn.cursor()

        # Real column names (database/models/core.py EmailAccount): the
        # storage quota is `storage_quota` (bytes) and the audit stamp is
        # `updated_at` -- the previous `quota`/`modified` names exist on no
        # table in this schema, so every call 500'd.
        cursor.execute(
            """
            UPDATE email_accounts
            SET storage_quota = %s, updated_at = %s
            WHERE email = %s
        """,
            (quota_bytes, datetime.now(), data["mailbox"]),
        )

        if cursor.rowcount > 0:
            return create_api_response("success", f"Quota updated for {data['mailbox']}")
        else:
            return JSONResponse(
                content=create_api_response("error", "Mailbox not found"), status_code=404
            )

    except Exception as e:
        logger.error(f"Edit mailbox quota error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update quota"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/get/stats/{mailbox}",
    summary="Get mailbox statistics (legacy)",
    description=(
        "Retrieve comprehensive statistics for a mailbox using the legacy raw-SQL path, including message counts, unread "
        "counts, average/largest message sizes, oldest/newest message dates, and 30-day login activity with unique IP counts."
    ),
    response_model=MailboxStatsResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Mailbox not found"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve mailbox statistics"},
    },
)
@require_api_key("read")
async def get_mailbox_stats(mailbox: str, request: Request):
    """Get comprehensive mailbox statistics"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        # This legacy raw-SQL route had NO org scoping at all: it keyed
        # straight off the mailbox address, so any authenticated tenant could
        # reach any other tenant's mailbox by guessing an address.
        # verify_mailbox_scope 404s for a foreign mailbox and passes platform
        # scope through unchanged (ADR-002 SS8).
        verify_mailbox_scope(cursor, mailbox, request.state.auth_context)

        # Basic mailbox info
        cursor.execute(
            """
            -- created_at AS created: the column is created_at (0001_baseline);
            -- `created` does not exist, so this query raised
            -- "Unknown column 'created' in 'field list'" and the endpoint
            -- returned 500 on every call. Aliased rather than renamed so the
            -- response keeps the key its consumers already read.
            SELECT email, created_at AS created, last_login, storage_quota, storage_used
            FROM email_accounts
            WHERE email = %s
        """,
            (mailbox,),
        )

        mailbox_info = cursor.fetchone()
        if not mailbox_info:
            return JSONResponse(
                content=create_api_response("error", "Mailbox not found"), status_code=404
            )

        # Message statistics.
        #
        # The previous query had no FROM clause at all -- it selected from
        # nothing, against columns (seen, received, mailbox) that exist on no
        # table in this schema, so this endpoint has always returned 500.
        #
        # mail_logs is the delivery record and the only per-recipient message
        # data the database holds. unread_messages is deliberately null rather
        # than 0: read state lives in the Maildir on disk (dovecot owns it),
        # so the database cannot answer it, and reporting 0 would be a
        # confident wrong answer rather than an honest absence.
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_messages,
                AVG(size) AS avg_message_size,
                MAX(size) AS largest_message_size,
                MIN(timestamp) AS oldest_message,
                MAX(timestamp) AS newest_message
            FROM mail_logs
            WHERE recipient = %s
        """,
            (mailbox,),
        )

        message_stats = cursor.fetchone() or {}
        message_stats["unread_messages"] = None

        # Login statistics. Same defect as above -- no FROM, and against
        # columns (login_time, ip_address, username) that user_logins does not
        # use. Its real columns are user_email / client_ip / logged_at.
        #
        # success = 1 because "how many times did this mailbox log in" should
        # not be inflated by failed attempts; those are tracked separately in
        # failed_auth_attempts and shown on the auth-security screen.
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_logins,
                MAX(logged_at) AS last_login,
                COUNT(DISTINCT client_ip) AS unique_ips
            FROM user_logins
            WHERE user_email = %s
              AND success = 1
              AND logged_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """,
            (mailbox,),
        )

        login_stats = cursor.fetchone()

        stats = {
            "mailbox_info": mailbox_info,
            "message_stats": message_stats,
            "login_stats": login_stats,
        }

        return create_api_response("success", "Mailbox statistics retrieved successfully", stats)

    except Exception as e:
        logger.error(f"Get mailbox stats error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve mailbox statistics"),
            status_code=500,
        )
    finally:
        conn.close()
