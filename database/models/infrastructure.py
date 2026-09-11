#!/usr/bin/env python3
"""
Infrastructure Models — JMAP state tracking, migration jobs, Kafka dead
letters, GDPR compliance records, and OAuth.
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
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


class JMAPState(Base):
    """Per-account JMAP state vectors for push/delta sync."""

    __tablename__ = "jmap_states"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    account_id = Column(String(255), nullable=False, comment="Email address as JMAP account ID")
    data_type = Column(String(50), nullable=False, comment="Mailbox, Email, Thread, etc.")
    state = Column(String(100), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("account_id", "data_type", name="uk_jmap_account_type"),
        Index("idx_jmap_account", "account_id"),
    )


class MigrationJob(Base):
    """IMAP-to-IMAP mailbox migration job tracking."""

    __tablename__ = "migration_jobs"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    job_id = Column(String(100), nullable=False, unique=True)
    org_id = Column(String(26), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    direction = Column(String(10), nullable=False, default="import")
    source_host = Column(String(255), nullable=True)
    source_port = Column(Integer, nullable=True)
    source_user = Column(String(255), nullable=True)
    source_email = Column(String(255), nullable=True)
    target_email = Column(String(255), nullable=False)
    target_host = Column(String(255), nullable=True)
    target_port = Column(Integer, nullable=True, default=993)
    target_ssl = Column(Boolean, nullable=True, default=True)
    status = Column(
        SQLEnum("pending", "running", "paused", "completed", "failed", "cancelled"),
        nullable=False,
        default="pending",
    )
    total_messages = Column(Integer, nullable=False, default=0)
    migrated_messages = Column(Integer, nullable=False, default=0)
    failed_messages = Column(Integer, nullable=False, default=0)
    current_folder = Column(String(255), nullable=True)
    speed = Column(Float, nullable=True, default=0.0)
    is_delta = Column(Boolean, nullable=False, default=False)
    is_retry = Column(Boolean, nullable=False, default=False)
    parent_job_id = Column(String(36), nullable=True)
    folder_mapping = Column(JSON, nullable=True)
    exclude_folders = Column(JSON, nullable=True)
    last_uid = Column(JSON, nullable=True)
    webhook_url = Column(String(500), nullable=True)
    error_log = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    errors = __import__("sqlalchemy.orm", fromlist=["relationship"]).relationship(
        "MigrationError", back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_migration_status", "status"),
        Index("idx_migration_org", "org_id"),
        Index("idx_direction", "direction"),
        Index("idx_parent", "parent_job_id"),
    )

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "direction": self.direction,
            "source_email": self.source_email,
            "target_email": self.target_email,
            "status": self.status,
            "total_messages": self.total_messages,
            "migrated_messages": self.migrated_messages,
            "failed_messages": self.failed_messages,
            "speed": self.speed,
        }


class MigrationError(Base):
    """Structured per-message error log for migration jobs."""

    __tablename__ = "migration_errors"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    job_id = Column(
        String(36), ForeignKey("migration_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    folder = Column(String(255), nullable=False)
    message_uid = Column(String(100), nullable=True, default="")
    message_id = Column(String(500), nullable=True, default="")
    error_type = Column(String(50), nullable=False)
    error_message = Column(Text, nullable=False)
    retryable = Column(Boolean, nullable=False, default=True)
    retried = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=func.now())

    job = __import__("sqlalchemy.orm", fromlist=["relationship"]).relationship(
        "MigrationJob", back_populates="errors"
    )

    __table_args__ = (
        Index("idx_mig_err_job", "job_id"),
        Index("idx_mig_err_retryable", "job_id", "retryable", "retried"),
    )


class KafkaDeadLetter(Base):
    """Unprocessable Kafka messages stored for inspection and retry."""

    __tablename__ = "kafka_dead_letters"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    topic = Column(String(255), nullable=False)
    partition_num = Column(Integer, nullable=True)
    offset_num = Column(BigInteger, nullable=True)
    key_data = Column(String(255), nullable=True)
    value_data = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    status = Column(
        SQLEnum("pending", "retrying", "resolved", "abandoned"), nullable=False, default="pending"
    )
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("idx_kafka_dl_topic_status", "topic", "status"),
        Index("idx_kafka_dl_status", "status"),
        Index("idx_kafka_dl_created", "created_at"),
    )


class ConsentRecord(Base):
    """GDPR consent records per user."""

    __tablename__ = "consent_records"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    user_email = Column(String(255), nullable=False)
    consent_type = Column(
        SQLEnum("marketing", "analytics", "third_party_sharing", "data_processing"), nullable=False
    )
    granted = Column(Boolean, nullable=False, default=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    granted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_consent_user_type", "user_email", "consent_type"),)


class DataExportRequest(Base):
    """GDPR data export (right of access) requests."""

    __tablename__ = "data_export_requests"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    user_email = Column(String(255), nullable=False)
    status = Column(
        SQLEnum("pending", "processing", "completed", "failed", "expired"),
        nullable=False,
        default="pending",
    )
    export_type = Column(
        SQLEnum("full", "emails", "contacts", "settings"), nullable=False, default="full"
    )
    file_path = Column(String(500), nullable=True)
    file_size = Column(BigInteger, nullable=True, default=0)
    requested_by = Column(String(255), nullable=False)
    error_message = Column(Text, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("idx_export_user_status", "user_email", "status"),
        Index("idx_export_expires", "expires_at"),
    )


class DataErasureRequest(Base):
    """GDPR right-to-erasure (right to be forgotten) requests."""

    __tablename__ = "data_erasure_requests"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    user_email = Column(String(255), nullable=False)
    status = Column(
        SQLEnum("pending", "soft_deleted", "hard_deleted", "cancelled"),
        nullable=False,
        default="pending",
    )
    requested_by = Column(String(255), nullable=False)
    reason = Column(Text, nullable=True)
    soft_deleted_at = Column(DateTime, nullable=True)
    hard_delete_scheduled_at = Column(DateTime, nullable=True)
    hard_deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("idx_erasure_user_status", "user_email", "status"),
        Index("idx_erasure_hard_delete", "hard_delete_scheduled_at", "status"),
    )


class OAuthClient(Base):
    """OAuth 2.0 registered client applications."""

    __tablename__ = "oauth_clients"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    client_id = Column(String(100), nullable=False, unique=True)
    client_secret = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    client_type = Column(SQLEnum("confidential", "public"), nullable=False, default="confidential")
    redirect_uris = Column(JSON, nullable=True)
    scopes = Column(JSON, nullable=True)
    grant_types = Column(JSON, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    grants = __import__("sqlalchemy.orm", fromlist=["relationship"]).relationship(
        "OAuthGrant", back_populates="client", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_oauth_client_org", "organization_id"),
        Index("idx_oauth_client_active", "active"),
    )


class OAuthGrant(Base):
    """OAuth 2.0 access/refresh token grants."""

    __tablename__ = "oauth_grants"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    client_id = Column(
        String(100), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False
    )
    user_email = Column(String(255), nullable=True)
    access_token_hash = Column(String(255), nullable=False, unique=True)
    refresh_token_hash = Column(String(255), nullable=True, unique=True)
    scopes = Column(JSON, nullable=True)
    access_expires_at = Column(DateTime, nullable=False)
    refresh_expires_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    client = __import__("sqlalchemy.orm", fromlist=["relationship"]).relationship(
        "OAuthClient", back_populates="grants"
    )

    __table_args__ = (
        Index("idx_oauth_grant_client", "client_id"),
        Index("idx_oauth_grant_user", "user_email"),
        Index("idx_oauth_grant_revoked", "revoked"),
        Index("idx_oauth_grant_expires", "access_expires_at"),
    )
