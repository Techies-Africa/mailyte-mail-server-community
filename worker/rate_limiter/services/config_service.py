#!/usr/bin/env python3
"""
Rate Limiter - Configuration Service

This service manages rate limit configurations stored in the database:
- Loading and caching rate limit rules
- Managing default configurations
- Organization, domain, and mailbox mappings
- Configuration validation and updates
- Cache invalidation on configuration changes

The service provides a high-performance interface for retrieving rate limit
configurations while ensuring data consistency and minimal database load.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import redis
from mysql.connector import pooling

from config import config

logger = logging.getLogger(__name__)

# The real rate_limit_configs table (confirmed via DESCRIBE, 2026-08-08) is
# one row PER TIME WINDOW -- (organization_id, entity_type, identifier,
# window, max_requests, warning_pct, critical_pct) -- not one row per entity
# with every window as a sibling column, which is what this file's queries
# assumed until now (every real DB call failed with "Unknown column 'type'
# in 'field list'", silently falling through to defaults). There is also no
# `direction`, `active`, `priority`, `description`, or `created_by` column
# on the real table -- a persisted row applies to both directions, is
# always active, and burst_limit is never persisted (the enum has no
# 'burst' value, and app.py's own enforcement never checks burst_limit
# anyway -- see _check_single_entity_limit).
_WINDOW_FIELD_MAP = {
    "second": "second_limit",
    "minute": "minute_limit",
    "hour": "hourly_limit",
    "day": "daily_limit",
    "month": "monthly_limit",
}


@dataclass
class RateLimitRule:
    """
    Rate limit rule configuration for a specific entity.

    This class encapsulates all rate limiting parameters for an organization,
    domain, or mailbox, including limits, thresholds, and metadata.
    """

    # Entity identification
    entity_type: str  # 'organization', 'domain', 'mailbox'
    identifier: str  # Actual identifier (org ID, domain name, email address)
    direction: str  # 'inbound' or 'outbound'

    # Rate limits (emails per time window) - 0 means no limit for that window
    second_limit: int = 0  # Max emails per second (spam prevention)
    minute_limit: int = 0  # Max emails per minute (burst protection)
    hourly_limit: int = 0
    daily_limit: int = 0
    monthly_limit: int = 0
    burst_limit: int = 0  # Legacy field for compatibility

    # Configuration
    active: bool = True
    priority: int = 1

    # Alert thresholds (percentage)
    warning_threshold: int = 80
    critical_threshold: int = 95

    # Metadata
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None


class RateLimitConfigService:
    """
    Service for managing rate limit configurations with caching and database fallback.

    This service provides a high-performance interface for retrieving and managing
    rate limit configurations. It uses Redis for caching to minimize database
    queries while ensuring data consistency.
    """

    def __init__(self):
        """Initialize the configuration service with database and cache connections"""
        self.config = config
        self.db_pool = self._init_database_pool()
        self.redis_client = self._init_redis()

        # Cache TTL settings
        self.config_cache_ttl = self.config.cache.rate_limit_cache_ttl
        self.org_mapping_ttl = self.config.cache.organization_mapping_ttl

        logger.info("Rate limit configuration service initialized")

    def _init_database_pool(self) -> pooling.MySQLConnectionPool | None:
        """
        Initialize MySQL connection pool for database operations.

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
                "pool_name": "rate_limit_config_pool",
                "pool_size": self.config.database.pool_size,
                "pool_reset_session": True,
            }

            pool = pooling.MySQLConnectionPool(**pool_config)
            logger.info(
                f"Database connection pool created with {self.config.database.pool_size} connections"
            )
            return pool

        except Exception as e:
            logger.error(f"Failed to create database connection pool: {e}")
            return None

    def _init_redis(self) -> redis.Redis | None:
        """
        Initialize Redis connection for caching.

        Returns:
            Redis client or None if connection fails
        """
        try:
            redis_kwargs = self.config.get_redis_connection_kwargs()
            redis_client = redis.Redis(**redis_kwargs)

            # Test connection
            redis_client.ping()
            logger.info("Redis connection established for configuration caching")
            return redis_client

        except Exception as e:
            logger.warning(f"Redis connection failed, using database-only mode: {e}")
            return None

    def get_rate_limit_rule(
        self, entity_type: str, identifier: str, direction: str
    ) -> RateLimitRule:
        """
        Get rate limit rule for a specific entity with caching.

        This method first checks the Redis cache for the configuration,
        then falls back to the database if not found. It also handles
        default configurations when no specific rule exists.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier (org ID, domain name, email)
            direction: 'inbound' or 'outbound'

        Returns:
            RateLimitRule: Rate limit configuration for the entity
        """
        # Generate cache key
        cache_key = f"rate_config:{entity_type}:{identifier}:{direction}"

        # Try to get from cache first
        if self.redis_client:
            try:
                cached_config = self.redis_client.get(cache_key)
                if cached_config:
                    config_data = json.loads(cached_config)
                    logger.debug(f"Rate limit config cache hit for {cache_key}")
                    return RateLimitRule(**config_data)
            except Exception as e:
                logger.warning(f"Cache read failed for {cache_key}: {e}")

        # Fallback to database
        rule = self._get_rule_from_database(entity_type, identifier, direction)

        # Cache the result
        if self.redis_client and rule:
            try:
                self.redis_client.setex(
                    cache_key, self.config_cache_ttl, json.dumps(asdict(rule), default=str)
                )
                logger.debug(f"Cached rate limit config for {cache_key}")
            except Exception as e:
                logger.warning(f"Cache write failed for {cache_key}: {e}")

        return rule

    def _get_rule_from_database(
        self, entity_type: str, identifier: str, direction: str
    ) -> RateLimitRule:
        """
        Retrieve rate limit rule from database or return default.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            RateLimitRule: Database configuration or default rule
        """
        if not self.db_pool:
            return self._get_default_rule(entity_type, identifier, direction)

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            # One row per window for this entity -- assembled into a single
            # RateLimitRule below, not a single-row SELECT.
            query = """
                SELECT `window`, max_requests, warning_pct, critical_pct
                FROM rate_limit_configs
                WHERE entity_type = %s AND identifier = %s
            """
            cursor.execute(query, (entity_type, identifier))
            rows = cursor.fetchall()

            cursor.close()
            conn.close()

            if not rows:
                return self._get_default_rule(entity_type, identifier, direction)

            logger.debug(f"Found database config for {entity_type}:{identifier}")
            limits = {field: 0 for field in _WINDOW_FIELD_MAP.values()}
            warning_threshold, critical_threshold = 80, 95
            for row in rows:
                field = _WINDOW_FIELD_MAP.get(row["window"])
                if field:
                    limits[field] = row["max_requests"]
                warning_threshold = row["warning_pct"]
                critical_threshold = row["critical_pct"]

            return RateLimitRule(
                entity_type=entity_type,
                identifier=identifier,
                direction=direction,
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                **limits,
            )

        except Exception as e:
            logger.error(f"Database query failed for rate limit config: {e}")

        # Return default configuration
        return self._get_default_rule(entity_type, identifier, direction)

    def _resolve_organization_id(self, entity_type: str, identifier: str) -> str:
        """The real table's organization_id column is always populated,
        regardless of entity_type, so every row can be filtered/reported by
        owning org (e.g. by the sudo console) without joining back through
        domains/email_accounts every time."""
        if entity_type == "organization":
            return identifier
        if entity_type == "domain":
            return self.get_organization_for_domain(identifier)
        if entity_type == "mailbox" and "@" in identifier:
            return self.get_organization_for_domain(identifier.split("@", 1)[-1])
        return identifier

    def _get_default_rule(self, entity_type: str, identifier: str, direction: str) -> RateLimitRule:
        """
        Generate default rate limit rule based on entity type and direction.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            RateLimitRule: Default rate limit configuration
        """
        defaults = self.config.get_default_limits(entity_type, direction)

        logger.debug(f"Using default rate limits for {entity_type}:{identifier}:{direction}")

        return RateLimitRule(
            entity_type=entity_type,
            identifier=identifier,
            direction=direction,
            second_limit=defaults.get("second_limit", 0),  # 0 means no limit
            minute_limit=defaults.get("minute_limit", 0),
            hourly_limit=defaults.get("hourly_limit", 0),
            daily_limit=defaults.get("daily_limit", 0),
            monthly_limit=defaults.get("monthly_limit", 0),
            burst_limit=defaults.get("burst_limit", 0),
            active=True,
            priority=999,  # Lowest priority for defaults
            warning_threshold=self.config.alerts.warning_threshold,
            critical_threshold=self.config.alerts.critical_threshold,
            description=f"Default limits for {entity_type}",
        )

    def get_organization_for_domain(self, domain: str) -> str:
        """
        Get organization ID for a domain with caching.

        This method maps domains to organizations for hierarchical rate limiting.
        It caches the mapping to reduce database queries.

        Args:
            domain: Domain name to look up

        Returns:
            str: Organization ID (defaults to domain if not found)
        """
        cache_key = f"org_mapping:{domain}"

        # Try cache first
        if self.redis_client:
            try:
                cached_org = self.redis_client.get(cache_key)
                if cached_org:
                    logger.debug(f"Organization mapping cache hit for {domain}")
                    return cached_org
            except Exception as e:
                logger.warning(f"Cache read failed for org mapping {domain}: {e}")

        # Query database
        organization_id = self._get_organization_from_database(domain)

        # Cache the result
        if self.redis_client:
            try:
                self.redis_client.setex(cache_key, self.org_mapping_ttl, organization_id)
                logger.debug(f"Cached organization mapping for {domain}")
            except Exception as e:
                logger.warning(f"Cache write failed for org mapping {domain}: {e}")

        return organization_id

    def _get_organization_from_database(self, domain: str) -> str:
        """
        Query database for organization ID associated with domain.

        Args:
            domain: Domain name to look up

        Returns:
            str: Organization ID or domain name as fallback
        """
        if not self.db_pool:
            return domain

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # Query for domain's organization. Was `SELECT domain as
            # organization_id` -- returning the domain name itself mislabeled
            # as organization_id, never the real column, on every call.
            query = """
                SELECT organization_id
                FROM domains
                WHERE domain = %s AND active = 1
                LIMIT 1
            """

            cursor.execute(query, (domain,))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result:
                organization_id = result[0]
                logger.debug(f"Found organization {organization_id} for domain {domain}")
                return organization_id

        except Exception as e:
            logger.error(f"Failed to get organization for domain {domain}: {e}")

        # Fallback to using domain as organization ID
        logger.debug(f"Using domain {domain} as organization ID (fallback)")
        return domain

    def get_smtp_credential(self, username: str) -> dict | None:
        """
        Resolve an SMTP API-key identity (a SASL username with no '@') to its
        org, domain and per-key limits (00-PRD-smtp-api-keys K2).

        Cached for 60s: the policy service asks once per RCPT, and a
        revoked/limits-changed key tolerates a minute of staleness here
        because authentication itself is enforced (and cache-flushed) at the
        Dovecot layer -- this lookup only shapes rate limiting.

        Returns None when the username is unknown -- callers treat that as
        fail-closed for an authenticated identity, per the PRD: unknown
        authenticated sender != infrastructure error.
        """
        cache_key = f"smtp_credential:{username}"
        if self.redis_client:
            try:
                cached = self.redis_client.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return data if data else None  # {} caches a miss
            except Exception as e:
                logger.warning(f"Cache read failed for {cache_key}: {e}")

        if not self.db_pool:
            raise RuntimeError("database not available")

        conn = self.db_pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT sc.username, sc.organization_id, d.domain,
                       sc.hourly_limit, sc.daily_limit, sc.active
                FROM smtp_credentials sc
                JOIN domains d ON d.id = sc.domain_id
                WHERE sc.username = %s
                LIMIT 1
                """,
                (username,),
            )
            row = cursor.fetchone()
            cursor.close()
        finally:
            conn.close()

        if self.redis_client:
            try:
                self.redis_client.setex(cache_key, 60, json.dumps(row or {}, default=str))
            except Exception as e:
                logger.warning(f"Cache write failed for {cache_key}: {e}")

        return row

    def create_rate_limit_rule(self, rule: RateLimitRule) -> bool:
        """
        Create or update a rate limit rule in the database.

        Args:
            rule: Rate limit rule to create/update

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.db_pool:
            logger.error("Cannot create rate limit rule: database not available")
            return False

        organization_id = self._resolve_organization_id(rule.entity_type, rule.identifier)

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # No unique constraint on (entity_type, identifier, window) in
            # the real table (confirmed via SHOW INDEX), so this is a manual
            # check-then-insert-or-update per window rather than a
            # single ON DUPLICATE KEY UPDATE statement.
            for window, field in _WINDOW_FIELD_MAP.items():
                max_requests = getattr(rule, field)

                cursor.execute(
                    "SELECT id FROM rate_limit_configs "
                    "WHERE entity_type = %s AND identifier = %s AND `window` = %s",
                    (rule.entity_type, rule.identifier, window),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute(
                        "UPDATE rate_limit_configs "
                        "SET organization_id = %s, max_requests = %s, warning_pct = %s, "
                        "critical_pct = %s WHERE id = %s",
                        (
                            organization_id,
                            max_requests,
                            rule.warning_threshold,
                            rule.critical_threshold,
                            existing[0],
                        ),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO rate_limit_configs "
                        "(organization_id, entity_type, identifier, `window`, max_requests, "
                        "warning_pct, critical_pct) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            organization_id,
                            rule.entity_type,
                            rule.identifier,
                            window,
                            max_requests,
                            rule.warning_threshold,
                            rule.critical_threshold,
                        ),
                    )

            conn.commit()
            cursor.close()
            conn.close()

            # Invalidate cache
            self._invalidate_config_cache(rule.entity_type, rule.identifier, rule.direction)

            logger.info(f"Created/updated rate limit rule for {rule.entity_type}:{rule.identifier}")
            return True

        except Exception as e:
            logger.error(f"Failed to create rate limit rule: {e}")
            return False

    def delete_rate_limit_rule(self, entity_type: str, identifier: str, direction: str) -> bool:
        """
        Delete a rate limit rule from the database.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.db_pool:
            logger.error("Cannot delete rate limit rule: database not available")
            return False

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # Deletes every window row for this entity -- the real table has
            # no direction column, so a rule isn't scoped by direction here.
            query = "DELETE FROM rate_limit_configs WHERE entity_type = %s AND identifier = %s"

            cursor.execute(query, (entity_type, identifier))
            affected_rows = cursor.rowcount
            conn.commit()

            cursor.close()
            conn.close()

            if affected_rows > 0:
                # Invalidate cache
                self._invalidate_config_cache(entity_type, identifier, direction)
                logger.info(
                    f"Deleted rate limit rule for {entity_type}:{identifier} "
                    f"({affected_rows} window rows)"
                )
                return True
            else:
                logger.warning(f"No rate limit rule found to delete for {entity_type}:{identifier}")
                return False

        except Exception as e:
            logger.error(f"Failed to delete rate limit rule: {e}")
            return False

    def list_rate_limit_rules(
        self, entity_type: str | None = None, identifier: str | None = None
    ) -> list[RateLimitRule]:
        """
        List rate limit rules with optional filtering.

        Args:
            entity_type: Optional filter by entity type
            identifier: Optional filter by identifier

        Returns:
            List[RateLimitRule]: List of matching rate limit rules
        """
        if not self.db_pool:
            logger.error("Cannot list rate limit rules: database not available")
            return []

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            # Build query with optional filters
            query = """
                SELECT entity_type, identifier, `window`, max_requests, warning_pct, critical_pct
                FROM rate_limit_configs
                WHERE 1=1
            """
            params = []

            if entity_type:
                query += " AND entity_type = %s"
                params.append(entity_type)

            if identifier:
                query += " AND identifier = %s"
                params.append(identifier)

            query += " ORDER BY entity_type, identifier"

            cursor.execute(query, params)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Group the one-row-per-window results back into one
            # RateLimitRule per (entity_type, identifier).
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for row in results:
                key = (row["entity_type"], row["identifier"])
                limits = grouped.setdefault(key, {field: 0 for field in _WINDOW_FIELD_MAP.values()})
                field = _WINDOW_FIELD_MAP.get(row["window"])
                if field:
                    limits[field] = row["max_requests"]
                limits["warning_threshold"] = row["warning_pct"]
                limits["critical_threshold"] = row["critical_pct"]

            # direction is a Laravel/app-level concept only -- the real
            # table has no such column, so a listed rule can't report one.
            rules = [
                RateLimitRule(entity_type=et, identifier=ident, direction="outbound", **limits)
                for (et, ident), limits in grouped.items()
            ]

            logger.info(f"Retrieved {len(rules)} rate limit rules")
            return rules

        except Exception as e:
            logger.error(f"Failed to list rate limit rules: {e}")
            return []

    def _invalidate_config_cache(self, entity_type: str, identifier: str, direction: str):
        """
        Invalidate cached configuration for a specific entity.

        Args:
            entity_type: Entity type
            identifier: Entity identifier
            direction: Rate limit direction
        """
        if not self.redis_client:
            return

        try:
            cache_key = f"rate_config:{entity_type}:{identifier}:{direction}"
            self.redis_client.delete(cache_key)
            logger.debug(f"Invalidated config cache for {cache_key}")
        except Exception as e:
            logger.warning(f"Failed to invalidate config cache: {e}")

    def get_config_stats(self) -> dict[str, Any]:
        """
        Get configuration service statistics.

        Returns:
            Dict containing service statistics
        """
        stats = {
            "service": "rate_limit_config",
            "database_available": self.db_pool is not None,
            "redis_available": self.redis_client is not None,
            "cache_ttl": self.config_cache_ttl,
            "org_mapping_ttl": self.org_mapping_ttl,
        }

        # Get cache statistics if Redis is available
        if self.redis_client:
            try:
                info = self.redis_client.info()
                stats["redis_stats"] = {
                    "connected_clients": info.get("connected_clients", 0),
                    "used_memory": info.get("used_memory", 0),
                    "keyspace_hits": info.get("keyspace_hits", 0),
                    "keyspace_misses": info.get("keyspace_misses", 0),
                }
            except Exception as e:
                logger.warning(f"Failed to get Redis stats: {e}")

        return stats
