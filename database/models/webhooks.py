#!/usr/bin/env python3
"""
Webhook Models - Webhook URL management and delivery tracking
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
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base
from .enums import WebhookDeliveryStatus


# Webhook Management
class WebhookURL(Base):
    """Dynamic webhook URL management for production-grade distributed logging"""

    __tablename__ = "webhook_urls"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(String(1000), nullable=False)

    # Event filtering - specify which events this webhook handles
    event_types = Column(
        JSON, nullable=False
    )  # Array: ['email.opened', 'email.clicked', 'smtp.delivered']

    # Service filtering - specify which services send to this webhook
    service_types = Column(JSON, nullable=False)  # Array: ['tracking', 'postfix', 'dovecot']

    # Security and encryption
    encryption_key = Column(String(255), nullable=False)  # AES-256 encryption key for payload
    webhook_secret = Column(String(255), nullable=False)  # HMAC signature secret

    # Configuration
    active = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=1)  # 1=highest, 10=lowest priority
    timeout_seconds = Column(Integer, nullable=False, default=30)
    retry_attempts = Column(Integer, nullable=False, default=3)
    retry_delay_seconds = Column(Integer, nullable=False, default=5)

    # Rate limiting per webhook
    rate_limit_per_minute = Column(Integer, nullable=True)  # NULL = no limit

    # Filtering and routing
    tenant_filter = Column(JSON, nullable=True)  # Array of tenant IDs (NULL = all tenants)
    domain_filter = Column(JSON, nullable=True)  # Array of domain IDs (NULL = all domains)

    # Headers and authentication
    custom_headers = Column(JSON, nullable=True)  # Custom HTTP headers
    auth_type = Column(String(50), nullable=True)  # 'bearer', 'basic', 'api_key', NULL
    auth_credentials = Column(String(500), nullable=True)  # Encrypted auth credentials

    # Monitoring and health
    last_success = Column(DateTime, nullable=True)
    last_failure = Column(DateTime, nullable=True)
    success_count = Column(BigInteger, nullable=False, default=0)
    failure_count = Column(BigInteger, nullable=False, default=0)

    # Metadata
    description = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)  # Array of tags for organization
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    created_by = Column(String(100), nullable=True)

    # Indexes for performance
    __table_args__ = (
        Index("idx_webhook_urls_active", "active"),
        Index("idx_webhook_urls_priority", "priority"),
        Index("idx_webhook_urls_event_types", "event_types"),
        Index("idx_webhook_urls_service_types", "service_types"),
        Index("idx_webhook_urls_last_success", "last_success"),
    )


class WebhookDeliveryLog(Base):
    """
    Webhook delivery log with configurable cleanup and retry logic.

    This model combines event tracking with delivery status and provides
    configurable retention policies, automatic cleanup of successful deliveries,
    and exponential backoff retry mechanisms.
    """

    __tablename__ = "webhook_delivery_logs"

    # Primary identification
    id = Column(String(26), primary_key=True, default=generate_ulid)
    webhook_url_id = Column(
        String(26), ForeignKey("webhook_urls.id"), nullable=True, index=True
    )  # NULL for ad-hoc webhooks

    # Event details
    event_type = Column(String(100), nullable=False, index=True)
    event_data = Column(JSON, nullable=False)  # Original event payload

    # Delivery target
    webhook_url = Column(String(1000), nullable=False, index=True)
    webhook_name = Column(String(255), nullable=True)  # Cache webhook name for performance

    # Delivery status and tracking
    delivery_status = Column(
        SQLEnum(WebhookDeliveryStatus),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        index=True,
    )
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)  # Configurable retry limit

    # Retry scheduling with exponential backoff
    next_retry_at = Column(DateTime, nullable=True, index=True)
    retry_delay_seconds = Column(Integer, nullable=False, default=5)  # Base retry delay
    backoff_multiplier = Column(
        Float, nullable=False, default=2.0
    )  # Exponential backoff multiplier
    max_retry_delay = Column(Integer, nullable=False, default=3600)  # Max 1 hour between retries

    # HTTP response tracking
    http_status_code = Column(Integer, nullable=True, index=True)
    response_headers = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)
    response_size_bytes = Column(Integer, nullable=True)

    # Performance metrics
    request_duration_ms = Column(Integer, nullable=True)
    dns_resolution_ms = Column(Integer, nullable=True)
    connection_time_ms = Column(Integer, nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    last_error_at = Column(DateTime, nullable=True)

    # Security and payload info
    payload_size_bytes = Column(Integer, nullable=True)
    payload_encrypted = Column(Boolean, nullable=False, default=False)
    signature_verified = Column(Boolean, nullable=True)

    # Organization and filtering
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=True, index=True)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    first_attempt_at = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True, index=True)
    abandoned_at = Column(DateTime, nullable=True)

    # Cleanup configuration (per record for flexibility)
    auto_cleanup_enabled = Column(Boolean, nullable=False, default=True)
    cleanup_after_hours = Column(Integer, nullable=True)  # NULL = use global config

    # Indexes for performance and cleanup
    __table_args__ = (
        Index("idx_webhook_delivery_status_retry", "delivery_status", "next_retry_at"),
        Index(
            "idx_webhook_delivery_cleanup_success",
            "delivery_status",
            "delivered_at",
            "auto_cleanup_enabled",
        ),
        Index(
            "idx_webhook_delivery_cleanup_failed",
            "delivery_status",
            "abandoned_at",
            "auto_cleanup_enabled",
        ),
        Index("idx_webhook_delivery_attempts", "attempts", "max_attempts"),
        Index("idx_webhook_delivery_org_time", "organization_id", "created_at"),
        Index("idx_webhook_delivery_url_time", "webhook_url", "created_at"),
        Index("idx_webhook_delivery_event_time", "event_type", "created_at"),
    )

    def to_dict(self) -> dict:
        """Convert model instance to dictionary for API responses"""
        return {
            "id": self.id,
            "webhook_url_id": self.webhook_url_id,
            "event_type": self.event_type,
            "event_data": self.event_data,
            "webhook_url": self.webhook_url,
            "webhook_name": self.webhook_name,
            "delivery_status": self.delivery_status.value if self.delivery_status else None,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "http_status_code": self.http_status_code,
            "response_body": self.response_body,
            "request_duration_ms": self.request_duration_ms,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "payload_size_bytes": self.payload_size_bytes,
            "organization_id": self.organization_id,
            "domain_id": self.domain_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "first_attempt_at": self.first_attempt_at.isoformat()
            if self.first_attempt_at
            else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "abandoned_at": self.abandoned_at.isoformat() if self.abandoned_at else None,
        }
