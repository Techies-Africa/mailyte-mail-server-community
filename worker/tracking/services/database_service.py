#!/usr/bin/env python3
"""
Database Service for Email Tracking

This service handles all database operations for the email tracking system using the
updated organization/domain/email account structure:
- Tracking event logging (opens, clicks, deliveries) with organization hierarchy
- Configuration management per organization, domain, and email account
- Analytics data aggregation with multi-tenant support
- Database connection pooling and health monitoring
- Backward compatibility with existing tracking data

Key Changes from Legacy Structure:
- Uses organization_id, domain_id hierarchy instead of tenant_id
- Maintains compatibility with existing tracking_data format
- Supports both legacy and new model queries
- Preserves all existing tracking functionality

Supports multi-tenant architecture with per-organization data isolation.
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, create_engine, desc, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool
from user_agents import parse

from config import config_manager

# Add project root to Python path for database models
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from database.models import EmailTracking

logger = logging.getLogger(__name__)


class DatabaseService:
    """
    Database service for handling all tracking-related database operations using SQLAlchemy.

    This service manages:
    - SQLAlchemy engine and session management
    - Connection pooling with configurable parameters
    - Tracking event storage with ORM
    - Statistics queries with optimized SQL
    - Data cleanup and retention policies
    """

    def __init__(self):
        """Initialize the database service with SQLAlchemy engine and session factory."""
        self.config = config_manager.config
        self.database_url = config_manager.get_database_url()

        # Create SQLAlchemy engine with connection pooling
        try:
            self.engine = create_engine(
                self.database_url,
                poolclass=QueuePool,
                pool_size=self.config.connection_pool_size,
                max_overflow=self.config.connection_pool_size * 2,
                pool_pre_ping=True,  # Validate connections before use
                pool_recycle=3600,  # Recycle connections every hour
                echo=False,  # Set to True for SQL logging during development
                echo_pool=False,
            )

            # Create session factory
            self.SessionLocal = sessionmaker(bind=self.engine)

            # Note: Tables are now managed by Alembic migrations
            # Use 'python migrate.py upgrade' to create/update tables

            logger.info(
                f"SQLAlchemy engine created with pool size {self.config.connection_pool_size}"
            )
        except Exception as e:
            logger.error(f"Failed to create SQLAlchemy engine: {e}")
            raise

        # Thread pool for async operations
        self.executor = ThreadPoolExecutor(max_workers=10)

    @contextmanager
    def get_session(self) -> Session:
        """
        Context manager for database sessions.

        Provides automatic session cleanup and error handling.

        Yields:
            Session: SQLAlchemy database session
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()

    def execute_query(self, query: str, params: tuple = (), fetch_results: bool = True):
        """Raw %s-placeholder SQL passthrough for services (SuppressionService)
        that build their own queries directly against the schema instead of
        through this class's SQLAlchemy ORM models. This method didn't exist
        at all before -- every caller was hitting AttributeError, silently
        swallowed by their own broad try/except. Uses the engine's raw
        pymysql connection (not a SQLAlchemy Session) so those existing
        %s-style queries work completely unchanged -- SQLAlchemy's own
        text() expects :name-style bound params, a different paramstyle.
        """
        import pymysql

        conn = self.engine.raw_connection()
        try:
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute(query, params)
            if fetch_results:
                results = cursor.fetchall()
            else:
                results = cursor.rowcount
                conn.commit()
            cursor.close()
            return results
        finally:
            conn.close()

    def test_connection(self) -> bool:
        """
        Test database connectivity.

        Returns:
            bool: True if connection is successful, False otherwise
        """
        try:
            with self.get_session() as session:
                from sqlalchemy import text

                session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False

    def log_tracking_event(
        self,
        event_type: str,
        tracking_data: dict[str, str],
        request_info: dict[str, Any],
        additional_data: dict[str, Any] = None,
    ) -> bool:
        """
        Log a tracking event to the database using SQLAlchemy ORM with organization hierarchy.

        This method maintains backward compatibility with existing tracking data format
        while supporting the new organization/domain/email account structure.

        Args:
            event_type: Type of tracking event ('opened', 'clicked', etc.)
            tracking_data: Decoded tracking information containing:
                          - email_id: Unique identifier for the email
                          - recipient: Email address of recipient
                          - tenant_id: Legacy field (mapped to organization_id)
                          - domain_id: Domain identifier in new structure
            request_info: Request metadata (IP, user agent, etc.)
            additional_data: Optional additional event data

        Returns:
            bool: True if logging succeeded, False otherwise

        Note: The tracking_data format remains unchanged to maintain compatibility
              with existing tracking pixel/link generation code.
        """
        if self.config.async_logging:
            # Submit to thread pool for async processing
            self.executor.submit(
                self._log_tracking_event_sync,
                event_type,
                tracking_data,
                request_info,
                additional_data,
            )
            return True
        else:
            return self._log_tracking_event_sync(
                event_type, tracking_data, request_info, additional_data
            )

    def _log_tracking_event_sync(
        self,
        event_type: str,
        tracking_data: dict[str, str],
        request_info: dict[str, Any],
        additional_data: dict[str, Any] = None,
    ) -> bool:
        """
        Synchronously log a tracking event to the database using SQLAlchemy ORM.

        Args:
            event_type: Type of tracking event
            tracking_data: Decoded tracking information
            request_info: Request metadata
            additional_data: Optional additional event data

        Returns:
            bool: True if logging succeeded, False otherwise
        """
        try:
            with self.get_session() as session:
                # Parse user agent if device tracking is enabled
                device_info = {}
                if self.config.track_device_info and request_info.get("user_agent"):
                    try:
                        user_agent = parse(request_info["user_agent"])
                        device_info = {
                            "device_type": f"{user_agent.device.family} {user_agent.device.model}".strip()
                            or "Unknown",
                            "browser": f"{user_agent.browser.family} {user_agent.browser.version_string}".strip()
                            or "Unknown",
                            "operating_system": f"{user_agent.os.family} {user_agent.os.version_string}".strip()
                            or "Unknown",
                        }
                    except Exception as e:
                        logger.warning(f"Failed to parse user agent: {e}")
                        device_info = {
                            "device_type": "Unknown",
                            "browser": "Unknown",
                            "operating_system": "Unknown",
                        }

                # Create tracking event record using the new organization structure
                # Note: tenant_id from tracking_data maps to organization_id in the new structure
                # This maintains backward compatibility with existing tracking URLs
                tracking_event = EmailTracking(
                    email_id=tracking_data["email_id"],
                    recipient=tracking_data["recipient"],
                    # The model's real column is organization_id -- this was
                    # passing the literal kwarg name `tenant_id`, which
                    # EmailTracking's constructor doesn't have, throwing on
                    # every single call (confirmed live: silently swallowed
                    # by this method's own except below, so it looked like
                    # tracking was working while nothing was ever written).
                    organization_id=tracking_data["tenant_id"],
                    domain_id=tracking_data["domain_id"],  # References domains table
                    event_type=event_type,
                    timestamp=request_info["timestamp"],
                    user_agent=request_info.get("user_agent", "")
                    if self.config.track_user_agent
                    else "",
                    ip_address=request_info.get("ip_address", "")
                    if self.config.track_ip_address
                    else "",
                    device_type=device_info.get("device_type", "")
                    if self.config.track_device_info
                    else "",
                    browser=device_info.get("browser", "") if self.config.track_device_info else "",
                    operating_system=device_info.get("operating_system", "")
                    if self.config.track_device_info
                    else "",
                    country=request_info.get("country", "")
                    if self.config.track_geolocation
                    else "",
                    region=request_info.get("region", "") if self.config.track_geolocation else "",
                    city=request_info.get("city", "") if self.config.track_geolocation else "",
                    referer=request_info.get("referer", "") if self.config.track_referrer else "",
                    accept_language=request_info.get("accept_language", ""),
                    additional_data=json.dumps(additional_data) if additional_data else None,
                )

                session.add(tracking_event)
                session.commit()

                logger.debug(
                    f"Tracking event logged: {event_type} for email {tracking_data['email_id']}"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to log tracking event: {e}")
            return False

    def get_tracking_stats(self, email_id: str) -> dict[str, Any]:
        """
        Get comprehensive tracking statistics for a specific email using SQLAlchemy queries.

        Args:
            email_id: Email identifier to get stats for

        Returns:
            Dict[str, Any]: Comprehensive tracking statistics
        """
        try:
            with self.get_session() as session:
                # Get event type statistics
                stats_query = (
                    session.query(
                        EmailTracking.event_type,
                        func.count(EmailTracking.id).label("total_count"),
                        func.count(func.distinct(EmailTracking.recipient)).label("unique_count"),
                        func.count(func.distinct(EmailTracking.ip_address)).label("unique_ips"),
                        func.min(EmailTracking.timestamp).label("first_event"),
                        func.max(EmailTracking.timestamp).label("last_event"),
                    )
                    .filter(EmailTracking.email_id == email_id)
                    .group_by(EmailTracking.event_type)
                )

                stats = {}
                for row in stats_query.all():
                    stats[row.event_type] = {
                        "total": row.total_count,
                        "unique": row.unique_count,
                        "unique_ips": row.unique_ips,
                        "first_event": row.first_event.isoformat() if row.first_event else None,
                        "last_event": row.last_event.isoformat() if row.last_event else None,
                    }

                # Get device and browser breakdown
                device_query = (
                    session.query(
                        EmailTracking.device_type,
                        EmailTracking.browser,
                        EmailTracking.operating_system,
                        func.count().label("count"),
                    )
                    .filter(
                        and_(
                            EmailTracking.email_id == email_id, EmailTracking.event_type == "opened"
                        )
                    )
                    .group_by(
                        EmailTracking.device_type,
                        EmailTracking.browser,
                        EmailTracking.operating_system,
                    )
                    .order_by(desc("count"))
                    .limit(10)
                )

                device_breakdown = [
                    {
                        "device_type": row.device_type,
                        "browser": row.browser,
                        "operating_system": row.operating_system,
                        "count": row.count,
                    }
                    for row in device_query.all()
                ]

                # Get geographic breakdown
                geo_query = (
                    session.query(
                        EmailTracking.country,
                        EmailTracking.region,
                        EmailTracking.city,
                        func.count().label("count"),
                    )
                    .filter(EmailTracking.email_id == email_id)
                    .group_by(EmailTracking.country, EmailTracking.region, EmailTracking.city)
                    .order_by(desc("count"))
                    .limit(20)
                )

                geographic_breakdown = [
                    {
                        "country": row.country,
                        "region": row.region,
                        "city": row.city,
                        "count": row.count,
                    }
                    for row in geo_query.all()
                ]

                # Get recent event timeline
                timeline_query = (
                    session.query(EmailTracking)
                    .filter(EmailTracking.email_id == email_id)
                    .order_by(desc(EmailTracking.timestamp))
                    .limit(100)
                )

                timeline = []
                for event in timeline_query.all():
                    event_dict = event.to_dict()
                    if event_dict["additional_data"]:
                        try:
                            event_dict["additional_data"] = json.loads(
                                event_dict["additional_data"]
                            )
                        except:
                            pass
                    timeline.append(event_dict)

                return {
                    "email_id": email_id,
                    "statistics": stats,
                    "device_breakdown": device_breakdown,
                    "geographic_breakdown": geographic_breakdown,
                    "timeline": timeline,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                }

        except Exception as e:
            logger.error(f"Failed to get tracking stats for {email_id}: {e}")
            return {}

    def cleanup_old_data(self):
        """
        Clean up old tracking data based on retention policies using SQLAlchemy.

        This method removes tracking data older than the configured
        retention period to maintain database performance and comply
        with data retention policies.
        """
        try:
            with self.get_session() as session:
                # Calculate cutoff date
                cutoff_date = datetime.utcnow() - timedelta(
                    days=self.config.tracking_data_retention_days
                )

                # Delete old tracking events
                deleted_count = (
                    session.query(EmailTracking)
                    .filter(EmailTracking.timestamp < cutoff_date)
                    .delete(synchronize_session=False)
                )

                session.commit()

                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old tracking records")

        except Exception as e:
            logger.error(f"Failed to cleanup old data: {e}")

    def get_tenant_stats(self, tenant_id: str, days: int = 30) -> dict[str, Any]:
        """
        Get tracking statistics for a specific tenant.

        Args:
            tenant_id: Tenant identifier
            days: Number of days to include in statistics

        Returns:
            Dict[str, Any]: Tenant tracking statistics
        """
        try:
            with self.get_session() as session:
                # Calculate date range
                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=days)

                # Get event statistics by type
                events_query = (
                    session.query(
                        EmailTracking.event_type,
                        func.count(EmailTracking.id).label("total"),
                        func.count(func.distinct(EmailTracking.email_id)).label("unique_emails"),
                        func.count(func.distinct(EmailTracking.recipient)).label(
                            "unique_recipients"
                        ),
                    )
                    .filter(
                        and_(
                            # Same tenant_id/organization_id mismatch as
                            # _log_tracking_event_sync -- the model has no
                            # tenant_id attribute at all, so this always
                            # threw before a single row was ever queried.
                            EmailTracking.organization_id == tenant_id,
                            EmailTracking.timestamp >= start_date,
                            EmailTracking.timestamp <= end_date,
                        )
                    )
                    .group_by(EmailTracking.event_type)
                )

                event_stats = {}
                for row in events_query.all():
                    event_stats[row.event_type] = {
                        "total": row.total,
                        "unique_emails": row.unique_emails,
                        "unique_recipients": row.unique_recipients,
                    }

                return {
                    "tenant_id": tenant_id,
                    "period_days": days,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "event_statistics": event_stats,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                }

        except Exception as e:
            logger.error(f"Failed to get tenant stats for {tenant_id}: {e}")
            return {}

    def get_domain_stats(self, domain_id: str, days: int = 30) -> dict[str, Any]:
        """
        Get tracking statistics for a specific domain. Mirrors
        get_tenant_stats exactly, filtered by domain_id instead of
        organization_id -- this method didn't exist at all before; the
        gateway's domain-stats route had nothing real to proxy to.

        Args:
            domain_id: Domain identifier (the domains table's id, not its
                       name -- name-to-id resolution happens at the caller)
            days: Number of days to include in statistics

        Returns:
            Dict[str, Any]: Domain tracking statistics
        """
        try:
            with self.get_session() as session:
                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=days)

                events_query = (
                    session.query(
                        EmailTracking.event_type,
                        func.count(EmailTracking.id).label("total"),
                        func.count(func.distinct(EmailTracking.email_id)).label("unique_emails"),
                        func.count(func.distinct(EmailTracking.recipient)).label(
                            "unique_recipients"
                        ),
                    )
                    .filter(
                        and_(
                            EmailTracking.domain_id == domain_id,
                            EmailTracking.timestamp >= start_date,
                            EmailTracking.timestamp <= end_date,
                        )
                    )
                    .group_by(EmailTracking.event_type)
                )

                event_stats = {}
                for row in events_query.all():
                    event_stats[row.event_type] = {
                        "total": row.total,
                        "unique_emails": row.unique_emails,
                        "unique_recipients": row.unique_recipients,
                    }

                return {
                    "domain_id": domain_id,
                    "period_days": days,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "event_statistics": event_stats,
                    "total_sent": event_stats.get("delivered", {}).get("total", 0),
                    "total_opens": event_stats.get("opened", {}).get("total", 0),
                    "total_clicks": event_stats.get("clicked", {}).get("total", 0),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                }

        except Exception as e:
            logger.error(f"Failed to get domain stats for {domain_id}: {e}")
            return {}
