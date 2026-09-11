#!/usr/bin/env python3
"""
SMTP API keys -- read-side reporting routes
(00-PRD-smtp-api-keys Phases K1/K2).

Split from routes/smtp_credentials.py so the lifecycle file stays inside
the engineering-standards size limits: this module owns the audit-event
and usage read paths; the lifecycle module owns every mutation.

Registered under the same /api/v1/smtp-credentials prefix.
"""

import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from utils.auth import create_api_response, require_api_key
from utils.smtp_credentials import fetch_scoped_credential

from database.models.core import SmtpCredentialEvent

logger = logging.getLogger(__name__)
router = APIRouter()

# One pooled engine for the module, created lazily on FIRST USE -- see the
# matching comment in routes/smtp_credentials.py: an import-time
# create_engine() with incomplete DB_* env made app.py's try/except router
# loader silently drop this router (and its spec paths). A broken DB config
# now fails loudly on the first request instead.
_engine = None
_Session = None


def get_db_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(
            f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
            f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
            pool_pre_ping=True,
        )
        _Session = sessionmaker(bind=_engine)
    return _Session()


@router.get(
    "/{credential_id}/events",
    summary="Audit trail for an SMTP credential",
    description="Append-only key events (created/rotated/revoked/...), newest first.",
)
@require_api_key("read")
async def list_smtp_credential_events(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("error", "SMTP credential not found"),
                status_code=404,
            )

        limit = min(int(request.query_params.get("limit", 100)), 500)
        rows = (
            session.query(SmtpCredentialEvent)
            .filter_by(credential_id=credential.id)
            .order_by(SmtpCredentialEvent.created_at.desc(), SmtpCredentialEvent.id.desc())
            .limit(limit)
            .all()
        )
        return JSONResponse(
            content=create_api_response(
                "success",
                "SMTP credential events retrieved successfully",
                {"events": [r.to_dict() for r in rows]},
            )
        )
    finally:
        session.close()


@router.get(
    "/{credential_id}/usage",
    summary="Delivery counts for an SMTP credential",
    description=(
        "Messages attributed to this key via mail_logs.sasl_username (populated by the log "
        "ingestor from smtpd's client= lines). Counts start when 0018 shipped -- earlier "
        "traffic was never attributed and is honestly absent, not zero-filled."
    ),
)
@require_api_key("read")
async def smtp_credential_usage(credential_id: str, request: Request):
    session = get_db_session()
    try:
        credential = fetch_scoped_credential(session, request, credential_id)
        if not credential:
            return JSONResponse(
                content=create_api_response("error", "SMTP credential not found"),
                status_code=404,
            )

        days = min(max(int(request.query_params.get("days", 30)), 1), 365)
        since = datetime.utcnow() - timedelta(days=days)

        # Textual on purpose: mail_logs.status holds the lowercase VALUES
        # ('delivered') written by the log ingestor's raw SQL, while the
        # MailLog model's SQLEnum maps by NAME ('DELIVERED') -- reading the
        # column through the ORM raises LookupError on every real row.
        rows = session.execute(
            text(
                "SELECT status, COUNT(*) FROM mail_logs "
                "WHERE sasl_username = :u AND timestamp >= :s GROUP BY status"
            ),
            {"u": credential.username, "s": since},
        ).all()
        by_status = {str(status): int(count) for status, count in rows}
        return JSONResponse(
            content=create_api_response(
                "success",
                "SMTP credential usage retrieved successfully",
                {
                    "window_days": days,
                    "since": since.isoformat(),
                    "total": sum(by_status.values()),
                    "delivered": by_status.get("delivered", 0),
                    "bounced": by_status.get("bounced", 0),
                    "deferred": by_status.get("deferred", 0),
                    "rejected": by_status.get("rejected", 0),
                    "by_status": by_status,
                    "last_used_at": (
                        credential.last_used_at.isoformat() if credential.last_used_at else None
                    ),
                },
            )
        )
    finally:
        session.close()
