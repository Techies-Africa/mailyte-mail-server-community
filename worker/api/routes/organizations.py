
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
from pathlib import Path
from utils.database import get_db_connection
from utils.auth import require_api_key, create_api_response
from database.models.core import Organization, Domain, EmailAccount
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import os

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.webhook_dispatcher import dispatch_event, Events

# ---------------------------------------------------------------------------
# Pydantic request/response models for OpenAPI documentation
# ---------------------------------------------------------------------------

class OrganizationCreate(BaseModel):
    """Request body for creating a new organization."""
    id: str = Field(..., description="Unique organization identifier (alphanumeric, hyphens, underscores)", example="acme-corp")
    name: str = Field(..., description="Organization display name", example="Acme Corp")
    external_id: Optional[str] = Field(None, description="External system ID for integration", example="acme-123")
    admin_email: Optional[str] = Field(None, description="Admin contact email", example="admin@acme.com")
    admin_name: Optional[str] = Field(None, description="Admin contact name", example="Jane Doe")
    description: Optional[str] = Field(None, description="Organization description", example="Primary business unit")
    settings: Optional[Dict[str, Any]] = Field(None, description="Arbitrary organization-level settings")
    rate_limits: Optional[Dict[str, Any]] = Field(None, description="Rate-limiting rules applied across all domains")
    storage_quotas: Optional[Dict[str, Any]] = Field(None, description="Organization-wide storage quota overrides")
    webhook_urls: Optional[List[str]] = Field(None, description="Webhook endpoints for event notifications", example=["https://hooks.example.com/mailyte"])
    webhook_secret: Optional[str] = Field(None, description="Shared secret for signing webhook payloads")
    active: bool = Field(True, description="Whether the organization is active")

class OrganizationUpdate(BaseModel):
    """Request body for updating an existing organization."""
    name: Optional[str] = Field(None, description="Updated organization display name")
    external_id: Optional[str] = Field(None, description="Updated external system ID")
    admin_email: Optional[str] = Field(None, description="Updated admin contact email")
    admin_name: Optional[str] = Field(None, description="Updated admin contact name")
    description: Optional[str] = Field(None, description="Updated organization description")
    settings: Optional[Dict[str, Any]] = Field(None, description="Updated organization settings")
    rate_limits: Optional[Dict[str, Any]] = Field(None, description="Updated rate-limiting rules")
    storage_quotas: Optional[Dict[str, Any]] = Field(None, description="Updated storage quota overrides")
    webhook_urls: Optional[List[str]] = Field(None, description="Updated webhook endpoint list")
    webhook_secret: Optional[str] = Field(None, description="Updated webhook secret")
    active: Optional[bool] = Field(None, description="Enable or disable the organization")

class OrganizationQuotaUpdate(BaseModel):
    """Request body for updating organization quota settings."""
    storage_quotas: Optional[Dict[str, Any]] = Field(None, description="Organization-wide storage quota overrides")
    rate_limits: Optional[Dict[str, Any]] = Field(None, description="Organization-wide rate-limiting rules")

logger = logging.getLogger(__name__)
router = APIRouter()

def get_db_session():
    """Get SQLAlchemy session"""
    engine = create_engine(f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")
    Session = sessionmaker(bind=engine)
    return Session()

def validate_organization_data(data, is_update=False):
    """Validate organization data"""
    errors = []

    if not is_update and ('id' not in data or not data['id']):
        errors.append("Organization ID is required")
    elif 'id' in data and not re.match(r'^[a-zA-Z0-9_-]+$', data['id']):
        errors.append("Organization ID must contain only alphanumeric characters, hyphens, and underscores")

    if not is_update and ('name' not in data or not data['name']):
        errors.append("Organization name is required")
    elif 'name' in data and len(data['name']) > 255:
        errors.append("Organization name must be 255 characters or less")

    if 'external_id' in data and data['external_id'] and len(data['external_id']) > 255:
        errors.append("External ID must be 255 characters or less")

    if 'admin_email' in data and data['admin_email']:
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, data['admin_email']):
            errors.append("Invalid admin email format")

    return errors

@router.get('/', summary="List all organizations", description="Retrieve a paginated list of all organizations with domain counts, email account counts, and total storage usage.")
@require_api_key('read')
async def list_organizations(page: int = Query(1), per_page: int = Query(50)):
    """List all organizations with usage statistics"""
    per_page = min(per_page, 200)

    session = get_db_session()
    try:
        base_query = session.query(Organization)
        total = base_query.count()
        organizations = base_query.offset((page - 1) * per_page).limit(per_page).all()
        result = []

        for org in organizations:
            org_data = org.to_dict()

            # Add domain count
            domain_count = session.query(Domain).filter_by(organization_id=org.id).count()
            org_data['domain_count'] = domain_count

            # Add email account count
            account_count = session.query(EmailAccount).filter_by(organization_id=org.id).count()
            org_data['email_account_count'] = account_count

            # Add total storage usage across all domains
            domains = session.query(Domain).filter_by(organization_id=org.id).all()
            total_storage = sum(domain.total_storage_used for domain in domains)
            org_data['total_storage_used'] = total_storage

            result.append(org_data)

        return create_api_response(
            'success',
            'Organizations retrieved successfully',
            {'items': result, 'pagination': {'page': page, 'per_page': per_page, 'total': total, 'total_pages': (total + per_page - 1) // per_page}}
        )

    except Exception as e:
        logger.error(f"List organizations error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve organizations'
        ), status_code=500)
    finally:
        session.close()

@router.get('/{organization_id}', summary="Get organization details", description="Retrieve detailed information about a specific organization, including all its domains, email account counts, and aggregated storage statistics.")
@require_api_key('read')
async def get_organization(organization_id: str):
    """Get specific organization with detailed statistics"""
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        org_data = org.to_dict()

        # Add detailed statistics
        domains = session.query(Domain).filter_by(organization_id=organization_id).all()
        org_data['domains'] = [domain.to_dict() for domain in domains]
        org_data['domain_count'] = len(domains)

        # Email account statistics
        email_accounts = session.query(EmailAccount).filter_by(organization_id=organization_id).all()
        org_data['email_account_count'] = len(email_accounts)

        # Storage statistics
        total_storage = sum(domain.total_storage_used for domain in domains)
        total_quota = sum(domain.max_quota for domain in domains)
        org_data['storage_statistics'] = {
            'total_storage_used': total_storage,
            'total_quota': total_quota,
            'usage_percentage': (total_storage / total_quota * 100) if total_quota > 0 else 0
        }

        return create_api_response(
            'success',
            'Organization retrieved successfully',
            org_data
        )

    except Exception as e:
        logger.error(f"Get organization error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve organization'
        ), status_code=500)
    finally:
        session.close()

@router.post('/', summary="Create a new organization", description="Register a new organization that can own domains and email accounts. The organization ID must be unique and is used as the primary identifier.")
@require_api_key('write')
async def create_organization(request: Request):
    """Create new organization"""
    data = await request.json()

    if not data:
        return JSONResponse(content=create_api_response(
            'error',
            'No data provided'
        ), status_code=400)

    # Validate data
    errors = validate_organization_data(data)
    if errors:
        return JSONResponse(content=create_api_response(
            'error',
            'Validation failed',
            {'errors': errors}
        ), status_code=400)

    session = get_db_session()
    try:
        # Check if organization already exists by ID
        existing = session.query(Organization).filter_by(id=data['id']).first()
        if existing:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization with this ID already exists'
            ), status_code=409)

        # Check if external_id already exists (if provided)
        if data.get('external_id'):
            existing_external = session.query(Organization).filter_by(external_id=data['external_id']).first()
            if existing_external:
                return JSONResponse(content=create_api_response(
                    'error',
                    'Organization with this external ID already exists'
                ), status_code=409)

        # Create organization
        org = Organization(
            id=data['id'],
            external_id=data.get('external_id'),
            name=sanitize_text(data['name']),
            description=sanitize_text(data.get('description')),
            admin_email=data.get('admin_email'),
            admin_name=data.get('admin_name'),
            settings=data.get('settings', {}),
            rate_limits=data.get('rate_limits', {}),
            storage_quotas=data.get('storage_quotas', {}),
            webhook_urls=data.get('webhook_urls', []),
            webhook_secret=data.get('webhook_secret'),
            active=data.get('active', True)
        )

        session.add(org)
        session.commit()

        dispatch_event(
            Events.ORG_CREATED,
            data={"organization_id": org.id, "name": org.name, "admin_email": org.admin_email},
            org_id=org.id,
            source_service="api",
        )

        return JSONResponse(content=create_api_response(
            'success',
            'Organization created successfully',
            org.to_dict()
        ), status_code=201)

    except Exception as e:
        session.rollback()
        logger.error(f"Create organization error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to create organization'
        ), status_code=500)
    finally:
        session.close()

@router.put('/{organization_id}', summary="Update an organization", description="Update the settings of an existing organization such as name, admin contact, webhooks, and rate limits.")
@require_api_key('write')
async def update_organization(organization_id: str, request: Request):
    """Update organization"""
    data = await request.json()

    if not data:
        return JSONResponse(content=create_api_response(
            'error',
            'No data provided'
        ), status_code=400)

    # Validate data
    errors = validate_organization_data(data, is_update=True)
    if errors:
        return JSONResponse(content=create_api_response(
            'error',
            'Validation failed',
            {'errors': errors}
        ), status_code=400)

    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        # Check if external_id is being updated and already exists
        if 'external_id' in data and data['external_id'] != org.external_id:
            existing_external = session.query(Organization).filter_by(external_id=data['external_id']).first()
            if existing_external:
                return JSONResponse(content=create_api_response(
                    'error',
                    'Organization with this external ID already exists'
                ), status_code=409)

        # Update fields
        if 'external_id' in data:
            org.external_id = data['external_id']
        if 'name' in data:
            org.name = sanitize_text(data['name'])
        if 'description' in data:
            org.description = data['description']
        if 'admin_email' in data:
            org.admin_email = data['admin_email']
        if 'admin_name' in data:
            org.admin_name = data['admin_name']
        if 'settings' in data:
            org.settings = data['settings']
        if 'rate_limits' in data:
            org.rate_limits = data['rate_limits']
        if 'storage_quotas' in data:
            org.storage_quotas = data['storage_quotas']
        if 'webhook_urls' in data:
            org.webhook_urls = data['webhook_urls']
        if 'webhook_secret' in data:
            org.webhook_secret = data['webhook_secret']
        if 'active' in data:
            org.active = data['active']

        org.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.ORG_UPDATED,
            data={"organization_id": org.id, "name": org.name, "updated_fields": list(data.keys())},
            org_id=org.id,
            source_service="api",
        )

        return create_api_response(
            'success',
            'Organization updated successfully',
            org.to_dict()
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update organization error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to update organization'
        ), status_code=500)
    finally:
        session.close()

@router.delete('/{organization_id}', summary="Delete an organization", description="Permanently remove an organization. The organization must have no remaining domains or email accounts; delete those first.")
@require_api_key('write')
async def delete_organization(organization_id: str):
    """Delete organization and all related data"""
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        # Check for existing domains/accounts
        domain_count = session.query(Domain).filter_by(organization_id=organization_id).count()
        account_count = session.query(EmailAccount).filter_by(organization_id=organization_id).count()

        if domain_count > 0 or account_count > 0:
            return JSONResponse(content=create_api_response(
                'error',
                f'Cannot delete organization with {domain_count} domains and {account_count} email accounts. Delete them first.'
            ), status_code=400)

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

        return create_api_response(
            'success',
            'Organization deleted successfully'
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Delete organization error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to delete organization'
        ), status_code=500)
    finally:
        session.close()

@router.get('/{organization_id}/quotas', summary="Get organization quotas", description="Retrieve quota limits and current storage usage for an organization, broken down by domain with per-domain account counts.")
@require_api_key('read')
async def get_organization_quotas(organization_id: str):
    """Get organization quota and usage information"""
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        domains = session.query(Domain).filter_by(organization_id=organization_id).all()
        email_accounts = session.query(EmailAccount).filter_by(organization_id=organization_id).all()

        quota_info = {
            'organization_id': organization_id,
            'organization_quotas': org.storage_quotas or {},
            'total_domains': len(domains),
            'total_email_accounts': len(email_accounts),
            'storage_summary': {
                'total_storage_used': sum(domain.total_storage_used for domain in domains),
                'total_quota': sum(domain.max_quota for domain in domains),
                'domains': []
            }
        }

        for domain in domains:
            domain_accounts = [acc for acc in email_accounts if acc.domain_id == domain.id]
            quota_info['storage_summary']['domains'].append({
                'domain': domain.domain,
                'storage_used': domain.total_storage_used,
                'quota': domain.max_quota,
                'usage_percentage': domain.get_storage_usage_percentage(),
                'email_accounts': len(domain_accounts),
                'max_users': domain.max_users
            })

        return create_api_response(
            'success',
            'Organization quota information retrieved successfully',
            quota_info
        )

    except Exception as e:
        logger.error(f"Get organization quotas error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve quota information'
        ), status_code=500)
    finally:
        session.close()

@router.get('/by-external-id/{external_id}', summary="Look up organization by external ID", description="Find an organization using its external system identifier. Returns the same detailed view as the primary get-organization endpoint.")
@require_api_key('read')
async def get_organization_by_external_id(external_id: str):
    """Get organization by external ID"""
    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(external_id=external_id).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        org_data = org.to_dict()

        # Add detailed statistics
        domains = session.query(Domain).filter_by(organization_id=org.id).all()
        org_data['domains'] = [domain.to_dict() for domain in domains]
        org_data['domain_count'] = len(domains)

        # Email account statistics
        email_accounts = session.query(EmailAccount).filter_by(organization_id=org.id).all()
        org_data['email_account_count'] = len(email_accounts)

        # Storage statistics
        total_storage = sum(domain.total_storage_used for domain in domains)
        total_quota = sum(domain.max_quota for domain in domains)
        org_data['storage_statistics'] = {
            'total_storage_used': total_storage,
            'total_quota': total_quota,
            'usage_percentage': (total_storage / total_quota * 100) if total_quota > 0 else 0
        }

        return create_api_response(
            'success',
            'Organization retrieved successfully',
            org_data
        )

    except Exception as e:
        logger.error(f"Get organization by external ID error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to retrieve organization'
        ), status_code=500)
    finally:
        session.close()

@router.put('/{organization_id}/quotas', summary="Update organization quotas", description="Adjust the storage quotas and rate limits for an organization. These settings apply across all domains owned by the organization.")
@require_api_key('write')
async def update_organization_quotas(organization_id: str, request: Request):
    """Update organization quota settings"""
    data = await request.json()

    if not data:
        return JSONResponse(content=create_api_response(
            'error',
            'No quota data provided'
        ), status_code=400)

    session = get_db_session()
    try:
        org = session.query(Organization).filter_by(id=organization_id).first()
        if not org:
            return JSONResponse(content=create_api_response(
                'error',
                'Organization not found'
            ), status_code=404)

        # Update storage quotas
        if 'storage_quotas' in data:
            org.storage_quotas = data['storage_quotas']

        # Update rate limits
        if 'rate_limits' in data:
            org.rate_limits = data['rate_limits']

        org.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.ORG_UPDATED,
            data={"organization_id": organization_id, "update_type": "quotas", "storage_quotas": org.storage_quotas, "rate_limits": org.rate_limits},
            org_id=organization_id,
            source_service="api",
        )

        return create_api_response(
            'success',
            'Organization quotas updated successfully',
            {
                'organization_id': organization_id,
                'storage_quotas': org.storage_quotas,
                'rate_limits': org.rate_limits
            }
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update organization quotas error: {e}")
        return JSONResponse(content=create_api_response(
            'error',
            'Failed to update quotas'
        ), status_code=500)
    finally:
        session.close()
