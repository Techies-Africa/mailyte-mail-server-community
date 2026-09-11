#!/usr/bin/env python3
"""
Collaboration Models — shared mailboxes, distribution groups, transport
rules, and message quarantine.
"""

from sqlalchemy import (
    DECIMAL,
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
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


class SharedMailboxMember(Base):
    """Maps users to shared mailboxes with granular permissions."""

    __tablename__ = "shared_mailbox_members"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    shared_mailbox_id = Column(
        String(26), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False
    )
    email_account_id = Column(
        String(26), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False
    )
    permission = Column(
        SQLEnum("full_access", "send_as", "send_on_behalf", "read_only"),
        nullable=False,
        default="read_only",
    )
    granted_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("shared_mailbox_id", "email_account_id", name="uk_shared_member"),
        Index("idx_shared_mailbox", "shared_mailbox_id"),
        Index("idx_shared_member", "email_account_id"),
        Index("idx_shared_permission", "permission"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "shared_mailbox_id": self.shared_mailbox_id,
            "email_account_id": self.email_account_id,
            "permission": self.permission,
            "granted_by": self.granted_by,
        }


class DistributionGroup(Base):
    """Mailing list / distribution group."""

    __tablename__ = "distribution_groups"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email = Column(String(255), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    group_type = Column(
        SQLEnum("distribution", "security", "dynamic"), nullable=False, default="distribution"
    )
    moderation_enabled = Column(Boolean, nullable=False, default=False)
    external_delivery = Column(Boolean, nullable=False, default=True)
    max_message_size = Column(BigInteger, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    members = relationship(
        "DistributionGroupMember", back_populates="group", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_group_org", "organization_id"),
        Index("idx_group_active", "active"),
        Index("idx_group_type", "group_type"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "email": self.email,
            "name": self.name,
            "group_type": self.group_type,
            "active": self.active,
        }


class DistributionGroupMember(Base):
    """Members of a distribution group (internal or external)."""

    __tablename__ = "distribution_group_members"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    group_id = Column(
        String(26), ForeignKey("distribution_groups.id", ondelete="CASCADE"), nullable=False
    )
    email_account_id = Column(
        String(26), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    external_email = Column(String(255), nullable=True)
    role = Column(SQLEnum("member", "owner", "moderator"), nullable=False, default="member")
    created_at = Column(DateTime, nullable=False, default=func.now())

    group = relationship("DistributionGroup", back_populates="members")

    __table_args__ = (
        Index("idx_group_member_group", "group_id"),
        Index("idx_group_member_account", "email_account_id"),
        Index("idx_group_member_role", "role"),
    )


class TransportRule(Base):
    """Organization-level mail flow rules (Exchange Transport Rules equivalent)."""

    __tablename__ = "transport_rules"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    direction = Column(SQLEnum("inbound", "outbound", "both"), nullable=False, default="both")
    conditions = Column(JSON, nullable=False, comment="[{field, operator, value}]")
    condition_logic = Column(SQLEnum("all", "any"), nullable=False, default="all")
    actions = Column(JSON, nullable=False, comment="[{type, params}]")
    priority = Column(Integer, nullable=False, default=100)
    enabled = Column(Boolean, nullable=False, default=True)
    hit_count = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_transport_org", "organization_id"),
        Index("idx_transport_enabled", "enabled"),
        Index("idx_transport_priority", "priority"),
        Index("idx_transport_org_enabled", "organization_id", "enabled", "priority"),
        Index("idx_transport_direction", "direction"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "organization_id": self.organization_id,
            "direction": self.direction,
            "conditions": self.conditions,
            "condition_logic": self.condition_logic,
            "actions": self.actions,
            "priority": self.priority,
            "enabled": self.enabled,
            "hit_count": self.hit_count,
        }


class Quarantine(Base):
    """Messages held pending admin review."""

    __tablename__ = "quarantine"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(
        String(26), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    message_id = Column(String(255), nullable=True)
    queue_id = Column(String(100), nullable=True)
    sender = Column(String(255), nullable=False)
    recipient = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=True)
    reason = Column(
        SQLEnum("spam", "virus", "policy", "transport_rule", "admin"),
        nullable=False,
        default="spam",
    )
    spam_score = Column(DECIMAL(6, 2), nullable=True)
    virus_name = Column(String(255), nullable=True)
    rule_id = Column(
        String(26), ForeignKey("transport_rules.id", ondelete="SET NULL"), nullable=True
    )
    headers = Column(JSON, nullable=True)
    storage_key = Column(String(500), nullable=True)
    status = Column(
        SQLEnum("quarantined", "released", "deleted", "expired"),
        nullable=False,
        default="quarantined",
    )
    released_by = Column(String(255), nullable=True)
    released_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("idx_quarantine_org", "organization_id"),
        Index("idx_quarantine_sender", "sender"),
        Index("idx_quarantine_recipient", "recipient"),
        Index("idx_quarantine_status", "status"),
        Index("idx_quarantine_reason", "reason"),
        Index("idx_quarantine_expires", "expires_at"),
        Index("idx_quarantine_org_status", "organization_id", "status", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "message_id": self.message_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "subject": self.subject,
            "reason": self.reason,
            "spam_score": float(self.spam_score) if self.spam_score else None,
            "status": self.status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
