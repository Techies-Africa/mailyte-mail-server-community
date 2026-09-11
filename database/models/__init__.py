#!/usr/bin/env python3
"""
Enterprise Mail Server - Database Models Package

This package contains all database models organized by functionality.
This file serves as the main entry point to maintain backward compatibility.
"""

from sqlalchemy.ext.declarative import declarative_base

# Create the shared Base
Base = declarative_base()

# Import all models to make them available
from .ai import AITransaction, MailboxAiConsent, MailboxAiConsentEvent
from .alerts import Alert, UsageHistory
from .analytics import AnalyticsData
from .authentication import APIKey, User, UserSession, WebSession
from .certificates import DKIMKey, SSLCertificate

# Collaboration models (migration 003 + 004)
from .collaboration import (
    DistributionGroup,
    DistributionGroupMember,
    Quarantine,
    SharedMailboxMember,
    TransportRule,
)
from .core import Alias, Domain, EmailAccount, Organization, SmtpCredential, SmtpCredentialEvent
from .enums import (
    AccountStatus,
    AlertLevel,
    CertificateStatus,
    EventType,
    LimitType,
    MailStatus,
    WebhookDeliveryStatus,
)

# Infrastructure models (migration 005 + 006 + 008)
from .infrastructure import (
    ConsentRecord,
    DataErasureRequest,
    DataExportRequest,
    JMAPState,
    KafkaDeadLetter,
    MigrationError,
    MigrationJob,
    OAuthClient,
    OAuthGrant,
)
from .mail import MailLog, MailQueue
from .reputation import DomainReputation, EmailSuppression, FeedbackLoop

# Security models (migration 002 + 006)
from .security import (
    AuditLog,
    DLPPolicy,
    DLPViolation,
    FailedAuthAttempt,
    GeoPolicy,
    IPAccessRule,
    IPReputation,
    TOTPSecret,
)
from .system import HealthCheck, ServiceMetrics, SystemConfig
from .tracking import EmailTracking, TrackingStatistics
from .webhooks import WebhookDeliveryLog, WebhookURL

# Export all models for backward compatibility
__all__ = [
    "Base",
    # Core models
    "Organization",
    "Domain",
    "EmailAccount",
    "Alias",
    "SmtpCredential",
    "SmtpCredentialEvent",
    # Tracking models
    "EmailTracking",
    "TrackingStatistics",
    # Mail processing models
    "MailQueue",
    "MailLog",
    # Security models
    "SSLCertificate",
    "DKIMKey",
    # Authentication models
    "APIKey",
    "UserSession",
    "User",
    "WebSession",
    # Webhook models
    "WebhookURL",
    "WebhookDeliveryLog",
    # Alert models
    "Alert",
    "UsageHistory",
    # AI models
    "AITransaction",
    "MailboxAiConsent",
    "MailboxAiConsentEvent",
    # Reputation models
    "EmailSuppression",
    "DomainReputation",
    "FeedbackLoop",
    # Analytics models
    "AnalyticsData",
    # System models
    "SystemConfig",
    "HealthCheck",
    "ServiceMetrics",
    # Enums
    "AccountStatus",
    "CertificateStatus",
    "MailStatus",
    "EventType",
    "WebhookDeliveryStatus",
    "AlertLevel",
    "LimitType",
    # Security models
    "IPAccessRule",
    "AuditLog",
    "FailedAuthAttempt",
    "IPReputation",
    "DLPPolicy",
    "DLPViolation",
    "TOTPSecret",
    "GeoPolicy",
    # Collaboration models
    "SharedMailboxMember",
    "DistributionGroup",
    "DistributionGroupMember",
    "TransportRule",
    "Quarantine",
    # Infrastructure models
    "JMAPState",
    "MigrationJob",
    "MigrationError",
    "KafkaDeadLetter",
    "ConsentRecord",
    "DataExportRequest",
    "DataErasureRequest",
    "OAuthClient",
    "OAuthGrant",
]
