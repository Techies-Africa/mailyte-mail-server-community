#!/usr/bin/env python3
"""
Rate Limiter - Database Service

This service handles all database operations for the rate limiting system:
- Usage data storage and retrieval using new organization structure
- Historical statistics with organization/domain/email_account hierarchy
- Database cleanup operations
- Transaction management
- Connection pooling

The service provides reliable data persistence with proper error handling
and connection management for high-availability operations.
"""

import logging
import mysql.connector
from mysql.connector import pooling
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import json

from config import config

logger = logging.getLogger(__name__)


class RateLimitDatabaseService:
    """
    Database service for rate limiting persistent storage and fallback operations.

    This service handles all database operations for the rate limiting system
    including usage tracking, configuration storage, and cleanup operations.
    It provides a reliable fallback when Redis is unavailable.
    """

    def __init__(self):
        """Initialize the database service with connection pooling"""
        self.config = config
        self.db_pool = self._init_database_pool()

        # Performance tracking
        self.query_count = 0
        self.error_count = 0
        self.total_query_time = 0

        logger.info("Rate limit database service initialized")

    def _init_database_pool(self) -> Optional[pooling.MySQLConnectionPool]:
        """
        Initialize MySQL connection pool with retry logic and error handling.

        Returns:
            MySQL connection pool or None if connection fails
        """
        try:
            pool_config = {
                "host": self.config.database.host,
                "port": self.config.database.port,
                "database": self.config.database.database,
                "user": self.config.database.user,
                "password": self.config.database.password,
                "charset": self.config.database.charset,
                "autocommit": True,
                "pool_name": "rate_limit_db_pool",
                "pool_size": self.config.database.pool_size,
                "pool_reset_session": True,
                "use_unicode": True,
                "sql_mode": "TRADITIONAL",
                "time_zone": "+00:00",  # Use UTC
            }

            pool = pooling.MySQLConnectionPool(**pool_config)

            # Test connection
            test_conn = pool.get_connection()
            test_conn.close()

            logger.info(
                f"Database connection pool created successfully with {self.config.database.pool_size} connections"
            )
            return pool

        except Exception as e:
            logger.error(f"Failed to create database connection pool: {e}")
            return None

    def _execute_query(
        self, query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False
    ) -> Any:
        """
        Execute a database query with connection management and error handling.

        Args:
            query: SQL query to execute
            params: Query parameters
            fetch_one: Whether to fetch one row
            fetch_all: Whether to fetch all rows

        Returns:
            Query result or None on error
        """
        if not self.db_pool:
            logger.error("Database pool not available")
            return None

        start_time = time.time()

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            result = None
            if fetch_one:
                result = cursor.fetchone()
            elif fetch_all:
                result = cursor.fetchall()
            else:
                result = cursor.rowcount

            cursor.close()
            conn.close()

            # Update performance metrics
            self.query_count += 1
            self.total_query_time += time.time() - start_time

            return result

        except MySQLError as e:
            self.error_count += 1
            logger.error(f"Database query error: {e}")
            return None
        except Exception as e:
            self.error_count += 1
            logger.error(f"Unexpected database error: {e}")
            return None

    def store_usage_data(
        self,
        entity_type: str,
        identifier: str,
        direction: str,
        period: str,
        timestamp: datetime,
        count: int,
    ) -> bool:
        """
        Store usage data in the database using the new organization structure.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier (org_id, domain, or email)
            direction: 'inbound' or 'outbound'
            period: Time period ('hourly', 'daily', 'monthly')
            timestamp: Usage timestamp
            count: Usage count

        Returns:
            bool: True if successful
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # Get organization_id and additional identifiers
            org_id, domain_id, email_account_id = self._resolve_identifiers(
                entity_type, identifier, cursor
            )

            # Generate time-based keys
            time_key = self._generate_time_key(timestamp, period)

            # Insert or update usage record
            query = """
                INSERT INTO usage_history 
                (organization_id, domain_id, email_account_id, entity_type, identifier, 
                 direction, usage_type, day_key, hour_key, minute_key, usage_count, 
                 created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON DUPLICATE KEY UPDATE
                    usage_count = usage_count + VALUES(usage_count),
                    updated_at = NOW()
            """

            # Generate different time keys
            day_key = timestamp.strftime("%Y-%m-%d")
            hour_key = timestamp.strftime("%Y-%m-%d %H:00:00")
            minute_key = timestamp.strftime("%Y-%m-%d %H:%M:00")

            cursor.execute(
                query,
                (
                    org_id,
                    domain_id,
                    email_account_id,
                    entity_type,
                    identifier,
                    direction,
                    period,
                    day_key,
                    hour_key,
                    minute_key,
                    count,
                ),
            )

            cursor.close()
            conn.close()

            logger.debug(
                f"Stored usage data: {entity_type}:{identifier}:{direction}:{period} = {count}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to store usage data: {e}")
            return False

    def _resolve_identifiers(self, entity_type: str, identifier: str, cursor) -> tuple:
        """
        Resolve organization_id, domain_id, and email_account_id based on entity type.

        Returns:
            tuple: (organization_id, domain_id, email_account_id)
        """
        org_id = None
        domain_id = None
        email_account_id = None

        try:
            if entity_type == "organization":
                org_id = identifier

            elif entity_type == "domain":
                query = "SELECT id, organization_id FROM domains WHERE domain = %s"
                cursor.execute(query, (identifier,))
                result = cursor.fetchone()
                if result:
                    domain_id, org_id = result

            elif entity_type == "mailbox":
                query = """
                    SELECT ea.id, ea.domain_id, ea.organization_id 
                    FROM email_accounts ea 
                    WHERE ea.email = %s
                """
                cursor.execute(query, (identifier,))
                result = cursor.fetchone()
                if result:
                    email_account_id, domain_id, org_id = result

        except Exception as e:
            logger.warning(f"Failed to resolve identifiers for {entity_type}:{identifier}: {e}")

        return org_id, domain_id, email_account_id

    def get_usage_data(
        self,
        entity_type: str,
        identifier: str,
        direction: str,
        period: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve usage data from database using the new organization structure.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            period: Time period ('hourly', 'daily', 'monthly')
            start_time: Start time for data retrieval
            end_time: End time for data retrieval

        Returns:
            List of usage data records
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            # Build query based on entity type
            where_conditions = []
            params = []

            if entity_type == "organization":
                where_conditions.append("organization_id = %s")
                params.append(identifier)
            elif entity_type == "domain":
                where_conditions.append("entity_type = 'domain' AND identifier = %s")
                params.append(identifier)
            elif entity_type == "mailbox":
                where_conditions.append("entity_type = 'mailbox' AND identifier = %s")
                params.append(identifier)

            where_conditions.extend(
                ["direction = %s", "usage_type = %s", "created_at >= %s", "created_at <= %s"]
            )
            params.extend([direction, period, start_time, end_time])

            query = f"""
                SELECT * FROM usage_history 
                WHERE {" AND ".join(where_conditions)}
                ORDER BY created_at ASC
            """

            cursor.execute(query, params)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            return results

        except Exception as e:
            logger.error(f"Failed to get usage data: {e}")
            return []

    def get_current_usage_stats(
        self, entity_type: str, identifier: str, direction: str
    ) -> Dict[str, int]:
        """
        Get current usage statistics for entity using new organization structure.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            Dictionary with current usage counts
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            now = datetime.now()
            current_hour = now.replace(minute=0, second=0, microsecond=0)
            current_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            current_minute = now.replace(second=0, microsecond=0)
            current_second = now.replace(microsecond=0)

            stats = {
                "second_count": 0,
                "minute_count": 0,
                "hourly_count": 0,
                "daily_count": 0,
                "monthly_count": 0,
            }

            # Build base query conditions
            where_conditions = []
            params = []

            if entity_type == "organization":
                where_conditions.append("organization_id = %s")
                params.append(identifier)
            elif entity_type == "domain":
                where_conditions.append("entity_type = 'domain' AND identifier = %s")
                params.append(identifier)
            elif entity_type == "mailbox":
                where_conditions.append("entity_type = 'mailbox' AND identifier = %s")
                params.append(identifier)

            where_conditions.append("direction = %s")
            params.append(direction)

            # Get hourly count
            hourly_params = params + [current_hour]
            hourly_query = f"""
                SELECT COALESCE(SUM(usage_count), 0) as count
                FROM usage_history 
                WHERE {" AND ".join(where_conditions)} 
                AND usage_type = 'hourly' AND hour_key >= %s
            """
            cursor.execute(hourly_query, hourly_params)
            result = cursor.fetchone()
            stats["hourly_count"] = result["count"] if result else 0

            # Get daily count
            daily_params = params + [current_day]
            daily_query = f"""
                SELECT COALESCE(SUM(usage_count), 0) as count
                FROM usage_history 
                WHERE {" AND ".join(where_conditions)} 
                AND usage_type = 'daily' AND day_key >= %s
            """
            cursor.execute(daily_query, daily_params)
            result = cursor.fetchone()
            stats["daily_count"] = result["count"] if result else 0

            # Get monthly count
            monthly_params = params + [current_month]
            monthly_query = f"""
                SELECT COALESCE(SUM(usage_count), 0) as count
                FROM usage_history 
                WHERE {" AND ".join(where_conditions)} 
                AND usage_type = 'monthly' AND created_at >= %s
            """
            cursor.execute(monthly_query, monthly_params)
            result = cursor.fetchone()
            stats["monthly_count"] = result["count"] if result else 0

            cursor.close()
            conn.close()

            return stats

        except Exception as e:
            logger.error(f"Failed to get current usage stats: {e}")
            return {
                "second_count": 0,
                "minute_count": 0,
                "hourly_count": 0,
                "daily_count": 0,
                "monthly_count": 0,
            }

    def _generate_time_key(self, timestamp: datetime, period: str) -> str:
        """
        Generate a time key based on the specified period.

        Args:
            timestamp: The datetime object.
            period: 'hourly', 'daily', or 'monthly'.

        Returns:
            str: A formatted time key.
        """
        if period == "hourly":
            return timestamp.strftime("%Y-%m-%d %H:00:00")
        elif period == "daily":
            return timestamp.strftime("%Y-%m-%d")
        elif period == "monthly":
            return timestamp.strftime("%Y-%m-01")  # Consistent for the whole month
        else:
            raise ValueError("Invalid period. Must be 'hourly', 'daily', or 'monthly'.")

    def increment_usage_data(
        self, entity_type: str, identifier: str, direction: str, amount: int = 1
    ) -> Dict[str, int]:
        """
        Increment usage counters in database (fallback when Redis unavailable).

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            amount: Amount to increment

        Returns:
            Dict containing updated usage counts
        """
        try:
            now = datetime.now()
            hour_key = now.strftime("%Y-%m-%d-%H")
            day_key = now.strftime("%Y-%m-%d")
            month_key = now.strftime("%Y-%m")

            # Use INSERT ... ON DUPLICATE KEY UPDATE for atomic increment
            query = """
                INSERT INTO rate_limit_usage 
                (type, identifier, direction, hour_key, day_key, month_key, 
                 hourly_count, daily_count, monthly_count, first_request, last_request)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                hourly_count = hourly_count + VALUES(hourly_count),
                daily_count = daily_count + VALUES(daily_count),
                monthly_count = monthly_count + VALUES(monthly_count),
                last_request = VALUES(last_request),
                last_updated = CURRENT_TIMESTAMP
            """

            params = (
                entity_type,
                identifier,
                direction,
                hour_key,
                day_key,
                month_key,
                amount,
                amount,
                amount,
                now,
                now,
            )

            self._execute_query(query, params)

            # Get updated counts
            return self.get_usage_data(
                entity_type, identifier, direction, "hourly", now - timedelta(hours=1), now
            )

        except Exception as e:
            logger.error(f"Error incrementing usage data: {e}")
            return {"hourly_count": 0, "daily_count": 0, "monthly_count": 0}

    def get_usage_history(
        self, entity_type: str, identifier: str, direction: str, days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get historical usage data for an entity.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            days: Number of days of history to retrieve

        Returns:
            List of usage records with timestamps
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)

            query = """
                SELECT hour_key, day_key, month_key, hourly_count, daily_count, 
                       monthly_count, first_request, last_request, last_updated
                FROM rate_limit_usage
                WHERE type = %s AND identifier = %s AND direction = %s
                  AND STR_TO_DATE(day_key, '%Y-%m-%d') >= %s
                ORDER BY day_key DESC, hour_key DESC
                LIMIT 1000
            """

            results = self._execute_query(
                query,
                (entity_type, identifier, direction, cutoff_date.strftime("%Y-%m-%d")),
                fetch_all=True,
            )

            if results:
                logger.debug(f"Retrieved {len(results)} usage history records")
                return results
            else:
                return []

        except Exception as e:
            logger.error(f"Error getting usage history: {e}")
            return []

    def cleanup_old_usage_data(self, retention_days: int = 90) -> int:
        """
        Clean up old usage data beyond retention period.

        Args:
            retention_days: Number of days to retain data

        Returns:
            int: Number of records deleted
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=retention_days)
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")

            # Delete old usage records
            query = """
                DELETE FROM rate_limit_usage 
                WHERE STR_TO_DATE(day_key, '%Y-%m-%d') < %s
            """

            deleted_count = self._execute_query(query, (cutoff_str,))

            if deleted_count and deleted_count > 0:
                logger.info(
                    f"Cleaned up {deleted_count} old usage records (older than {retention_days} days)"
                )
                return deleted_count
            else:
                logger.debug("No old usage records to clean up")
                return 0

        except Exception as e:
            logger.error(f"Error cleaning up old usage data: {e}")
            return 0

    def get_top_usage_entities(
        self, entity_type: str, direction: str, window: str = "daily", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get top entities by usage for monitoring and alerting.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            direction: 'inbound' or 'outbound'
            window: 'hourly', 'daily', or 'monthly'
            limit: Maximum number of results

        Returns:
            List of entities with their usage counts
        """
        try:
            # Map window to column and time filter
            column_map = {
                "hourly": "hourly_count",
                "daily": "daily_count",
                "monthly": "monthly_count",
            }

            if window not in column_map:
                logger.error(f"Invalid window: {window}")
                return []

            count_column = column_map[window]

            # Query top entities by usage
            query = f"""
                SELECT identifier, {count_column} as usage_count, 
                       last_request, last_updated
                FROM rate_limit_usage
                WHERE type = %s AND direction = %s 
                  AND {count_column} > 0
                ORDER BY {count_column} DESC
                LIMIT %s
            """

            results = self._execute_query(query, (entity_type, direction, limit), fetch_all=True)

            if results:
                logger.debug(f"Retrieved top {len(results)} entities by {window} usage")
                return results
            else:
                return []

        except Exception as e:
            logger.error(f"Error getting top usage entities: {e}")
            return []

    def create_alert_record(
        self,
        entity_type: str,
        identifier: str,
        direction: str,
        alert_level: str,
        current_usage: int,
        limit_value: int,
        window_type: str,
    ) -> bool:
        """
        Create an alert record for rate limit threshold breach.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            alert_level: 'warning', 'critical', or 'exceeded'
            current_usage: Current usage count
            limit_value: The limit that was approached/exceeded
            window_type: 'hourly', 'daily', or 'monthly'

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            usage_percentage = (current_usage / limit_value * 100) if limit_value > 0 else 0

            query = """
                INSERT INTO rate_limit_alerts 
                (type, identifier, direction, alert_level, current_usage, 
                 limit_value, usage_percentage, window_type, webhook_sent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            params = (
                entity_type,
                identifier,
                direction,
                alert_level,
                current_usage,
                limit_value,
                usage_percentage,
                window_type,
                False,
            )

            result = self._execute_query(query, params)

            if result is not None:
                logger.info(
                    f"Created {alert_level} alert for {entity_type}:{identifier} "
                    f"({current_usage}/{limit_value} = {usage_percentage:.1f}%)"
                )
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Error creating alert record: {e}")
            return False

    def get_pending_webhook_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get alerts that need webhook notifications sent.

        Args:
            limit: Maximum number of alerts to retrieve

        Returns:
            List of alert records pending webhook delivery
        """
        try:
            query = """
                SELECT id, type, identifier, direction, alert_level, current_usage,
                       limit_value, usage_percentage, window_type, webhook_attempts,
                       created_at
                FROM rate_limit_alerts
                WHERE webhook_sent = FALSE 
                  AND webhook_attempts < 3
                  AND created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
                ORDER BY created_at ASC
                LIMIT %s
            """

            results = self._execute_query(query, (limit,), fetch_all=True)

            if results:
                logger.debug(f"Retrieved {len(results)} pending webhook alerts")
                return results
            else:
                return []

        except Exception as e:
            logger.error(f"Error getting pending webhook alerts: {e}")
            return []

    def update_webhook_alert_status(
        self, alert_id: int, success: bool, error_message: str = None
    ) -> bool:
        """
        Update webhook delivery status for an alert.

        Args:
            alert_id: Alert record ID
            success: Whether webhook was delivered successfully
            error_message: Error message if delivery failed

        Returns:
            bool: True if update successful, False otherwise
        """
        try:
            if success:
                query = """
                    UPDATE rate_limit_alerts 
                    SET webhook_sent = TRUE, webhook_success = TRUE,
                        webhook_last_attempt = CURRENT_TIMESTAMP
                    WHERE id = %s
                """
                params = (alert_id,)
            else:
                query = """
                    UPDATE rate_limit_alerts 
                    SET webhook_attempts = webhook_attempts + 1,
                        webhook_last_attempt = CURRENT_TIMESTAMP,
                        webhook_success = FALSE
                    WHERE id = %s
                """
                params = (alert_id,)

            result = self._execute_query(query, params)

            if result is not None:
                logger.debug(f"Updated webhook status for alert {alert_id}: success={success}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Error updating webhook alert status: {e}")
            return False

    def get_database_stats(self) -> Dict[str, Any]:
        """
        Get database service statistics and performance metrics.

        Returns:
            Dict containing database statistics
        """
        stats = {
            "service": "rate_limit_database",
            "pool_available": self.db_pool is not None,
            "query_count": self.query_count,
            "error_count": self.error_count,
            "average_query_time": (self.total_query_time / self.query_count)
            if self.query_count > 0
            else 0,
            "error_rate": (self.error_count / self.query_count * 100)
            if self.query_count > 0
            else 0,
        }

        # Get database size information
        if self.db_pool:
            try:
                # Get table row counts
                tables_query = """
                    SELECT table_name, table_rows 
                    FROM information_schema.tables 
                    WHERE table_schema = %s 
                      AND table_name IN ('rate_limit_configs', 'rate_limit_usage', 'rate_limit_alerts')
                """

                table_stats = self._execute_query(
                    tables_query, (self.config.database.database,), fetch_all=True
                )

                if table_stats:
                    stats["table_stats"] = {
                        row["table_name"]: row["table_rows"] for row in table_stats
                    }

            except Exception as e:
                logger.warning(f"Could not get database stats: {e}")
                stats["table_stats"] = {"error": str(e)}

        return stats

    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on the database service.

        Returns:
            Dict containing health check results
        """
        health = {"service": "rate_limit_database", "status": "healthy", "checks": {}}

        # Check database connectivity
        if self.db_pool:
            try:
                start_time = time.time()
                test_query = "SELECT 1 as test"
                result = self._execute_query(test_query, fetch_one=True)
                response_time = (time.time() - start_time) * 1000

                if result and result.get("test") == 1:
                    health["checks"]["database"] = {
                        "status": "healthy",
                        "response_time_ms": round(response_time, 2),
                    }
                else:
                    health["status"] = "unhealthy"
                    health["checks"]["database"] = {
                        "status": "unhealthy",
                        "error": "Query test failed",
                    }

            except Exception as e:
                health["status"] = "unhealthy"
                health["checks"]["database"] = {"status": "unhealthy", "error": str(e)}
        else:
            health["status"] = "unhealthy"
            health["checks"]["database"] = {
                "status": "unavailable",
                "error": "Database pool not initialized",
            }

        return health
