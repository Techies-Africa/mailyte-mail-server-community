#!/usr/bin/env python3
"""
Domain Management API Routes
Production-grade domain management with comprehensive features and organization support.

This module provides API endpoints for:
- Domain CRUD operations with proper validation and relationships
- Domain quota and rate limit management
- Domain statistics and analytics with organization hierarchy
- Integration with the new organization/domain/email account structure

Key Features:
- Organization-aware domain management
- Comprehensive validation and error handling
- Quota and usage management
- Enhanced multi-tenant support
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import re
import html as html_module

def sanitize_text(value):
    """Sanitize free-text input to prevent stored XSS."""
    if not value or not isinstance(value, str):
        return value
    return html_module.escape(value, quote=True)
import sys
import base64
import subprocess
from pathlib import Path
from utils.database import get_db_connection
from utils.auth import require_api_key, create_api_response
from database.models.core import Organization, Domain, EmailAccount
from database.models.certificates import DKIMKey
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import os

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.webhook_dispatcher import dispatch_event, Events

# ---------------------------------------------------------------------------
# Pydantic request/response models for OpenAPI documentation
# ---------------------------------------------------------------------------

class DomainCreate(BaseModel):
    """Request body for creating a new mail domain."""
    domain: str = Field(..., description="The domain name to add (e.g., example.com)", example="example.com")
    organization_id: str = Field(..., description="ID of the organization that owns this domain", example="org-123")
    description: Optional[str] = Field(None, description="Human-readable description of this domain", example="Main company domain")
    dkim_enabled: bool = Field(True, description="Whether to enable DKIM signing for outbound email")
    dkim_selector: str = Field("default", description="DKIM selector name used in DNS record", example="default")
    max_users: int = Field(1000, description="Maximum number of mailboxes allowed on this domain")
    max_quota: int = Field(10737418240, description="Maximum total storage in bytes (default 10GB)")
    active: bool = Field(True, description="Whether the domain is active and accepting mail")
    rate_limits: Optional[Dict[str, Any]] = Field(None, description="Rate-limiting rules for outbound email")
    storage_quotas: Optional[Dict[str, Any]] = Field(None, description="Per-account default storage quota overrides")
    external_id: Optional[str] = Field(None, description="External system ID for integration", example="dom-ext-456")

class DomainUpdate(BaseModel):
    """Request body for updating an existing domain."""
    description: Optional[str] = Field(None, description="Updated description")
    active: Optional[bool] = Field(None, description="Enable or disable the domain")
    max_users: Optional[int] = Field(None, description="Updated max users limit")
    max_quota: Optional[int] = Field(None, description="Updated max storage quota in bytes")
    dkim_enabled: Optional[bool] = Field(None, description="Enable or disable DKIM signing")
    dkim_selector: Optional[str] = Field(None, description="Updated DKIM selector name")
    rate_limits: Optional[Dict[str, Any]] = Field(None, description="Updated rate-limiting rules")
    storage_quotas: Optional[Dict[str, Any]] = Field(None, description="Updated storage quota overrides")
    external_id: Optional[str] = Field(None, description="Updated external system ID")

class DomainQuotaUpdate(BaseModel):
    """Request body for updating domain quota settings."""
    max_quota: Optional[int] = Field(None, description="New total storage quota in bytes", example=21474836480)
    max_users: Optional[int] = Field(None, description="New maximum mailbox count", example=500)
    rate_limits: Optional[Dict[str, Any]] = Field(None, description="Updated rate-limiting rules")
    storage_quotas: Optional[Dict[str, Any]] = Field(None, description="Updated per-account storage quota overrides")

class DNSRecord(BaseModel):
    """A single DNS record that must be configured for the domain."""
    type: str = Field(..., description="DNS record type (MX, TXT, CNAME)", example="MX")
    name: str = Field(..., description="DNS record name", example="example.com")
    value: str = Field(..., description="DNS record value", example="mx.mailyte.com.")
    priority: Optional[int] = Field(None, description="Priority (for MX records)", example=10)
    description: str = Field(..., description="Human-readable explanation of this record")

class LegacyDomainEdit(BaseModel):
    """Request body for the legacy (mailcow-compatible) domain edit endpoint."""
    items: List[str] = Field(..., description="List of domain names or IDs to update", example=["example.com"])
    attr: Dict[str, Any] = Field(..., description="Attribute key-value pairs to set on each domain", example={"active": 1, "maxquota": 10240})

class DomainPolicyEdit(BaseModel):
    """Request body for editing domain spam/security policies."""
    domain: str = Field(..., description="Domain name to update the policy for", example="example.com")
    policy_bl_only: int = Field(0, description="Blacklist-only mode (0 or 1)")
    policy_reject_spam: int = Field(0, description="Reject detected spam (0 or 1)")
    policy_greylist: int = Field(1, description="Enable greylisting (0 or 1)")
    policy_rbl: int = Field(1, description="Enable RBL checks (0 or 1)")

# Allowed columns for the legacy edit_domain endpoint (mailcow-compatible field names)
ALLOWED_LEGACY_DOMAIN_EDIT_COLUMNS = {
    'description', 'aliases', 'mailboxes', 'maxquota',
    'quota', 'defquota', 'transport', 'backupmx', 'active',
    'relay_all_recipients', 'rl_value', 'rl_frame', 'gal',
}

logger = logging.getLogger(__name__)
router = APIRouter()

def get_db_session():
    """Get SQLAlchemy session"""
    engine = create_engine(f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")
    Session = sessionmaker(bind=engine)
    return Session()

def validate_domain(domain):
    """Validate domain name format"""
    pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
    return re.match(pattern, domain) is not None

def validate_domain_data(data, is_update=False):
    """Validate domain data"""
    errors = []

    if not is_update and ('domain' not in data or not data['domain']):
        errors.append("Domain name is required")
    elif 'domain' in data and not validate_domain(data['domain']):
        errors.append("Invalid domain name format")

    if not is_update and ('organization_id' not in data or not data['organization_id']):
        errors.append("Organization ID is required")

    if 'max_quota' in data:
        try:
            quota = int(data['max_quota'])
            if quota < 0:
                errors.append("Max quota must be non-negative")
        except (ValueError, TypeError):
            errors.append("Max quota must be a valid number")

    if 'max_users' in data:
        try:
            users = int(data['max_users'])
            if users < 0:
                errors.append("Max users must be non-negative")
        except (ValueError, TypeError):
            errors.append("Max users must be a valid number")

    return errors

def generate_dkim_keypair():
    """Generate a 2048-bit RSA key pair for DKIM signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()
    ).decode()

    public_key = private_key.public_key()
    public_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_b64 = base64.b64encode(public_der).decode()

    return private_pem, public_b64

def generate_dns_records(domain: str, server_hostname: str, dkim_public_key: str = None):
    """Generate the DNS records a customer needs to configure."""
    records = [
        {
            "type": "MX",
            "name": domain,
            "value": f"{server_hostname}.",
            "priority": 10,
            "description": "Routes all email for this domain to Mailyte"
        },
        {
            "type": "TXT",
            "name": domain,
            "value": f"v=spf1 include:spf.{server_hostname} ~all",
            "description": "Authorizes Mailyte servers to send email for this domain"
        },
        {
            "type": "TXT",
            "name": f"_dmarc.{domain}",
            "value": "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@" + domain,
            "description": "Policy for handling emails that fail SPF/DKIM checks"
        },
    ]
    if dkim_public_key:
        records.append({
            "type": "TXT",
            "name": f"default._domainkey.{domain}",
            "value": f"v=DKIM1; k=rsa; p={dkim_public_key}",
            "description": "Public key for DKIM signature verification"
        })
    return records

def check_dns_record(query_name, record_type):
    """Perform a DNS lookup using dig."""
    try:
        result = subprocess.run(
            ['dig', '+short', record_type, query_name],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception:
        return None

@router.get('/', summary="List all domains", description="Retrieve a paginated list of all mail domains. Optionally filter by organization ID to see only domains belonging to a specific organization.")
@require_api_key('read')
async def list_domains(organization_id: str = Query(None), page: int = Query(1), per_page: int = Query(50)):
    """List all domains with organization context"""
    per_page = min(per_page, 200)

    session = get_db_session()
    try:
        query = session.query(Domain)
        if organization_id:
            query = query.filter_by(organization_id=organization_id)

        total = query.count()
        domains = query.offset((page - 1) * per_page).limit(per_page).all()
        result = []

        for domain in domains:
            domain_data = domain.to_dict()

            # Add email account count
            account_count = session.query(EmailAccount).filter_by(domain_id=domain.id).count()
            domain_data['email_account_count'] = account_count

            # Add organization name
            org = session.query(Organization).filter_by(id=domain.organization_id).first()
            domain_data['organization_name'] = org.name if org else 'Unknown'

            result.append(domain_data)

        return create_api_response(
            'success',
            'Domains retrieved successfully',
            {'items': result, 'pagination': {'page': page, 'per_page': per_page, 'total': total, 'total_pages': (total + per_page - 1) // per_page}}
        )

    except Exception as e:
        logger.error(f"List domains error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve domains'
        ), status_code=500)
    finally:
        session.close()

@router.get('/{domain_id}', summary="Get domain details", description="Retrieve detailed information about a specific domain, including its organization, email accounts, and usage statistics.")
@require_api_key('read')
async def get_domain(domain_id: str):
    """Get specific domain with detailed information"""
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        domain_data = domain.to_dict()

        # Add organization information
        org = session.query(Organization).filter_by(id=domain.organization_id).first()
        domain_data['organization'] = org.to_dict() if org else None

        # Add email accounts
        email_accounts = session.query(EmailAccount).filter_by(domain_id=domain_id).all()
        domain_data['email_accounts'] = [acc.to_dict() for acc in email_accounts]
        domain_data['email_account_count'] = len(email_accounts)

        # Add usage statistics
        total_account_storage = sum(acc.storage_used for acc in email_accounts)
        domain_data['usage_statistics'] = {
            'account_usage_percentage': domain.get_account_usage_percentage(),
            'storage_usage_percentage': domain.get_storage_usage_percentage(),
            'total_account_storage': total_account_storage
        }

        return create_api_response(
            'success',
            'Domain retrieved successfully',
            domain_data
        )

    except Exception as e:
        logger.error(f"Get domain error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve domain'
        ), status_code=500)
    finally:
        session.close()

@router.post('/', summary="Create a new domain", description="Add a mail domain to the organization. Automatically generates DKIM signing keys and returns the DNS records needed for email delivery.")
@require_api_key('write')
async def create_domain(request: Request):
    """Create new domain"""
    data = await request.json()

    if not data:
        return JSONResponse(content=create_api_response(
            'error',
            'No data provided'
        ), status_code=400)

    # Validate data
    errors = validate_domain_data(data)
    if errors:
        return JSONResponse(content=create_api_response(
            'error',
            'Validation failed',
            {'errors': errors}
        ), status_code=400)

    session = get_db_session()
    try:
        # Check if organization exists
        org = session.query(Organization).filter_by(id=data['organization_id']).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        # Check if domain already exists (case-insensitive)
        existing = session.query(Domain).filter(Domain.domain.ilike(data['domain'])).first()
        if existing:
            return JSONResponse(content=create_api_response(
                'error',
                f'Domain {data["domain"]} already exists'
            ), status_code=409)

        # Check if external_id already exists (if provided)
        if data.get('external_id'):
            existing_external = session.query(Domain).filter_by(external_id=data['external_id']).first()
            if existing_external:
                return JSONResponse(content=create_api_response(
                    'error',
                    'Domain with this external_id already exists'
                ), status_code=409)

        # Create domain
        domain = Domain(
            domain=data['domain'],
            organization_id=data['organization_id'],
            description=sanitize_text(data.get('description')),
            active=data.get('active', True),
            max_quota=data.get('max_quota', 10737418240),  # 10GB default
            max_users=data.get('max_users', 1000),
            dkim_enabled=data.get('dkim_enabled', True),
            dkim_selector=data.get('dkim_selector', 'default'),
            rate_limits=data.get('rate_limits', {}),
            storage_quotas=data.get('storage_quotas', {}),
            external_id=data.get('external_id')
        )

        session.add(domain)
        session.commit()

        # Generate DKIM keys if DKIM is enabled
        dkim_record = None
        if domain.dkim_enabled:
            try:
                private_pem, public_b64 = generate_dkim_keypair()
                selector = domain.dkim_selector or 'default'

                dkim_key = DKIMKey(
                    domain_id=domain.id,
                    selector=selector,
                    private_key=private_pem,
                    public_key=public_b64,
                    active=True,
                )
                session.add(dkim_key)
                session.commit()

                dkim_record = f"v=DKIM1; k=rsa; p={public_b64}"
                logger.info(f"DKIM keys generated for domain {domain.domain} (selector: {selector})")
            except Exception as dkim_err:
                logger.error(f"Failed to generate DKIM keys for domain {domain.domain}: {dkim_err}")
                # Domain was already created successfully; don't fail the whole request

        dispatch_event(
            Events.DOMAIN_ADDED,
            data={"domain_id": domain.id, "domain": domain.domain, "organization_id": domain.organization_id},
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        response_data = domain.to_dict()
        if dkim_record:
            response_data['dkim_record'] = dkim_record
            response_data['dkim_selector'] = domain.dkim_selector or 'default'
            response_data['dkim_dns_name'] = f"{domain.dkim_selector or 'default'}._domainkey.{domain.domain}"

        server_hostname = os.getenv('HOSTNAME', 'mx.mailyte.com')
        dkim_pub = public_b64 if domain.dkim_enabled and dkim_record else None
        response_data['dns_records'] = generate_dns_records(domain.domain, server_hostname, dkim_pub)

        return JSONResponse(content=create_api_response(
            'success',
            'Domain created successfully',
            response_data
        ), status_code=201)

    except Exception as e:
        session.rollback()
        logger.error(f"Create domain error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to create domain'
        ), status_code=500)
    finally:
        session.close()

@router.put('/{domain_id}', summary="Update a domain", description="Update the settings of an existing domain such as description, active status, quotas, and DKIM configuration.")
@require_api_key('write')
async def update_domain(domain_id: str, request: Request):
    """Update domain"""
    data = await request.json()

    if not data:
        return JSONResponse(content=create_api_response(
            'error',
            'No data provided'
        ), status_code=400)

    # Validate data
    errors = validate_domain_data(data, is_update=True)
    if errors:
        return JSONResponse(content=create_api_response(
            'error',
            'Validation failed',
            {'errors': errors}
        ), status_code=400)

    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        # Update fields
        if 'description' in data:
            domain.description = data['description']
        if 'active' in data:
            domain.active = data['active']
        if 'max_quota' in data:
            domain.max_quota = data['max_quota']
        if 'max_users' in data:
            domain.max_users = data['max_users']
        if 'dkim_enabled' in data:
            domain.dkim_enabled = data['dkim_enabled']
        if 'dkim_selector' in data:
            domain.dkim_selector = data['dkim_selector']
        if 'rate_limits' in data:
            domain.rate_limits = data['rate_limits']
        if 'storage_quotas' in data:
            domain.storage_quotas = data['storage_quotas']
        if 'external_id' in data:
            # Check if the new external_id already exists for other domains
            existing_external = session.query(Domain).filter(
                Domain.external_id == data['external_id'],
                Domain.id != domain_id  # Exclude the current domain
            ).first()

            if existing_external:
                return JSONResponse(content=create_api_response(
                    'error',
                    'Domain with this external_id already exists'
                ), status_code=409)
            domain.external_id = data['external_id']

        domain.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.DOMAIN_UPDATED,
            data={"domain_id": domain.id, "domain": domain.domain, "organization_id": domain.organization_id, "updated_fields": list(data.keys())},
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        return create_api_response(
            'success',
            'Domain updated successfully',
            domain.to_dict()
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update domain error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to update domain'
        ), status_code=500)
    finally:
        session.close()

@router.delete('/{domain_id}', summary="Delete a domain", description="Permanently remove a domain. The domain must have no remaining email accounts; delete those first.")
@require_api_key('write')
async def delete_domain_by_id(domain_id: str):
    """Delete domain and all related email accounts"""
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        # Check for existing email accounts
        account_count = session.query(EmailAccount).filter_by(domain_id=domain_id).count()

        if account_count > 0:
            return JSONResponse(content=create_api_response(
                'error',
                f'Cannot delete domain with {account_count} email accounts. Delete them first.'
            ), status_code=400)

        domain_id_val = domain.id
        domain_name_val = domain.domain
        org_id_val = domain.organization_id
        session.delete(domain)
        session.commit()

        dispatch_event(
            Events.DOMAIN_DELETED,
            data={"domain_id": domain_id_val, "domain": domain_name_val, "organization_id": org_id_val},
            org_id=org_id_val,
            domain=domain_name_val,
            source_service="api",
        )

        return create_api_response(
            'success',
            'Domain deleted successfully'
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Delete domain error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to delete domain'
        ), status_code=500)
    finally:
        session.close()

@router.get('/{domain_id}/verify-dns', summary="Verify domain DNS records", description="Perform live DNS lookups to check whether MX, SPF, DKIM, and DMARC records are correctly configured for the domain.")
@require_api_key('read')
async def verify_domain_dns(domain_id: str):
    """Verify DNS records for a domain"""
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        domain_name = domain.domain
        server_hostname = os.getenv('HOSTNAME', 'mx.mailyte.com')

        # Check MX record
        mx_result = check_dns_record(domain_name, 'MX')
        mx_status = 'fail'
        mx_actual = None
        if mx_result:
            # MX output format: "10 mx.mailyte.com." — strip priority and trailing dot
            parts = mx_result.split()
            if len(parts) >= 2:
                mx_actual = parts[-1].rstrip('.')
            else:
                mx_actual = mx_result.rstrip('.')
            if server_hostname in mx_actual:
                mx_status = 'pass'
        mx_verification = {
            'status': mx_status,
            'expected': server_hostname,
            'actual': mx_actual
        }

        # Check SPF record (TXT on the domain)
        spf_result = check_dns_record(domain_name, 'TXT')
        spf_status = 'fail'
        spf_found = False
        if spf_result and f'spf.{server_hostname}' in spf_result:
            spf_status = 'pass'
            spf_found = True
        spf_verification = {
            'status': spf_status,
            'found': spf_found
        }
        if spf_status == 'fail':
            spf_verification['message'] = 'SPF record not found or does not include Mailyte'

        # Check DKIM record
        dkim_selector = domain.dkim_selector or 'default'
        dkim_query = f"{dkim_selector}._domainkey.{domain_name}"
        dkim_result = check_dns_record(dkim_query, 'TXT')
        dkim_status = 'fail'
        dkim_verification = {}
        if dkim_result and 'v=DKIM1' in dkim_result:
            dkim_status = 'pass'
            dkim_verification = {'status': dkim_status, 'found': True}
        else:
            dkim_verification = {'status': dkim_status, 'message': 'DKIM record not found'}

        # Check DMARC record
        dmarc_query = f"_dmarc.{domain_name}"
        dmarc_result = check_dns_record(dmarc_query, 'TXT')
        dmarc_status = 'fail'
        dmarc_verification = {}
        if dmarc_result and 'v=DMARC1' in dmarc_result:
            dmarc_status = 'pass'
            # Extract policy
            policy = None
            for part in dmarc_result.replace('"', '').split(';'):
                part = part.strip()
                if part.startswith('p='):
                    policy = part[2:]
                    break
            dmarc_verification = {'status': dmarc_status, 'policy': policy}
        else:
            dmarc_verification = {'status': dmarc_status, 'message': 'DMARC record not found'}

        return create_api_response(
            'success',
            'DNS verification completed',
            {
                'domain': domain_name,
                'verification': {
                    'mx': mx_verification,
                    'spf': spf_verification,
                    'dkim': dkim_verification,
                    'dmarc': dmarc_verification
                }
            }
        )

    except Exception as e:
        logger.error(f"DNS verification error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to verify DNS records'
        ), status_code=500)
    finally:
        session.close()

@router.get('/{domain_id}/quotas', summary="Get domain quotas", description="Retrieve quota limits and current usage for a domain, including per-account storage breakdowns.")
@require_api_key('read')
async def get_domain_quotas(domain_id: str):
    """Get domain quota and usage information"""
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        email_accounts = session.query(EmailAccount).filter_by(domain_id=domain_id).all()

        quota_info = {
            'domain_id': domain_id,
            'domain': domain.domain,
            'max_quota': domain.max_quota,
            'max_users': domain.max_users,
            'storage_used': domain.total_storage_used,
            'usage_percentage': domain.get_storage_usage_percentage(),
            'account_usage_percentage': domain.get_account_usage_percentage(),
            'rate_limits': domain.rate_limits or {},
            'storage_quotas': domain.storage_quotas or {},
            'email_accounts': [
                {
                    'id': acc.id,
                    'email': acc.email,
                    'storage_quota': acc.storage_quota,
                    'storage_used': acc.storage_used,
                    'usage_percentage': acc.get_storage_usage_percentage()
                }
                for acc in email_accounts
            ]
        }

        return create_api_response(
            'success',
            'Domain quota information retrieved successfully',
            quota_info
        )

    except Exception as e:
        logger.error(f"Get domain quotas error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve quota information'
        ), status_code=500)
    finally:
        session.close()

@router.put('/{domain_id}/quotas', summary="Update domain quotas", description="Adjust the storage quota, maximum mailbox count, rate limits, or per-account storage defaults for a domain.")
@require_api_key('write')
async def update_domain_quotas(domain_id: str, request: Request):
    """Update domain quota settings"""
    data = await request.json()

    if not data:
        return JSONResponse(content=create_api_response(
            'error',
            'No quota data provided'
        ), status_code=400)

    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        # Update quotas
        if 'max_quota' in data:
            domain.max_quota = data['max_quota']
        if 'max_users' in data:
            domain.max_users = data['max_users']
        if 'rate_limits' in data:
            domain.rate_limits = data['rate_limits']
        if 'storage_quotas' in data:
            domain.storage_quotas = data['storage_quotas']

        domain.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.DOMAIN_UPDATED,
            data={"domain_id": domain_id, "domain": domain.domain, "update_type": "quotas", "max_quota": domain.max_quota, "max_users": domain.max_users},
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        return create_api_response(
            'success',
            'Domain quotas updated successfully',
            {
                'domain_id': domain_id,
                'max_quota': domain.max_quota,
                'max_users': domain.max_users,
                'rate_limits': domain.rate_limits,
                'storage_quotas': domain.storage_quotas
            }
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update domain quotas error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to update quotas'
        ), status_code=500)
    finally:
        session.close()

@router.post('/edit', summary="Edit domain (legacy)", description="Mailcow-compatible bulk domain edit endpoint. Accepts a list of domain names or IDs and a set of attributes to update on each.")
@require_api_key('write')
async def edit_domain(request: Request):
    """Edit domain settings"""
    data = await request.json()

    if not data or 'items' not in data or 'attr' not in data:
        return JSONResponse(content=create_api_response(
            'error',
            'Invalid request format'
        ), status_code=400)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response(
            'error',
            'Database connection failed'
        ), status_code=500)

    try:
        cursor = conn.cursor()
        results = []

        for domain in data['items']:
            update_fields = []
            update_values = []

            # Build dynamic update query (column names whitelisted to prevent SQL injection)
            for key, value in data['attr'].items():
                if key in ALLOWED_LEGACY_DOMAIN_EDIT_COLUMNS:
                    update_fields.append(f"{key} = %s")
                    update_values.append(value)

            if update_fields:
                update_fields.append("modified = %s")
                update_values.extend([datetime.now(), domain])

                cursor.execute(f"""
                    UPDATE domains
                    SET {', '.join(update_fields)}
                    WHERE domain = %s OR id = %s
                """, update_values + [domain])

                if cursor.rowcount > 0:
                    dispatch_event(
                        Events.DOMAIN_UPDATED,
                        data={"domain": domain, "updated_fields": list(data['attr'].keys())},
                        domain=domain if isinstance(domain, str) else None,
                        source_service="api",
                    )
                    results.append({
                        'domain': domain,
                        'status': 'success',
                        'msg': f'Domain {domain} updated successfully'
                    })
                else:
                    results.append({
                        'domain': domain,
                        'status': 'error',
                        'msg': f'Domain {domain} not found'
                    })

        return create_api_response(
            'success',
            'Domain update completed',
            results
        )

    except Exception as e:
        logger.error(f"Edit domain error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to edit domain'
        ), status_code=500)
    finally:
        conn.close()

@router.post('/delete/domain', summary="Delete domains (legacy)", description="Legacy bulk domain deletion endpoint. Accepts an array of domain names and removes each domain along with its mailboxes, aliases, and DKIM keys.")
@require_api_key('write')
async def delete_domain(request: Request):
    """Delete domain(s) and associated data"""
    data = await request.json()

    if not data or not isinstance(data, list):
        return JSONResponse(content=create_api_response(
            'error',
            'Invalid request format - array of domains expected'
        ), status_code=400)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response(
            'error',
            'Database connection failed'
        ), status_code=500)

    try:
        cursor = conn.cursor()
        results = []

        for domain in data:
            try:
                # Start transaction for each domain
                cursor.execute("START TRANSACTION")

                # Delete associated data first
                cursor.execute("DELETE FROM email_accounts WHERE domain = %s", (domain,))
                mailboxes_deleted = cursor.rowcount

                cursor.execute("DELETE FROM aliases WHERE address LIKE %s", (f"%@{domain}",))
                aliases_deleted = cursor.rowcount

                cursor.execute("DELETE FROM dkim_keys WHERE domain = %s", (domain,))

                cursor.execute("DELETE FROM domain_admins WHERE domain = %s", (domain,))

                cursor.execute("DELETE FROM domains WHERE domain = %s", (domain,))
                domain_deleted = cursor.rowcount

                if domain_deleted > 0:
                    cursor.execute("COMMIT")
                    dispatch_event(
                        Events.DOMAIN_DELETED,
                        data={"domain": domain, "mailboxes_deleted": mailboxes_deleted, "aliases_deleted": aliases_deleted},
                        domain=domain if isinstance(domain, str) else None,
                        source_service="api",
                    )
                    results.append({
                        'domain': domain,
                        'status': 'success',
                        'msg': f'Domain {domain} deleted successfully',
                        'stats': {
                            'mailboxes_deleted': mailboxes_deleted,
                            'aliases_deleted': aliases_deleted
                        }
                    })
                else:
                    cursor.execute("ROLLBACK")
                    results.append({
                        'domain': domain,
                        'status': 'error',
                        'msg': f'Domain {domain} not found'
                    })

            except Exception as e:
                cursor.execute("ROLLBACK")
                results.append({
                    'domain': domain,
                    'status': 'error',
                    'msg': f'Failed to delete domain {domain}: {str(e)}'
                })

        return create_api_response(
            'success',
            'Domain deletion completed',
            results
        )

    except Exception as e:
        logger.error(f"Delete domain error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to delete domains'
        ), status_code=500)
    finally:
        conn.close()

@router.get('/get/domain/policy/{domain}', summary="Get domain policy", description="Retrieve the spam and security policy settings for a domain, including greylisting, RBL, and blacklist-only flags.")
@require_api_key('read')
async def get_domain_policy(domain: str):
    """Get domain policies and restrictions"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response(
            'error',
            'Database connection failed'
        ), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)

        # Get domain policy information
        cursor.execute("""
            SELECT d.domain, d.rl_value, d.rl_frame, d.active,
                   dp.policy_bl_only, dp.policy_reject_spam,
                   dp.policy_greylist, dp.policy_rbl
            FROM domains d
            LEFT JOIN domain_policy dp ON d.domain = dp.domain
            WHERE d.domain = %s
        """, (domain,))

        policy = cursor.fetchone()
        if not policy:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        return create_api_response(
            'success',
            'Domain policy retrieved successfully',
            policy
        )

    except Exception as e:
        logger.error(f"Get domain policy error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve domain policy'
        ), status_code=500)
    finally:
        conn.close()

@router.post('/edit/domain/policy', summary="Edit domain policy", description="Create or update the spam and security policy for a domain. Uses upsert semantics so the record is created if it does not exist.")
@require_api_key('write')
async def edit_domain_policy(request: Request):
    """Edit domain policies and restrictions"""
    data = await request.json()

    if not data or 'domain' not in data:
        return JSONResponse(content=create_api_response(
            'error',
            'Missing required field: domain'
        ), status_code=400)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response(
            'error',
            'Database connection failed'
        ), status_code=500)

    try:
        cursor = conn.cursor()

        # Update or insert domain policy
        cursor.execute("""
            INSERT INTO domain_policy (
                domain, policy_bl_only, policy_reject_spam,
                policy_greylist, policy_rbl, created, modified
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                policy_bl_only = VALUES(policy_bl_only),
                policy_reject_spam = VALUES(policy_reject_spam),
                policy_greylist = VALUES(policy_greylist),
                policy_rbl = VALUES(policy_rbl),
                modified = VALUES(modified)
        """, (
            data['domain'],
            data.get('policy_bl_only', 0),
            data.get('policy_reject_spam', 0),
            data.get('policy_greylist', 1),
            data.get('policy_rbl', 1),
            datetime.now(),
            datetime.now()
        ))

        dispatch_event(
            Events.DOMAIN_UPDATED,
            data={"domain": data['domain'], "update_type": "policy"},
            domain=data['domain'],
            source_service="api",
        )

        return create_api_response(
            'success',
            f"Domain policy updated for {data['domain']}"
        )

    except Exception as e:
        logger.error(f"Edit domain policy error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to update domain policy'
        ), status_code=500)
    finally:
        conn.close()

@router.get('/stats/{domain}', summary="Get domain statistics", description="Retrieve aggregate statistics for a domain including mailbox count, alias count, and total quota usage.")
@require_api_key('read')
async def get_domain_stats(domain: str):
    """Get domain statistics"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response(
            'error',
            'Database connection failed'
        ), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)

        # Get domain details
        cursor.execute("SELECT * FROM domains WHERE domain = %s", (domain,))
        domain_details = cursor.fetchone()

        if not domain_details:
            return JSONResponse(content=create_api_response(
                'error',
                'Domain not found'
            ), status_code=404)

        # Get mailbox count
        cursor.execute("""
            SELECT COUNT(*) AS mailbox_count FROM email_accounts
            WHERE domain_id = (SELECT id FROM domains WHERE domain = %s LIMIT 1) AND status = 'active'
        """, (domain,))
        mailbox_count = cursor.fetchone()['mailbox_count']

        # Get alias count
        cursor.execute("""
            SELECT COUNT(*) AS alias_count FROM aliases
            WHERE domain_id = (SELECT id FROM domains WHERE domain = %s LIMIT 1) AND active = 1
        """, (domain,))
        alias_count = cursor.fetchone()['alias_count']

        # Get total storage used
        cursor.execute("""
            SELECT COALESCE(SUM(storage_used), 0) AS total_storage_used FROM email_accounts
            WHERE domain_id = (SELECT id FROM domains WHERE domain = %s LIMIT 1) AND status = 'active'
        """, (domain,))
        total_quota_used = cursor.fetchone()['total_storage_used']

        # Structure the response
        stats = {
            'domain': domain,
            'mailbox_count': mailbox_count,
            'alias_count': alias_count,
            'total_quota_used': total_quota_used,
            'domain_details': domain_details  # Include domain details
        }

        return create_api_response(
            'success',
            'Domain statistics retrieved successfully',
            stats
        )

    except Exception as e:
        logger.error(f"Get domain statistics error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve domain statistics'
        ), status_code=500)
    finally:
        conn.close()
