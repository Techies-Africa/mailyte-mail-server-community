#!/usr/bin/env python3
"""
Mailyte Mail Server Community Edition - Database Models Package

This package contains all database models organized by functionality.
This file serves as the main entry point.
"""

from sqlalchemy.ext.declarative import declarative_base

# Create the shared Base
Base = declarative_base()

# Import all models to make them available
from .authentication import APIKey, UserSession
from .certificates import DKIMKey, SSLCertificate
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
from .mail import MailLog, MailQueue
from .system import HealthCheck, ServiceMetrics, SystemConfig
from .tracking import EmailTracking, TrackingStatistics
from .webhooks import WebhookDeliveryLog, WebhookURL

# Export all models
__all__ = [
    "Base",
    # Core models
    "Organization",
    "Domain",
    "EmailAccount",
    "Alias",
    "SmtpCredential",
    "SmtpCredentialEvent",
    # Mail processing models
    "MailQueue",
    "MailLog",
    # Tracking models
    "EmailTracking",
    "TrackingStatistics",
    # Webhook models
    "WebhookURL",
    "WebhookDeliveryLog",
    # Certificate models
    "SSLCertificate",
    "DKIMKey",
    # Authentication models
    "APIKey",
    "UserSession",
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
]
