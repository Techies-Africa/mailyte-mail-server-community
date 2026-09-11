#!/usr/bin/env python3
"""
Authentication Models - API keys and user sessions
"""

from sqlalchemy import (
    CHAR,
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
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


# API Authentication
class APIKey(Base):
    """API keys for authentication"""

    __tablename__ = "api_keys"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    key_id = Column(String(100), nullable=False, unique=True, index=True)
    key_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    permissions = Column(JSON, nullable=True)  # JSON array of permissions
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    rate_limit = Column(Integer, nullable=True)  # Requests per minute
    last_used = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    expires_at = Column(DateTime, nullable=True, index=True)

    # Additional security fields
    ip_whitelist = Column(JSON, nullable=True)  # Array of allowed IPs
    usage_count = Column(BigInteger, nullable=False, default=0)

    __table_args__ = (Index("idx_api_key_active", "key_id", "active"),)


# Session Management
class UserSession(Base):
    """User session management"""

    __tablename__ = "user_sessions"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    email_account_id = Column(String(26), ForeignKey("email_accounts.id"), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    last_activity = Column(DateTime, nullable=False, default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True)

    # Relationships
    email_account_rel = relationship("EmailAccount", back_populates="sessions")


# Dashboard/control-plane users (phase-03) — distinct from EmailAccount
# (mailbox users) and from UserSession above (webmail sessions, keyed on
# email_account_id). These are organization admins who log into mailyte-web
# in community mode, which talks to this API directly with no Laravel
# in front of it (ADR-001).
class User(Base):
    """Dashboard user — one organization admin account (CE ships a single role)."""

    __tablename__ = "users"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="admin")
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("uq_users_org_email", "organization_id", "email", unique=True),)


# Browser sessions for User (dashboard) logins. Named web_sessions, not
# user_sessions — that name is already taken by UserSession above (webmail
# sessions for EmailAccount). See migration 012_web_sessions.sql for why.
class WebSession(Base):
    """Browser session for a dashboard User, resolved from the mailyte_session cookie."""

    __tablename__ = "web_sessions"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    user_id = Column(String(26), ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(String(26), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    absolute_expiry = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())


class MailboxSession(Base):
    """Webmail session for a mailbox holder, resolved from the
    mailyte_mailbox_session cookie.

    The third credential tier, alongside WebSession (dashboard user) and the
    operator sessions in platform_auth. A mailbox holder has no `users` row --
    they are an `email_accounts` row authenticated by Dovecot -- which is why
    this cannot reuse WebSession. See ADR-002 and migration
    0015_mailbox_sessions for the full reasoning.
    """

    __tablename__ = "mailbox_sessions"

    # CHAR(26), not String(26). The older session models here declare ULIDs as
    # String while their tables are really CHAR -- harmless until something
    # generates DDL from them, at which point the VARCHAR that String emits
    # cannot carry a foreign key to email_accounts.id (CHAR(26)); MySQL rejects
    # it with errno 3780. 0015_mailbox_sessions hit exactly that. Declaring the
    # true type keeps the model and the table telling the same story.
    id = Column(CHAR(26), primary_key=True, default=generate_ulid)
    email_account_id = Column(
        CHAR(26),
        ForeignKey("email_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id = Column(CHAR(26), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    absolute_expiry = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    # Phase 4: a session issued after the password step but before TOTP has
    # mfa_satisfied=0 and grants nothing. Mirrors platform_auth's operator
    # pending-session shape rather than inventing a second convention.
    mfa_satisfied = Column(Boolean, nullable=False, default=True)
    # 0023_mailbox_password_policy: the normalised X-Client-Platform sent at
    # sign-in (web|ios|android|macos|windows|linux, NULL if absent), and this
    # session's own idle window. The sliding-expiry update reads the stored
    # window rather than a module constant, which is what lets a native
    # client's 30-day idle survive its first request.
    client_platform = Column(String(20), nullable=True)
    idle_timeout_seconds = Column(Integer, nullable=False, default=28800)
    created_at = Column(DateTime, nullable=False, default=func.now())
