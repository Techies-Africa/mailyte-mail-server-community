#!/usr/bin/env python3
"""
Mail Processing Models - Mail queue and delivery logs
"""

from sqlalchemy import (
    JSON,
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
from .enums import MailStatus, enum_values


# Mail Processing and Queue
class MailQueue(Base):
    """Mail queue for processing"""

    __tablename__ = "mail_queue"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    sender = Column(String(255), nullable=False, index=True)
    recipient = Column(String(255), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=True, index=True)
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    headers = Column(JSON, nullable=True)
    priority = Column(Integer, nullable=False, default=5, index=True)
    status = Column(SQLEnum(MailStatus, values_callable=enum_values), nullable=False, default=MailStatus.QUEUED, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    scheduled_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    processed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Processing metadata
    worker_id = Column(String(100), nullable=True)
    processing_time = Column(Float, nullable=True)  # seconds

    __table_args__ = (Index("idx_queue_processing", "status", "scheduled_at", "priority"),)


class MailLog(Base):
    """Mail delivery logs"""

    __tablename__ = "mail_logs"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    timestamp = Column(DateTime, nullable=False, default=func.now(), index=True)
    sender = Column(String(255), nullable=False, index=True)
    recipient = Column(String(255), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=True, index=True)
    subject = Column(Text, nullable=True)
    status = Column(SQLEnum(MailStatus, values_callable=enum_values), nullable=False, index=True)
    message_id = Column(String(255), nullable=True, index=True)
    size = Column(Integer, nullable=True)
    relay = Column(String(255), nullable=True)
    delays = Column(String(100), nullable=True)
    dsn = Column(String(10), nullable=True)

    # Additional fields for analytics
    bounce_reason = Column(Text, nullable=True)
    spam_score = Column(Float, nullable=True)
    # Authenticated SASL identity from the smtpd client= line (0018) -- what
    # ties a delivery to the SMTP API key that sent it. NULL for inbound and
    # for pre-0018 rows.
    sasl_username = Column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_mail_logs_time_status", "timestamp", "status"),
        Index("idx_mail_logs_sender_time", "sender", "timestamp"),
        Index("idx_mail_logs_sasl_time", "sasl_username", "timestamp"),
    )
