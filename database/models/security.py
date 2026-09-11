#!/usr/bin/env python3
"""
Security Models — IP access rules, audit logs, auth tracking, DLP,
TOTP secrets, geo-blocking policies, and related compliance tables.
"""

from sqlalchemy import (
    DECIMAL,
    JSON,
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
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


class IPAccessRule(Base):
    """Per-organization IP whitelisting/blacklisting for SMTP relay."""

    __tablename__ = "ip_access_rules"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    rule_type = Column(SQLEnum("whitelist", "blacklist"), nullable=False, default="whitelist")
    ip_address = Column(String(45), nullable=False, comment="IPv4/IPv6 or CIDR")
    description = Column(String(255), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_ip_rules_org", "organization_id"),
        Index("idx_ip_rules_type", "rule_type"),
        Index("idx_ip_rules_active", "organization_id", "active", "rule_type"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "rule_type": self.rule_type,
            "ip_address": self.ip_address,
            "description": self.description,
            "active": self.active,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditLog(Base):
    """Security-relevant event audit trail (GDPR/SOC2 compliance)."""

    __tablename__ = "audit_logs"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    event_type = Column(String(100), nullable=False)
    event_source = Column(String(50), nullable=False)
    organization_id = Column(String(26), nullable=True)
    user_email = Column(String(255), nullable=True)
    performed_by = Column(String(255), nullable=True)
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    details = Column(JSON, nullable=True)
    severity = Column(SQLEnum("info", "warning", "critical"), nullable=False, default="info")
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("idx_audit_type", "event_type"),
        Index("idx_audit_source", "event_source"),
        Index("idx_audit_org", "organization_id"),
        Index("idx_audit_user", "user_email"),
        Index("idx_audit_ip", "client_ip"),
        Index("idx_audit_severity", "severity"),
        Index("idx_audit_created", "created_at"),
        Index("idx_audit_org_type", "organization_id", "event_type", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "event_type": self.event_type,
            "event_source": self.event_source,
            "organization_id": self.organization_id,
            "user_email": self.user_email,
            "client_ip": self.client_ip,
            "details": self.details,
            "severity": self.severity,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FailedAuthAttempt(Base):
    """Persistent brute-force attempt tracking (survives container restarts)."""

    __tablename__ = "failed_auth_attempts"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    client_ip = Column(String(45), nullable=False)
    username = Column(String(255), nullable=True)
    service = Column(SQLEnum("smtp", "imap", "pop3", "api", "sieve"), nullable=False)
    failure_reason = Column(String(255), nullable=True)
    blocked_until = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=1)
    first_attempt_at = Column(DateTime, nullable=False, default=func.now())
    last_attempt_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_failed_auth_ip", "client_ip"),
        Index("idx_failed_auth_user", "username"),
        Index("idx_failed_auth_service", "service"),
        Index("idx_failed_auth_blocked", "blocked_until"),
        Index("idx_failed_auth_ip_service", "client_ip", "service", "last_attempt_at"),
    )


class IPReputation(Base):
    """IP behavior tracking for sender reputation scoring."""

    __tablename__ = "ip_reputation"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    ip_address = Column(String(45), nullable=False, unique=True)
    reputation_score = Column(DECIMAL(5, 2), nullable=False, default=50.00)
    total_connections = Column(Integer, nullable=False, default=0)
    total_messages = Column(Integer, nullable=False, default=0)
    spam_count = Column(Integer, nullable=False, default=0)
    bounce_count = Column(Integer, nullable=False, default=0)
    auth_failure_count = Column(Integer, nullable=False, default=0)
    last_seen = Column(DateTime, nullable=False, default=func.now())
    first_seen = Column(DateTime, nullable=False, default=func.now())
    is_blocked = Column(Boolean, nullable=False, default=False)
    blocked_reason = Column(String(255), nullable=True)
    blocked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_ip_rep_score", "reputation_score"),
        Index("idx_ip_rep_blocked", "is_blocked"),
        Index("idx_ip_rep_last_seen", "last_seen"),
    )


class DLPPolicy(Base):
    """Per-organization Data Loss Prevention policy rules."""

    __tablename__ = "dlp_policies"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    policy_type = Column(
        SQLEnum("pii", "keyword", "regex", "file_type"), nullable=False, default="keyword"
    )
    patterns = Column(JSON, nullable=False)
    action = Column(
        SQLEnum("block", "quarantine", "encrypt", "notify", "log_only"),
        nullable=False,
        default="notify",
    )
    severity = Column(
        SQLEnum("low", "medium", "high", "critical"), nullable=False, default="medium"
    )
    enabled = Column(Boolean, nullable=False, default=True)
    apply_to = Column(SQLEnum("inbound", "outbound", "both"), nullable=False, default="outbound")
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    violations = relationship("DLPViolation", back_populates="policy", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_dlp_org_enabled", "organization_id", "enabled"),)


class DLPViolation(Base):
    """Log of every DLP policy match."""

    __tablename__ = "dlp_violations"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    policy_id = Column(
        String(26), ForeignKey("dlp_policies.id", ondelete="CASCADE"), nullable=False
    )
    message_id = Column(String(255), nullable=True)
    sender = Column(String(255), nullable=False)
    recipient = Column(String(255), nullable=True)
    violation_type = Column(String(100), nullable=False)
    matched_pattern = Column(Text, nullable=True)
    action_taken = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    policy = relationship("DLPPolicy", back_populates="violations")

    __table_args__ = (
        Index("idx_dlp_viol_org_date", "organization_id", "created_at"),
        Index("idx_dlp_viol_policy", "policy_id"),
    )


class TOTPSecret(Base):
    """Per-user TOTP 2FA enrollment."""

    __tablename__ = "totp_secrets"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    user_email = Column(String(255), nullable=False, unique=True)
    secret = Column(String(64), nullable=False)
    backup_codes = Column(JSON, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    verified = Column(Boolean, nullable=False, default=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_totp_email", "user_email"),)


class GeoPolicy(Base):
    """Per-organization country-level access restriction policy."""

    __tablename__ = "geo_policies"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    allowed_countries = Column(JSON, nullable=True)
    blocked_countries = Column(JSON, nullable=True)
    time_restrictions = Column(JSON, nullable=True)
    action = Column(SQLEnum("block", "challenge", "log_only"), nullable=False, default="block")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
