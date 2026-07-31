#!/usr/bin/env python3
"""
Rate Limiter - Configuration Management

This module handles all configuration for the rate limiting service:
- Environment variable loading and validation
- Database connection configuration
- Redis configuration
- Default rate limits and thresholds
- Webhook configuration
- Cache settings

The configuration is designed to be highly customizable while providing
sensible defaults for production environments.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    """Database connection configuration for MySQL/MariaDB"""

    host: str
    port: int
    database: str
    user: str
    password: str
    charset: str = "utf8mb4"
    pool_size: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 3600  # 1 hour


@dataclass
class RedisConfig:
    """Redis connection configuration for distributed caching"""

    host: str
    port: int
    db: int
    password: str | None = None
    socket_timeout: int = 5
    socket_connect_timeout: int = 5
    retry_on_timeout: bool = True
    decode_responses: bool = True


@dataclass
class RateLimitDefaults:
    """Default rate limits for different entity types"""

    organization_inbound_second: int
    organization_inbound_minute: int
    organization_inbound_hourly: int
    organization_inbound_daily: int
    organization_inbound_monthly: int
    organization_inbound_burst: int

    organization_outbound_second: int
    organization_outbound_minute: int
    organization_outbound_hourly: int
    organization_outbound_daily: int
    organization_outbound_monthly: int
    organization_outbound_burst: int

    domain_inbound_second: int
    domain_inbound_minute: int
    domain_inbound_hourly: int
    domain_inbound_daily: int
    domain_inbound_monthly: int
    domain_inbound_burst: int

    domain_outbound_second: int
    domain_outbound_minute: int
    domain_outbound_hourly: int
    domain_outbound_daily: int
    domain_outbound_monthly: int
    domain_outbound_burst: int

    mailbox_inbound_second: int
    mailbox_inbound_minute: int
    mailbox_inbound_hourly: int
    mailbox_inbound_daily: int
    mailbox_inbound_monthly: int
    mailbox_inbound_burst: int

    mailbox_outbound_second: int
    mailbox_outbound_minute: int
    mailbox_outbound_hourly: int
    mailbox_outbound_daily: int
    mailbox_outbound_monthly: int
    mailbox_outbound_burst: int


@dataclass
class CacheConfig:
    """Cache configuration settings"""

    rate_limit_cache_ttl: int = 300  # 5 minutes
    organization_mapping_ttl: int = 600  # 10 minutes
    webhook_urls_ttl: int = 300  # 5 minutes
    usage_stats_ttl: int = 60  # 1 minute


@dataclass
class WebhookConfig:
    """Webhook notification configuration"""

    timeout: int = 10
    max_attempts: int = 3
    retry_delay: int = 5
    rate_limit_per_minute: int | None = None


@dataclass
class AlertConfig:
    """Alert threshold configuration"""

    warning_threshold: int = 80  # 80% usage
    critical_threshold: int = 95  # 95% usage
    exceeded_threshold: int = 100  # 100%+ usage


class RateLimiterConfig:
    """
    Centralized configuration manager for the rate limiting service.

    This class loads configuration from environment variables and provides
    validated configuration objects for all components of the rate limiter.
    """

    def __init__(self):
        """Initialize configuration from environment variables"""
        self._load_environment_config()
        self._validate_config()

        logger.info("Rate limiter configuration loaded successfully")

    def _load_environment_config(self):
        """Load and parse configuration from environment variables"""

        # Service configuration
        self.service_name = os.getenv("RATE_LIMITER_SERVICE_NAME", "rate_limiter")
        self.service_host = os.getenv("RATE_LIMITER_HOST", "0.0.0.0")
        self.service_port = int(os.getenv("RATE_LIMITER_PORT", "8082"))
        self.debug_mode = os.getenv("RATE_LIMITER_DEBUG", "false").lower() == "true"

        # Database configuration
        self.database = DatabaseConfig(
            host=os.getenv("DB_HOST", "mysql"),
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.getenv("DB_NAME", "mailserver"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", "password"),
            charset=os.getenv("DB_CHARSET", "utf8mb4"),
            pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "3600")),
        )

        # Redis configuration
        self.redis = RedisConfig(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "1")),
            password=os.getenv("REDIS_PASSWORD"),
            socket_timeout=int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
            socket_connect_timeout=int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
            retry_on_timeout=os.getenv("REDIS_RETRY_ON_TIMEOUT", "true").lower() == "true",
        )

        # Cache configuration
        self.cache = CacheConfig(
            rate_limit_cache_ttl=int(os.getenv("RATE_LIMIT_CACHE_TTL", "300")),
            organization_mapping_ttl=int(os.getenv("ORG_MAPPING_CACHE_TTL", "600")),
            webhook_urls_ttl=int(os.getenv("WEBHOOK_URLS_CACHE_TTL", "300")),
            usage_stats_ttl=int(os.getenv("USAGE_STATS_CACHE_TTL", "60")),
        )

        # Webhook configuration
        self.webhook = WebhookConfig(
            timeout=int(os.getenv("WEBHOOK_TIMEOUT", "10")),
            max_attempts=int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "3")),
            retry_delay=int(os.getenv("WEBHOOK_RETRY_DELAY", "5")),
            rate_limit_per_minute=int(os.getenv("WEBHOOK_RATE_LIMIT", "0")) or None,
        )

        # Alert configuration
        self.alerts = AlertConfig(
            warning_threshold=int(os.getenv("RATE_LIMIT_WARNING_THRESHOLD", "80")),
            critical_threshold=int(os.getenv("RATE_LIMIT_CRITICAL_THRESHOLD", "95")),
            exceeded_threshold=int(os.getenv("RATE_LIMIT_EXCEEDED_THRESHOLD", "100")),
        )

        # Default rate limits
        self.defaults = RateLimitDefaults(
            # Organization limits (high capacity for production customers)
            organization_inbound_second=int(os.getenv("ORG_INBOUND_SECOND_DEFAULT", "10")),
            organization_inbound_minute=int(os.getenv("ORG_INBOUND_MINUTE_DEFAULT", "100")),
            organization_inbound_hourly=int(os.getenv("ORG_INBOUND_HOURLY_DEFAULT", "5000")),
            organization_inbound_daily=int(os.getenv("ORG_INBOUND_DAILY_DEFAULT", "50000")),
            organization_inbound_monthly=int(os.getenv("ORG_INBOUND_MONTHLY_DEFAULT", "500000")),
            organization_inbound_burst=int(os.getenv("ORG_INBOUND_BURST_DEFAULT", "1000")),
            organization_outbound_second=int(os.getenv("ORG_OUTBOUND_SECOND_DEFAULT", "20")),
            organization_outbound_minute=int(os.getenv("ORG_OUTBOUND_MINUTE_DEFAULT", "200")),
            organization_outbound_hourly=int(os.getenv("ORG_OUTBOUND_HOURLY_DEFAULT", "10000")),
            organization_outbound_daily=int(os.getenv("ORG_OUTBOUND_DAILY_DEFAULT", "100000")),
            organization_outbound_monthly=int(os.getenv("ORG_OUTBOUND_MONTHLY_DEFAULT", "1000000")),
            organization_outbound_burst=int(os.getenv("ORG_OUTBOUND_BURST_DEFAULT", "2000")),
            # Domain limits (moderate capacity for individual domains)
            domain_inbound_second=int(os.getenv("DOMAIN_INBOUND_SECOND_DEFAULT", "2")),
            domain_inbound_minute=int(os.getenv("DOMAIN_INBOUND_MINUTE_DEFAULT", "20")),
            domain_inbound_hourly=int(os.getenv("DOMAIN_INBOUND_HOURLY_DEFAULT", "1000")),
            domain_inbound_daily=int(os.getenv("DOMAIN_INBOUND_DAILY_DEFAULT", "10000")),
            domain_inbound_monthly=int(os.getenv("DOMAIN_INBOUND_MONTHLY_DEFAULT", "100000")),
            domain_inbound_burst=int(os.getenv("DOMAIN_INBOUND_BURST_DEFAULT", "200")),
            domain_outbound_second=int(os.getenv("DOMAIN_OUTBOUND_SECOND_DEFAULT", "5")),
            domain_outbound_minute=int(os.getenv("DOMAIN_OUTBOUND_MINUTE_DEFAULT", "40")),
            domain_outbound_hourly=int(os.getenv("DOMAIN_OUTBOUND_HOURLY_DEFAULT", "2000")),
            domain_outbound_daily=int(os.getenv("DOMAIN_OUTBOUND_DAILY_DEFAULT", "20000")),
            domain_outbound_monthly=int(os.getenv("DOMAIN_OUTBOUND_MONTHLY_DEFAULT", "200000")),
            domain_outbound_burst=int(os.getenv("DOMAIN_OUTBOUND_BURST_DEFAULT", "500")),
            # Mailbox limits (conservative for individual users)
            mailbox_inbound_second=int(os.getenv("MAILBOX_INBOUND_SECOND_DEFAULT", "1")),
            mailbox_inbound_minute=int(os.getenv("MAILBOX_INBOUND_MINUTE_DEFAULT", "5")),
            mailbox_inbound_hourly=int(os.getenv("MAILBOX_INBOUND_HOURLY_DEFAULT", "100")),
            mailbox_inbound_daily=int(os.getenv("MAILBOX_INBOUND_DAILY_DEFAULT", "1000")),
            mailbox_inbound_monthly=int(os.getenv("MAILBOX_INBOUND_MONTHLY_DEFAULT", "10000")),
            mailbox_inbound_burst=int(os.getenv("MAILBOX_INBOUND_BURST_DEFAULT", "50")),
            mailbox_outbound_second=int(os.getenv("MAILBOX_OUTBOUND_SECOND_DEFAULT", "2")),
            mailbox_outbound_minute=int(os.getenv("MAILBOX_OUTBOUND_MINUTE_DEFAULT", "10")),
            mailbox_outbound_hourly=int(os.getenv("MAILBOX_OUTBOUND_HOURLY_DEFAULT", "200")),
            mailbox_outbound_daily=int(os.getenv("MAILBOX_OUTBOUND_DAILY_DEFAULT", "2000")),
            mailbox_outbound_monthly=int(os.getenv("MAILBOX_OUTBOUND_MONTHLY_DEFAULT", "20000")),
            mailbox_outbound_burst=int(os.getenv("MAILBOX_OUTBOUND_BURST_DEFAULT", "100")),
        )

        # Quota reporting
        self.quota_report_interval = int(os.getenv("QUOTA_REPORT_INTERVAL", "300"))  # 5 minutes
        self.cleanup_interval = int(os.getenv("RATE_LIMIT_CLEANUP_INTERVAL", "3600"))  # 1 hour
        self.cleanup_retention_days = int(
            os.getenv("RATE_LIMIT_CLEANUP_RETENTION_DAYS", "90")
        )  # 90 days

        # Performance settings
        self.max_concurrent_checks = int(os.getenv("RATE_LIMIT_MAX_CONCURRENT_CHECKS", "100"))
        self.batch_size = int(os.getenv("RATE_LIMIT_BATCH_SIZE", "100"))

        # Logging configuration
        self.log_level = os.getenv("RATE_LIMITER_LOG_LEVEL", "INFO").upper()
        self.log_format = os.getenv("RATE_LIMITER_LOG_FORMAT", "json")

    def _validate_config(self):
        """Validate configuration values and relationships"""

        # Validate database configuration
        if not all(
            [self.database.host, self.database.database, self.database.user, self.database.password]
        ):
            raise ValueError("Incomplete database configuration")

        if self.database.port <= 0 or self.database.port > 65535:
            raise ValueError(f"Invalid database port: {self.database.port}")

        if self.database.pool_size <= 0:
            raise ValueError(f"Invalid database pool size: {self.database.pool_size}")

        # Validate Redis configuration
        if self.redis.port <= 0 or self.redis.port > 65535:
            raise ValueError(f"Invalid Redis port: {self.redis.port}")

        if self.redis.db < 0:
            raise ValueError(f"Invalid Redis database: {self.redis.db}")

        # Validate service configuration
        if self.service_port <= 0 or self.service_port > 65535:
            raise ValueError(f"Invalid service port: {self.service_port}")

        # Validate alert thresholds
        if not (0 <= self.alerts.warning_threshold <= 100):
            raise ValueError(f"Invalid warning threshold: {self.alerts.warning_threshold}")

        if not (0 <= self.alerts.critical_threshold <= 100):
            raise ValueError(f"Invalid critical threshold: {self.alerts.critical_threshold}")

        if self.alerts.warning_threshold >= self.alerts.critical_threshold:
            raise ValueError("Warning threshold must be less than critical threshold")

        # Validate rate limits (all should be positive)
        for attr_name in dir(self.defaults):
            if not attr_name.startswith("_"):
                value = getattr(self.defaults, attr_name)
                if isinstance(value, int) and value < 0:
                    raise ValueError(f"Invalid rate limit value for {attr_name}: {value}")

        logger.info("Configuration validation completed successfully")

    def get_default_limits(self, entity_type: str, direction: str) -> dict[str, int]:
        """
        Get default rate limits for a specific entity type and direction.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            direction: 'inbound' or 'outbound'

        Returns:
            Dictionary with second, minute, hourly, daily, monthly, and burst limits
        """
        prefix = f"{entity_type}_{direction}"

        return {
            "second_limit": getattr(self.defaults, f"{prefix}_second"),
            "minute_limit": getattr(self.defaults, f"{prefix}_minute"),
            "hourly_limit": getattr(self.defaults, f"{prefix}_hourly"),
            "daily_limit": getattr(self.defaults, f"{prefix}_daily"),
            "monthly_limit": getattr(self.defaults, f"{prefix}_monthly"),
            "burst_limit": getattr(self.defaults, f"{prefix}_burst"),
        }

    def get_database_url(self) -> str:
        """Get database connection URL for SQLAlchemy"""
        return (
            f"mysql+pymysql://{self.database.user}:{self.database.password}"
            f"@{self.database.host}:{self.database.port}/{self.database.database}"
            f"?charset={self.database.charset}"
        )

    def get_redis_connection_kwargs(self) -> dict[str, Any]:
        """Get Redis connection parameters as kwargs"""
        kwargs = {
            "host": self.redis.host,
            "port": self.redis.port,
            "db": self.redis.db,
            "socket_timeout": self.redis.socket_timeout,
            "socket_connect_timeout": self.redis.socket_connect_timeout,
            "retry_on_timeout": self.redis.retry_on_timeout,
            "decode_responses": self.redis.decode_responses,
        }

        if self.redis.password:
            kwargs["password"] = self.redis.password

        return kwargs

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary (for logging/debugging)"""
        return {
            "service": {
                "name": self.service_name,
                "host": self.service_host,
                "port": self.service_port,
                "debug": self.debug_mode,
            },
            "database": {
                "host": self.database.host,
                "port": self.database.port,
                "database": self.database.database,
                "user": self.database.user,
                "pool_size": self.database.pool_size,
            },
            "redis": {"host": self.redis.host, "port": self.redis.port, "db": self.redis.db},
            "cache": {
                "rate_limit_ttl": self.cache.rate_limit_cache_ttl,
                "org_mapping_ttl": self.cache.organization_mapping_ttl,
            },
            "alerts": {
                "warning_threshold": self.alerts.warning_threshold,
                "critical_threshold": self.alerts.critical_threshold,
            },
        }


# Global configuration instance
config = RateLimiterConfig()
