#!/usr/bin/env python3
"""
Suppression Service - Manages email suppression lists

This service handles bounce suppression, complaint suppression,
and unsubscribe management to maintain sending reputation.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from services.database_service import DatabaseService
from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)


class SuppressionService:
    """
    Manages email suppression lists and automatic list hygiene.

    Features:
    - Automatic suppression based on bounces and complaints
    - Unsubscribe management
    - Suppression list cleanup
    - Integration with rate limiting
    """

    def __init__(self):
        self.db = DatabaseService()

        # Configuration
        self.hard_bounce_threshold = 1  # Suppress after 1 hard bounce
        self.soft_bounce_threshold = 5  # Suppress after 5 soft bounces
        self.complaint_threshold = 1  # Suppress after 1 complaint
        self.suppression_duration = 30  # Days to keep suppressed

    def add_bounce_suppression(
        self, email: str, bounce_type: str, bounce_reason: str, organization_id: str
    ) -> bool:
        """Add email to bounce suppression list."""
        try:
            # Check if already suppressed
            if self.is_suppressed(email, organization_id):
                return True

            # Count recent bounces, +1 for the current one being processed
            # right now: the caller's own tracking-event write for this
            # exact bounce runs asynchronously (config.async_logging) and
            # isn't guaranteed to have landed in email_tracking yet, so a
            # bare _count_recent_bounces() undercounts by exactly one on
            # every call -- confirmed live: a first HARD bounce (threshold
            # 1) never suppressed; only the second one did, one bounce late.
            bounce_count = self._count_recent_bounces(email, organization_id) + 1

            should_suppress = False
            if bounce_type == "HARD":
                should_suppress = bounce_count >= self.hard_bounce_threshold
            elif bounce_type == "SOFT":
                should_suppress = bounce_count >= self.soft_bounce_threshold

            if should_suppress:
                return self._add_suppression(
                    email=email,
                    suppression_type="BOUNCE",
                    reason=f"{bounce_type} bounce: {bounce_reason}",
                    organization_id=organization_id,
                )

            return False

        except Exception as e:
            logger.error(f"Error adding bounce suppression for {email}: {e}")
            return False

    def add_complaint_suppression(
        self, email: str, complaint_reason: str, organization_id: str
    ) -> bool:
        """Add email to complaint suppression list."""
        try:
            return self._add_suppression(
                email=email,
                suppression_type="COMPLAINT",
                reason=f"Spam complaint: {complaint_reason}",
                organization_id=organization_id,
            )

        except Exception as e:
            logger.error(f"Error adding complaint suppression for {email}: {e}")
            return False

    def add_unsubscribe_suppression(
        self, email: str, organization_id: str, unsubscribe_method: str = "manual"
    ) -> bool:
        """Add email to unsubscribe suppression list."""
        try:
            return self._add_suppression(
                email=email,
                suppression_type="UNSUBSCRIBE",
                reason=f"Unsubscribed via {unsubscribe_method}",
                organization_id=organization_id,
            )

        except Exception as e:
            logger.error(f"Error adding unsubscribe suppression for {email}: {e}")
            return False

    def is_suppressed(self, email: str, organization_id: str) -> bool:
        """Check if email is suppressed."""
        try:
            query = """
                SELECT COUNT(*) as count
                FROM email_suppressions
                WHERE email = %s 
                AND organization_id = %s
                AND (expires_at IS NULL OR expires_at > %s)
                AND active = 1
            """

            result = self.db.execute_query(query, (email, organization_id, datetime.utcnow()))
            return result[0]["count"] > 0 if result else False

        except Exception as e:
            logger.error(f"Error checking suppression for {email}: {e}")
            return False

    def get_suppression_reason(self, email: str, organization_id: str) -> str | None:
        """Get suppression reason for email."""
        try:
            query = """
                SELECT suppression_type, reason, created_at
                FROM email_suppressions
                WHERE email = %s 
                AND organization_id = %s
                AND (expires_at IS NULL OR expires_at > %s)
                AND active = 1
                ORDER BY created_at DESC
                LIMIT 1
            """

            result = self.db.execute_query(query, (email, organization_id, datetime.utcnow()))
            if result:
                return f"{result[0]['suppression_type']}: {result[0]['reason']}"

            return None

        except Exception as e:
            logger.error(f"Error getting suppression reason for {email}: {e}")
            return None

    def remove_suppression(self, email: str, organization_id: str) -> bool:
        """Remove email from suppression list."""
        try:
            query = """
                UPDATE email_suppressions
                SET active = 0, updated_at = %s
                WHERE email = %s AND organization_id = %s
            """

            self.db.execute_query(query, (datetime.utcnow(), email, organization_id))
            logger.info(f"Removed suppression for {email}")
            return True

        except Exception as e:
            logger.error(f"Error removing suppression for {email}: {e}")
            return False

    def cleanup_expired_suppressions(self) -> int:
        """Clean up expired suppressions."""
        try:
            query = """
                UPDATE email_suppressions
                SET active = 0, updated_at = %s
                WHERE expires_at <= %s AND active = 1
            """

            rows_affected = self.db.execute_query(
                query, (datetime.utcnow(), datetime.utcnow()), fetch_results=False
            )

            if rows_affected:
                logger.info(f"Cleaned up {rows_affected} expired suppressions")
                return rows_affected

            return 0

        except Exception as e:
            logger.error(f"Error cleaning up expired suppressions: {e}")
            return 0

    def get_suppression_stats(self, organization_id: str) -> dict[str, Any]:
        """Get suppression statistics for organization."""
        try:
            query = """
                SELECT 
                    suppression_type,
                    COUNT(*) as count,
                    COUNT(CASE WHEN created_at >= %s THEN 1 END) as recent_count
                FROM email_suppressions
                WHERE organization_id = %s 
                AND active = 1
                GROUP BY suppression_type
            """

            recent_date = datetime.utcnow() - timedelta(days=30)
            results = self.db.execute_query(query, (recent_date, organization_id))

            stats = {"total_suppressed": 0, "by_type": {}, "recent_suppressions": 0}

            for row in results:
                suppression_type = row["suppression_type"]
                count = row["count"]
                recent_count = row["recent_count"]

                stats["total_suppressed"] += count
                stats["recent_suppressions"] += recent_count
                stats["by_type"][suppression_type] = {"total": count, "recent": recent_count}

            return stats

        except Exception as e:
            logger.error(f"Error getting suppression stats: {e}")
            return {}

    def _add_suppression(
        self, email: str, suppression_type: str, reason: str, organization_id: str
    ) -> bool:
        """Add suppression record to database."""
        try:
            expires_at = None
            if suppression_type in ["BOUNCE", "COMPLAINT"]:
                expires_at = datetime.utcnow() + timedelta(days=self.suppression_duration)

            query = """
                INSERT INTO email_suppressions
                (id, email, suppression_type, reason, organization_id, expires_at, created_at, updated_at, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                reason = VALUES(reason),
                updated_at = VALUES(updated_at),
                active = 1
            """

            now = datetime.utcnow()
            # id has no DB-side default (char(26), no auto_increment) --
            # every insert needs an application-generated ULID, matching
            # this table's actual primary key type.
            self.db.execute_query(
                query,
                (
                    generate_ulid(),
                    email,
                    suppression_type,
                    reason,
                    organization_id,
                    expires_at,
                    now,
                    now,
                ),
                fetch_results=False,
            )

            logger.info(f"Added {suppression_type} suppression for {email}")
            return True

        except Exception as e:
            logger.error(f"Error adding suppression record: {e}")
            return False

    def _count_recent_bounces(self, email: str, organization_id: str, days: int = 30) -> int:
        """Count recent bounces for email."""
        try:
            query = """
                SELECT COUNT(*) as count
                FROM email_tracking
                WHERE recipient = %s 
                AND organization_id = %s
                AND event_type = 'BOUNCED'
                AND timestamp >= %s
            """

            since_date = datetime.utcnow() - timedelta(days=days)
            result = self.db.execute_query(query, (email, organization_id, since_date))

            return result[0]["count"] if result else 0

        except Exception as e:
            logger.error(f"Error counting recent bounces: {e}")
            return 0
