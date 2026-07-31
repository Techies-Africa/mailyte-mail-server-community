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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


def sanitize_text(value):
    """Sanitize free-text input to prevent stored XSS."""
    if not value or not isinstance(value, str):
        return value
    # Escape HTML entities
    import re as _re

    value = _re.sub(r"<[^>]+>", "", value)  # Strip HTML tags
    return html_module.escape(value, quote=True)


import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from utils.auth import create_api_response, hash_password, require_api_key
from utils.database import get_db_connection

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


def validate_password(password):
    """Validate password strength"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain letters"
    if not re.search(r"\d", password):
        return False, "Password must contain numbers"
    return True, "Valid password"


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


@router.get(
    "/email-accounts",
    summary="List all email accounts",
    description="Retrieve a paginated list of email accounts with optional filtering by domain, organization, or status. Includes domain and organization context for each account.",
)
@require_api_key("read")
async def list_email_accounts(
    domain_id: str = Query(None),
    organization_id: str = Query(None),
    status: str = Query(None),
    page: int = Query(1),
    per_page: int = Query(50),
):
    """List all email accounts with domain and organization context"""
    per_page = min(per_page, 200)

    session = get_db_session()
    try:
        query = session.query(EmailAccount)

        if domain_id:
            query = query.filter_by(domain_id=domain_id)
        if organization_id:
            query = query.filter_by(organization_id=organization_id)
        if status:
            query = query.filter_by(status=AccountStatus(status))

        total = query.count()
        accounts = query.offset((page - 1) * per_page).limit(per_page).all()
        result = []

        for account in accounts:
            account_data = account.to_dict()

            # Add domain and organization information
            domain = session.query(Domain).filter_by(id=account.domain_id).first()
            org = session.query(Organization).filter_by(id=account.organization_id).first()

            account_data["domain_name"] = domain.domain if domain else "Unknown"
            account_data["organization_name"] = org.name if org else "Unknown"

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
)
@require_api_key("read")
async def get_email_account(account_id: str):
    """Get specific email account with detailed information"""
    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        if not account:
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

        # Find domain
        domain = session.query(Domain).filter_by(domain=domain_name).first()
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
            status=AccountStatus(data.get("status", "ACTIVE")),
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
)
@require_api_key("write")
async def update_email_account(account_id: str, request: Request):
    """Update email account"""
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
        if not account:
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

        account.updated_at = datetime.now()
        session.commit()

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
)
@require_api_key("write")
async def delete_email_account(account_id: str):
    """Delete email account"""
    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        if not account:
            return JSONResponse(
                content=create_api_response("error", "Email account not found"), status_code=404
            )

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
)
@require_api_key("read")
async def get_account_quotas(account_id: str):
    """Get email account quota and usage information"""
    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        if not account:
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
)
@require_api_key("write")
async def update_account_quotas(account_id: str, request: Request):
    """Update email account quota settings"""
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No quota data provided"), status_code=400
        )

    session = get_db_session()
    try:
        account = session.query(EmailAccount).filter_by(id=account_id).first()
        if not account:
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


@router.post(
    "/add",
    summary="Add a new mailbox (legacy)",
    description="Provision a new mailbox using the legacy raw-SQL path. Requires local_part, domain, and password. Validates email format, password strength, and domain existence. Supports TLS enforcement, quarantine settings, and rate limiting.",
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

    try:
        cursor = conn.cursor(dictionary=True)

        # Check if domain exists
        cursor.execute("SELECT id FROM domains WHERE domain = %s AND active = 1", (data["domain"],))
        if not cursor.fetchone():
            return JSONResponse(
                content=create_api_response(
                    "error", f"Domain {data['domain']} not found or inactive"
                ),
                status_code=400,
            )

        # Check if mailbox already exists
        cursor.execute("SELECT id FROM email_accounts WHERE email = %s", (email,))
        if cursor.fetchone():
            return JSONResponse(
                content=create_api_response("error", f"Mailbox {email} already exists"),
                status_code=409,
            )

        # Get domain_id
        cursor.execute("SELECT id FROM domains WHERE domain = %s AND active = 1", (data["domain"],))
        domain_row = cursor.fetchone()
        if not domain_row:
            return JSONResponse(
                content=create_api_response("error", f"Domain {data['domain']} not found"),
                status_code=400,
            )
        domain_id = domain_row["id"] if isinstance(domain_row, dict) else domain_row[0]

        # Hash password
        hashed_password = hash_password(data["password"])

        # Get organization from API key
        org_id = getattr(request.state, "organization_id", None) or "default"

        # Insert mailbox
        cursor.execute(
            """
            INSERT INTO email_accounts (
                email, local_part, domain_id, organization_id,
                password, name, status, storage_quota
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                email,
                data["local_part"],
                domain_id,
                org_id,
                hashed_password,
                sanitize_text(data.get("name", "")),
                "active" if data.get("active", 1) else "inactive",
                data.get("quota", 5368709120),
            ),
        )

        user_id = cursor.lastrowid

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
)
@require_api_key("read")
async def get_mailboxes(mailbox_id: str):
    """Get mailbox information"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        if mailbox_id == "all":
            cursor.execute("""
                SELECT ea.*, d.domain as domain_name
                FROM email_accounts ea
                LEFT JOIN domains d ON ea.domain_id = d.id
                WHERE ea.status = 'active'
                ORDER BY ea.email
            """)
            mailboxes = cursor.fetchall()
        else:
            cursor.execute(
                """
                SELECT ea.*, d.domain as domain_name
                FROM email_accounts ea
                LEFT JOIN domains d ON ea.domain_id = d.id
                WHERE ea.email = %s OR ea.id = %s
            """,
                (mailbox_id, mailbox_id),
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
)
@require_api_key("write")
async def edit_mailbox(request: Request):
    """Edit mailbox settings"""
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
        cursor = conn.cursor()
        results = []

        for mailbox in data["items"]:
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
                elif key in [
                    "name",
                    "quota",
                    "active",
                    "force_pw_update",
                    "tls_enforce_in",
                    "tls_enforce_out",
                    "quarantine_notification",
                    "quarantine_category",
                    "rl_value",
                    "rl_frame",
                ]:
                    update_fields.append(f"{key} = %s")
                    update_values.append(value)

            if update_fields:
                update_fields.append("modified = %s")
                update_values.extend([datetime.now(), mailbox])

                cursor.execute(
                    f"""
                    UPDATE email_accounts
                    SET {", ".join(update_fields)}
                    WHERE email = %s
                """,
                    update_values,
                )

                if cursor.rowcount > 0:
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
)
@require_api_key("write")
async def delete_mailbox(request: Request):
    """Delete mailbox(es) and associated data"""
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
        cursor = conn.cursor()
        results = []

        for mailbox in data:
            try:
                cursor.execute("START TRANSACTION")

                # Delete associated data
                cursor.execute(
                    "DELETE FROM aliases WHERE destination LIKE %s OR source = %s",
                    (f"%{mailbox}%", mailbox),
                )
                aliases_deleted = cursor.rowcount

                cursor.execute("DELETE FROM email_accounts WHERE email = %s", (mailbox,))
                user_deleted = cursor.rowcount

                if user_deleted > 0:
                    cursor.execute("COMMIT")
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
    description="Retrieve detailed quota information for a mailbox using the legacy raw-SQL path, including total quota, usage, available space, usage percentage, and a per-folder size breakdown.",
)
@require_api_key("read")
async def get_mailbox_quota(mailbox: str):
    """Get detailed quota information for a mailbox"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT email, storage_quota, storage_used,
                   (storage_used / quota * 100) as usage_percent,
                   (quota - storage_used) as available
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

        # Get quota breakdown by folder
        cursor.execute(
            """
            SELECT folder, COUNT(*) as message_count, SUM(size) as folder_size
            WHERE mailbox = %s
            GROUP BY folder
            ORDER BY folder_size DESC
        """,
            (mailbox,),
        )

        folder_breakdown = cursor.fetchall()
        quota_info["folder_breakdown"] = folder_breakdown

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
    description="Update the storage quota for a mailbox using the legacy raw-SQL path. Requires the mailbox email address and the new quota value.",
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

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE email_accounts
            SET quota = %s, modified = %s
            WHERE email = %s
        """,
            (data["quota"], datetime.now(), data["mailbox"]),
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
    description="Retrieve comprehensive statistics for a mailbox using the legacy raw-SQL path, including message counts, unread counts, average/largest message sizes, oldest/newest message dates, and 30-day login activity with unique IP counts.",
)
@require_api_key("read")
async def get_mailbox_stats(mailbox: str):
    """Get comprehensive mailbox statistics"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        # Basic mailbox info
        cursor.execute(
            """
            SELECT email, created, last_login, storage_quota, storage_used
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

        # Message statistics
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_messages,
                SUM(CASE WHEN seen = 0 THEN 1 ELSE 0 END) as unread_messages,
                AVG(size) as avg_message_size,
                MAX(size) as largest_message_size,
                MIN(received) as oldest_message,
                MAX(received) as newest_message
            WHERE mailbox = %s
        """,
            (mailbox,),
        )

        message_stats = cursor.fetchone()

        # Login statistics
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_logins,
                MAX(login_time) as last_login,
                COUNT(DISTINCT ip_address) as unique_ips
            WHERE username = %s
            AND login_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
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
