#!/usr/bin/env python3
"""
Shared Configuration Module

Centralized configuration for all Mailyte email server services.
All config is read from environment variables, making it easy to swap
between local development (Docker) and production (AWS managed services).

Development: local MySQL, Redis, MinIO in Docker
Production:  AWS RDS, ElastiCache, S3
"""

import os
from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    """MySQL database configuration."""

    host: str = ""
    port: int = 3306
    name: str = "mailserver"
    user: str = "root"
    password: str = ""
    pool_size: int = 20
    max_overflow: int = 30
    pool_timeout: int = 30
    pool_recycle: int = 3600

    def __post_init__(self):
        self.host = os.getenv("DB_HOST", self.host or "mysql")
        self.port = int(os.getenv("DB_PORT", str(self.port)))
        self.name = os.getenv("DB_NAME", self.name)
        self.user = os.getenv("DB_USER", self.user)
        self.password = os.getenv("DB_PASSWORD", self.password)
        self.pool_size = int(os.getenv("DB_POOL_SIZE", str(self.pool_size)))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", str(self.max_overflow)))
        self.pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", str(self.pool_timeout)))
        self.pool_recycle = int(os.getenv("DB_POOL_RECYCLE", str(self.pool_recycle)))

    @property
    def url(self) -> str:
        """SQLAlchemy connection URL."""
        return f"mysql+mysqlconnector://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def url_pymysql(self) -> str:
        """SQLAlchemy connection URL using PyMySQL driver."""
        return f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@dataclass
class RedisConfig:
    """Redis configuration. Local Redis in dev, AWS ElastiCache in prod."""

    url: str = ""
    host: str = ""
    port: int = 6379
    password: str = ""
    db: int = 0
    ssl: bool = False
    max_connections: int = 50

    def __post_init__(self):
        self.url = os.getenv("REDIS_URL", self.url)
        if not self.url:
            self.host = os.getenv("REDIS_HOST", self.host or "redis")
            self.port = int(os.getenv("REDIS_PORT", str(self.port)))
            self.password = os.getenv("REDIS_PASSWORD", self.password)
            self.db = int(os.getenv("REDIS_DB", str(self.db)))
            self.ssl = os.getenv("REDIS_SSL", "false").lower() == "true"
            if self.password:
                self.url = f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
            else:
                self.url = f"redis://{self.host}:{self.port}/{self.db}"
            if self.ssl:
                self.url = self.url.replace("redis://", "rediss://")
        self.max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", str(self.max_connections)))


@dataclass
class S3Config:
    """AWS S3 object storage for email files, attachments, and backups."""

    bucket: str = "development-local-1"
    prefix: str = "mailyte"
    access_key: str = ""
    secret_key: str = ""
    region: str = "eu-west-2"
    use_ssl: bool = True
    use_path_style: bool = False
    endpoint: str = ""

    def __post_init__(self):
        self.bucket = os.getenv("AWS_BUCKET", os.getenv("S3_BUCKET", self.bucket))
        self.prefix = os.getenv("AWS_S3_PREFIX", self.prefix)
        self.access_key = os.getenv("AWS_ACCESS_KEY_ID", self.access_key)
        self.secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", self.secret_key)
        self.region = os.getenv("AWS_DEFAULT_REGION", self.region)
        self.use_ssl = os.getenv("S3_USE_SSL", str(self.use_ssl)).lower() == "true"
        self.use_path_style = os.getenv("AWS_USE_PATH_STYLE_ENDPOINT", "false").lower() == "true"
        self.endpoint = os.getenv("S3_ENDPOINT", self.endpoint)

    @property
    def is_configured(self) -> bool:
        return bool(self.access_key and self.secret_key and self.bucket)

    def get_key(self, path: str) -> str:
        """Get full S3 key with prefix. e.g. 'mailyte/attachments/file.eml'"""
        return f"{self.prefix}/{path}" if self.prefix else path


@dataclass
class MailServerConfig:
    """General mail server configuration."""

    hostname: str = ""
    domain: str = ""
    admin_email: str = ""

    def __post_init__(self):
        self.hostname = os.getenv("HOSTNAME", self.hostname or "mail.example.com")
        self.domain = os.getenv("DOMAIN", self.domain or "example.com")
        self.admin_email = os.getenv("ADMIN_EMAIL", self.admin_email or f"admin@{self.domain}")


@dataclass
class ServiceConfig:
    """Configuration for a specific worker service."""

    name: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    log_level: str = "INFO"
    admin_password: str = ""
    admin_token_secret: str = ""
    webhook_secret: str = ""
    webhook_urls: str = ""

    def __post_init__(self):
        self.debug = os.getenv("FLASK_DEBUG", os.getenv("DEBUG", "0")) == "1"
        self.log_level = os.getenv("LOG_LEVEL", self.log_level)
        self.admin_password = os.getenv("ADMIN_PASSWORD", self.admin_password)
        self.admin_token_secret = os.getenv("ADMIN_TOKEN_SECRET", self.admin_token_secret)
        self.webhook_secret = os.getenv("WEBHOOK_SECRET", self.webhook_secret)
        self.webhook_urls = os.getenv("WEBHOOK_URLS", self.webhook_urls)


class AppConfig:
    """
    Master application configuration.

    Usage:
        config = AppConfig()
        db_url = config.db.url
        redis_url = config.redis.url
        s3_configured = config.s3.is_configured
    """

    def __init__(self):
        self.db = DatabaseConfig()
        self.redis = RedisConfig()
        self.s3 = S3Config()
        self.mail = MailServerConfig()
        self.service = ServiceConfig()

    @property
    def is_production(self) -> bool:
        env = os.getenv("FLASK_ENV", os.getenv("ENVIRONMENT", "development"))
        return env == "production"

    @property
    def is_development(self) -> bool:
        return not self.is_production


# Singleton instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the global application configuration (singleton)."""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
