
#!/usr/bin/env python3
"""
Authentication Models - API keys and user sessions
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Boolean,
    Index, BigInteger, ForeignKey, JSON
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from . import Base
from shared.ulid_utils import generate_ulid

# API Authentication
class APIKey(Base):
    """API keys for authentication"""
    __tablename__ = 'api_keys'

    id = Column(String(26), primary_key=True, default=generate_ulid)
    key_id = Column(String(100), nullable=False, unique=True, index=True)
    key_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    permissions = Column(JSON, nullable=True)  # JSON array of permissions
    organization_id = Column(String(26), ForeignKey('organizations.id'), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    rate_limit = Column(Integer, nullable=True)  # Requests per minute
    last_used = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    expires_at = Column(DateTime, nullable=True, index=True)

    # Additional security fields
    ip_whitelist = Column(JSON, nullable=True)  # Array of allowed IPs
    usage_count = Column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index('idx_api_key_active', 'key_id', 'active'),
    )

# Session Management
class UserSession(Base):
    """User session management"""
    __tablename__ = 'user_sessions'

    id = Column(String(26), primary_key=True, default=generate_ulid)
    email_account_id = Column(String(26), ForeignKey('email_accounts.id'), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    last_activity = Column(DateTime, nullable=False, default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True)

    # Relationships
    email_account_rel = relationship("EmailAccount", back_populates="sessions")
