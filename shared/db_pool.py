#!/usr/bin/env python3
"""
Shared Database Connection Pooling Module

Provides a robust MySQL connection pooling layer for all Mailyte email server
services. Built on top of mysql-connector-python's native pooling with added
support for read replicas, connection health checks, Redis-backed query caching,
and date-based table partitioning helpers.

Features:
- Configurable primary and read-replica connection pools
- Context manager interface for safe connection handling
- Automatic connection health checking before returning from pool
- Redis-backed query caching with configurable TTL
- Date-based partition SQL generation for large tables

Development: local MySQL and Redis in Docker
Production:  AWS RDS (primary + read replica) and ElastiCache

Usage:
    from shared.db_pool import get_db_pool

    pool = get_db_pool()

    # Write query
    with pool.connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("INSERT INTO domains (name) VALUES (%s)", ("example.com",))
        conn.commit()

    # Read query (routed to replica if configured)
    with pool.read_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM domains WHERE name = %s", ("example.com",))
        rows = cursor.fetchall()

    # Cached read query
    rows = pool.cached_query("SELECT * FROM domains WHERE active = %s", (1,), ttl=600)
"""

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from mysql.connector import Error as MySQLError
from mysql.connector import pooling

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    MySQL connection pool with read-replica support, health checking,
    Redis-backed query caching, and partition helpers.

    Args:
        pool_size: Number of connections in each pool. Defaults to 10.
        pool_name: Name prefix for the connection pools.
        db_host: Primary database host. Falls back to DB_HOST env var.
        db_port: Database port. Falls back to DB_PORT env var.
        db_name: Database name. Falls back to DB_NAME env var.
        db_user: Database user. Falls back to DB_USER env var.
        db_password: Database password. Falls back to DB_PASSWORD env var.
        read_host: Read-replica host. Falls back to DB_READ_HOST env var.
                   If not set, read queries are routed to the primary pool.
        redis_host: Redis host for query caching. Falls back to REDIS_HOST env var.
        redis_port: Redis port. Falls back to REDIS_PORT env var.
    """

    def __init__(
        self,
        pool_size: int = 10,
        pool_name: str = "mailyte_pool",
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
        read_host: str | None = None,
        redis_host: str | None = None,
        redis_port: int | None = None,
    ):
        self.pool_size = pool_size
        self.pool_name = pool_name

        # Primary database configuration from arguments or environment
        self._db_config = {
            "host": db_host or os.getenv("DB_HOST", "mysql"),
            "port": int(db_port or os.getenv("DB_PORT", "3306")),
            "database": db_name or os.getenv("DB_NAME", "mailserver"),
            "user": db_user or os.getenv("DB_USER", "mailuser"),
            "password": db_password or os.getenv("DB_PASSWORD", "mailpassword"),
        }

        # Read-replica configuration
        self._read_host = read_host or os.getenv("DB_READ_HOST", "")

        # Redis configuration
        self._redis_host = redis_host or os.getenv("REDIS_HOST", "redis")
        self._redis_port = int(redis_port or os.getenv("REDIS_PORT", "6379"))

        # Pools are created lazily on first use
        self._primary_pool: pooling.MySQLConnectionPool | None = None
        self._replica_pool: pooling.MySQLConnectionPool | None = None
        self._redis_client = None
        self._redis_available: bool | None = None

    # ------------------------------------------------------------------
    # Pool creation
    # ------------------------------------------------------------------

    def _create_primary_pool(self) -> pooling.MySQLConnectionPool:
        """Create the primary (read-write) connection pool."""
        logger.info(
            "Creating primary connection pool '%s' (size=%d) -> %s:%s/%s",
            self.pool_name,
            self.pool_size,
            self._db_config["host"],
            self._db_config["port"],
            self._db_config["database"],
        )
        return pooling.MySQLConnectionPool(
            pool_name=self.pool_name,
            pool_size=self.pool_size,
            pool_reset_session=True,
            **self._db_config,
        )

    def _create_replica_pool(self) -> pooling.MySQLConnectionPool:
        """Create the read-replica connection pool."""
        replica_config = {**self._db_config, "host": self._read_host}
        pool_name = f"{self.pool_name}_replica"
        logger.info(
            "Creating read-replica pool '%s' (size=%d) -> %s:%s/%s",
            pool_name,
            self.pool_size,
            self._read_host,
            self._db_config["port"],
            self._db_config["database"],
        )
        return pooling.MySQLConnectionPool(
            pool_name=pool_name,
            pool_size=self.pool_size,
            pool_reset_session=True,
            **replica_config,
        )

    @property
    def primary_pool(self) -> pooling.MySQLConnectionPool:
        """Lazy accessor for the primary connection pool."""
        if self._primary_pool is None:
            self._primary_pool = self._create_primary_pool()
        return self._primary_pool

    @property
    def replica_pool(self) -> pooling.MySQLConnectionPool | None:
        """Lazy accessor for the replica pool. Returns None when no replica is configured."""
        if not self._read_host:
            return None
        if self._replica_pool is None:
            self._replica_pool = self._create_replica_pool()
        return self._replica_pool

    # ------------------------------------------------------------------
    # Connection health checking
    # ------------------------------------------------------------------

    @staticmethod
    def _check_connection_health(conn) -> bool:
        """
        Verify a connection is still usable by issuing a lightweight ping.

        Returns True if the connection is healthy, False otherwise.
        """
        try:
            conn.ping(reconnect=True, attempts=2, delay=1)
            return True
        except MySQLError as exc:
            logger.warning("Connection health check failed: %s", exc)
            return False

    def _get_healthy_connection(self, pool: pooling.MySQLConnectionPool):
        """
        Retrieve a connection from the given pool and verify it is healthy.

        If the first connection fails the health check, it is closed and a
        fresh connection is requested (up to 3 attempts).
        """
        last_error = None
        for attempt in range(1, 4):
            try:
                conn = pool.get_connection()
                if self._check_connection_health(conn):
                    return conn
                # Unhealthy -- close and retry
                logger.warning(
                    "Discarding unhealthy connection from pool (attempt %d/3)",
                    attempt,
                )
                try:
                    conn.close()
                except Exception:
                    pass
            except MySQLError as exc:
                last_error = exc
                logger.warning(
                    "Failed to obtain connection from pool (attempt %d/3): %s",
                    attempt,
                    exc,
                )
        raise MySQLError(f"Unable to obtain a healthy connection after 3 attempts: {last_error}")

    # ------------------------------------------------------------------
    # Public connection accessors
    # ------------------------------------------------------------------

    def get_connection(self):
        """
        Get a healthy connection from the primary (read-write) pool.

        Returns:
            A mysql.connector PooledMySQLConnection instance.
        """
        return self._get_healthy_connection(self.primary_pool)

    def get_read_connection(self):
        """
        Get a healthy connection for read queries.

        If a read replica is configured (DB_READ_HOST), the connection is
        drawn from the replica pool. Otherwise it falls back to the primary pool.

        Returns:
            A mysql.connector PooledMySQLConnection instance.
        """
        pool = self.replica_pool if self.replica_pool is not None else self.primary_pool
        return self._get_healthy_connection(pool)

    # ------------------------------------------------------------------
    # Context managers
    # ------------------------------------------------------------------

    @contextmanager
    def connection(self):
        """
        Context manager for a primary (read-write) connection.

        Usage::

            with pool.connection() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("INSERT INTO ...")
                conn.commit()
        """
        conn = self.get_connection()
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def read_connection(self):
        """
        Context manager for a read-only connection.

        Routes to the read replica when available, otherwise falls back
        to the primary pool.

        Usage::

            with pool.read_connection() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT ...")
        """
        conn = self.get_read_connection()
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Redis query caching
    # ------------------------------------------------------------------

    def _get_redis(self):
        """
        Lazily initialise and return the Redis client.

        If Redis is not reachable, ``_redis_available`` is set to False so
        subsequent calls skip the connection attempt and queries fall through
        to the database directly.
        """
        if self._redis_available is False:
            return None

        if self._redis_client is not None:
            return self._redis_client

        try:
            import redis

            self._redis_client = redis.Redis(
                host=self._redis_host,
                port=self._redis_port,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            # Verify connectivity
            self._redis_client.ping()
            self._redis_available = True
            logger.info(
                "Redis connected for query caching at %s:%s",
                self._redis_host,
                self._redis_port,
            )
            return self._redis_client
        except Exception as exc:
            logger.warning(
                "Redis unavailable at %s:%s -- query caching disabled: %s",
                self._redis_host,
                self._redis_port,
                exc,
            )
            self._redis_available = False
            self._redis_client = None
            return None

    @staticmethod
    def _cache_key(query: str, params: tuple | None) -> str:
        """Generate a deterministic cache key from query text and parameters."""
        raw = json.dumps({"q": query, "p": params}, sort_keys=True, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"mailyte:qcache:{digest}"

    def cached_query(
        self,
        query: str,
        params: tuple | None = None,
        ttl: int = 300,
    ) -> list[dict[str, Any]]:
        """
        Execute a read query with Redis-backed caching.

        The result set is serialised as JSON and stored in Redis with the given
        TTL (in seconds). Subsequent identical queries within the TTL window
        are served directly from cache.

        If Redis is unavailable the query is executed against the database
        without caching.

        Args:
            query: SQL SELECT statement.
            params: Optional tuple of query parameters.
            ttl: Cache time-to-live in seconds. Defaults to 300 (5 minutes).

        Returns:
            List of dictionaries representing result rows.
        """
        cache_key = self._cache_key(query, params)

        # Attempt cache lookup
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                cached = redis_client.get(cache_key)
                if cached is not None:
                    logger.debug("Cache HIT for key %s", cache_key)
                    return json.loads(cached)
                logger.debug("Cache MISS for key %s", cache_key)
            except Exception as exc:
                logger.warning("Redis GET failed, falling through to DB: %s", exc)

        # Execute against database
        with self.read_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, params)
            rows = cursor.fetchall()

        # Serialise rows (handle datetime and other non-JSON types)
        serialisable_rows = _make_serialisable(rows)

        # Store in cache
        if redis_client is not None:
            try:
                redis_client.setex(cache_key, ttl, json.dumps(serialisable_rows))
            except Exception as exc:
                logger.warning("Redis SETEX failed: %s", exc)

        return serialisable_rows

    def invalidate_cache(self, query: str, params: tuple | None = None) -> bool:
        """
        Remove a cached query result from Redis.

        Args:
            query: The original SQL query.
            params: The original query parameters.

        Returns:
            True if the key was deleted, False otherwise.
        """
        redis_client = self._get_redis()
        if redis_client is None:
            return False
        try:
            return bool(redis_client.delete(self._cache_key(query, params)))
        except Exception as exc:
            logger.warning("Redis DELETE failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        """
        Close all underlying pool resources.

        After calling close() the pool instance should not be reused.
        """
        # mysql-connector-python pools do not expose a bulk close method.
        # We reset our references so that fresh pools are created if the
        # instance is accidentally reused.
        self._primary_pool = None
        self._replica_pool = None

        if self._redis_client is not None:
            try:
                self._redis_client.close()
            except Exception:
                pass
            self._redis_client = None
            self._redis_available = None

        logger.info("DatabasePool '%s' closed", self.pool_name)


# ======================================================================
# Singleton helper
# ======================================================================

_db_pool_instance: DatabasePool | None = None


def get_db_pool(**kwargs) -> DatabasePool:
    """
    Return the global DatabasePool singleton.

    On the first call a new ``DatabasePool`` is created using the provided
    keyword arguments (or their defaults).  Subsequent calls return the same
    instance regardless of arguments.

    Usage::

        pool = get_db_pool()
        with pool.connection() as conn:
            ...
    """
    global _db_pool_instance
    if _db_pool_instance is None:
        _db_pool_instance = DatabasePool(**kwargs)
    return _db_pool_instance


def reset_db_pool():
    """
    Tear down the singleton pool.

    Primarily useful in tests or when the application needs to re-initialise
    the pool (e.g. after a configuration change).
    """
    global _db_pool_instance
    if _db_pool_instance is not None:
        _db_pool_instance.close()
        _db_pool_instance = None


# ======================================================================
# Partition helper
# ======================================================================


def generate_partition_sql(
    table_name: str,
    partition_column: str,
    interval: str = "monthly",
    start_date: datetime | None = None,
    num_partitions: int = 12,
) -> str:
    """
    Generate ALTER TABLE SQL for date-based RANGE partitioning.

    Produces a statement that partitions the given table by RANGE on the
    specified date/datetime column, creating ``num_partitions`` partitions
    starting from ``start_date`` and a trailing MAXVALUE catch-all partition.

    Args:
        table_name: Fully-qualified table name.
        partition_column: Column used for partitioning (date or datetime).
        interval: Partition granularity -- ``'monthly'`` or ``'daily'``.
        start_date: First partition boundary. Defaults to the first day of
                    the current month.
        num_partitions: Number of date-range partitions to generate.

    Returns:
        A string containing the ALTER TABLE ... PARTITION BY RANGE SQL.

    Example::

        sql = generate_partition_sql("email_logs", "created_at", interval="monthly")
        print(sql)
        # ALTER TABLE email_logs
        # PARTITION BY RANGE (TO_DAYS(created_at)) (
        #     PARTITION p202602 VALUES LESS THAN (TO_DAYS('2026-03-01')),
        #     ...
        #     PARTITION p_future VALUES LESS THAN MAXVALUE
        # );
    """
    if interval not in ("monthly", "daily"):
        raise ValueError(f"Unsupported interval '{interval}': use 'monthly' or 'daily'")

    if start_date is None:
        today = datetime.utcnow()
        start_date = today.replace(day=1)

    partitions: list[str] = []
    current = start_date

    for _ in range(num_partitions):
        if interval == "monthly":
            # Advance by one month
            if current.month == 12:
                next_boundary = current.replace(year=current.year + 1, month=1, day=1)
            else:
                next_boundary = current.replace(month=current.month + 1, day=1)
            partition_name = f"p{current.strftime('%Y%m')}"
        else:  # daily
            next_boundary = current + timedelta(days=1)
            partition_name = f"p{current.strftime('%Y%m%d')}"

        boundary_str = next_boundary.strftime("%Y-%m-%d")
        partitions.append(
            f"    PARTITION {partition_name} VALUES LESS THAN (TO_DAYS('{boundary_str}'))"
        )
        current = next_boundary

    # Catch-all partition for dates beyond the last explicit boundary
    partitions.append("    PARTITION p_future VALUES LESS THAN MAXVALUE")

    partition_list = ",\n".join(partitions)

    sql = (
        f"ALTER TABLE {table_name}\n"
        f"PARTITION BY RANGE (TO_DAYS({partition_column})) (\n"
        f"{partition_list}\n"
        f");"
    )
    return sql


# ======================================================================
# Internal helpers
# ======================================================================


def _make_serialisable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert row dictionaries so that every value is JSON-serialisable.

    datetime, date, timedelta, bytes, and Decimal values are converted to
    their string representations.
    """
    import decimal
    from datetime import date
    from datetime import timedelta as td

    clean: list[dict[str, Any]] = []
    for row in rows:
        clean_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (datetime, date)):
                clean_row[key] = value.isoformat()
            elif isinstance(value, td):
                clean_row[key] = str(value)
            elif isinstance(value, bytes):
                clean_row[key] = value.decode("utf-8", errors="replace")
            elif isinstance(value, decimal.Decimal):
                clean_row[key] = float(value)
            else:
                clean_row[key] = value
        clean.append(clean_row)
    return clean
