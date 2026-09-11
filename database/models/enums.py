#!/usr/bin/env python3
"""
Database Enums - Type safety enums for all models
"""

from enum import Enum


def enum_values(enum_cls: type[Enum]) -> list[str]:
    """`values_callable` for every SQLEnum column built from these classes.

    SQLAlchemy's default Enum column stores and validates against the Python
    member NAME ("PENDING"), but every MySQL column here is declared with the
    lowercase VALUES (enum('active','expired','revoked','pending')), and every
    raw-SQL write path in this codebase writes those values directly. Without
    this callable, any ORM read of such a row raises "'pending' is not among
    the defined enum values" -- and because SQLAlchemy loads relationships to
    cascade, a single unreadable row can break an unrelated operation: a
    'pending' ssl_certificates row made its domain undeletable, surfacing only
    as an opaque 500 (found live 2026-09-08).

    Every enum class in this module defines .value as the exact string stored
    in its column, so this is the correct mapping for all of them. Any new
    SQLEnum(SomeEnumClass) column must pass it too.
    """
    return [member.value for member in enum_cls]


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
