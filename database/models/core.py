#!/usr/bin/env python3
"""
Core Models - Organization, Domain, EmailAccount, and Alias management
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
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base
from .enums import AccountStatus, enum_values


# Core Organization Management
class Organization(Base):
    """Organization/tenant management table"""

    __tablename__ = "organizations"

    id = Column(String(26), primary_key=True, default=generate_ulid)  # ULID primary key
    external_id = Column(
        String(255), nullable=True, unique=True, index=True
    )  # External system identifier
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    # Outbound stream class (SMTP-Send phase-01): transactional|marketing|both.
    # Postfix's sender-dependent transport map routes 'marketing' orgs out the
    # marketing source IP; 'both' deliberately routes as transactional until
    # per-credential stream classing exists (phase-04). Laravel becomes the
    # writer of record in phase-04.
    sending_profile = Column(
        String(20), nullable=False, default="transactional", index=True
    )

    # Contact information
    admin_email = Column(String(255), nullable=True)
    admin_name = Column(String(255), nullable=True)

    # Settings
    settings = Column(JSON, nullable=True)  # Organization-specific settings

    # Rate limiting configuration
    rate_limits = Column(JSON, nullable=True)  # Default rate limits for organization

    # Storage quotas
    storage_quotas = Column(JSON, nullable=True)  # Default storage quotas

    # Webhooks
    webhook_urls = Column(JSON, nullable=True)  # Organization webhook endpoints
    webhook_secret = Column(String(255), nullable=True)

    # Quota override (migration 0009, console phase-02 SS2.6 decision (a)).
    # Set when a human operator edits quotas through the console; Laravel's
    # automated plan sync refuses to overwrite an overridden org unless it
    # passes force=true. quota_override_by is the operator's EMAIL, kept
    # denormalised for the same reason operator_audit.operator_email is --
    # "who set this, and when" has to stay readable after the operator row
    # is renamed or removed.
    quota_override = Column(Boolean, nullable=False, default=False)
    quota_override_at = Column(DateTime, nullable=True)
    quota_override_by = Column(String(255), nullable=True)

    # Maya (mailbox AI assistant) -- mobile v1 section 13, migration 0022.
    # Entitlement (ai_entitled/ai_plan/ai_monthly_quota) is a commercial fact
    # written by Laravel or staff via PUT /organizations/{id}/ai-settings;
    # ai_org_policy is the organisation's own decision layered on top:
    # 'unset' (treated as blocked), 'allowed', 'blocked' (hard veto that
    # revokes standing individual consents), or 'accepted_for_all' (skips the
    # individual ask). ai_policy_updated_by is a denormalised identity string
    # for the same reason quota_override_by is.
    ai_entitled = Column(Boolean, nullable=False, default=False)
    ai_plan = Column(String(64), nullable=True)
    ai_org_policy = Column(
        SQLEnum(
            "unset",
            "allowed",
            "blocked",
            "accepted_for_all",
            name="organizations_ai_org_policy",
        ),
        nullable=False,
        default="unset",
    )
    ai_monthly_quota = Column(Integer, nullable=True)  # NULL -> deployment default
    ai_policy_updated_at = Column(DateTime, nullable=True)
    ai_policy_updated_by = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Relationships
    domains = relationship("Domain", back_populates="organization_rel")
    email_accounts = relationship("EmailAccount", back_populates="organization_rel")

    __table_args__ = (
        Index("idx_org_active", "active"),
        Index("idx_org_name", "name"),
        Index("idx_org_external_id", "external_id"),
        Index("idx_org_active_created", "active", "created_at"),
        Index("idx_org_name_active", "name", "active"),
    )

    def to_dict(self) -> dict:
        """Convert model instance to dictionary"""
        return {
            "id": self.id,
            "external_id": self.external_id,
            "name": self.name,
            "description": self.description,
            "active": self.active,
            "admin_email": self.admin_email,
            "admin_name": self.admin_name,
            "settings": self.settings,
            "rate_limits": self.rate_limits,
            "storage_quotas": self.storage_quotas,
            "webhook_urls": self.webhook_urls,
            "sending_profile": self.sending_profile,
            "quota_override": bool(self.quota_override),
            "quota_override_at": self.quota_override_at.isoformat()
            if self.quota_override_at
            else None,
            "quota_override_by": self.quota_override_by,
            "ai_entitled": bool(self.ai_entitled),
            "ai_plan": self.ai_plan,
            "ai_org_policy": self.ai_org_policy or "unset",
            "ai_monthly_quota": self.ai_monthly_quota,
            "ai_policy_updated_at": self.ai_policy_updated_at.isoformat()
            if self.ai_policy_updated_at
            else None,
            "ai_policy_updated_by": self.ai_policy_updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Domain Management
class Domain(Base):
    """Domain configuration table"""

    __tablename__ = "domains"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    domain = Column(String(255), nullable=False, unique=True, index=True)
    external_id = Column(
        String(255), nullable=True, unique=True, index=True
    )  # External system identifier
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    description = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    # Mail server configuration
    max_quota = Column(BigInteger, nullable=False, default=10737418240)  # 10GB default
    max_users = Column(Integer, nullable=False, default=1000)

    # DKIM settings
    dkim_enabled = Column(Boolean, nullable=False, default=True)
    dkim_selector = Column(String(100), nullable=False, default="default")

    # Rate limiting (inherits from organization but can override)
    rate_limits = Column(JSON, nullable=True)  # Domain-specific rate limits

    # Storage quotas (inherits from organization but can override)
    storage_quotas = Column(JSON, nullable=True)  # Domain-specific storage quotas

    # Domain-level storage usage tracking
    total_storage_used = Column(BigInteger, nullable=False, default=0)
    total_attachment_storage = Column(BigInteger, nullable=False, default=0)
    total_email_storage = Column(BigInteger, nullable=False, default=0)

    # Domain-level counters
    total_email_accounts = Column(Integer, nullable=False, default=0)
    total_emails = Column(BigInteger, nullable=False, default=0)
    total_attachments = Column(BigInteger, nullable=False, default=0)

    # Rate limiting usage (domain-level aggregates)
    rate_usage_data = Column(JSON, nullable=True)  # Domain-level rate usage statistics

    # Storage calculation tracking
    last_storage_calculation = Column(DateTime, nullable=True)
    storage_calculation_time_ms = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Relationships
    organization_rel = relationship("Organization", back_populates="domains")
    email_accounts = relationship("EmailAccount", back_populates="domain_rel")
    ssl_certificates = relationship("SSLCertificate", back_populates="domain_rel")
    dkim_keys = relationship("DKIMKey", back_populates="domain_rel")

    def to_dict(self) -> dict:
        """Convert model instance to dictionary"""
        return {
            "id": self.id,
            "domain": self.domain,
            "external_id": self.external_id,
            "organization_id": self.organization_id,
            "description": self.description,
            "active": self.active,
            "max_quota": self.max_quota,
            "max_users": self.max_users,
            "dkim_enabled": self.dkim_enabled,
            "dkim_selector": self.dkim_selector,
            "rate_limits": self.rate_limits,
            "storage_quotas": self.storage_quotas,
            "total_storage_used": self.total_storage_used,
            "total_attachment_storage": self.total_attachment_storage,
            "total_email_storage": self.total_email_storage,
            "total_email_accounts": self.total_email_accounts,
            "total_emails": self.total_emails,
            "total_attachments": self.total_attachments,
            "rate_usage_data": self.rate_usage_data,
            "last_storage_calculation": self.last_storage_calculation.isoformat()
            if self.last_storage_calculation
            else None,
            "storage_calculation_time_ms": self.storage_calculation_time_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_storage_usage_percentage(self) -> float:
        """Calculate domain storage usage percentage against max quota"""
        if self.max_quota <= 0:
            return 0.0
        return (self.total_storage_used / self.max_quota) * 100

    def is_storage_over_threshold(self, threshold_percentage: int = 80) -> bool:
        """Check if domain storage usage is over threshold"""
        return self.get_storage_usage_percentage() >= threshold_percentage

    def get_account_usage_percentage(self) -> float:
        """Calculate email account usage percentage against max users"""
        if self.max_users <= 0:
            return 0.0
        return (self.total_email_accounts / self.max_users) * 100

    __table_args__ = (
        Index("idx_domain_org_active", "organization_id", "active"),
        Index("idx_domain_name_org", "domain", "organization_id"),
        Index("idx_domain_active_created", "active", "created_at"),
        Index("idx_domain_org_updated", "organization_id", "updated_at"),
        Index("idx_domain_storage_used", "total_storage_used"),
        Index("idx_domain_storage_calc", "last_storage_calculation"),
        Index("idx_domain_external_id", "external_id"),
    )


# Email Account Management (replaces User table)
class EmailAccount(Base):
    """Email accounts table - replaces User table with better structure"""

    __tablename__ = "email_accounts"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    email = Column(String(255), nullable=False, unique=True, index=True)  # Full email address
    external_id = Column(
        String(255), nullable=True, unique=True, index=True
    )  # External system identifier
    local_part = Column(String(255), nullable=False, index=True)  # Part before @
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False, index=True)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)

    # Authentication
    password = Column(String(255), nullable=False)
    # Forced change (0021_mailbox_password_policy): while must_change_password
    # is set, the holder's webmail session is refused everywhere except the
    # password endpoint and sign-out. password_change_reason is one of
    # temporary | expired | admin_reset (enforced in code, not a DB enum).
    must_change_password = Column(Boolean, nullable=False, default=False)
    password_change_reason = Column(String(20), nullable=True)
    password_changed_at = Column(DateTime, nullable=True)
    # When /api/v1/mailbox/security/2fa/confirm last succeeded. totp_secrets
    # has no confirmation timestamp of its own (see the 0021 docstring).
    two_factor_confirmed_at = Column(DateTime, nullable=True)

    # Account details
    name = Column(String(255), nullable=True)
    # values_callable is required here: SQLAlchemy's default Enum column
    # stores/validates against the Python member NAME ("ACTIVE"), but every
    # raw-SQL write path in this codebase (mailboxes.py's legacy endpoints,
    # setup-first-user.sh, bootstrap.py) writes the lowercase MySQL enum
    # value ("active") directly, matching the DB column's real
    # enum('active','inactive','suspended') definition. Without this, any
    # ORM read of a row written that way raises "'active' is not among the
    # defined enum values" -- e.g. every mailbox list/detail endpoint,
    # for every account in the normal 'active' state.
    status = Column(
        SQLEnum(AccountStatus, values_callable=enum_values),
        nullable=False,
        default=AccountStatus.ACTIVE,
        index=True,
    )

    # Storage configuration and usage
    storage_quota = Column(BigInteger, nullable=False, default=1073741824)  # 1GB default
    storage_used = Column(BigInteger, nullable=False, default=0)
    attachment_storage_used = Column(BigInteger, nullable=False, default=0)
    email_storage_used = Column(BigInteger, nullable=False, default=0)

    # File counts
    total_files = Column(Integer, nullable=False, default=0)
    total_attachments = Column(Integer, nullable=False, default=0)
    total_emails = Column(Integer, nullable=False, default=0)

    # Rate limiting usage (real-time counters)
    rate_usage_data = Column(JSON, nullable=True)  # Current usage statistics

    # Account-specific limits (overrides domain/org defaults)
    rate_limits = Column(JSON, nullable=True)  # Account-specific rate limits
    storage_quotas = Column(JSON, nullable=True)  # Account-specific storage quotas

    # Mail settings
    forward_enabled = Column(Boolean, nullable=False, default=False)
    forward_destination = Column(String(255), nullable=True)
    vacation_enabled = Column(Boolean, nullable=False, default=False)
    vacation_message = Column(Text, nullable=True)

    # Billing (migration 0024). Laravel owns these values; the mail server
    # stores and enforces them. NULL quota means "use the organization's
    # ai_monthly_quota", which is every mailbox billing has never touched.
    ai_monthly_quota = Column(Integer, nullable=True)
    billing_tier = Column(String(16), nullable=True)

    # Activity tracking
    last_login = Column(DateTime, nullable=True)
    last_activity = Column(DateTime, nullable=True)

    # Storage tracking
    last_storage_calculation = Column(DateTime, nullable=True)
    storage_calculation_time_ms = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Relationships
    organization_rel = relationship("Organization", back_populates="email_accounts")
    domain_rel = relationship("Domain", back_populates="email_accounts")
    sessions = relationship("UserSession", back_populates="email_account_rel")

    __table_args__ = (
        Index("idx_email_domain_org", "domain_id", "organization_id"),
        Index("idx_email_status", "status"),
        Index("idx_email_storage_used", "storage_used"),
        Index("idx_email_last_activity", "last_activity"),
        Index("idx_email_org_status", "organization_id", "status"),
        Index("idx_email_org_created", "organization_id", "created_at"),
        Index("idx_email_local_domain", "local_part", "domain_id"),
        Index("idx_email_status_activity", "status", "last_activity"),
        Index("idx_email_org_activity", "organization_id", "last_activity"),
        Index("idx_email_external_id", "external_id"),
    )

    def to_dict(self) -> dict:
        """Convert model instance to dictionary"""
        return {
            "id": self.id,
            "email": self.email,
            "external_id": self.external_id,
            "local_part": self.local_part,
            "domain_id": self.domain_id,
            "organization_id": self.organization_id,
            "name": self.name,
            "status": self.status.value if self.status else None,
            "storage_quota": self.storage_quota,
            "storage_used": self.storage_used,
            "attachment_storage_used": self.attachment_storage_used,
            "email_storage_used": self.email_storage_used,
            "total_files": self.total_files,
            "total_attachments": self.total_attachments,
            "total_emails": self.total_emails,
            "rate_usage_data": self.rate_usage_data,
            "rate_limits": self.rate_limits,
            "storage_quotas": self.storage_quotas,
            "forward_enabled": self.forward_enabled,
            "forward_destination": self.forward_destination,
            "vacation_enabled": self.vacation_enabled,
            "vacation_message": self.vacation_message,
            "ai_monthly_quota": self.ai_monthly_quota,
            "billing_tier": self.billing_tier,
            "must_change_password": bool(self.must_change_password),
            "password_change_reason": self.password_change_reason,
            "password_changed_at": self.password_changed_at.isoformat()
            if self.password_changed_at
            else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "last_storage_calculation": self.last_storage_calculation.isoformat()
            if self.last_storage_calculation
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_storage_usage_percentage(self) -> float:
        """Calculate storage usage percentage"""
        if self.storage_quota <= 0:
            return 0.0
        return (self.storage_used / self.storage_quota) * 100

    def is_storage_over_threshold(self, threshold_percentage: int = 80) -> bool:
        """Check if storage usage is over threshold"""
        return self.get_storage_usage_percentage() >= threshold_percentage


class Alias(Base):
    """Email aliases table"""

    __tablename__ = "aliases"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    source = Column(String(255), nullable=False)
    destination = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class SmtpCredential(Base):
    """Domain-scoped SMTP credentials table -- distinct from a mailbox
    password. Checked by a protocol-scoped Dovecot passdb that falls
    through to email_accounts when a username isn't found here, so
    mailbox-based SMTP auth is unaffected. See
    01-mailyte-email-server/phase-10-smtp-credential-auth.md."""

    __tablename__ = "smtp_credentials"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False, index=True)
    username = Column(String(255), nullable=False, unique=True, index=True)
    password = Column(String(255), nullable=False)  # bcrypt-hashed, same as EmailAccount.password
    name = Column(String(255), nullable=True)
    prefix = Column(
        String(16), nullable=True
    )  # secret's first chars, for display after the one-time reveal
    created_by = Column(String(255), nullable=True)
    allowed_ips = Column(JSON, nullable=True)
    # Gate separate from the list itself, so IPs can be staged before enforcement is turned on.
    ip_allowlist_enabled = Column(Boolean, nullable=False, default=False)
    # 0 = the tracking injector skips pixel/link/List-Unsubscribe injection
    # for mail authenticated as this credential (campaign tools that do their
    # own tracking would otherwise get double-wrapped links). Suppression,
    # pacing and rate limits are unaffected.
    tracking_enabled = Column(Boolean, nullable=False, default=True)
    # 'marketing' = the tracking injector stamps X-Mailyte-Stream so this
    # credential's mail egresses the marketing IP (alembic 0027); read by the
    # injector's cached sasl_username lookup alongside tracking_enabled.
    stream = Column(String(16), nullable=False, default="transactional")
    # Enforced inside the Dovecot passdb query -- not by any scheduler.
    expires_at = Column(DateTime, nullable=True)
    # Per-key outbound caps; NULL inherits the org's limits (K2 rate limiter).
    hourly_limit = Column(Integer, nullable=True)
    daily_limit = Column(Integer, nullable=True)
    # Written by the log ingestor, never by the auth path (row-lock serialization).
    last_used_at = Column(DateTime, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "domain_id": self.domain_id,
            "username": self.username,
            "name": self.name,
            "prefix": self.prefix,
            "created_by": self.created_by,
            "allowed_ips": self.allowed_ips,
            "ip_allowlist_enabled": self.ip_allowlist_enabled,
            "tracking_enabled": self.tracking_enabled,
            "stream": self.stream,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "hourly_limit": self.hourly_limit,
            "daily_limit": self.daily_limit,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SmtpCredentialEvent(Base):
    """Audit trail for SMTP API keys (00-PRD-smtp-api-keys section 6.7).

    Deliberately FK-free: an audit row must survive the deletion of the
    credential -- and the organization -- it describes, so the ids are plain
    columns and the username is denormalized for display after deletion.
    Rows are append-only; nothing updates or deletes them."""

    __tablename__ = "smtp_credential_events"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    credential_id = Column(String(26), nullable=False, index=True)
    organization_id = Column(String(26), nullable=False, index=True)
    username = Column(String(255), nullable=False)
    event = Column(
        String(32), nullable=False
    )  # created/rotated/revoked/enabled/updated/deleted/suspended
    actor = Column(String(255), nullable=True)
    source_ip = Column(String(45), nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "credential_id": self.credential_id,
            "organization_id": self.organization_id,
            "username": self.username,
            "event": self.event,
            "actor": self.actor,
            "source_ip": self.source_ip,
            "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
