#!/usr/bin/env python3
"""
Core Models - Organization, Domain, EmailAccount, and Alias management
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    Boolean,
    Index,
    BigInteger,
    Float,
    ForeignKey,
    JSON,
    Enum as SQLEnum,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from . import Base
from .enums import AccountStatus
from shared.ulid_utils import generate_ulid


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

    # Account details
    name = Column(String(255), nullable=True)
    status = Column(
        SQLEnum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE, index=True
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
