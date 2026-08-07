#!/usr/bin/env python3
"""
SMTP Credentials API Routes

Domain-scoped SMTP credentials, distinct from a mailbox password. A
credential authenticates only over SMTP (via a new protocol-scoped Dovecot
passdb, see 02-mailyte-community/phase-05-smtp-credential-auth-parity.md)
and is authorized to send as any address at its domain, not just one
mailbox.

This is additive: it never reads or writes email_accounts, and mailbox
password SMTP auth is unaffected.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import os

from database.models.core import Domain, SmtpCredential
from utils.auth import create_api_response, hash_password, require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


def get_db_session():
    """Get SQLAlchemy session"""
    engine = create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    Session = sessionmaker(bind=engine)
    return Session()


class SmtpCredentialCreate(BaseModel):
    username: str = Field(..., description="SASL login for SMTP AUTH")
    password: str = Field(..., description="Plaintext secret (will be bcrypt-hashed)")
    domain_id: str = Field(..., description="ULID of the domain this credential may send as")
    allowed_ips: list[str] | None = Field(
        default=None, description="Optional IP allowlist for this credential"
    )


@router.post(
    "/",
    summary="Create a new SMTP credential",
    description="Provision a domain-scoped SMTP credential. The password is bcrypt-hashed, same as a mailbox password.",
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

    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=payload.domain_id).first()
        if not domain:
            return JSONResponse(
                content=create_api_response("error", f"Domain {payload.domain_id} not found"),
                status_code=404,
            )

        existing = session.query(SmtpCredential).filter_by(username=payload.username).first()
        if existing:
            return JSONResponse(
                content=create_api_response(
                    "error", f"SMTP credential {payload.username} already exists"
                ),
                status_code=409,
            )

        credential = SmtpCredential(
            organization_id=domain.organization_id,
            domain_id=domain.id,
            username=payload.username,
            password=hash_password(payload.password),
            allowed_ips=payload.allowed_ips or [],
            active=True,
        )
        session.add(credential)
        session.commit()

        return JSONResponse(
            content=create_api_response(
                "success", "SMTP credential created successfully", credential.to_dict()
            ),
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


@router.get(
    "/{credential_id}",
    summary="Get an SMTP credential",
)
@require_api_key("read")
async def get_smtp_credential(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = session.query(SmtpCredential).filter_by(id=credential_id).first()
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


@router.delete(
    "/{credential_id}",
    summary="Delete an SMTP credential",
    description="Idempotent -- deleting an already-absent credential also returns success, matching the mailbox/domain delete convention.",
)
@require_api_key("write")
async def delete_smtp_credential(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = session.query(SmtpCredential).filter_by(id=credential_id).first()
        if not credential:
            return JSONResponse(
                content=create_api_response("success", "SMTP credential already absent"),
                status_code=200,
            )

        session.delete(credential)
        session.commit()

        return JSONResponse(
            content=create_api_response("success", "SMTP credential deleted successfully")
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
