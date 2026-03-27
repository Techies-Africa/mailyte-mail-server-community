#!/usr/bin/env python3
"""
Webhooks CRUD API Routes

Manages per-organization webhook endpoints directly in MySQL.
All operations are org-scoped via the API key's organization_id.
"""

import json
import logging
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from utils.auth import require_api_key, create_api_response, get_org_context
from utils.database import get_db_connection

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.webhook_dispatcher import dispatch_event, Events

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_org_id(request: Request):
    """Return the organization_id from the current API key context."""
    return getattr(request.state, 'organization_id', None)


# ---------------------------------------------------------------------------
# Endpoint CRUD
# ---------------------------------------------------------------------------

@router.get('/endpoints')
@require_api_key('read')
async def list_endpoints(request: Request, page: int = Query(1), per_page: int = Query(50)):
    """List all webhook endpoints for the authenticated organization."""
    org_id = _get_org_id(request)
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)

        params = []
        where = ""
        if org_id:
            where = "WHERE organization_id = %s"
            params.append(org_id)

        cursor.execute(f"SELECT COUNT(*) AS total FROM webhook_urls {where}", params)
        total = cursor.fetchone()['total']

        cursor.execute(
            f"""
            SELECT id, organization_id, url, description, active, event_types,
                   created_at, updated_at
            FROM webhook_urls
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall()
        cursor.close()

        # Never expose secret in list responses
        for row in rows:
            if isinstance(row.get('event_types'), str):
                try:
                    row['event_types'] = json.loads(row['event_types'])
                except Exception:
                    pass
            if row.get('created_at'):
                row['created_at'] = row['created_at'].isoformat()
            if row.get('updated_at'):
                row['updated_at'] = row['updated_at'].isoformat()

        return create_api_response(
            'success', 'Webhook endpoints retrieved',
            {'items': rows, 'pagination': {'page': page, 'per_page': per_page, 'total': total,
                                           'total_pages': (total + per_page - 1) // per_page}}
        )

    except Exception as e:
        logger.error(f"List webhook endpoints error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to retrieve webhook endpoints'), status_code=500)
    finally:
        conn.close()


@router.post('/endpoints')
@require_api_key('write')
async def create_endpoint(request: Request):
    """Create a new webhook endpoint."""
    org_id = _get_org_id(request)
    data = await request.json()

    if not data or not data.get('url'):
        return JSONResponse(content=create_api_response('error', 'url is required'), status_code=400)

    url = data['url'].strip()
    if not url.startswith(('http://', 'https://')):
        return JSONResponse(content=create_api_response('error', 'url must start with http:// or https://'), status_code=400)

    event_types = data.get('event_types')  # None = all events
    if event_types is not None and not isinstance(event_types, list):
        return JSONResponse(content=create_api_response('error', 'event_types must be a list or null'), status_code=400)

    # Generate a signing secret if not provided
    secret = data.get('secret') or secrets.token_hex(32)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO webhook_urls
                (organization_id, url, description, active, event_types, secret, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (
                org_id,
                url,
                sanitize_text(data.get('description', '')),
                1 if data.get('active', True) else 0,
                json.dumps(event_types) if event_types is not None else None,
                secret,
            ),
        )
        conn.commit()
        endpoint_id = cursor.lastrowid
        cursor.close()

        dispatch_event(
            Events.WEBHOOK_TEST,
            data={'endpoint_id': endpoint_id, 'url': url, 'action': 'created'},
            org_id=org_id,
            source_service='api',
        )

        return JSONResponse(content=create_api_response(
            'success', 'Webhook endpoint created',
            {'id': endpoint_id, 'url': url, 'secret': secret, 'active': True}
        ), status_code=201)

    except Exception as e:
        logger.error(f"Create webhook endpoint error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to create webhook endpoint'), status_code=500)
    finally:
        conn.close()


@router.get('/endpoints/{endpoint_id}')
@require_api_key('read')
async def get_endpoint(endpoint_id: str, request: Request):
    """Get a specific webhook endpoint."""
    org_id = _get_org_id(request)
    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, organization_id, url, description, active, event_types, created_at, updated_at
            FROM webhook_urls WHERE id = %s
            """,
            (endpoint_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)

        # Org isolation
        if org_id and row.get('organization_id') != org_id:
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)

        if isinstance(row.get('event_types'), str):
            try:
                row['event_types'] = json.loads(row['event_types'])
            except Exception:
                pass
        if row.get('created_at'):
            row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'):
            row['updated_at'] = row['updated_at'].isoformat()

        return create_api_response('success', 'Webhook endpoint retrieved', row)

    except Exception as e:
        logger.error(f"Get webhook endpoint error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to retrieve webhook endpoint'), status_code=500)
    finally:
        conn.close()


@router.put('/endpoints/{endpoint_id}')
@require_api_key('write')
async def update_endpoint(endpoint_id: str, request: Request):
    """Update a webhook endpoint."""
    org_id = _get_org_id(request)
    data = await request.json()
    if not data:
        return JSONResponse(content=create_api_response('error', 'No data provided'), status_code=400)

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)

        # Verify ownership
        cursor.execute("SELECT id, organization_id FROM webhook_urls WHERE id = %s", (endpoint_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)
        if org_id and row.get('organization_id') != org_id:
            cursor.close()
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)

        # Build update
        updates = []
        values = []
        allowed = {'url', 'description', 'active', 'event_types', 'secret'}
        for key in allowed:
            if key in data:
                if key == 'event_types':
                    values.append(json.dumps(data[key]) if data[key] is not None else None)
                elif key == 'active':
                    values.append(1 if data[key] else 0)
                else:
                    values.append(data[key])
                updates.append(f"{key} = %s")

        if not updates:
            cursor.close()
            return JSONResponse(content=create_api_response('error', 'No valid fields to update'), status_code=400)

        updates.append("updated_at = NOW()")
        cursor.execute(
            f"UPDATE webhook_urls SET {', '.join(updates)} WHERE id = %s",
            values + [endpoint_id],
        )
        conn.commit()
        cursor.close()

        return create_api_response('success', 'Webhook endpoint updated', {'id': endpoint_id})

    except Exception as e:
        logger.error(f"Update webhook endpoint error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to update webhook endpoint'), status_code=500)
    finally:
        conn.close()


@router.delete('/endpoints/{endpoint_id}')
@require_api_key('write')
async def delete_endpoint(endpoint_id: str, request: Request):
    """Delete a webhook endpoint."""
    org_id = _get_org_id(request)
    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, organization_id FROM webhook_urls WHERE id = %s", (endpoint_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)
        if org_id and row.get('organization_id') != org_id:
            cursor.close()
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)

        cursor.execute("DELETE FROM webhook_urls WHERE id = %s", (endpoint_id,))
        conn.commit()
        cursor.close()

        return create_api_response('success', 'Webhook endpoint deleted')

    except Exception as e:
        logger.error(f"Delete webhook endpoint error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to delete webhook endpoint'), status_code=500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test endpoint
# ---------------------------------------------------------------------------

@router.post('/endpoints/{endpoint_id}/test')
@require_api_key('write')
async def test_endpoint(endpoint_id: str, request: Request):
    """Send a test event to a webhook endpoint."""
    org_id = _get_org_id(request)
    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, organization_id, url FROM webhook_urls WHERE id = %s AND active = 1",
            (endpoint_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found or inactive'), status_code=404)
        if org_id and row.get('organization_id') != org_id:
            return JSONResponse(content=create_api_response('error', 'Webhook endpoint not found'), status_code=404)

        dispatch_event(
            Events.WEBHOOK_TEST,
            data={'endpoint_id': endpoint_id, 'url': row['url'], 'test': True, 'timestamp': datetime.utcnow().isoformat()},
            org_id=org_id,
            source_service='api',
        )

        return create_api_response('success', 'Test event dispatched', {'endpoint_id': endpoint_id})

    except Exception as e:
        logger.error(f"Test webhook endpoint error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to test webhook endpoint'), status_code=500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Delivery log
# ---------------------------------------------------------------------------

@router.get('/deliveries')
@require_api_key('read')
async def list_deliveries(request: Request, page: int = Query(1), per_page: int = Query(50), status: str = Query(None)):
    """List webhook delivery history for the authenticated organization."""
    org_id = _get_org_id(request)
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)

        conditions = []
        params = []
        if org_id:
            conditions.append("organization_id = %s")
            params.append(org_id)
        if status:
            conditions.append("delivery_status = %s")
            params.append(status.upper())

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cursor.execute(f"SELECT COUNT(*) AS total FROM webhook_delivery_logs {where}", params)
        total = cursor.fetchone()['total']

        cursor.execute(
            f"""
            SELECT id, event_type, webhook_url, delivery_status, http_status_code,
                   attempts, error_message, created_at, delivered_at
            FROM webhook_delivery_logs
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall()
        cursor.close()

        for row in rows:
            if row.get('created_at'):
                row['created_at'] = row['created_at'].isoformat()
            if row.get('delivered_at'):
                row['delivered_at'] = row['delivered_at'].isoformat()

        return create_api_response(
            'success', 'Delivery log retrieved',
            {'items': rows, 'pagination': {'page': page, 'per_page': per_page, 'total': total,
                                           'total_pages': (total + per_page - 1) // per_page}}
        )

    except Exception as e:
        logger.error(f"List deliveries error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to retrieve delivery log'), status_code=500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dead Letter Queue
# ---------------------------------------------------------------------------

@router.get('/dead-letters')
@require_api_key('read')
async def list_dead_letters(request: Request, page: int = Query(1), per_page: int = Query(50)):
    """List permanently failed webhook events (dead letter queue)."""
    org_id = _get_org_id(request)
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    if not conn:
        return JSONResponse(content=create_api_response('error', 'Database connection failed'), status_code=500)

    try:
        cursor = conn.cursor(dictionary=True)

        conditions = []
        params = []
        if org_id:
            conditions.append("organization_id = %s")
            params.append(org_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cursor.execute(f"SELECT COUNT(*) AS total FROM webhook_dead_letters {where}", params)
        total = cursor.fetchone()['total']

        cursor.execute(
            f"""
            SELECT id, event_type, webhook_url, error_message, retry_count, created_at
            FROM webhook_dead_letters
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cursor.fetchall()
        cursor.close()

        for row in rows:
            if row.get('created_at'):
                row['created_at'] = row['created_at'].isoformat()

        return create_api_response(
            'success', 'Dead letter queue retrieved',
            {'items': rows, 'pagination': {'page': page, 'per_page': per_page, 'total': total,
                                           'total_pages': (total + per_page - 1) // per_page}}
        )

    except Exception as e:
        logger.error(f"List dead letters error: {e}")
        return JSONResponse(content=create_api_response('error', 'Failed to retrieve dead letter queue'), status_code=500)
    finally:
        conn.close()
