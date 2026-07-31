#!/usr/bin/env python3
"""
Certificate Models - SSL certificates and DKIM keys
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base
from .enums import CertificateStatus


# SSL Certificate Management
class SSLCertificate(Base):
    """SSL certificate management table"""

    __tablename__ = "ssl_certificates"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False)
    certificate_path = Column(String(500), nullable=False)
    private_key_path = Column(String(500), nullable=False)
    chain_path = Column(String(500), nullable=True)
    status = Column(
        SQLEnum(CertificateStatus), nullable=False, default=CertificateStatus.ACTIVE, index=True
    )
    issuer = Column(String(255), nullable=True)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True, index=True)
    auto_renew = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    last_renewed = Column(DateTime, nullable=True)

    # Certificate details
    fingerprint = Column(String(255), nullable=True)
    algorithm = Column(String(50), nullable=True)
    key_size = Column(Integer, nullable=True)

    # Relationships
    domain_rel = relationship("Domain", back_populates="ssl_certificates")

    __table_args__ = (Index("idx_ssl_expiry", "valid_until", "status"),)


# DKIM Keys Management
class DKIMKey(Base):
    """DKIM keys for domains"""

    __tablename__ = "dkim_keys"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    domain_id = Column(String(26), ForeignKey("domains.id"), nullable=False)
    selector = Column(String(100), nullable=False, default="default")
    private_key = Column(Text, nullable=False)
    public_key = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Relationships
    domain_rel = relationship("Domain", back_populates="dkim_keys")

    __table_args__ = (Index("idx_dkim_domain_selector", "domain_id", "selector"),)
