#!/usr/bin/env python3
"""
AI Models - AI transaction tracking for cost analysis and usage monitoring
"""

from sqlalchemy import (
    CHAR,
    DECIMAL,
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


# AI Transaction Tracking
class AITransaction(Base):
    """AI transaction tracking for cost analysis and usage monitoring"""

    __tablename__ = "ai_transactions"

    # Primary identification
    id = Column(String(26), primary_key=True, default=generate_ulid)
    transaction_id = Column(String(255), nullable=False, unique=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)

    # Multi-tenancy
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(String(100), nullable=True, index=True)

    # Service and operation details
    service_name = Column(String(100), nullable=False, index=True)
    operation_type = Column(String(100), nullable=False, index=True)
    model_provider = Column(String(100), nullable=False, index=True)
    model_name = Column(String(255), nullable=False, index=True)
    model_version = Column(String(100), nullable=True)

    # Token usage metrics
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)

    # Cost analysis
    cost_per_token = Column(DECIMAL(10, 8), nullable=True)
    total_cost = Column(DECIMAL(10, 6), nullable=True)
    currency = Column(String(10), nullable=False, default="USD")

    # Performance metrics
    processing_time_ms = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    batch_size = Column(Integer, nullable=False, default=1)

    # Request details
    input_text_length = Column(Integer, nullable=True)
    output_text_length = Column(Integer, nullable=True)
    request_size_bytes = Column(Integer, nullable=True)
    response_size_bytes = Column(Integer, nullable=True)

    # API details
    api_endpoint = Column(String(500), nullable=True)
    api_version = Column(String(50), nullable=True)
    request_id = Column(String(255), nullable=True)

    # Status and error tracking
    status = Column(String(50), nullable=False, default="success", index=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)

    # Context and metadata
    context_type = Column(String(100), nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)

    # Rate limiting and quotas
    rate_limit_remaining = Column(Integer, nullable=True)
    quota_consumed = Column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    completed_at = Column(DateTime, nullable=True)

    # Usage aggregation
    billing_period = Column(String(20), nullable=False, default="monthly", index=True)
    usage_date = Column(DateTime, nullable=False, index=True)

    # Indexes
    __table_args__ = (
        Index("idx_ai_trans_org_date", "organization_id", "usage_date"),
        Index("idx_ai_trans_model_date", "model_provider", "model_name", "usage_date"),
        Index("idx_ai_trans_service_date", "service_name", "operation_type", "usage_date"),
        Index("idx_ai_trans_cost", "total_cost", "usage_date"),
        Index("idx_ai_trans_tokens", "total_tokens", "usage_date"),
    )

    def to_dict(self) -> dict:
        """Convert model instance to dictionary"""
        return {
            "id": self.id,
            "transaction_id": self.transaction_id,
            "session_id": self.session_id,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "service_name": self.service_name,
            "operation_type": self.operation_type,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_per_token": float(self.cost_per_token) if self.cost_per_token else None,
            "total_cost": float(self.total_cost) if self.total_cost else None,
            "currency": self.currency,
            "processing_time_ms": self.processing_time_ms,
            "latency_ms": self.latency_ms,
            "batch_size": self.batch_size,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "context_type": self.context_type,
            "metadata": self.extra_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# Maya (mailbox AI assistant) consent -- mobile v1 section 13, migration 0022.
class MailboxAiConsent(Base):
    """Current consent state, one row per mailbox.

    The queryable "may Maya run for this mailbox right now" answer. The full
    story of how it got that way lives in MailboxAiConsentEvent; this row is
    rewritten in step with every ledger append, inside the same transaction.
    """

    __tablename__ = "mailbox_ai_consents"

    id = Column(CHAR(26), primary_key=True, default=generate_ulid)
    email_account_id = Column(
        CHAR(26),
        ForeignKey("email_accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Denormalised so "revoke every holder in this org" (policy -> blocked)
    # is one indexed scan, not a join fanned out per organization.
    organization_id = Column(CHAR(26), nullable=False, index=True)
    ai_assistant_opt_in = Column(Boolean, nullable=False, default=False)
    ai_training_opt_in = Column(Boolean, nullable=False, default=False)
    ai_terms_version = Column(String(32), nullable=True)
    ai_assistant_opted_in_at = Column(DateTime, nullable=True)
    ai_training_opted_in_at = Column(DateTime, nullable=True)
    # The acceptance ledger entry whose PDF is "the" current consent document.
    consent_event_id = Column(CHAR(26), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email_account_id": self.email_account_id,
            "organization_id": self.organization_id,
            "ai_assistant_opt_in": bool(self.ai_assistant_opt_in),
            "ai_training_opt_in": bool(self.ai_training_opt_in),
            "ai_terms_version": self.ai_terms_version,
            "ai_assistant_opted_in_at": self.ai_assistant_opted_in_at.isoformat()
            if self.ai_assistant_opted_in_at
            else None,
            "ai_training_opted_in_at": self.ai_training_opted_in_at.isoformat()
            if self.ai_training_opted_in_at
            else None,
            "consent_event_id": self.consent_event_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MailboxAiConsentEvent(Base):
    """Append-only consent ledger, one row per acceptance or withdrawal.

    Deliberately FK-free, like SmtpCredentialEvent and for the same reason: a
    consent record must survive the deletion of the mailbox -- and the
    organization -- it describes, so the ids are plain columns and the email
    address is denormalised for display after deletion. Rows are never
    updated or deleted.

    Each row carries the canonical JSON record, its SHA-256 digest, an HMAC
    seal over that digest (integrity-sealed, NOT an X.509 signature -- see
    utils/ai_consent.py), and a PDF rendering served back to the holder at
    GET /mailbox/ai/consent/{id}/document.
    """

    __tablename__ = "mailbox_ai_consent_events"

    id = Column(CHAR(26), primary_key=True, default=generate_ulid)
    email_account_id = Column(CHAR(26), nullable=False)
    organization_id = Column(CHAR(26), nullable=False)
    email = Column(String(255), nullable=False)
    action = Column(
        SQLEnum(
            "accepted",
            "revoked",
            "training_accepted",
            "training_revoked",
            "revoked_by_organisation",
            name="mailbox_ai_consent_action",
        ),
        nullable=False,
    )
    terms_version = Column(String(32), nullable=True)
    included_training = Column(Boolean, nullable=False, default=False)
    # Who recorded it: the holder's own address for self-service actions, the
    # admin identity (operator email / api-key label) for org-level ones.
    actor = Column(String(255), nullable=True)
    record_json = Column(Text, nullable=False)
    record_sha256 = Column(CHAR(64), nullable=False)
    record_hmac = Column(CHAR(64), nullable=True)
    document_pdf = Column(LargeBinary, nullable=True)  # LONGBLOB on MySQL (0022)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("idx_ai_consent_events_account", "email_account_id", "created_at"),
        Index("idx_ai_consent_events_org", "organization_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email_account_id": self.email_account_id,
            "organization_id": self.organization_id,
            "email": self.email,
            "action": self.action,
            "terms_version": self.terms_version,
            "included_training": bool(self.included_training),
            "actor": self.actor,
            "record_sha256": self.record_sha256,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
