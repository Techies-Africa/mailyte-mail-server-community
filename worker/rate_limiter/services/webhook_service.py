#!/usr/bin/env python3
"""
Rate Limiter - Webhook Service

This service manages webhook delivery for rate limit alerts and notifications:
- Dynamic webhook URL management from database
- Encrypted payload delivery with authentication
- Retry logic with exponential backoff
- Webhook health monitoring and failover
- Event filtering and routing
- Performance optimization and monitoring

The service ensures reliable delivery of rate limit alerts to external systems
via webhook endpoints with comprehensive error handling and monitoring.
"""

import logging
import time
import json
import threading
import queue
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError
import hashlib
import hmac
import base64
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class WebhookDeliveryQueue:
    """
    Queue manager for webhook delivery with retry logic.

    This class manages a queue of webhook deliveries with automatic
    retry logic, rate limiting, and failure handling.
    """

    def __init__(self, max_workers: int = 5):
        """Initialize webhook delivery queue with worker threads"""
        self.webhook_queue = queue.Queue(maxsize=10000)
        self.max_workers = max_workers
        self.workers = []
        self.running = True

        # Performance tracking
        self.delivered_count = 0
        self.failed_count = 0
        self.retry_count = 0

        # Start worker threads
        self._start_workers()

    def _start_workers(self):
        """Start worker threads for webhook delivery"""
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._delivery_worker, name=f"webhook-worker-{i}", daemon=True
            )
            worker.start()
            self.workers.append(worker)

        logger.info(f"Started {len(self.workers)} webhook delivery workers")

    def _delivery_worker(self):
        """Background worker for processing webhook deliveries"""
        worker_name = threading.current_thread().name

        while self.running:
            try:
                # Get delivery task from queue
                delivery_task = self.webhook_queue.get(timeout=1)

                # Process delivery
                success = self._process_delivery(delivery_task)

                if success:
                    self.delivered_count += 1
                else:
                    self.failed_count += 1

                self.webhook_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Webhook worker {worker_name} error: {e}")

    def _process_delivery(self, delivery_task: Dict[str, Any]) -> bool:
        """
        Process individual webhook delivery with retry logic.

        Args:
            delivery_task: Webhook delivery task containing URL, payload, etc.

        Returns:
            bool: True if delivery successful, False otherwise
        """
        webhook_url = delivery_task.get("url")
        payload = delivery_task.get("payload")
        headers = delivery_task.get("headers", {})
        timeout = delivery_task.get("timeout", 30)
        max_retries = delivery_task.get("max_retries", 3)

        for attempt in range(max_retries + 1):
            try:
                # Add retry attempt to headers
                headers["X-Delivery-Attempt"] = str(attempt + 1)
                headers["X-Max-Attempts"] = str(max_retries + 1)

                # Make HTTP request
                response = requests.post(
                    webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    verify=True,  # Always verify SSL certificates
                )

                # Check response status
                if response.status_code < 400:
                    logger.debug(f"Webhook delivered successfully to {webhook_url}")
                    return True
                else:
                    logger.warning(
                        f"Webhook delivery failed with status {response.status_code}: {webhook_url}"
                    )

                    # Don't retry on client errors (4xx)
                    if 400 <= response.status_code < 500:
                        break

            except (ConnectionError, Timeout) as e:
                logger.warning(f"Webhook delivery attempt {attempt + 1} failed: {e}")

                # Exponential backoff for retries
                if attempt < max_retries:
                    retry_delay = min(2**attempt, 60)  # Cap at 60 seconds
                    time.sleep(retry_delay)
                    self.retry_count += 1

            except Exception as e:
                logger.error(f"Unexpected error in webhook delivery: {e}")
                break

        logger.error(f"Webhook delivery failed after {max_retries + 1} attempts: {webhook_url}")
        return False

    def queue_delivery(self, delivery_task: Dict[str, Any]) -> bool:
        """
        Queue webhook delivery task.

        Args:
            delivery_task: Webhook delivery task

        Returns:
            bool: True if queued successfully
        """
        try:
            self.webhook_queue.put_nowait(delivery_task)
            return True
        except queue.Full:
            logger.error("Webhook delivery queue is full")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get webhook delivery queue statistics"""
        return {
            "queue_size": self.webhook_queue.qsize(),
            "worker_count": len(self.workers),
            "delivered_count": self.delivered_count,
            "failed_count": self.failed_count,
            "retry_count": self.retry_count,
            "success_rate": (
                self.delivered_count / (self.delivered_count + self.failed_count) * 100
            )
            if (self.delivered_count + self.failed_count) > 0
            else 100,
        }


class RateLimitWebhookService:
    """
    Service for managing webhook deliveries for rate limit alerts.

    This service handles webhook URL management, payload encryption,
    authentication, and reliable delivery with monitoring and health checks.
    """

    def __init__(self, database_service=None):
        """
        Initialize webhook service.

        Args:
            database_service: Database service for webhook URL management
        """
        self.database_service = database_service
        self.delivery_queue = WebhookDeliveryQueue()

        # Webhook configuration cache
        self.webhook_urls_cache = {}
        self.cache_ttl = 300  # 5 minutes
        self.last_cache_update = None

        # Performance tracking
        self.webhooks_sent = 0
        self.webhooks_failed = 0
        self.total_processing_time = 0

        logger.info("Rate limit webhook service initialized")

    def send_rate_limit_alert(self, alert_data: Dict[str, Any]) -> bool:
        """
        Send rate limit alert via webhooks.

        Args:
            alert_data: Alert data to send

        Returns:
            bool: True if at least one webhook was delivered successfully
        """
        try:
            start_time = time.time()

            # Get webhook URLs for rate limit events
            webhook_urls = self._get_webhook_urls_for_event("rate_limit.threshold_breach")

            if not webhook_urls:
                logger.warning("No webhook URLs configured for rate limit alerts")
                return False

            delivery_success = False

            for webhook_config in webhook_urls:
                # Prepare webhook payload
                webhook_payload = self._prepare_webhook_payload(alert_data, webhook_config)

                # Prepare headers with authentication
                headers = self._prepare_webhook_headers(webhook_config, webhook_payload)

                # Queue webhook delivery
                delivery_task = {
                    "url": webhook_config["url"],
                    "payload": webhook_payload,
                    "headers": headers,
                    "timeout": webhook_config.get("timeout_seconds", 30),
                    "max_retries": webhook_config.get("retry_attempts", 3),
                }

                if self.delivery_queue.queue_delivery(delivery_task):
                    delivery_success = True
                    logger.debug(f"Queued webhook delivery to {webhook_config['name']}")
                else:
                    logger.error(f"Failed to queue webhook delivery to {webhook_config['name']}")

            # Update performance metrics
            processing_time = time.time() - start_time
            self.total_processing_time += processing_time

            if delivery_success:
                self.webhooks_sent += 1
            else:
                self.webhooks_failed += 1

            return delivery_success

        except Exception as e:
            self.webhooks_failed += 1
            logger.error(f"Error sending rate limit alert: {e}")
            return False

    def send_quota_notification(self, quota_data: Dict[str, Any]) -> bool:
        """
        Send quota usage notification via webhooks.

        Args:
            quota_data: Quota usage data to send

        Returns:
            bool: True if at least one webhook was delivered successfully
        """
        try:
            # Get webhook URLs for quota events
            webhook_urls = self._get_webhook_urls_for_event("rate_limit.quota_status")

            if not webhook_urls:
                logger.debug("No webhook URLs configured for quota notifications")
                return False

            delivery_success = False

            for webhook_config in webhook_urls:
                # Prepare notification payload
                notification_payload = {
                    "event_type": "rate_limit.quota_status",
                    "quota_data": quota_data,
                    "timestamp": datetime.now().isoformat(),
                    "service": "rate_limiter",
                }

                # Prepare webhook payload
                webhook_payload = self._prepare_webhook_payload(
                    notification_payload, webhook_config
                )

                # Prepare headers
                headers = self._prepare_webhook_headers(webhook_config, webhook_payload)

                # Queue delivery
                delivery_task = {
                    "url": webhook_config["url"],
                    "payload": webhook_payload,
                    "headers": headers,
                    "timeout": webhook_config.get("timeout_seconds", 30),
                    "max_retries": webhook_config.get("retry_attempts", 3),
                }

                if self.delivery_queue.queue_delivery(delivery_task):
                    delivery_success = True

            return delivery_success

        except Exception as e:
            logger.error(f"Error sending quota notification: {e}")
            return False

    def _get_webhook_urls_for_event(self, event_type: str) -> List[Dict[str, Any]]:
        """
        Get webhook URLs configured for specific event type.

        Args:
            event_type: Type of event to send

        Returns:
            List of webhook configurations
        """
        try:
            # Check cache first
            if self._is_cache_valid():
                cached_webhooks = self.webhook_urls_cache.get(event_type, [])
                if cached_webhooks:
                    return cached_webhooks

            # Refresh cache from database
            self._refresh_webhook_cache()

            return self.webhook_urls_cache.get(event_type, [])

        except Exception as e:
            logger.error(f"Error getting webhook URLs: {e}")
            return []

    def _refresh_webhook_cache(self):
        """Refresh webhook URLs cache from database"""
        try:
            if not self.database_service:
                logger.warning("Database service not available for webhook URL refresh")
                return

            # Query webhook URLs from database
            query = """
                SELECT id, name, url, event_types, service_types, encryption_key,
                       webhook_secret, active, priority, timeout_seconds, retry_attempts,
                       custom_headers, auth_type, auth_credentials, organization_id
                FROM webhook_urls
                WHERE active = 1 AND JSON_CONTAINS(service_types, '"rate_limiter"')
                ORDER BY priority ASC
            """

            webhook_configs = self.database_service._execute_query(query, fetch_all=True)

            if webhook_configs:
                # Organize by event type for efficient lookup
                new_cache = {}

                for config in webhook_configs:
                    event_types = json.loads(config["event_types"]) if config["event_types"] else []

                    for event_type in event_types:
                        if event_type not in new_cache:
                            new_cache[event_type] = []
                        new_cache[event_type].append(config)

                self.webhook_urls_cache = new_cache
                self.last_cache_update = datetime.now()

                logger.debug(f"Refreshed webhook cache with {len(webhook_configs)} configurations")
            else:
                logger.warning("No webhook URLs found for rate limiter service")

        except Exception as e:
            logger.error(f"Error refreshing webhook cache: {e}")

    def _is_cache_valid(self) -> bool:
        """Check if webhook cache is still valid"""
        if not self.last_cache_update:
            return False

        cache_age = datetime.now() - self.last_cache_update
        return cache_age.total_seconds() < self.cache_ttl

    def _prepare_webhook_payload(
        self, data: Dict[str, Any], webhook_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Prepare webhook payload with encryption if configured.

        Args:
            data: Original data to send
            webhook_config: Webhook configuration

        Returns:
            Prepared webhook payload
        """
        try:
            # Base payload structure
            payload = {
                "webhook_id": webhook_config.get("id"),
                "webhook_name": webhook_config.get("name"),
                "service": "rate_limiter",
                "data": data,
                "timestamp": datetime.now().isoformat(),
                "version": "1.0",
            }

            # Encrypt payload if encryption key is configured
            encryption_key = webhook_config.get("encryption_key")
            if encryption_key:
                try:
                    # Use Fernet for symmetric encryption
                    cipher = Fernet(encryption_key.encode())

                    # Encrypt the data portion
                    encrypted_data = cipher.encrypt(json.dumps(data).encode())

                    payload["data"] = {
                        "encrypted": True,
                        "content": base64.b64encode(encrypted_data).decode(),
                    }

                    logger.debug("Webhook payload encrypted successfully")

                except Exception as e:
                    logger.warning(f"Failed to encrypt webhook payload: {e}")
                    # Fall back to unencrypted payload

            return payload

        except Exception as e:
            logger.error(f"Error preparing webhook payload: {e}")
            return {"error": "Failed to prepare payload"}

    def _prepare_webhook_headers(
        self, webhook_config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Prepare HTTP headers for webhook delivery.

        Args:
            webhook_config: Webhook configuration
            payload: Webhook payload

        Returns:
            Dict of HTTP headers
        """
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Rate-Limiter-Webhook/1.0",
            "X-Webhook-Source": "rate_limiter",
            "X-Webhook-Timestamp": datetime.now().isoformat(),
            "X-Webhook-ID": str(webhook_config.get("id", "")),
        }

        # Add HMAC signature for webhook verification
        webhook_secret = webhook_config.get("webhook_secret")
        if webhook_secret:
            payload_json = json.dumps(payload, sort_keys=True)
            signature = hmac.new(
                webhook_secret.encode(), payload_json.encode(), hashlib.sha256
            ).hexdigest()

            headers["X-Webhook-Signature"] = f"sha256={signature}"

        # Add custom headers if configured
        custom_headers = webhook_config.get("custom_headers")
        if custom_headers:
            try:
                if isinstance(custom_headers, str):
                    custom_headers = json.loads(custom_headers)
                headers.update(custom_headers)
            except Exception as e:
                logger.warning(f"Failed to parse custom headers: {e}")

        # Add authentication headers
        auth_type = webhook_config.get("auth_type")
        auth_credentials = webhook_config.get("auth_credentials")

        if auth_type and auth_credentials:
            if auth_type == "bearer":
                headers["Authorization"] = f"Bearer {auth_credentials}"
            elif auth_type == "api_key":
                headers["X-API-Key"] = auth_credentials
            elif auth_type == "basic":
                headers["Authorization"] = f"Basic {auth_credentials}"

        return headers

    def test_webhook_delivery(self, webhook_id: int) -> Dict[str, Any]:
        """
        Test webhook delivery for a specific webhook configuration.

        Args:
            webhook_id: Webhook configuration ID

        Returns:
            Dict containing test results
        """
        try:
            # Get webhook configuration
            query = """
                SELECT id, name, url, webhook_secret, timeout_seconds, retry_attempts
                FROM webhook_urls
                WHERE id = %s AND active = 1
            """

            webhook_config = self.database_service._execute_query(
                query, (webhook_id,), fetch_one=True
            )

            if not webhook_config:
                return {"success": False, "error": "Webhook configuration not found"}

            # Prepare test payload
            test_data = {
                "event_type": "rate_limit.test",
                "message": "This is a test webhook delivery",
                "timestamp": datetime.now().isoformat(),
                "test": True,
            }

            # Prepare webhook payload and headers
            webhook_payload = self._prepare_webhook_payload(test_data, webhook_config)
            headers = self._prepare_webhook_headers(webhook_config, webhook_payload)

            # Make test delivery
            start_time = time.time()

            try:
                response = requests.post(
                    webhook_config["url"],
                    json=webhook_payload,
                    headers=headers,
                    timeout=webhook_config.get("timeout_seconds", 30),
                    verify=True,
                )

                response_time = (time.time() - start_time) * 1000

                return {
                    "success": response.status_code < 400,
                    "status_code": response.status_code,
                    "response_time_ms": round(response_time, 2),
                    "response_body": response.text[:500],  # Limit response body
                    "webhook_name": webhook_config["name"],
                    "webhook_url": webhook_config["url"],
                }

            except Exception as e:
                response_time = (time.time() - start_time) * 1000

                return {
                    "success": False,
                    "error": str(e),
                    "response_time_ms": round(response_time, 2),
                    "webhook_name": webhook_config["name"],
                    "webhook_url": webhook_config["url"],
                }

        except Exception as e:
            logger.error(f"Error testing webhook delivery: {e}")
            return {"success": False, "error": f"Test failed: {str(e)}"}

    def get_webhook_stats(self) -> Dict[str, Any]:
        """
        Get webhook service statistics and performance metrics.

        Returns:
            Dict containing webhook service statistics
        """
        delivery_stats = self.delivery_queue.get_stats()

        return {
            "service": "rate_limit_webhooks",
            "webhooks_sent": self.webhooks_sent,
            "webhooks_failed": self.webhooks_failed,
            "total_processing_time": self.total_processing_time,
            "average_processing_time": (self.total_processing_time / max(self.webhooks_sent, 1)),
            "cache_size": len(self.webhook_urls_cache),
            "cache_last_updated": self.last_cache_update.isoformat()
            if self.last_cache_update
            else None,
            "delivery_queue": delivery_stats,
        }
