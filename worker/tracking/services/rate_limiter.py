#!/usr/bin/env python3
"""
Email Tracking Service - Rate Limiting

This service provides rate limiting functionality to prevent abuse:
- IP-based rate limiting
- Redis-backed distributed limiting
- In-memory fallback for high availability
- Configurable limits and windows

Rate limiting helps protect the tracking service from abuse
and ensures fair usage across all clients.
"""

import time
import logging
from typing import Dict, Any
import redis

from config import config_manager

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded"""

    pass


class RateLimiter:
    """
    Rate limiter for tracking requests to prevent abuse.

    This service provides:
    - Distributed rate limiting via Redis
    - In-memory fallback when Redis is unavailable
    - Configurable rate limits per IP
    - Sliding window rate limiting
    """

    def __init__(self):
        """Initialize the rate limiter with Redis backend and fallback."""
        self.config = config_manager.config

        # Try to connect to Redis for distributed rate limiting
        try:
            redis_host = config_manager.get_database_config().get("redis_host", "redis")
            redis_port = int(config_manager.get_database_config().get("redis_port", 6379))

            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

            # Test Redis connection
            self.redis_client.ping()
            self.redis_available = True
            logger.info("Rate limiter connected to Redis")

        except Exception as e:
            logger.warning(f"Redis not available for rate limiting, using in-memory fallback: {e}")
            self.redis_available = False
            self.in_memory_cache = {}

    def is_rate_limited(self, ip_address: str) -> bool:
        """
        Check if an IP address is rate limited.

        Args:
            ip_address: IP address to check

        Returns:
            bool: True if the IP is rate limited, False otherwise
        """
        if not ip_address or ip_address == "unknown":
            return False

        # Apply IP anonymization if configured
        if self.config.anonymize_ip:
            ip_address = config_manager.anonymize_ip_address(ip_address)

        if self.redis_available:
            return self._check_redis_rate_limit(ip_address)
        else:
            return self._check_memory_rate_limit(ip_address)

    def _check_redis_rate_limit(self, ip_address: str) -> bool:
        """
        Check rate limit using Redis backend.

        Args:
            ip_address: IP address to check

        Returns:
            bool: True if rate limited, False otherwise
        """
        try:
            key = f"rate_limit:tracking:{ip_address}"
            window = self.config.rate_limit_window
            limit = self.config.rate_limit_per_ip

            # Use Redis pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window)
            results = pipe.execute()

            current_count = results[0]

            if current_count > limit:
                logger.warning(f"Rate limit exceeded for IP {ip_address}: {current_count}/{limit}")
                return True

            logger.debug(f"Rate limit check for IP {ip_address}: {current_count}/{limit}")
            return False

        except Exception as e:
            logger.error(f"Redis rate limit check failed for {ip_address}: {e}")
            # Fallback to allow request if Redis fails
            return False

    def _check_memory_rate_limit(self, ip_address: str) -> bool:
        """
        Check rate limit using in-memory cache (fallback).

        Note: This is not distributed and will reset on service restart.

        Args:
            ip_address: IP address to check

        Returns:
            bool: True if rate limited, False otherwise
        """
        try:
            key = f"rate_limit:{ip_address}"
            now = time.time()
            window = self.config.rate_limit_window
            limit = self.config.rate_limit_per_ip

            if key not in self.in_memory_cache:
                self.in_memory_cache[key] = {"count": 1, "window_start": now}
                return False

            cache_entry = self.in_memory_cache[key]

            # Check if we're in a new window
            if now - cache_entry["window_start"] > window:
                cache_entry["count"] = 1
                cache_entry["window_start"] = now
                return False

            # Increment counter
            cache_entry["count"] += 1

            if cache_entry["count"] > limit:
                logger.warning(
                    f"Rate limit exceeded for IP {ip_address}: {cache_entry['count']}/{limit}"
                )
                return True

            return False

        except Exception as e:
            logger.error(f"Memory rate limit check failed for {ip_address}: {e}")
            # Allow request if check fails
            return False

    def get_rate_limit_info(self, ip_address: str) -> Dict[str, Any]:
        """
        Get rate limit information for an IP address.

        Args:
            ip_address: IP address to get info for

        Returns:
            Dict[str, Any]: Rate limit information
        """
        if not ip_address or ip_address == "unknown":
            return {
                "ip_address": ip_address,
                "rate_limited": False,
                "requests_remaining": self.config.rate_limit_per_ip,
                "window_remaining": self.config.rate_limit_window,
                "backend": "none",
            }

        # Apply IP anonymization if configured
        if self.config.anonymize_ip:
            ip_address = config_manager.anonymize_ip_address(ip_address)

        if self.redis_available:
            return self._get_redis_rate_limit_info(ip_address)
        else:
            return self._get_memory_rate_limit_info(ip_address)

    def _get_redis_rate_limit_info(self, ip_address: str) -> Dict[str, Any]:
        """Get rate limit info from Redis."""
        try:
            key = f"rate_limit:tracking:{ip_address}"
            current_count = int(self.redis_client.get(key) or 0)
            ttl = self.redis_client.ttl(key)

            return {
                "ip_address": ip_address,
                "rate_limited": current_count >= self.config.rate_limit_per_ip,
                "requests_used": current_count,
                "requests_remaining": max(0, self.config.rate_limit_per_ip - current_count),
                "window_remaining": max(0, ttl),
                "backend": "redis",
            }
        except Exception as e:
            logger.error(f"Failed to get Redis rate limit info: {e}")
            return {
                "ip_address": ip_address,
                "rate_limited": False,
                "requests_remaining": self.config.rate_limit_per_ip,
                "window_remaining": self.config.rate_limit_window,
                "backend": "redis_error",
            }

    def _get_memory_rate_limit_info(self, ip_address: str) -> Dict[str, Any]:
        """Get rate limit info from memory cache."""
        try:
            key = f"rate_limit:{ip_address}"
            now = time.time()

            if key not in self.in_memory_cache:
                return {
                    "ip_address": ip_address,
                    "rate_limited": False,
                    "requests_used": 0,
                    "requests_remaining": self.config.rate_limit_per_ip,
                    "window_remaining": self.config.rate_limit_window,
                    "backend": "memory",
                }

            cache_entry = self.in_memory_cache[key]
            window_elapsed = now - cache_entry["window_start"]
            window_remaining = max(0, self.config.rate_limit_window - window_elapsed)

            # Reset if window expired
            if window_elapsed > self.config.rate_limit_window:
                requests_used = 0
                requests_remaining = self.config.rate_limit_per_ip
            else:
                requests_used = cache_entry["count"]
                requests_remaining = max(0, self.config.rate_limit_per_ip - requests_used)

            return {
                "ip_address": ip_address,
                "rate_limited": requests_used >= self.config.rate_limit_per_ip,
                "requests_used": requests_used,
                "requests_remaining": requests_remaining,
                "window_remaining": int(window_remaining),
                "backend": "memory",
            }
        except Exception as e:
            logger.error(f"Failed to get memory rate limit info: {e}")
            return {
                "ip_address": ip_address,
                "rate_limited": False,
                "requests_remaining": self.config.rate_limit_per_ip,
                "window_remaining": self.config.rate_limit_window,
                "backend": "memory_error",
            }
