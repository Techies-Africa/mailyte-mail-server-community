
#!/usr/bin/env python3
"""
Database Enums - Type safety enums for all models
"""

from enum import Enum

class AccountStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"

class CertificateStatus(Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PENDING = "pending"

class MailStatus(Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    REJECTED = "rejected"
    DEFERRED = "deferred"

class EventType(Enum):
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    UNSUBSCRIBED = "unsubscribed"

class WebhookDeliveryStatus(Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    ABANDONED = "abandoned"

class AlertLevel(Enum):
    WARNING = "warning"
    CRITICAL = "critical"
    EXCEEDED = "exceeded"

class LimitType(Enum):
    RATE_LIMIT = "rate_limit"
    STORAGE_QUOTA = "storage_quota"
