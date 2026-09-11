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

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from mysql.connector import pooling

from config import config
from shared.ulid_utils import generate_ulid

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

    def _init_database_pool(self) -> pooling.MySQLConnectionPool | None:
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
        count: int,
        timestamp: datetime = None,
    ) -> bool:
        """
        Append a usage_history row for this increment.

        The real usage_history table (confirmed via DESCRIBE, 2026-08-08) is
        append-only history, not a mutable counter: no entity_type/
        identifier/direction/minute_key/usage_count/updated_at columns at
        all -- it has usage_type (enum 'rate_limit'|'storage_quota', shared
        with the separate storage-quota subsystem -- NOT a period name),
        organization_id/domain_id/email_account_id FKs, hour_key/day_key/
        month_key string period keys, and a JSON usage_data blob. There's
        also no unique key to upsert against, consistent with "append a
        row, SUM at read time" rather than "increment one row per bucket".
        entity_type/identifier/direction are carried inside usage_data
        instead, matching what get_usage_data/get_current_usage_stats below
        now read back out.

        This method's previous body inserted into columns that don't exist
        on this table at all -- it has never once successfully written a
        row (confirmed: 0 rows in usage_history before this fix, in an
        environment where this method is called on every cache-backed
        increment).

        domain_id/email_account_id are left NULL, not resolved and
        populated: the real columns are plain `int` (confirmed via
        DESCRIBE), while `domains.id`/`email_accounts.id` are ULID
        char(26) strings elsewhere in this same database -- the ORM model
        for this table (database/models/alerts.py) claims String(26) FKs,
        which doesn't match the live column type either. There is no
        numeric id to put there without a second, different lookup this
        fix has no mandate to invent; organization_id (real char(26),
        correctly populated) plus identifier inside usage_data already
        carries everything needed to scope a row to one entity.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier (org_id, domain, or email)
            direction: 'inbound' or 'outbound'
            count: Usage count to record for this increment
            timestamp: When this usage occurred (defaults to now)

        Returns:
            bool: True if successful
        """
        if not self.db_pool:
            return False

        ts = timestamp or datetime.now()

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            org_id, _domain_id, _email_account_id = self._resolve_identifiers(
                entity_type, identifier, cursor
            )
            if not org_id:
                logger.warning(
                    f"Could not resolve organization for {entity_type}:{identifier}, "
                    "skipping usage_history write"
                )
                cursor.close()
                conn.close()
                return False

            cursor.execute(
                "INSERT INTO usage_history "
                "(id, usage_type, organization_id, hour_key, day_key, month_key, "
                "usage_data, recorded_at) "
                "VALUES (%s, 'rate_limit', %s, %s, %s, %s, %s, %s)",
                (
                    generate_ulid(),
                    org_id,
                    ts.strftime("%Y-%m-%d-%H"),
                    ts.strftime("%Y-%m-%d"),
                    ts.strftime("%Y-%m"),
                    json.dumps(
                        {
                            "entity_type": entity_type,
                            "identifier": identifier,
                            "direction": direction,
                            "count": count,
                        }
                    ),
                    ts,
                ),
            )
            conn.commit()
            cursor.close()
            conn.close()

            logger.debug(f"Stored usage data: {entity_type}:{identifier}:{direction} = {count}")
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
    ) -> list[dict[str, Any]]:
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
        if not self.db_pool:
            return []

        # period selects which real period-key column to range on; all
        # three are populated on every row regardless of period (see
        # store_usage_data), entity_type/identifier/direction live inside
        # usage_data (no such columns on the real table -- see
        # store_usage_data's docstring for why).
        key_column = {"hourly": "hour_key", "daily": "day_key", "monthly": "month_key"}.get(
            period, "hour_key"
        )
        key_format = {
            "hour_key": "%Y-%m-%d-%H",
            "day_key": "%Y-%m-%d",
            "month_key": "%Y-%m",
        }[key_column]

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                f"SELECT usage_data, recorded_at FROM usage_history "
                f"WHERE usage_type = 'rate_limit' "
                f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.entity_type')) = %s "
                f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.identifier')) = %s "
                f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.direction')) = %s "
                f"AND {key_column} >= %s AND {key_column} <= %s "
                f"ORDER BY recorded_at ASC",
                (
                    entity_type,
                    identifier,
                    direction,
                    start_time.strftime(key_format),
                    end_time.strftime(key_format),
                ),
            )
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            return results

        except Exception as e:
            logger.error(f"Failed to get usage data: {e}")
            return []

    def get_current_usage_stats(
        self, entity_type: str, identifier: str, direction: str
    ) -> dict[str, int]:
        """
        Get current usage statistics for entity using new organization structure.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            Dictionary with current usage counts
        """
        if not self.db_pool:
            return {
                "second_count": 0,
                "minute_count": 0,
                "hourly_count": 0,
                "daily_count": 0,
                "monthly_count": 0,
            }

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            now = datetime.now()

            stats = {
                "second_count": 0,
                "minute_count": 0,
                "hourly_count": 0,
                "daily_count": 0,
                "monthly_count": 0,
            }

            # second/minute granularity isn't representable in usage_history
            # at all -- there's no such key column, only hour/day/month --
            # Redis is the only real second/minute counter (see
            # delivery_optimizer's send_burst-style keys for the established
            # pattern elsewhere in this codebase). This DB fallback can only
            # ever answer hourly/daily/monthly, left at 0 above.
            #
            # entity_type/identifier/direction live inside usage_data (JSON),
            # not as real columns -- see store_usage_data's docstring.
            for count_key, key_column, key_value in (
                ("hourly_count", "hour_key", now.strftime("%Y-%m-%d-%H")),
                ("daily_count", "day_key", now.strftime("%Y-%m-%d")),
                ("monthly_count", "month_key", now.strftime("%Y-%m")),
            ):
                cursor.execute(
                    f"SELECT COALESCE(SUM(CAST(JSON_EXTRACT(usage_data, '$.count') AS UNSIGNED)), 0) as total "
                    f"FROM usage_history "
                    f"WHERE usage_type = 'rate_limit' "
                    f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.entity_type')) = %s "
                    f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.identifier')) = %s "
                    f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.direction')) = %s "
                    f"AND {key_column} = %s",
                    (entity_type, identifier, direction, key_value),
                )
                result = cursor.fetchone()
                stats[count_key] = int(result["total"]) if result and result["total"] else 0

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

    def increment_usage_data(
        self, entity_type: str, identifier: str, direction: str, amount: int = 1
    ) -> dict[str, int]:
        """
        Increment usage counters in database (fallback when Redis unavailable).

        Previously targeted `rate_limit_usage`, a table that doesn't exist
        anywhere in this database -- every call threw and was swallowed by
        the except below. Reuses store_usage_data (append) +
        get_current_usage_stats (SUM at read time) against the real
        usage_history table instead of duplicating a second, separate
        broken write path.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            amount: Amount to increment

        Returns:
            Dict containing updated usage counts
        """
        try:
            self.store_usage_data(entity_type, identifier, direction, amount)
            return self.get_current_usage_stats(entity_type, identifier, direction)

        except Exception as e:
            logger.error(f"Error incrementing usage data: {e}")
            return {"hourly_count": 0, "daily_count": 0, "monthly_count": 0}

    def get_usage_history(
        self, entity_type: str, identifier: str, direction: str, days: int = 7
    ) -> list[dict[str, Any]]:
        """
        Get historical usage data for an entity.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            days: Number of days of history to retrieve

        Returns:
            List of usage records with timestamps. `daily_count` per record
            is that one record's own increment amount, not a pre-summed
            daily total -- callers (get_usage_trends) already sum these
            themselves grouped by day_key, which is what this now supports.
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)

            # rate_limit_usage doesn't exist (confirmed via DESCRIBE) --
            # every call threw before reaching a single row. usage_history
            # is one row per increment (see store_usage_data), not one row
            # per day with a pre-aggregated count, so day_key >= cutoff on
            # the real string-keyed column replaces the old STR_TO_DATE
            # comparison against a datetime column that also doesn't exist.
            results = self._execute_query(
                "SELECT hour_key, day_key, month_key, recorded_at, "
                # CAST ... AS UNSIGNED, not a bare JSON_EXTRACT -- otherwise
                # this comes back as a string, and get_usage_trends'
                # `daily_totals[day] += record["daily_count"]` silently
                # concatenates instead of summing (confirmed live: '5' + '3'
                # is not 8 in Python).
                "CAST(JSON_EXTRACT(usage_data, '$.count') AS UNSIGNED) as daily_count "
                "FROM usage_history "
                "WHERE usage_type = 'rate_limit' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.entity_type')) = %s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.identifier')) = %s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.direction')) = %s "
                "AND day_key >= %s "
                "ORDER BY day_key DESC, hour_key DESC "
                "LIMIT 1000",
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

            # rate_limit_usage doesn't exist -- this ran on every scheduled
            # cleanup cycle (app.py's _run_cleanup_service) and always threw,
            # caught here and logged as "0 cleaned up" rather than a visible
            # failure. Scoped to usage_type='rate_limit' specifically so this
            # never touches the separate storage_quota rows sharing the table.
            query = """
                DELETE FROM usage_history
                WHERE usage_type = 'rate_limit' AND day_key < %s
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
    ) -> list[dict[str, Any]]:
        """
        Get top entities by usage for monitoring and alerting.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            direction: 'inbound' or 'outbound'
            window: 'hourly', 'daily', or 'monthly'
            limit: Maximum number of results

        Returns:
            List of entities with their usage counts

        No caller anywhere in this codebase currently reaches this method,
        but fixed alongside its siblings for consistency: rate_limit_usage
        doesn't exist, and usage_history has no per-entity running total to
        sort by -- aggregates raw increments grouped by identifier instead.
        """
        try:
            key_column = {"hourly": "hour_key", "daily": "day_key", "monthly": "month_key"}.get(
                window
            )
            if not key_column:
                logger.error(f"Invalid window: {window}")
                return []

            key_format = {
                "hour_key": "%Y-%m-%d-%H",
                "day_key": "%Y-%m-%d",
                "month_key": "%Y-%m",
            }[key_column]
            current_key = datetime.now().strftime(key_format)

            results = self._execute_query(
                f"SELECT JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.identifier')) as identifier, "
                f"SUM(CAST(JSON_EXTRACT(usage_data, '$.count') AS UNSIGNED)) as usage_count, "
                f"MAX(recorded_at) as last_request "
                f"FROM usage_history "
                f"WHERE usage_type = 'rate_limit' "
                f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.entity_type')) = %s "
                f"AND JSON_UNQUOTE(JSON_EXTRACT(usage_data, '$.direction')) = %s "
                f"AND {key_column} = %s "
                f"GROUP BY identifier "
                f"HAVING usage_count > 0 "
                f"ORDER BY usage_count DESC "
                f"LIMIT %s",
                (entity_type, direction, current_key, limit),
                fetch_all=True,
            )

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

        The real rate_limit_alerts table (confirmed via DESCRIBE) is
        `id, organization_id, entity_type, identifier, window, usage_pct,
        alert_level, created_at` -- no direction/current_usage/limit_value/
        window_type/webhook_sent columns exist at all. This previously
        inserted into columns that don't exist, throwing on every call --
        a real problem since alert_service.py's background threshold
        monitor (a real, always-running caller, not a dead code path)
        calls this on every warning/critical/exceeded breach. direction/
        current_usage/limit_value have nowhere to persist on this schema
        and are dropped from the write (still visible in the log line
        below); get_pending_webhook_alerts/update_webhook_alert_status
        further down assume webhook-delivery-tracking columns that were
        never added to this table either, and have no caller anywhere --
        left as-is rather than guessing a schema this fix has no mandate
        to change.
        """
        try:
            usage_percentage = (current_usage / limit_value * 100) if limit_value > 0 else 0
            window = {"hourly": "hour", "daily": "day", "monthly": "month"}.get(
                window_type, window_type
            )
            organization_id = self._resolve_organization_id(entity_type, identifier)

            result = self._execute_query(
                "INSERT INTO rate_limit_alerts "
                "(organization_id, entity_type, identifier, `window`, usage_pct, alert_level) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    organization_id,
                    entity_type,
                    identifier,
                    window,
                    round(usage_percentage, 2),
                    alert_level,
                ),
            )

            if result is not None:
                logger.info(
                    f"Created {alert_level} alert for {entity_type}:{identifier} "
                    f"({current_usage}/{limit_value} = {usage_percentage:.1f}%, direction={direction})"
                )
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Error creating alert record: {e}")
            return False

    def _resolve_organization_id(self, entity_type: str, identifier: str) -> str | None:
        """Resolve just the organization_id for an entity, without the
        domain_id/email_account_id _resolve_identifiers also looks up --
        used by callers (create_alert_record) that only need the org."""
        if entity_type == "organization":
            return identifier
        if entity_type == "domain":
            row = self._execute_query(
                "SELECT organization_id FROM domains WHERE domain = %s",
                (identifier,),
                fetch_one=True,
            )
        elif entity_type == "mailbox":
            row = self._execute_query(
                "SELECT organization_id FROM email_accounts WHERE email = %s",
                (identifier,),
                fetch_one=True,
            )
        else:
            row = None
        return row["organization_id"] if row else None

    def get_pending_webhook_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
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

    def get_database_stats(self) -> dict[str, Any]:
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

    def health_check(self) -> dict[str, Any]:
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
