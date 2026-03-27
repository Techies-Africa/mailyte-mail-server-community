
#!/usr/bin/env python3
"""
Email Tracking Service - Statistics and Injection API

This module provides endpoints for:
- Injecting tracking into email content
- Retrieving tracking statistics
- Managing tenant-specific tracking data
- Analytics and reporting

All endpoints include comprehensive error handling, validation,
and multi-tenant isolation.
"""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Create router for stats and injection endpoints
stats_api = APIRouter()

@stats_api.post('/tracking/inject')
async def inject_tracking(request: Request):
    """
    Inject tracking pixels and rewrite links in email content.

    This endpoint is the core of the tracking system, responsible for:
    - Adding tracking pixels for open tracking
    - Rewriting links for click tracking
    - Applying tenant-specific configurations
    - Validating input parameters

    Expected JSON payload:
    {
        "html_content": "HTML email content",
        "email_id": "unique_email_id",
        "recipient": "recipient@example.com",
        "tenant_id": "tenant_identifier",
        "domain_id": "domain_identifier",
        "enable_open_tracking": true,  // optional, defaults to true
        "enable_click_tracking": true  // optional, defaults to true
    }

    Returns:
        JSON: Modified email content with tracking injected
    """
    try:
        data = await request.json()

        if not data:
            logger.warning("Missing JSON payload in tracking injection request")
            return JSONResponse({'error': 'JSON payload required'}, status_code=400)

        # Validate required fields
        required_fields = ['html_content', 'email_id', 'recipient', 'tenant_id', 'domain_id']
        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing required field in tracking injection: {field}")
                return JSONResponse({'error': f'Missing required field: {field}'}, status_code=400)

        # Extract and validate input parameters
        html_content = data['html_content']
        email_id = data['email_id']
        recipient = data['recipient']
        tenant_id = data['tenant_id']
        domain_id = data['domain_id']

        # Validate HTML content is not empty
        if not html_content or not html_content.strip():
            return JSONResponse({'error': 'HTML content cannot be empty'}, status_code=400)

        # Validate email format (basic validation)
        if '@' not in recipient or '.' not in recipient:
            return JSONResponse({'error': 'Invalid recipient email format'}, status_code=400)

        # Get tracking preferences (default to enabled)
        enable_open_tracking = data.get('enable_open_tracking', True)
        enable_click_tracking = data.get('enable_click_tracking', True)

        # Check tenant configuration and apply overrides
        tenant_config = request.app.tracking_service.get_tenant_config(tenant_id, domain_id)

        # Override with tenant settings if specified
        if not tenant_config.get('open_tracking_enabled', True):
            enable_open_tracking = False
            logger.info(f"Open tracking disabled by tenant config for {tenant_id}")

        if not tenant_config.get('click_tracking_enabled', True):
            enable_click_tracking = False
            logger.info(f"Click tracking disabled by tenant config for {tenant_id}")

        # If both tracking types are disabled, return original content
        if not enable_open_tracking and not enable_click_tracking:
            logger.info(f"All tracking disabled for tenant {tenant_id}, returning original content")
            return JSONResponse({
                'success': True,
                'modified_content': html_content,
                'tracking_injected': {
                    'open_tracking': False,
                    'click_tracking': False,
                    'links_rewritten': 0
                },
                'tracking_urls': {},
                'email_id': email_id,
                'message': 'Tracking disabled by configuration'
            })

        # Start with original content
        modified_content = html_content
        tracking_urls = {}
        links_rewritten = 0

        # Inject open tracking pixel
        if enable_open_tracking:
            try:
                tracking_pixel_url = request.app.tracking_service.create_tracking_pixel_url(
                    email_id, recipient, tenant_id, domain_id
                )
                modified_content = request.app.tracking_service.inject_tracking_pixel(
                    modified_content, tracking_pixel_url
                )
                tracking_urls['open_tracking'] = tracking_pixel_url
                logger.debug(f"Open tracking pixel injected for email {email_id}")
            except Exception as e:
                logger.error(f"Failed to inject tracking pixel: {e}")
                return JSONResponse({'error': 'Failed to inject tracking pixel'}, status_code=500)

        # Rewrite links for click tracking
        if enable_click_tracking:
            try:
                modified_content, links_rewritten = request.app.tracking_service.rewrite_links_for_tracking(
                    modified_content, email_id, recipient, tenant_id, domain_id
                )
                logger.debug(f"Rewritten {links_rewritten} links for click tracking in email {email_id}")
            except Exception as e:
                logger.error(f"Failed to rewrite links for tracking: {e}")
                return JSONResponse({'error': 'Failed to rewrite links for tracking'}, status_code=500)

        # Prepare successful response
        response_data = {
            'success': True,
            'modified_content': modified_content,
            'tracking_injected': {
                'open_tracking': enable_open_tracking,
                'click_tracking': enable_click_tracking,
                'links_rewritten': links_rewritten
            },
            'tracking_urls': tracking_urls,
            'email_id': email_id,
            'tenant_id': tenant_id,
            'domain_id': domain_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }

        logger.info(f"Tracking successfully injected for email {email_id} "
                   f"(open: {enable_open_tracking}, click: {enable_click_tracking}, links: {links_rewritten})")

        return JSONResponse(response_data)

    except Exception as e:
        logger.error(f"Unexpected error in tracking injection: {e}", exc_info=True)
        return JSONResponse({'error': 'Internal server error during tracking injection'}, status_code=500)

@stats_api.get('/tracking/stats/{email_id}')
async def get_email_stats(email_id: str, request: Request):
    """
    Get comprehensive tracking statistics for a specific email.

    This endpoint provides detailed analytics including:
    - Event counts by type (opens, clicks)
    - Unique recipient and IP statistics
    - Device and browser breakdown
    - Geographic distribution
    - Timeline of events

    Args:
        email_id: Email identifier to get stats for

    Returns:
        JSON: Comprehensive tracking statistics
    """
    try:
        if not email_id:
            return JSONResponse({'error': 'Email ID is required'}, status_code=400)

        # Get tracking statistics from database service
        stats = request.app.database_service.get_tracking_stats(email_id)

        if not stats:
            logger.warning(f"No tracking stats found for email {email_id}")
            return JSONResponse({
                'email_id': email_id,
                'message': 'No tracking data found for this email',
                'statistics': {},
                'device_breakdown': [],
                'geographic_breakdown': [],
                'timeline': [],
                'generated_at': datetime.utcnow().isoformat() + 'Z'
            })

        logger.debug(f"Retrieved tracking stats for email {email_id}")
        return JSONResponse(stats)

    except Exception as e:
        logger.error(f"Error retrieving stats for email {email_id}: {e}")
        return JSONResponse({'error': 'Failed to retrieve email statistics'}, status_code=500)

@stats_api.get('/tracking/tenant/{tenant_id}/stats')
async def get_tenant_stats(tenant_id: str, request: Request, days: int = Query(default=30), domain_id: Optional[str] = Query(default=None)):
    """
    Get tracking statistics for a specific tenant.

    This endpoint provides tenant-level analytics for multi-tenant isolation
    and business intelligence.

    Query parameters:
        days: Number of days to include (default: 30)
        domain_id: Optional domain filter

    Args:
        tenant_id: Tenant identifier

    Returns:
        JSON: Tenant tracking statistics
    """
    try:
        if not tenant_id:
            return JSONResponse({'error': 'Tenant ID is required'}, status_code=400)

        # Validate days parameter
        if days <= 0 or days > 365:
            return JSONResponse({'error': 'Days must be between 1 and 365'}, status_code=400)

        # Get tenant statistics from database service
        stats = request.app.database_service.get_tenant_stats(tenant_id, days)

        if not stats:
            logger.warning(f"No tracking stats found for tenant {tenant_id}")
            return JSONResponse({
                'tenant_id': tenant_id,
                'message': 'No tracking data found for this tenant',
                'period_days': days,
                'event_statistics': {},
                'generated_at': datetime.utcnow().isoformat() + 'Z'
            })

        # Add domain filter information if specified
        if domain_id:
            stats['domain_filter'] = domain_id

        logger.debug(f"Retrieved tenant stats for {tenant_id} (last {days} days)")
        return JSONResponse(stats)

    except Exception as e:
        logger.error(f"Error retrieving tenant stats for {tenant_id}: {e}")
        return JSONResponse({'error': 'Failed to retrieve tenant statistics'}, status_code=500)

@stats_api.get('/tracking/stats/summary')
async def get_summary_stats(request: Request, hours: int = Query(default=24)):
    """
    Get high-level summary statistics across all tenants.

    This endpoint provides system-wide analytics for monitoring
    and capacity planning.

    Query parameters:
        hours: Number of hours to include (default: 24)

    Returns:
        JSON: System-wide tracking summary
    """
    try:
        # Validate hours parameter
        if hours <= 0 or hours > 168:  # Max 1 week
            return JSONResponse({'error': 'Hours must be between 1 and 168'}, status_code=400)

        # For now, return a basic summary - this can be expanded
        summary = {
            'period_hours': hours,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'service_status': 'active',
            'tracking_enabled': request.app.tracking_service.config.enabled,
            'message': 'Summary statistics endpoint - implement detailed aggregation as needed'
        }

        return JSONResponse(summary)

    except Exception as e:
        logger.error(f"Error retrieving summary stats: {e}")
        return JSONResponse({'error': 'Failed to retrieve summary statistics'}, status_code=500)

@stats_api.get('/tracking/health')
async def tracking_health(request: Request):
    """
    Health check endpoint specifically for tracking functionality.

    Returns:
        JSON: Tracking service health status
    """
    try:
        # Test database connectivity
        db_healthy = request.app.database_service.test_connection()

        # Test tracking service configuration
        config_valid = request.app.tracking_service.config.enabled

        health_status = {
            'service': 'tracking',
            'status': 'healthy' if db_healthy and config_valid else 'unhealthy',
            'database_connection': 'ok' if db_healthy else 'failed',
            'configuration': 'valid' if config_valid else 'invalid',
            'tracking_enabled': config_valid,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }

        status_code = 200 if (db_healthy and config_valid) else 503
        return JSONResponse(health_status, status_code=status_code)

    except Exception as e:
        logger.error(f"Error in tracking health check: {e}")
        return JSONResponse({
            'service': 'tracking',
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }, status_code=503)
