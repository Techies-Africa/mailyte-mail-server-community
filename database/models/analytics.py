#!/usr/bin/env python3
"""
Analytics Models - Analytics data aggregation
"""

from sqlalchemy import DECIMAL, JSON, Column, DateTime, ForeignKey, Index, String
from sqlalchemy.sql import func

from shared.ulid_utils import generate_ulid

from . import Base


# Analytics and Statistics
class AnalyticsData(Base):
    """Analytics data aggregation"""

    __tablename__ = "analytics_data"

    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(String(26), ForeignKey("organizations.id"), nullable=False, index=True)
    metric_name = Column(String(100), nullable=False, index=True)
    metric_value = Column(DECIMAL(15, 4), nullable=False)
    dimensions = Column(JSON, nullable=True)  # Additional grouping dimensions
    timestamp = Column(DateTime, nullable=False, default=func.now(), index=True)
    period = Column(String(20), nullable=False, index=True)  # hour, day, week, month

    __table_args__ = (
        Index("idx_analytics_org_metric", "organization_id", "metric_name", "timestamp"),
    )
