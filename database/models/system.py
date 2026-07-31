#!/usr/bin/env python3
"""
System Models - System configuration and health monitoring
"""

from sqlalchemy import JSON, Column, DateTime, Float, Index, String, Text
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


# System Configuration
class SystemConfig(Base):
    """System-wide configuration settings"""

    __tablename__ = "system_config"

    key = Column(String(255), primary_key=True)
    value = Column(JSON, nullable=True)  # Use JSON for complex values
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


# Health Monitoring
class HealthCheck(Base):
    """System health check results"""

    __tablename__ = "health_checks"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    service_name = Column(String(100), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)  # healthy, degraded, down
    response_time = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=func.now(), index=True)

    __table_args__ = (Index("idx_health_service_time", "service_name", "timestamp"),)


class ServiceMetrics(Base):
    """Service performance metrics"""

    __tablename__ = "service_metrics"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    service_name = Column(String(100), nullable=False, index=True)
    metric_name = Column(String(100), nullable=False, index=True)
    metric_value = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=func.now(), index=True)

    __table_args__ = (
        Index("idx_metrics_service_metric", "service_name", "metric_name", "timestamp"),
    )
