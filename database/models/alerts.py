#!/usr/bin/env python3
"""
Alert Models - Unified alert system and usage history tracking
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base
from .enums import AlertLevel, LimitType, enum_values


# Unified Alert System
class Alert(Base):
    """Unified alert system for rate limits, storage quotas, and other alerts"""

    __tablename__ = "alerts"

    id = Column(String(26), primary_key=True, default=generate_ulid)

    # Alert identification
    alert_type = Column(
        SQLEnum(LimitType, values_callable=enum_values), nullable=False, index=True
    )  # rate_limit or storage_quota
    alert_level = Column(SQLEnum(AlertLevel, values_callable=enum_values), nullable=False, index=True)

    # Entity identification
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=True, index=True)
    email_account_id = Column(
        String(26), ForeignKey("email_accounts.id"), nullable=True, index=True
    )

    # Alert details
    current_usage = Column(BigInteger, nullable=False)
    limit_value = Column(BigInteger, nullable=False)
    usage_percentage = Column(Float, nullable=False)

    # Context for the alert
    context_data = Column(
        JSON, nullable=True
    )  # Additional context (e.g., time window, storage type)

    # Webhook delivery tracking
    webhook_sent = Column(Boolean, nullable=False, default=False, index=True)
    webhook_attempts = Column(Integer, nullable=False, default=0)
    webhook_last_attempt = Column(DateTime, nullable=True)
    webhook_success = Column(Boolean, nullable=True)

    # Resolution tracking
    resolved = Column(Boolean, nullable=False, default=False, index=True)
    resolved_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_alerts_pending_webhook", "webhook_sent", "webhook_attempts"),
        Index("idx_alerts_org_type", "organization_id", "alert_type"),
        Index("idx_alerts_level_created", "alert_level", "created_at"),
        Index("idx_alerts_resolved", "resolved", "created_at"),
    )

    def to_dict(self) -> dict:
        """Convert model instance to dictionary"""
        return {
            "id": self.id,
            "alert_type": self.alert_type.value if self.alert_type else None,
            "alert_level": self.alert_level.value if self.alert_level else None,
            "organization_id": self.organization_id,
            "domain_id": self.domain_id,
            "email_account_id": self.email_account_id,
            "current_usage": self.current_usage,
            "limit_value": self.limit_value,
            "usage_percentage": self.usage_percentage,
            "context_data": self.context_data,
            "webhook_sent": self.webhook_sent,
            "webhook_attempts": self.webhook_attempts,
            "webhook_last_attempt": self.webhook_last_attempt.isoformat()
            if self.webhook_last_attempt
            else None,
            "webhook_success": self.webhook_success,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Historical Usage Tracking
class UsageHistory(Base):
    """Historical usage tracking for both rate limits and storage quotas"""

    __tablename__ = "usage_history"

    id = Column(String(26), primary_key=True, default=generate_ulid)

    # Usage type
    usage_type = Column(SQLEnum(LimitType, values_callable=enum_values), nullable=False, index=True)

    # Entity identification
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=True, index=True)
    email_account_id = Column(
        String(26), ForeignKey("email_accounts.id"), nullable=True, index=True
    )

    # Time period keys for efficient querying
    hour_key = Column(String(13), nullable=False, index=True)  # Format: YYYY-MM-DD-HH
    day_key = Column(String(10), nullable=False, index=True)  # Format: YYYY-MM-DD
    month_key = Column(String(7), nullable=False, index=True)  # Format: YYYY-MM

    # Usage data (flexible JSON for different types)
    usage_data = Column(JSON, nullable=False)

    # Metadata
    recorded_at = Column(DateTime, nullable=False, default=func.now(), index=True)

    __table_args__ = (
        Index("idx_usage_history_org_type_day", "organization_id", "usage_type", "day_key"),
        Index("idx_usage_history_cleanup", "day_key"),  # For cleanup operations
        Index("idx_usage_history_entity_time", "email_account_id", "usage_type", "hour_key"),
    )
