#!/usr/bin/env python3
"""
Authentication utilities for API Gateway — FastAPI version

Uses FastAPI Depends() for clean dependency injection.
Route handlers that need auth should add: `auth: dict = Depends(require_api_key('read'))`
The legacy decorator syntax `@require_api_key('read')` also works via the wrapper.
"""

import os
import logging
import inspect
from typing import Optional
from fastapi import Request, HTTPException, Depends
from functools import wraps
from datetime import datetime
from .database import get_db_connection

logger = logging.getLogger(__name__)

# Allowed column names for dynamic UPDATE queries (SQL injection prevention)
ALLOWED_DOMAIN_UPDATE_COLUMNS = {
    'domain', 'active', 'description', 'catch_all', 'dkim_enabled',
    'dmarc_policy', 'spf_record', 'custom_mx', 'settings',
}

ALLOWED_MAILBOX_UPDATE_COLUMNS = {
    'name', 'quota', 'active', 'status', 'settings', 'storage_quota',
    'send_quota', 'receive_quota', 'auto_reply_enabled', 'auto_reply_message',
}


def hash_password(password: str) -> str:
    """Hash password using bcrypt. Raises an error if bcrypt is not available."""
    try:
        import bcrypt
    except ImportError:
        raise RuntimeError(
            "bcrypt package is required for password hashing. "
            "Install it with: pip install bcrypt"
        )
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash. Legacy SHA-256 hashes are rejected."""
    try:
        import bcrypt
    except ImportError:
        raise RuntimeError(
            "bcrypt package is required for password verification. "
            "Install it with: pip install bcrypt"
        )

    if hashed.startswith('$2b$') or hashed.startswith('$2a$'):
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

    # Reject legacy SHA-256 hashes — users must reset their password
    logger.warning("Rejected login attempt using legacy SHA-256 password hash. User must reset password.")
    return False


def create_api_response(response_type, message, data=None):
    """Create standardized API response"""
    response = {
        'type': response_type,
        'msg': message
    }
    if data is not None:
        response['data'] = data
    return response


def get_org_context(request: Request) -> Optional[str]:
    """Return the organization_id bound to the current API key, if any."""
    api_key_data = getattr(request.state, 'api_key_data', None)
    if api_key_data:
        return api_key_data.get('organization_id')
    return None


def _authenticate_api_key(request: Request, permission_level: str = 'read') -> dict:
    """Core auth logic — validates API key and returns key data."""
    api_key = request.headers.get('X-API-Key')
    if not api_key:
        raise HTTPException(status_code=401, detail=create_api_response('error', 'API key required'))

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail=create_api_response('error', 'Database connection failed'))

    try:
        cursor = conn.cursor(dictionary=True)
        # Try key_id column (current schema)
        cursor.execute("""
            SELECT * FROM api_keys
            WHERE key_id = %s AND active = 1
        """, (api_key,))

        api_key_data = cursor.fetchone()
        if not api_key_data:
            raise HTTPException(status_code=401, detail=create_api_response('error', 'Invalid API key'))

        # Check permissions from JSON field
        permissions = api_key_data.get('permissions') or {}
        if isinstance(permissions, str):
            import json
            permissions = json.loads(permissions)

        if permission_level == 'admin' and not permissions.get('admin_access'):
            raise HTTPException(status_code=403, detail=create_api_response('error', 'Admin access required'))

        if permission_level == 'write' and permissions.get('read_only'):
            raise HTTPException(status_code=403, detail=create_api_response('error', 'Write access required'))

        # Update last used
        cursor.execute("UPDATE api_keys SET last_used = %s WHERE key_id = %s", (datetime.now(), api_key))
        conn.commit()

        # Store on request state for downstream access
        request.state.api_key_data = {**api_key_data, **permissions}
        request.state.api_key = api_key
        request.state.organization_id = api_key_data.get('organization_id')

        return api_key_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API key validation error: {e}")
        raise HTTPException(status_code=401, detail=create_api_response('error', 'Authentication failed'))
    finally:
        cursor.close()
        conn.close()


def require_api_key(permission_level='read'):
    """
    Decorator for routes that require API key auth.

    Works by injecting a `request: Request` parameter into the wrapped function
    so FastAPI automatically populates it, then runs auth before the handler.
    """
    def decorator(f):
        # Get the original function's signature
        original_sig = inspect.signature(f)
        original_params = list(original_sig.parameters.values())

        # Check if 'request' is already a parameter
        has_request = any(p.name == 'request' for p in original_params)

        @wraps(f)
        async def decorated_function(*args, request: Request, **kwargs):
            # Run authentication
            _authenticate_api_key(request, permission_level)
            # Call the original handler — pass request only if it expects it
            if has_request:
                return await f(*args, request=request, **kwargs)
            return await f(*args, **kwargs)

        # Rebuild the signature to include `request: Request` if not already there
        if not has_request:
            request_param = inspect.Parameter('request', inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request)
            new_params = [request_param] + original_params
            decorated_function.__signature__ = original_sig.replace(parameters=new_params)

        return decorated_function
    return decorator


def require_admin():
    """Decorator for routes that require admin auth via X-Admin-Token header."""
    def decorator(f):
        original_sig = inspect.signature(f)
        original_params = list(original_sig.parameters.values())
        has_request = any(p.name == 'request' for p in original_params)

        @wraps(f)
        async def decorated_function(*args, request: Request, **kwargs):
            token = request.headers.get('X-Admin-Token') or request.headers.get('X-Admin-Password')
            expected = os.getenv('ADMIN_TOKEN_SECRET') or os.getenv('ADMIN_PASSWORD')
            if not token or token != expected:
                raise HTTPException(status_code=401, detail=create_api_response('error', 'Admin authentication required'))
            if has_request:
                return await f(*args, request=request, **kwargs)
            return await f(*args, **kwargs)

        if not has_request:
            request_param = inspect.Parameter('request', inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request)
            new_params = [request_param] + original_params
            decorated_function.__signature__ = original_sig.replace(parameters=new_params)

        return decorated_function
    return decorator


def validate_update_columns(columns: list, allowed: set) -> list:
    """Whitelist-validate column names for dynamic SQL UPDATE queries."""
    for col in columns:
        if col not in allowed:
            raise ValueError(f"Column '{col}' is not allowed in update operations")
    return columns
