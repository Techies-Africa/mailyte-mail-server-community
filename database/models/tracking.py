#!/usr/bin/env python3
"""
Tracking Models - Email tracking events and statistics
"""

from sqlalchemy import (
    JSON,
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
from .enums import EventType


# Email Tracking
class EmailTracking(Base):
    """Email tracking events"""

    __tablename__ = "email_tracking"

    # Primary key
    id = Column(String(26), primary_key=True, default=generate_ulid)

    # Email identification
    email_id = Column(String(255), nullable=False, index=True)
    recipient = Column(String(255), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False, index=True)

    # Event details
    event_type = Column(SQLEnum(EventType), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=func.now(), index=True)

    # Request metadata
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True, index=True)  # Supports IPv6
    referer = Column(Text, nullable=True)
    accept_language = Column(String(255), nullable=True)

    # Device and browser information
    device_type = Column(String(100), nullable=True)
    browser = Column(String(100), nullable=True)
    operating_system = Column(String(100), nullable=True)

    # Geographic information
    country = Column(String(100), nullable=True, index=True)
    region = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)

    # Additional data as JSON
    additional_data = Column(JSON, nullable=True)

    # Indexes for performance
    __table_args__ = (
        Index("idx_email_event_time", "email_id", "event_type", "timestamp"),
        Index("idx_org_time", "organization_id", "timestamp"),
        Index("idx_recipient_time", "recipient", "timestamp"),
        Index("idx_event_time", "event_type", "timestamp"),
    )

    def to_dict(self) -> dict:
        """Convert model instance to dictionary"""
        return {
            "id": self.id,
            "email_id": self.email_id,
            "recipient": self.recipient,
            "organization_id": self.organization_id,
            "domain_id": self.domain_id,
            "event_type": self.event_type.value if self.event_type else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_agent": self.user_agent,
            "ip_address": self.ip_address,
            "referer": self.referer,
            "accept_language": self.accept_language,
            "device_type": self.device_type,
            "browser": self.browser,
            "operating_system": self.operating_system,
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "additional_data": self.additional_data,
        }


class TrackingStatistics(Base):
    """Aggregated tracking statistics"""

    __tablename__ = "tracking_statistics"

    # Primary key
    id = Column(String(26), primary_key=True, default=generate_ulid)

    # Aggregation keys
    email_id = Column(String(255), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False, index=True)
    event_type = Column(SQLEnum(EventType), nullable=False, index=True)

    # Statistics
    total_count = Column(Integer, nullable=False, default=0)
    unique_count = Column(Integer, nullable=False, default=0)
    unique_ips = Column(Integer, nullable=False, default=0)

    # Timestamps
    first_event = Column(DateTime, nullable=True)
    last_event = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Indexes
    __table_args__ = (
        Index("idx_stats_email_event", "email_id", "event_type"),
        Index("idx_stats_org_event", "organization_id", "event_type"),
    )
