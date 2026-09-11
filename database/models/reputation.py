#!/usr/bin/env python3
"""
Reputation Models - Email suppression, domain reputation, and feedback loops
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


# Email Suppression Management
class EmailSuppression(Base):
    """Email suppression list for bounces, complaints, and unsubscribes"""

    __tablename__ = "email_suppressions"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    email = Column(String(255), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    suppression_type = Column(
        SQLEnum("BOUNCE", "COMPLAINT", "UNSUBSCRIBE", "MANUAL", name="suppression_type"),
        nullable=False,
        index=True,
    )
    reason = Column(Text, nullable=True)
    source = Column(String(100), nullable=True)  # Source of suppression (auto, manual, api)

    # Expiration for automatic cleanup
    expires_at = Column(DateTime, nullable=True, index=True)

    # Bounce-specific data
    bounce_type = Column(SQLEnum("HARD", "SOFT", "BLOCK", name="bounce_type"), nullable=True)
    bounce_count = Column(Integer, nullable=False, default=1)
    last_bounce_reason = Column(Text, nullable=True)

    # Metadata
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Indexes
    __table_args__ = (
        Index(
            "unique_email_suppression", "email", "organization_id", "suppression_type", unique=True
        ),
        Index("idx_suppression_expires", "expires_at", "active"),
        Index("idx_suppression_type_org", "suppression_type", "organization_id"),
    )


# Domain Reputation Management
class DomainReputation(Base):
    """Track domain reputation scores and metrics"""

    __tablename__ = "domain_reputation"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)

    # Reputation scores (0-100)
    overall_score = Column(Integer, nullable=False, default=50)
    deliverability_score = Column(Integer, nullable=False, default=50)
    engagement_score = Column(Integer, nullable=False, default=50)

    # Volume metrics
    emails_sent = Column(BigInteger, nullable=False, default=0)
    emails_delivered = Column(BigInteger, nullable=False, default=0)
    emails_bounced = Column(BigInteger, nullable=False, default=0)
    emails_complained = Column(BigInteger, nullable=False, default=0)

    # Engagement metrics
    emails_opened = Column(BigInteger, nullable=False, default=0)
    emails_clicked = Column(BigInteger, nullable=False, default=0)

    # Time period for metrics
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False, index=True)
    period_type = Column(
        SQLEnum("HOURLY", "DAILY", "WEEKLY", "MONTHLY", name="reputation_period"),
        nullable=False,
        default="DAILY",
    )

    # ISP-specific data
    isp_data = Column(JSON, nullable=True)  # Gmail, Outlook, Yahoo metrics

    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_domain_reputation_period", "domain_id", "period_type", "period_start"),
        Index("idx_domain_reputation_org", "organization_id", "period_start"),
    )


class FeedbackLoop(Base):
    """ISP feedback loop complaints management"""

    __tablename__ = "feedback_loops"

    id = Column(String(26), primary_key=True, default=generate_ulid)

    # Email identification
    original_recipient = Column(String(255), nullable=False, index=True)
    complaint_recipient = Column(String(255), nullable=True)  # FBL recipient
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)

    # ISP information
    isp_name = Column(String(100), nullable=False, index=True)  # gmail, outlook, yahoo
    feedback_type = Column(String(50), nullable=False, default="abuse")

    # Original email data
    email_id = Column(String(255), nullable=True, index=True)
    subject = Column(Text, nullable=True)
    sender = Column(String(255), nullable=True, index=True)

    # Raw feedback data
    raw_feedback = Column(Text, nullable=True)
    headers = Column(JSON, nullable=True)

    # Processing status
    processed = Column(Boolean, nullable=False, default=False, index=True)
    suppression_added = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_fbl_isp_date", "isp_name", "created_at"),
        Index("idx_fbl_processing", "processed", "created_at"),
        Index("idx_fbl_org", "organization_id", "created_at"),
    )
