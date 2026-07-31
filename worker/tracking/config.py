#!/usr/bin/env python3

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TrackingConfig:
    """Tracking configuration data class"""

    # Core tracking settings
    enabled: bool = True
    open_tracking_enabled: bool = True
    click_tracking_enabled: bool = True

    # Tracking pixel settings
    pixel_cache_control: str = "no-cache, no-store, must-revalidate"
    pixel_expires: int = 0
    pixel_size: str = "1x1"

    # Click tracking settings
    click_redirect_timeout: int = 30
    click_preserve_query_params: bool = True
    click_preserve_fragments: bool = True

    # Security settings
    tracking_domain: str = ""
    tracking_subdomain: str = "track"
    tracking_protocol: str = "https"
    require_ssl: bool = True

    # Database settings
    batch_insert_size: int = 100
    connection_pool_size: int = 10
    connection_timeout: int = 30

    # Webhook settings
    webhook_enabled: bool = True
    webhook_timeout: int = 10
    webhook_retry_attempts: int = 3
    webhook_retry_delay: int = 5

    # Analytics settings
    track_user_agent: bool = True
    track_ip_address: bool = True
    track_geolocation: bool = True
    track_device_info: bool = True
    track_referrer: bool = True

    # Privacy settings
    anonymize_ip: bool = False
    ip_retention_days: int = 90
    tracking_data_retention_days: int = 365

    # Rate limiting
    rate_limiting_enabled: bool = True

    # Performance settings
    async_logging: bool = True
    cache_tracking_configs: bool = True
    cache_ttl: int = 300

    # Link rewriting settings
    exclude_domains: list[str] = None
    exclude_link_patterns: list[str] = None
    preserve_utm_params: bool = True

    # Rate limiting
    rate_limit_per_ip: int = 1000
    rate_limit_window: int = 3600

    def __post_init__(self):
        if self.exclude_domains is None:
            self.exclude_domains = []
        if self.exclude_link_patterns is None:
            self.exclude_link_patterns = ["mailto:", "tel:", "ftp:", "file:"]


class ConfigManager:
    """Centralized configuration manager for tracking service"""

    def __init__(self):
        self.config = self._load_config()
        self._validate_config()

    def _load_config(self) -> TrackingConfig:
        """Load configuration from environment variables"""
        return TrackingConfig(
            # Core tracking settings
            enabled=self._get_bool("TRACKING_ENABLED", True),
            open_tracking_enabled=self._get_bool("OPEN_TRACKING_ENABLED", True),
            click_tracking_enabled=self._get_bool("CLICK_TRACKING_ENABLED", True),
            # Tracking pixel settings
            pixel_cache_control=os.getenv(
                "TRACKING_PIXEL_CACHE_CONTROL", "no-cache, no-store, must-revalidate"
            ),
            pixel_expires=self._get_int("TRACKING_PIXEL_EXPIRES", 0),
            pixel_size=os.getenv("TRACKING_PIXEL_SIZE", "1x1"),
            # Click tracking settings
            click_redirect_timeout=self._get_int("CLICK_REDIRECT_TIMEOUT", 30),
            click_preserve_query_params=self._get_bool("CLICK_PRESERVE_QUERY_PARAMS", True),
            click_preserve_fragments=self._get_bool("CLICK_PRESERVE_FRAGMENTS", True),
            # Security settings
            tracking_domain=os.getenv(
                "TRACKING_DOMAIN", os.getenv("HOSTNAME", "mail.yourdomain.com")
            ),
            tracking_subdomain=os.getenv("TRACKING_SUBDOMAIN", "track"),
            tracking_protocol=os.getenv("TRACKING_PROTOCOL", "https"),
            require_ssl=self._get_bool("TRACKING_REQUIRE_SSL", True),
            # Database settings
            batch_insert_size=self._get_int("TRACKING_BATCH_INSERT_SIZE", 100),
            connection_pool_size=self._get_int("TRACKING_DB_POOL_SIZE", 10),
            connection_timeout=self._get_int("TRACKING_DB_TIMEOUT", 30),
            # Webhook settings
            webhook_enabled=self._get_bool("TRACKING_WEBHOOK_ENABLED", True),
            webhook_timeout=self._get_int("TRACKING_WEBHOOK_TIMEOUT", 10),
            webhook_retry_attempts=self._get_int("TRACKING_WEBHOOK_RETRY_ATTEMPTS", 3),
            webhook_retry_delay=self._get_int("TRACKING_WEBHOOK_RETRY_DELAY", 5),
            # Analytics settings
            track_user_agent=self._get_bool("TRACK_USER_AGENT", True),
            track_ip_address=self._get_bool("TRACK_IP_ADDRESS", True),
            track_geolocation=self._get_bool("TRACK_GEOLOCATION", True),
            track_device_info=self._get_bool("TRACK_DEVICE_INFO", True),
            track_referrer=self._get_bool("TRACK_REFERRER", True),
            # Privacy settings
            anonymize_ip=self._get_bool("TRACKING_ANONYMIZE_IP", False),
            ip_retention_days=self._get_int("TRACKING_IP_RETENTION_DAYS", 90),
            tracking_data_retention_days=self._get_int("TRACKING_DATA_RETENTION_DAYS", 365),
            # Performance settings
            async_logging=self._get_bool("TRACKING_ASYNC_LOGGING", True),
            cache_tracking_configs=self._get_bool("TRACKING_CACHE_CONFIGS", True),
            cache_ttl=self._get_int("TRACKING_CACHE_TTL", 300),
            # Link rewriting settings
            exclude_domains=self._get_list("TRACKING_EXCLUDE_DOMAINS", []),
            exclude_link_patterns=self._get_list(
                "TRACKING_EXCLUDE_PATTERNS", ["mailto:", "tel:", "ftp:", "file:"]
            ),
            preserve_utm_params=self._get_bool("TRACKING_PRESERVE_UTM_PARAMS", True),
            # Rate limiting
            rate_limit_per_ip=self._get_int("TRACKING_RATE_LIMIT_PER_IP", 1000),
            rate_limit_window=self._get_int("TRACKING_RATE_LIMIT_WINDOW", 3600),
        )

    def _get_bool(self, key: str, default: bool) -> bool:
        """Get boolean value from environment variable"""
        value = os.getenv(key, str(default)).lower()
        return value in ("true", "1", "yes", "on", "enabled")

    def _get_int(self, key: str, default: int) -> int:
        """Get integer value from environment variable"""
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            logger.warning(f"Invalid integer value for {key}, using default: {default}")
            return default

    def _get_float(self, key: str, default: float) -> float:
        """Get float value from environment variable"""
        try:
            return float(os.getenv(key, str(default)))
        except ValueError:
            logger.warning(f"Invalid float value for {key}, using default: {default}")
            return default

    def _get_list(self, key: str, default: list[str]) -> list[str]:
        """Get list value from environment variable (comma-separated)"""
        value = os.getenv(key, "")
        if not value:
            return default
        return [item.strip() for item in value.split(",") if item.strip()]

    def _validate_config(self):
        """Validate configuration values"""
        if not self.config.tracking_domain:
            raise ValueError("TRACKING_DOMAIN or HOSTNAME must be set")

        if self.config.batch_insert_size <= 0:
            raise ValueError("TRACKING_BATCH_INSERT_SIZE must be positive")

        if self.config.connection_pool_size <= 0:
            raise ValueError("TRACKING_DB_POOL_SIZE must be positive")

        if self.config.webhook_timeout <= 0:
            raise ValueError("TRACKING_WEBHOOK_TIMEOUT must be positive")

        if self.config.tracking_data_retention_days <= 0:
            raise ValueError("TRACKING_DATA_RETENTION_DAYS must be positive")

    def get_tracking_url_base(self) -> str:
        """Get base URL for tracking endpoints"""
        protocol = self.config.tracking_protocol
        domain = self.config.tracking_domain
        subdomain = self.config.tracking_subdomain

        if subdomain and subdomain != domain:
            return f"{protocol}://{subdomain}.{domain}"
        else:
            return f"{protocol}://{domain}"

    def should_track_link(self, url: str) -> bool:
        """Check if a link should be tracked based on configuration"""
        url_lower = url.lower()

        # Check exclude patterns
        for pattern in self.config.exclude_link_patterns:
            if url_lower.startswith(pattern.lower()):
                return False

        # Check exclude domains
        for domain in self.config.exclude_domains:
            if domain.lower() in url_lower:
                return False

        return True

    def get_database_config(self) -> dict[str, Any]:
        """Get database configuration from environment variables"""
        return {
            "host": os.getenv("DB_HOST", "mysql"),
            "port": int(os.getenv("DB_PORT", 3306)),
            "database": os.getenv("DB_NAME", "mailserver"),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", "password"),
            "charset": "utf8mb4",
        }

    def get_database_url(self) -> str:
        """Get SQLAlchemy database URL"""
        config = self.get_database_config()
        return f"mysql+pymysql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}?charset={config['charset']}"

    def get_webhook_config(self) -> dict[str, Any]:
        """Get webhook configuration"""
        webhook_urls = os.getenv("WEBHOOK_URLS", "").split(",")
        webhook_urls = [url.strip() for url in webhook_urls if url.strip()]

        return {
            "enabled": self.config.webhook_enabled,
            "urls": webhook_urls,
            "secret": os.getenv("WEBHOOK_SECRET", "default-secret"),
            "timeout": self.config.webhook_timeout,
            "retry_attempts": self.config.webhook_retry_attempts,
            "retry_delay": self.config.webhook_retry_delay,
        }

    def anonymize_ip_address(self, ip_address: str) -> str:
        """Anonymize IP address if configured"""
        if not self.config.anonymize_ip:
            return ip_address

        # IPv4 anonymization (remove last octet)
        if "." in ip_address and ip_address.count(".") == 3:
            parts = ip_address.split(".")
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"

        # IPv6 anonymization (remove last 64 bits)
        if ":" in ip_address:
            parts = ip_address.split(":")
            if len(parts) >= 4:
                return ":".join(parts[:4]) + "::0"

        return ip_address

    def get_organization_tracking_config(
        self, organization_id: str, domain_id: str = None
    ) -> dict[str, Any]:
        """Get tenant-specific tracking configuration"""
        # This would typically load from database in a real implementation
        # For now, return default configuration
        return {
            "enabled": self.config.enabled,
            "open_tracking": self.config.open_tracking_enabled,
            "click_tracking": self.config.click_tracking_enabled,
            "custom_domain": None,
            "custom_subdomain": None,
            "webhook_urls": [],
            "exclude_domains": self.config.exclude_domains.copy(),
            "preserve_utm": self.config.preserve_utm_params,
        }

    def get_organization_config(self, organization_id: str) -> dict[str, Any]:
        """
        Get configuration for a specific organization.

        Args:
            organization_id: Organization identifier

        Returns:
            Organization-specific configuration
        """
        # Get organization-specific configs from environment
        organization_configs_str = os.getenv("TRACKING_ORGANIZATION_CONFIGS", "{}")
        try:
            organization_configs = json.loads(organization_configs_str)
            organization_config = organization_configs.get(organization_id, {})
        except json.JSONDecodeError:
            organization_config = {}

        # Merge with default config
        config = self.config.copy()
        config.update(organization_config)

        return config


# Global configuration instance
config_manager = ConfigManager()
