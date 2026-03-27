
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
from .core import Organization, Domain, EmailAccount, Alias
from .mail import MailQueue, MailLog
from .tracking import EmailTracking, TrackingStatistics
from .webhooks import WebhookURL, WebhookDeliveryLog
from .certificates import SSLCertificate, DKIMKey
from .authentication import APIKey, UserSession
from .system import SystemConfig, HealthCheck, ServiceMetrics
from .enums import (
    AccountStatus, CertificateStatus, MailStatus, EventType,
    WebhookDeliveryStatus, AlertLevel, LimitType
)

# Export all models
__all__ = [
    'Base',
    # Core models
    'Organization', 'Domain', 'EmailAccount', 'Alias',
    # Mail processing models
    'MailQueue', 'MailLog',
    # Tracking models
    'EmailTracking', 'TrackingStatistics',
    # Webhook models
    'WebhookURL', 'WebhookDeliveryLog',
    # Certificate models
    'SSLCertificate', 'DKIMKey',
    # Authentication models
    'APIKey', 'UserSession',
    # System models
    'SystemConfig', 'HealthCheck', 'ServiceMetrics',
    # Enums
    'AccountStatus', 'CertificateStatus', 'MailStatus', 'EventType',
    'WebhookDeliveryStatus', 'AlertLevel', 'LimitType',
]
