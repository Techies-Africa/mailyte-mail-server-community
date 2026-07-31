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

import logging
import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import mysql.connector
from mysql.connector import pooling
import redis

from config import config

logger = logging.getLogger(__name__)


@dataclass
class RateLimitRule:
    """Rate limit rule configuration"""

    entity_type: str  # 'organization', 'domain', 'mailbox'
    identifier: str
    direction: str  # 'inbound', 'outbound'
    second_limit: int = 0
    minute_limit: int = 0
    hourly_limit: int = 0
    daily_limit: int = 0
    monthly_limit: int = 0
    burst_limit: int = 0
    active: bool = True
    priority: int = 1
    warning_threshold: int = 80
    critical_threshold: int = 95
    description: str = None
    created_by: str = None
    created_at: datetime = None
    updated_at: datetime = None


class RateLimitConfigService:
    """
    Configuration service for rate limiting rules using the new organization structure.

    This service manages rate limit configurations stored in the organization,
    domain, and email_account tables with proper inheritance hierarchy.
    """

    def __init__(self):
        """Initialize the configuration service"""
        self.db_pool = self._create_connection_pool()
        self.redis_client = self._create_redis_client()

        # Cache configuration
        self.cache_ttl = config.cache.rate_limit_cache_ttl
        self.org_mapping_ttl = config.cache.organization_mapping_ttl

        logger.info("Rate limit configuration service initialized")

    def _create_connection_pool(self) -> pooling.MySQLConnectionPool:
        """Create MySQL connection pool"""
        try:
            pool = pooling.MySQLConnectionPool(
                pool_name="rate_limiter_config_pool",
                pool_size=config.database.pool_size,
                pool_reset_session=True,
                host=config.database.host,
                port=config.database.port,
                database=config.database.database,
                user=config.database.user,
                password=config.database.password,
                charset=config.database.charset,
                autocommit=True,
            )

            # Test connection
            conn = pool.get_connection()
            conn.close()

            logger.info("Database connection pool created successfully")
            return pool

        except Exception as e:
            logger.error(f"Failed to create database connection pool: {e}")
            raise

    def _create_redis_client(self) -> Optional[redis.Redis]:
        """Create Redis client for caching"""
        try:
            redis_client = redis.Redis(**config.get_redis_connection_kwargs())
            redis_client.ping()
            logger.info("Redis client connected successfully")
            return redis_client
        except Exception as e:
            logger.warning(f"Redis connection failed, caching disabled: {e}")
            return None

    def get_organization_for_domain(self, domain: str) -> str:
        """
        Get organization ID for a domain with caching.

        Args:
            domain: Domain name

        Returns:
            Organization ID or 'default' if not found
        """
        cache_key = f"org_mapping:domain:{domain}"

        # Try cache first
        if self.redis_client:
            try:
                cached_org = self.redis_client.get(cache_key)
                if cached_org:
                    return cached_org
            except Exception as e:
                logger.warning(f"Redis cache error: {e}")

        # Query database
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            query = """
                SELECT organization_id 
                FROM domains 
                WHERE domain = %s AND active = TRUE
            """
            cursor.execute(query, (domain,))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            org_id = result["organization_id"] if result else "default"

            # Cache the result
            if self.redis_client:
                try:
                    self.redis_client.setex(cache_key, self.org_mapping_ttl, org_id)
                except Exception as e:
                    logger.warning(f"Failed to cache organization mapping: {e}")

            return org_id

        except Exception as e:
            logger.error(f"Failed to get organization for domain {domain}: {e}")
            return "default"

    def get_rate_limit_rule(
        self, entity_type: str, identifier: str, direction: str
    ) -> RateLimitRule:
        """
        Get rate limit rule with proper inheritance hierarchy.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            RateLimitRule with inherited configuration
        """
        cache_key = f"rate_limit_rule:{entity_type}:{identifier}:{direction}"

        # Try cache first
        if self.redis_client:
            try:
                cached_rule = self.redis_client.get(cache_key)
                if cached_rule:
                    rule_data = json.loads(cached_rule)
                    return RateLimitRule(**rule_data)
            except Exception as e:
                logger.warning(f"Cache error for rate limit rule: {e}")

        # Get rule with inheritance
        try:
            if entity_type == "organization":
                rule = self._get_organization_rule(identifier, direction)
            elif entity_type == "domain":
                rule = self._get_domain_rule(identifier, direction)
            elif entity_type == "mailbox":
                rule = self._get_mailbox_rule(identifier, direction)
            else:
                raise ValueError(f"Invalid entity type: {entity_type}")

            # Cache the result
            if self.redis_client:
                try:
                    rule_data = asdict(rule)
                    # Convert datetime objects to strings for JSON serialization
                    for key, value in rule_data.items():
                        if isinstance(value, datetime):
                            rule_data[key] = value.isoformat() if value else None

                    self.redis_client.setex(cache_key, self.cache_ttl, json.dumps(rule_data))
                except Exception as e:
                    logger.warning(f"Failed to cache rate limit rule: {e}")

            return rule

        except Exception as e:
            logger.error(f"Failed to get rate limit rule for {entity_type}:{identifier}: {e}")
            # Return default rule
            return self._get_default_rule(entity_type, direction)

    def _get_organization_rule(self, org_id: str, direction: str) -> RateLimitRule:
        """Get rate limit rule for organization"""
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            query = """
                SELECT rate_limits, active, created_at, updated_at
                FROM organizations 
                WHERE id = %s AND active = TRUE
            """
            cursor.execute(query, (org_id,))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result and result["rate_limits"]:
                rate_limits = result["rate_limits"]
                direction_limits = rate_limits.get(direction, {})

                return RateLimitRule(
                    entity_type="organization",
                    identifier=org_id,
                    direction=direction,
                    second_limit=direction_limits.get("second_limit", 0),
                    minute_limit=direction_limits.get("minute_limit", 0),
                    hourly_limit=direction_limits.get("hourly_limit", 0),
                    daily_limit=direction_limits.get("daily_limit", 0),
                    monthly_limit=direction_limits.get("monthly_limit", 0),
                    burst_limit=direction_limits.get("burst_limit", 0),
                    active=result["active"],
                    warning_threshold=direction_limits.get("warning_threshold", 80),
                    critical_threshold=direction_limits.get("critical_threshold", 95),
                    created_at=result["created_at"],
                    updated_at=result["updated_at"],
                )

            # Return default if no custom limits
            return self._get_default_rule("organization", direction)

        except Exception as e:
            logger.error(f"Failed to get organization rule for {org_id}: {e}")
            return self._get_default_rule("organization", direction)

    def _get_domain_rule(self, domain: str, direction: str) -> RateLimitRule:
        """Get rate limit rule for domain with organization inheritance"""
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            query = """
                SELECT d.rate_limits, d.active, d.organization_id, d.created_at, d.updated_at,
                       o.rate_limits as org_rate_limits, o.active as org_active
                FROM domains d
                LEFT JOIN organizations o ON d.organization_id = o.id
                WHERE d.domain = %s AND d.active = TRUE
            """
            cursor.execute(query, (domain,))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result:
                # Start with organization defaults
                org_limits = {}
                if result["org_rate_limits"] and result["org_active"]:
                    org_limits = result["org_rate_limits"].get(direction, {})

                # Override with domain-specific limits
                domain_limits = {}
                if result["rate_limits"]:
                    domain_limits = result["rate_limits"].get(direction, {})

                # Merge limits (domain overrides organization)
                merged_limits = {**org_limits, **domain_limits}

                return RateLimitRule(
                    entity_type="domain",
                    identifier=domain,
                    direction=direction,
                    second_limit=merged_limits.get("second_limit", 0),
                    minute_limit=merged_limits.get("minute_limit", 0),
                    hourly_limit=merged_limits.get("hourly_limit", 0),
                    daily_limit=merged_limits.get("daily_limit", 0),
                    monthly_limit=merged_limits.get("monthly_limit", 0),
                    burst_limit=merged_limits.get("burst_limit", 0),
                    active=result["active"],
                    warning_threshold=merged_limits.get("warning_threshold", 80),
                    critical_threshold=merged_limits.get("critical_threshold", 95),
                    created_at=result["created_at"],
                    updated_at=result["updated_at"],
                )

            return self._get_default_rule("domain", direction)

        except Exception as e:
            logger.error(f"Failed to get domain rule for {domain}: {e}")
            return self._get_default_rule("domain", direction)

    def _get_mailbox_rule(self, email: str, direction: str) -> RateLimitRule:
        """Get rate limit rule for mailbox with domain and organization inheritance"""
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            query = """
                SELECT ea.rate_limits, ea.status, ea.created_at, ea.updated_at,
                       d.rate_limits as domain_rate_limits, d.domain,
                       o.rate_limits as org_rate_limits, o.active as org_active
                FROM email_accounts ea
                LEFT JOIN domains d ON ea.domain_id = d.id
                LEFT JOIN organizations o ON ea.organization_id = o.id
                WHERE ea.email = %s AND ea.status = 'ACTIVE'
            """
            cursor.execute(query, (email,))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result:
                # Start with organization defaults
                org_limits = {}
                if result["org_rate_limits"] and result["org_active"]:
                    org_limits = result["org_rate_limits"].get(direction, {})

                # Override with domain limits
                domain_limits = {}
                if result["domain_rate_limits"]:
                    domain_limits = result["domain_rate_limits"].get(direction, {})

                # Override with mailbox-specific limits
                mailbox_limits = {}
                if result["rate_limits"]:
                    mailbox_limits = result["rate_limits"].get(direction, {})

                # Merge limits (mailbox > domain > organization)
                merged_limits = {**org_limits, **domain_limits, **mailbox_limits}

                return RateLimitRule(
                    entity_type="mailbox",
                    identifier=email,
                    direction=direction,
                    second_limit=merged_limits.get("second_limit", 0),
                    minute_limit=merged_limits.get("minute_limit", 0),
                    hourly_limit=merged_limits.get("hourly_limit", 0),
                    daily_limit=merged_limits.get("daily_limit", 0),
                    monthly_limit=merged_limits.get("monthly_limit", 0),
                    burst_limit=merged_limits.get("burst_limit", 0),
                    active=result["status"] == "ACTIVE",
                    warning_threshold=merged_limits.get("warning_threshold", 80),
                    critical_threshold=merged_limits.get("critical_threshold", 95),
                    created_at=result["created_at"],
                    updated_at=result["updated_at"],
                )

            return self._get_default_rule("mailbox", direction)

        except Exception as e:
            logger.error(f"Failed to get mailbox rule for {email}: {e}")
            return self._get_default_rule("mailbox", direction)

    def _get_default_rule(self, entity_type: str, direction: str) -> RateLimitRule:
        """Get default rate limit rule from configuration"""
        defaults = config.get_default_limits(entity_type, direction)

        return RateLimitRule(
            entity_type=entity_type,
            identifier="default",
            direction=direction,
            second_limit=defaults.get("second_limit", 0),
            minute_limit=defaults.get("minute_limit", 0),
            hourly_limit=defaults["hourly_limit"],
            daily_limit=defaults["daily_limit"],
            monthly_limit=defaults["monthly_limit"],
            burst_limit=defaults["burst_limit"],
            active=True,
            warning_threshold=80,
            critical_threshold=95,
            description=f"Default {entity_type} {direction} limits",
        )

    def create_rate_limit_rule(self, rule: RateLimitRule) -> bool:
        """
        Create or update rate limit rule in the appropriate table.

        Args:
            rule: RateLimitRule to create/update

        Returns:
            bool: True if successful
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # Prepare rate limits JSON
            rate_limits_data = {
                rule.direction: {
                    "second_limit": rule.second_limit,
                    "minute_limit": rule.minute_limit,
                    "hourly_limit": rule.hourly_limit,
                    "daily_limit": rule.daily_limit,
                    "monthly_limit": rule.monthly_limit,
                    "burst_limit": rule.burst_limit,
                    "warning_threshold": rule.warning_threshold,
                    "critical_threshold": rule.critical_threshold,
                }
            }

            if rule.entity_type == "organization":
                query = """
                    UPDATE organizations 
                    SET rate_limits = JSON_MERGE_PATCH(COALESCE(rate_limits, '{}'), %s),
                        updated_at = NOW()
                    WHERE id = %s
                """
                cursor.execute(query, (json.dumps(rate_limits_data), rule.identifier))

            elif rule.entity_type == "domain":
                query = """
                    UPDATE domains 
                    SET rate_limits = JSON_MERGE_PATCH(COALESCE(rate_limits, '{}'), %s),
                        updated_at = NOW()
                    WHERE domain = %s
                """
                cursor.execute(query, (json.dumps(rate_limits_data), rule.identifier))

            elif rule.entity_type == "mailbox":
                query = """
                    UPDATE email_accounts 
                    SET rate_limits = JSON_MERGE_PATCH(COALESCE(rate_limits, '{}'), %s),
                        updated_at = NOW()
                    WHERE email = %s
                """
                cursor.execute(query, (json.dumps(rate_limits_data), rule.identifier))

            cursor.close()
            conn.close()

            # Invalidate cache
            self._invalidate_cache(rule.entity_type, rule.identifier, rule.direction)

            logger.info(f"Rate limit rule created/updated for {rule.entity_type}:{rule.identifier}")
            return True

        except Exception as e:
            logger.error(f"Failed to create rate limit rule: {e}")
            return False

    def _invalidate_cache(self, entity_type: str, identifier: str, direction: str):
        """Invalidate cached rate limit rule"""
        if not self.redis_client:
            return

        try:
            cache_key = f"rate_limit_rule:{entity_type}:{identifier}:{direction}"
            self.redis_client.delete(cache_key)

            # Also invalidate organization mapping if it's a domain
            if entity_type == "domain":
                org_cache_key = f"org_mapping:domain:{identifier}"
                self.redis_client.delete(org_cache_key)

        except Exception as e:
            logger.warning(f"Failed to invalidate cache: {e}")

    def get_config_stats(self) -> Dict[str, Any]:
        """Get configuration service statistics"""
        stats = {
            "service": "config_service",
            "status": "healthy",
            "database_pool_size": self.db_pool.pool_size
            if hasattr(self.db_pool, "pool_size")
            else 0,
            "redis_connected": self.redis_client is not None,
            "cache_ttl": self.cache_ttl,
        }

        # Add database connection test
        try:
            conn = self.db_pool.get_connection()
            conn.close()
            stats["database_status"] = "connected"
        except Exception:
            stats["database_status"] = "disconnected"

        # Add Redis connection test
        if self.redis_client:
            try:
                self.redis_client.ping()
                stats["redis_status"] = "connected"
            except Exception:
                stats["redis_status"] = "disconnected"
        else:
            stats["redis_status"] = "disabled"

        return stats


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
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None


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

    def _init_database_pool(self) -> Optional[pooling.MySQLConnectionPool]:
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

    def _init_redis(self) -> Optional[redis.Redis]:
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

            # Query for specific configuration
            query = """
                SELECT type, identifier, direction, second_limit, minute_limit,
                       hourly_limit, daily_limit, monthly_limit, burst_limit, 
                       active, priority, warning_threshold, critical_threshold, 
                       description, created_at, updated_at, created_by
                FROM rate_limit_configs 
                WHERE type = %s AND identifier = %s AND direction = %s AND active = 1
                ORDER BY priority ASC
                LIMIT 1
            """

            cursor.execute(query, (entity_type, identifier, direction))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result:
                logger.debug(f"Found database config for {entity_type}:{identifier}:{direction}")
                return RateLimitRule(
                    entity_type=result["type"],
                    identifier=result["identifier"],
                    direction=result["direction"],
                    second_limit=result.get("second_limit", 0),
                    minute_limit=result.get("minute_limit", 0),
                    hourly_limit=result["hourly_limit"],
                    daily_limit=result["daily_limit"],
                    monthly_limit=result["monthly_limit"],
                    burst_limit=result["burst_limit"],
                    active=bool(result["active"]),
                    priority=result["priority"],
                    warning_threshold=result["warning_threshold"],
                    critical_threshold=result["critical_threshold"],
                    description=result["description"],
                    created_at=result["created_at"],
                    updated_at=result["updated_at"],
                    created_by=result["created_by"],
                )

        except Exception as e:
            logger.error(f"Database query failed for rate limit config: {e}")

        # Return default configuration
        return self._get_default_rule(entity_type, identifier, direction)

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

            # Query for domain's organization
            query = """
                SELECT domain as organization_id
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

        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # Insert or update configuration
            query = """
                INSERT INTO rate_limit_configs 
                (type, identifier, direction, second_limit, minute_limit, hourly_limit, 
                 daily_limit, monthly_limit, burst_limit, active, priority, 
                 warning_threshold, critical_threshold, description, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                second_limit = VALUES(second_limit),
                minute_limit = VALUES(minute_limit),
                hourly_limit = VALUES(hourly_limit),
                daily_limit = VALUES(daily_limit),
                monthly_limit = VALUES(monthly_limit),
                burst_limit = VALUES(burst_limit),
                active = VALUES(active),
                priority = VALUES(priority),
                warning_threshold = VALUES(warning_threshold),
                critical_threshold = VALUES(critical_threshold),
                description = VALUES(description),
                updated_at = CURRENT_TIMESTAMP
            """

            cursor.execute(
                query,
                (
                    rule.entity_type,
                    rule.identifier,
                    rule.direction,
                    rule.second_limit,
                    rule.minute_limit,
                    rule.hourly_limit,
                    rule.daily_limit,
                    rule.monthly_limit,
                    rule.burst_limit,
                    rule.active,
                    rule.priority,
                    rule.warning_threshold,
                    rule.critical_threshold,
                    rule.description,
                    rule.created_by,
                ),
            )

            cursor.close()
            conn.close()

            # Invalidate cache
            self._invalidate_config_cache(rule.entity_type, rule.identifier, rule.direction)

            logger.info(
                f"Created/updated rate limit rule for {rule.entity_type}:{rule.identifier}:{rule.direction}"
            )
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

            query = """
                DELETE FROM rate_limit_configs 
                WHERE type = %s AND identifier = %s AND direction = %s
            """

            cursor.execute(query, (entity_type, identifier, direction))
            affected_rows = cursor.rowcount

            cursor.close()
            conn.close()

            if affected_rows > 0:
                # Invalidate cache
                self._invalidate_config_cache(entity_type, identifier, direction)
                logger.info(f"Deleted rate limit rule for {entity_type}:{identifier}:{direction}")
                return True
            else:
                logger.warning(
                    f"No rate limit rule found to delete for {entity_type}:{identifier}:{direction}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to delete rate limit rule: {e}")
            return False

    def list_rate_limit_rules(
        self, entity_type: Optional[str] = None, identifier: Optional[str] = None
    ) -> List[RateLimitRule]:
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
                SELECT type, identifier, direction, second_limit, minute_limit,
                       hourly_limit, daily_limit, monthly_limit, burst_limit, 
                       active, priority, warning_threshold, critical_threshold, 
                       description, created_at, updated_at, created_by
                FROM rate_limit_configs 
                WHERE 1=1
            """
            params = []

            if entity_type:
                query += " AND type = %s"
                params.append(entity_type)

            if identifier:
                query += " AND identifier = %s"
                params.append(identifier)

            query += " ORDER BY type, identifier, direction"

            cursor.execute(query, params)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Convert to RateLimitRule objects
            rules = []
            for result in results:
                rule = RateLimitRule(
                    entity_type=result["type"],
                    identifier=result["identifier"],
                    direction=result["direction"],
                    second_limit=result.get("second_limit", 0),
                    minute_limit=result.get("minute_limit", 0),
                    hourly_limit=result["hourly_limit"],
                    daily_limit=result["daily_limit"],
                    monthly_limit=result["monthly_limit"],
                    burst_limit=result["burst_limit"],
                    active=bool(result["active"]),
                    priority=result["priority"],
                    warning_threshold=result["warning_threshold"],
                    critical_threshold=result["critical_threshold"],
                    description=result["description"],
                    created_at=result["created_at"],
                    updated_at=result["updated_at"],
                    created_by=result["created_by"],
                )
                rules.append(rule)

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

    def get_config_stats(self) -> Dict[str, Any]:
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

        # Default rate limits for different entity types with spam prevention
        self.default_limits = {
            "organization": {
                "second_limit": 50,  # Max 50 emails per second org-wide
                "minute_limit": 1000,  # Max 1000 emails per minute org-wide
                "hourly_limit": 10000,
                "daily_limit": 100000,
                "monthly_limit": 1000000,
                "burst_limit": 1000,
            },
            "domain": {
                "second_limit": 20,  # Max 20 emails per second per domain
                "minute_limit": 500,  # Max 500 emails per minute per domain
                "hourly_limit": 5000,
                "daily_limit": 50000,
                "monthly_limit": 500000,
                "burst_limit": 500,
            },
            "mailbox": {
                "second_limit": 5,  # Max 5 emails per second per mailbox (strict anti-spam)
                "minute_limit": 100,  # Max 100 emails per minute per mailbox
                "hourly_limit": 1000,
                "daily_limit": 10000,
                "monthly_limit": 100000,
                "burst_limit": 100,
            },
        }
