#!/usr/bin/env python3
"""
SMTP API keys -- authoritative lifecycle routes
(00-PRD-smtp-api-keys Phase K1).

Domain-scoped SMTP credentials, distinct from a mailbox password. A
credential authenticates only over SMTP (protocol-scoped Dovecot passdb,
see 01-mailyte-email-server/phase-10-smtp-credential-auth.md) and may send
as any address at its domain.

This server is the single source of truth for the whole lifecycle: the
console consumes these routes directly (CE deployments have no Laravel),
and Laravel consumes the same routes for the tenant surface. Secrets are
generated HERE, returned exactly once in the create/rotate response, and
stored only as a bcrypt hash plus a short display prefix.

Enforcement notes:
* active / expires_at / allowed_ips are all enforced inside the Dovecot
  passdb query (dovecot-sql-smtp.conf.ext). Every auth-affecting mutation
  here additionally flushes Dovecot's auth cache for the username via the
  doveadm HTTP API -- verified live on 2.3.16 that without the flush a
  revoked key keeps authenticating from cache for up to 1 hour.
* Mailbox-password SMTP auth is untouched; this table is additive.
"""

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models.core import Domain, SmtpCredential
from utils.auth import create_api_response, hash_password, require_api_key
from utils.smtp_credentials import (
    fetch_scoped_credential,
    flush_auth_cache,
    generate_secret,
    generate_username,
    org_scope_filter,
    parse_expires_at,
    record_event,
    validate_allowlist,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# One pooled engine for the module -- the previous per-request
# create_engine() built a fresh pool for every call.
_engine = create_engine(
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
    pool_pre_ping=True,
)
_Session = sessionmaker(bind=_engine)


def get_db_session():
    return _Session()


class SmtpCredentialCreate(BaseModel):
    domain_id: str = Field(..., description="ULID of the domain this credential may send as")
    # username/password stay optional for the pre-K4 Laravel provisioning
    # path, which still generates both itself. Omit them and the server
    # generates the pair and returns the secret once.
    username: str | None = Field(default=None, description="SASL login; generated when omitted")
    password: str | None = Field(
        default=None, description="Plaintext secret; generated (and returned once) when omitted"
    )
    name: str | None = Field(default=None, description="Human label shown in key lists")
    allowed_ips: list[str] | None = Field(
        default=None, description="IPs / CIDR networks the key may authenticate from"
    )
    ip_allowlist_enabled: bool = Field(default=False)
    expires_at: str | None = Field(default=None, description="ISO-8601; key stops working after")
    hourly_limit: int | None = Field(default=None, ge=1)
    daily_limit: int | None = Field(default=None, ge=1)
    created_by: str | None = Field(
        default=None, description="Attribution override when a service creates on a user's behalf"
    )


class SmtpCredentialUpdate(BaseModel):
    name: str | None = None
    allowed_ips: list[str] | None = None
    ip_allowlist_enabled: bool | None = None
    # Empty string clears the expiry; absent leaves it unchanged.
    expires_at: str | None = None
    hourly_limit: int | None = Field(default=None, ge=1)
    daily_limit: int | None = Field(default=None, ge=1)


def _allowlist_error(ips: list[str] | None, enabled: bool) -> str | None:
    if ips is not None:
        err = validate_allowlist(ips)
        if err:
            return err
    if enabled and not ips:
        # An enabled empty list would mean "no network may authenticate" --
        # a lockout nobody asks for on purpose. Fail the request instead.
        return "ip_allowlist_enabled requires at least one allowed_ips entry"
    return None


@router.get("/", summary="List SMTP credentials")
@require_api_key("read")
async def list_smtp_credentials(request: Request):
    session = get_db_session()
    try:
        query = org_scope_filter(session.query(SmtpCredential), request)

        domain_id = request.query_params.get("domain_id")
        if domain_id:
            query = query.filter_by(domain_id=domain_id)
        # Platform callers (console, cross-org) may narrow to one org.
        org_param = request.query_params.get("organization_id")
        ctx = getattr(request.state, "auth_context", None) or {}
        if org_param and ctx.get("scope") == "platform":
            query = query.filter_by(organization_id=org_param)

        limit = min(int(request.query_params.get("limit", 100)), 500)
        offset = max(int(request.query_params.get("offset", 0)), 0)
        total = query.count()
        rows = (
            query.order_by(SmtpCredential.created_at.desc()).limit(limit).offset(offset).all()
        )
        return JSONResponse(
            content=create_api_response(
                "success",
                "SMTP credentials retrieved successfully",
                {"credentials": [r.to_dict() for r in rows], "total": total},
            )
        )
    finally:
        session.close()


@router.post(
    "/",
    summary="Create an SMTP credential",
    description=(
        "Provision a domain-scoped SMTP API key. When username/password are omitted the "
        "server generates them and the response carries the plaintext secret exactly once -- "
        "it is never retrievable again."
    ),
)
@require_api_key("write")
async def create_smtp_credential(request: Request):
    data = await request.json()
    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    try:
        payload = SmtpCredentialCreate(**data)
    except Exception as e:
        return JSONResponse(
            content=create_api_response("error", f"Validation failed: {e}"), status_code=400
        )

    err = _allowlist_error(payload.allowed_ips, payload.ip_allowlist_enabled)
    if err:
        return JSONResponse(content=create_api_response("error", err), status_code=422)
    expires_at, err = parse_expires_at(payload.expires_at)
    if err:
        return JSONResponse(content=create_api_response("error", err), status_code=422)

    session = get_db_session()
    try:
        # Cross-org domain ids answer 404, not 403 (conventions section 8).
        domain_query = session.query(Domain).filter_by(id=payload.domain_id)
        ctx = getattr(request.state, "auth_context", None) or {}
        if ctx.get("scope") == "organization":
            domain_query = domain_query.filter_by(organization_id=ctx.get("organization_id"))
        domain = domain_query.first()
        if not domain:
            return JSONResponse(
                content=create_api_response("error", f"Domain {payload.domain_id} not found"),
                status_code=404,
            )

        if payload.username:
            username = payload.username
            if session.query(SmtpCredential).filter_by(username=username).first():
                return JSONResponse(
                    content=create_api_response(
                        "error", f"SMTP credential {username} already exists"
                    ),
                    status_code=409,
                )
        else:
            username = generate_username(session, domain.domain)

        generated_secret = None
        if payload.password:
            secret, prefix = payload.password, payload.password[:8]
        else:
            secret, prefix = generate_secret()
            generated_secret = secret

        credential = SmtpCredential(
            organization_id=domain.organization_id,
            domain_id=domain.id,
            username=username,
            password=hash_password(secret),
            name=payload.name,
            prefix=prefix,
            created_by=payload.created_by or None,
            allowed_ips=payload.allowed_ips or [],
            ip_allowlist_enabled=payload.ip_allowlist_enabled,
            expires_at=expires_at,
            hourly_limit=payload.hourly_limit,
            daily_limit=payload.daily_limit,
            active=True,
        )
        session.add(credential)
        session.flush()  # assigns the ULID so the audit row can reference it
        record_event(session, credential, "created", request)
        session.commit()

        body = credential.to_dict()
        if generated_secret:
            body["secret"] = generated_secret
        return JSONResponse(
            content=create_api_response("success", "SMTP credential created successfully", body),
            status_code=201,
        )
    except Exception as e:
        session.rollback()
        logger.error(f"Create SMTP credential error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to create SMTP credential"),
            status_code=500,
        )
    finally:
        session.close()


@router.get("/{credential_id}", summary="Get an SMTP credential")
@require_api_key("read")
async def get_smtp_credential(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("error", "SMTP credential not found"),
                status_code=404,
            )
        return JSONResponse(
            content=create_api_response(
                "success", "SMTP credential retrieved successfully", credential.to_dict()
            )
        )
    finally:
        session.close()


@router.patch("/{credential_id}", summary="Update an SMTP credential")
@require_api_key("write")
async def update_smtp_credential(credential_id: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )
    try:
        payload = SmtpCredentialUpdate(**data)
    except Exception as e:
        return JSONResponse(
            content=create_api_response("error", f"Validation failed: {e}"), status_code=400
        )

    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("error", "SMTP credential not found"),
                status_code=404,
            )

        # Only keys present in the request body are applied -- pydantic
        # defaults must not clobber fields the caller never mentioned.
        changed: dict = {}
        if "name" in data:
            credential.name = payload.name
            changed["name"] = payload.name

        effective_ips = data.get("allowed_ips", credential.allowed_ips)
        effective_enabled = data.get(
            "ip_allowlist_enabled", credential.ip_allowlist_enabled
        )
        if "allowed_ips" in data or "ip_allowlist_enabled" in data:
            err = _allowlist_error(effective_ips, bool(effective_enabled))
            if err:
                return JSONResponse(content=create_api_response("error", err), status_code=422)
        if "allowed_ips" in data:
            credential.allowed_ips = payload.allowed_ips or []
            changed["allowed_ips"] = credential.allowed_ips
        if "ip_allowlist_enabled" in data:
            credential.ip_allowlist_enabled = bool(payload.ip_allowlist_enabled)
            changed["ip_allowlist_enabled"] = credential.ip_allowlist_enabled

        if "expires_at" in data:
            expires_at, err = parse_expires_at(payload.expires_at)
            if err:
                return JSONResponse(content=create_api_response("error", err), status_code=422)
            credential.expires_at = expires_at
            changed["expires_at"] = expires_at.isoformat() if expires_at else None

        if "hourly_limit" in data:
            credential.hourly_limit = payload.hourly_limit
            changed["hourly_limit"] = payload.hourly_limit
        if "daily_limit" in data:
            credential.daily_limit = payload.daily_limit
            changed["daily_limit"] = payload.daily_limit

        if not changed:
            return JSONResponse(
                content=create_api_response("error", "No updatable fields provided"),
                status_code=422,
            )

        record_event(session, credential, "updated", request, detail=changed)
        session.commit()

        body = credential.to_dict()
        # allow_nets and expiry are read at AUTH time from the passdb query;
        # a cached entry would keep serving the old values until the TTL.
        if any(k in changed for k in ("allowed_ips", "ip_allowlist_enabled", "expires_at")):
            body["cache_flushed"] = flush_auth_cache(credential.username)
        return JSONResponse(
            content=create_api_response("success", "SMTP credential updated successfully", body)
        )
    except Exception as e:
        session.rollback()
        logger.error(f"Update SMTP credential error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update SMTP credential"),
            status_code=500,
        )
    finally:
        session.close()


@router.post(
    "/{credential_id}/rotate",
    summary="Rotate an SMTP credential's secret",
    description=(
        "Generates a new secret for the SAME username, so SMTP clients only change their "
        "password. The response carries the new plaintext exactly once."
    ),
)
@require_api_key("write")
async def rotate_smtp_credential(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("error", "SMTP credential not found"),
                status_code=404,
            )

        secret, prefix = generate_secret()
        credential.password = hash_password(secret)
        credential.prefix = prefix
        record_event(session, credential, "rotated", request)
        session.commit()

        body = credential.to_dict()
        body["secret"] = secret
        # After the commit: the old secret must stop working on the next
        # AUTH attempt, not after the 1h auth-cache TTL.
        body["cache_flushed"] = flush_auth_cache(credential.username)
        return JSONResponse(
            content=create_api_response("success", "SMTP credential rotated successfully", body)
        )
    except Exception as e:
        session.rollback()
        logger.error(f"Rotate SMTP credential error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to rotate SMTP credential"),
            status_code=500,
        )
    finally:
        session.close()


def _set_active(credential_id: str, request: Request, active: bool, event: str):
    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("error", "SMTP credential not found"),
                status_code=404,
            )
        changed = credential.active != active
        if changed:
            credential.active = active
            record_event(session, credential, event, request)
            session.commit()
        # Revocation is only real once the cache entry is gone. Flushing on
        # an idempotent re-revoke too means a caller whose first revoke
        # reported cache_flushed=false can simply revoke again.
        flushed = flush_auth_cache(credential.username) if (changed or not active) else None
        body = credential.to_dict()
        if flushed is not None:
            body["cache_flushed"] = flushed
        return JSONResponse(
            content=create_api_response("success", f"SMTP credential {event}", body)
        )
    except Exception as e:
        session.rollback()
        logger.error(f"{event} SMTP credential error: {e}")
        return JSONResponse(
            content=create_api_response("error", f"Failed to {event} SMTP credential"),
            status_code=500,
        )
    finally:
        session.close()


@router.post(
    "/{credential_id}/revoke",
    summary="Revoke an SMTP credential",
    description=(
        "Sets active=false and flushes Dovecot's auth cache for the username, so the key "
        "stops authenticating on the next AUTH attempt -- no auth-cache window. Idempotent."
    ),
)
@require_api_key("write")
async def revoke_smtp_credential(credential_id: str, request: Request):
    return _set_active(credential_id, request, active=False, event="revoked")


@router.post(
    "/{credential_id}/enable",
    summary="Re-enable a revoked SMTP credential",
)
@require_api_key("write")
async def enable_smtp_credential(credential_id: str, request: Request):
    return _set_active(credential_id, request, active=True, event="enabled")


@router.delete(
    "/{credential_id}",
    summary="Delete an SMTP credential",
    description="Idempotent -- deleting an already-absent credential also returns success, matching the mailbox/domain delete convention.",
)
@require_api_key("write")
async def delete_smtp_credential(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("success", "SMTP credential already absent"),
                status_code=200,
            )

        # Audit row first: it is FK-free and survives the delete.
        username = credential.username
        record_event(session, credential, "deleted", request)
        session.delete(credential)
        session.commit()
        flushed = flush_auth_cache(username)

        return JSONResponse(
            content=create_api_response(
                "success", "SMTP credential deleted successfully", {"cache_flushed": flushed}
            )
        )
    except Exception as e:
        session.rollback()
        logger.error(f"Delete SMTP credential error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete SMTP credential"),
            status_code=500,
        )
    finally:
        session.close()
