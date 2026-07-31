#!/usr/bin/env python3
"""
Mailyte Mail Server - Database Models Entry Point

This file serves as the main entry point for all database models.
All models are now organized in separate files within the models package
for better maintainability while maintaining backward compatibility.

Import this file to access all models as before:
    from database.models import Organization, Domain, EmailAccount, etc.
"""

# Import everything from the models package
from .models import *

# Add documentation string
__doc__ = """
Mailyte Mail Server - Centralized SQLAlchemy Models

This module provides all database models for the entire mail server system:
- Organization and email account management
- Email tracking and analytics  
- SSL certificates and API keys
- Mail processing and queue management
- Webhooks and alerts
- Analytics and statistics

The models are designed to work with Alembic for migration management
and are shared across all microservices in the system.

Models are now organized in separate files for better maintainability:
- core.py: Organization, Domain, EmailAccount, Alias
- tracking.py: EmailTracking, TrackingStatistics
- mail.py: MailQueue, MailLog
- certificates.py: SSLCertificate, DKIMKey
- authentication.py: APIKey, UserSession
- webhooks.py: WebhookURL, WebhookDeliveryLog
- alerts.py: Alert, UsageHistory
- ai.py: AITransaction
- reputation.py: EmailSuppression, DomainReputation, FeedbackLoop
- analytics.py: AnalyticsData
- system.py: SystemConfig, HealthCheck, ServiceMetrics
- enums.py: All enum definitions
"""
