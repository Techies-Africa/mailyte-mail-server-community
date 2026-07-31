#!/usr/bin/env python3
"""
Webhook Service - Notification Sender

Handles all webhook notifications with production-grade features:
- Multi-destination webhook delivery
- Encryption and security
- Failover and retry logic
- Rate limiting notifications
- Real-time event processing
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from cryptography.fernet import Fernet

# Add shared directory to path for logging
project_root = Path(__file__).parent.parent.parent.parent
sys.path.append(str(project_root / "shared"))

from shared.logging_config import LogTimer, get_service_logger

logger = get_service_logger("webhook_notification")


class WebhookNotificationSender:
    """
    Webhook notification sender with encryption,
    failover, and comprehensive event support including rate limiting.
    """

    def __init__(self):
        """Initialize webhook sender with configuration"""
        self.timeout = int(os.getenv("WEBHOOK_TIMEOUT", "30"))
        self.max_retries = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))
        self.retry_delay = int(os.getenv("WEBHOOK_RETRY_DELAY", "5"))

        # Encryption setup
        self.encryption_key = os.getenv("WEBHOOK_ENCRYPTION_KEY")
        if self.encryption_key:
            self.cipher_suite = Fernet(self.encryption_key.encode())
        else:
            self.cipher_suite = None

        # Event type configurations
        self.event_configs = {
            "rate_limit.quota_alert": {"priority": "high", "retry_attempts": 5, "timeout": 15},
            "rate_limit.limit_exceeded": {
                "priority": "critical",
                "retry_attempts": 3,
                "timeout": 10,
            },
            "email.smtp.inbound": {"priority": "normal", "retry_attempts": 3, "timeout": 30},
            "email.smtp.outbound": {"priority": "normal", "retry_attempts": 3, "timeout": 30},
        }

        logger.info("Webhook notification sender initialized")

    def send_webhook(
        self,
        event_type: str,
        payload: dict[str, Any],
        include_eml: bool = False,
        eml_content: str = None,
    ) -> dict[str, Any]:
        """
        Send webhook notification with production features

        Args:
            event_type: Type of event (e.g., 'rate_limit.quota_alert')
            payload: Event payload data
            include_eml: Whether to include EML content
            eml_content: Raw EML content if applicable

        Returns:
            Dictionary with success status and details
        """
        try:
            # Get event configuration
            event_config = self.event_configs.get(
                event_type, {"priority": "normal", "retry_attempts": 3, "timeout": 30}
            )

            # Prepare webhook payload
            webhook_payload = self._prepare_payload(event_type, payload, include_eml, eml_content)

            # Get webhook URLs for this event type
            webhook_urls = self._get_webhook_urls(event_type)

            if not webhook_urls:
                logger.warning(f"No webhook URLs configured for event type: {event_type}")
                return {"success": False, "message": "No webhook URLs configured"}

            # Send to all configured webhooks
            results = []
            for webhook_config in webhook_urls:
                result = self._send_single_webhook(webhook_config, webhook_payload, event_config)
                results.append(result)

            # Determine overall success
            successful_sends = sum(1 for r in results if r["success"])
            total_sends = len(results)

            return {
                "success": successful_sends > 0,
                "total_webhooks": total_sends,
                "successful_sends": successful_sends,
                "failed_sends": total_sends - successful_sends,
                "results": results,
            }

        except Exception as e:
            logger.error(f"Webhook send error: {e}")
            return {"success": False, "message": str(e)}

    def _prepare_payload(
        self, event_type: str, payload: dict[str, Any], include_eml: bool, eml_content: str
    ) -> dict[str, Any]:
        """Prepare webhook payload with encryption if configured"""
        webhook_payload = {
            "event": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": payload,
        }

        # Add EML content if requested
        if include_eml and eml_content:
            webhook_payload["eml_file"] = {
                "content_base64": base64.b64encode(eml_content.encode("utf-8")).decode("utf-8"),
                "size": len(eml_content),
            }

        # Encrypt payload if encryption is enabled
        if self.cipher_suite:
            try:
                payload_json = json.dumps(webhook_payload)
                encrypted_payload = self.cipher_suite.encrypt(payload_json.encode())
                webhook_payload = {
                    "encrypted": True,
                    "data": base64.b64encode(encrypted_payload).decode("utf-8"),
                }
            except Exception as e:
                logger.warning(f"Payload encryption failed: {e}")

        return webhook_payload

    def _get_webhook_urls(self, event_type: str) -> list[dict[str, Any]]:
        """Get webhook URLs configured for specific event type"""
        # This would typically query the database
        # For now, return environment-based configuration
        webhook_urls = []

        # Query database for webhook URLs first
        try:
            if hasattr(self, "database_service") and self.database_service:
                # Map event types to service types
                service_type = "general"
                if event_type.startswith("rate_limit"):
                    service_type = "rate_limiter"
                elif event_type.startswith("email"):
                    service_type = "tracking"
                elif event_type.startswith("storage"):
                    service_type = "storage_usage"

                query = """
                    SELECT url, webhook_secret, custom_headers, timeout_seconds,
                           retry_attempts, auth_type, auth_credentials
                    FROM webhook_urls
                    WHERE active = 1 
                    AND JSON_CONTAINS(service_types, :service_type)
                    AND JSON_CONTAINS(event_types, :event_type)
                    ORDER BY priority ASC
                """

                params = {"service_type": f'"{service_type}"', "event_type": f'"{event_type}"'}

                webhook_configs = self.database_service._execute_query(
                    query, params, fetch_all=True
                )

                if webhook_configs:
                    for config in webhook_configs:
                        webhook_urls.append(
                            {
                                "url": config["url"],
                                "secret": config["webhook_secret"] or "",
                                "headers": json.loads(config["custom_headers"])
                                if config["custom_headers"]
                                else {},
                                "timeout": config["timeout_seconds"] or 30,
                                "retry_attempts": config["retry_attempts"] or 3,
                                "auth_type": config["auth_type"],
                                "auth_credentials": config["auth_credentials"],
                            }
                        )
                    return webhook_urls
        except Exception as e:
            logger.error(f"Failed to query webhook URLs from database: {e}")

        # Fallback to environment variables
        # Rate limiting specific webhooks
        if event_type.startswith("rate_limit"):
            rate_limit_urls = os.getenv("RATE_LIMIT_WEBHOOK_URLS", "").split(",")
            for url in rate_limit_urls:
                if url.strip():
                    webhook_urls.append(
                        {
                            "url": url.strip(),
                            "secret": os.getenv("RATE_LIMIT_WEBHOOK_SECRET", ""),
                            "headers": {},
                        }
                    )

        # General email event webhooks
        if event_type.startswith("email"):
            email_urls = os.getenv("EMAIL_WEBHOOK_URLS", "").split(",")
            for url in email_urls:
                if url.strip():
                    webhook_urls.append(
                        {
                            "url": url.strip(),
                            "secret": os.getenv("EMAIL_WEBHOOK_SECRET", ""),
                            "headers": {},
                        }
                    )

        # Fallback to general webhook URLs
        if not webhook_urls:
            general_urls = os.getenv("WEBHOOK_URLS", "").split(",")
            for url in general_urls:
                if url.strip():
                    webhook_urls.append(
                        {
                            "url": url.strip(),
                            "secret": os.getenv("WEBHOOK_SECRET", ""),
                            "headers": {},
                        }
                    )

        return webhook_urls

    def _send_single_webhook(
        self, webhook_config: dict[str, Any], payload: dict[str, Any], event_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Send webhook to a single URL with retries"""
        url = webhook_config["url"]
        secret = webhook_config.get("secret", "")
        custom_headers = webhook_config.get("headers", {})

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mailyte-Mail-Server-Webhook/2.0",
        }
        headers.update(custom_headers)

        # Add HMAC signature if secret is provided
        if secret:
            payload_json = json.dumps(payload, separators=(",", ":"))
            signature = hmac.new(
                secret.encode("utf-8"), payload_json.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"

        # Retry logic
        max_attempts = event_config.get("retry_attempts", self.max_retries)
        timeout = event_config.get("timeout", self.timeout)

        for attempt in range(max_attempts + 1):
            try:
                with LogTimer(logger, f"webhook_send_{attempt}"):
                    response = requests.post(
                        url, json=payload, headers=headers, timeout=timeout, verify=True
                    )

                if response.status_code in [200, 201, 202]:
                    logger.info(f"Webhook sent successfully to {url} (attempt {attempt + 1})")
                    return {
                        "success": True,
                        "url": url,
                        "status_code": response.status_code,
                        "attempts": attempt + 1,
                        "response_time": response.elapsed.total_seconds(),
                    }
                else:
                    logger.warning(f"Webhook failed with status {response.status_code}: {url}")

            except requests.exceptions.Timeout:
                logger.warning(f"Webhook timeout for {url} (attempt {attempt + 1})")
            except requests.exceptions.ConnectionError:
                logger.warning(f"Webhook connection error for {url} (attempt {attempt + 1})")
            except Exception as e:
                logger.error(f"Webhook error for {url}: {e} (attempt {attempt + 1})")

            # Wait before retry (except on last attempt)
            if attempt < max_attempts:
                time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff

        return {
            "success": False,
            "url": url,
            "attempts": max_attempts + 1,
            "error": "All retry attempts failed",
        }

    def send_rate_limit_alert(
        self, organization_id: str, quota_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Send rate limit quota alert webhook"""
        return self.send_webhook(
            event_type="rate_limit.quota_alert",
            payload={
                "organization_id": organization_id,
                "alert_details": quota_data,
                "recommended_action": self._get_recommended_action(quota_data),
            },
        )

    def send_rate_limit_exceeded(
        self, organization_id: str, limit_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Send rate limit exceeded webhook"""
        return self.send_webhook(
            event_type="rate_limit.limit_exceeded",
            payload={
                "organization_id": organization_id,
                "limit_details": limit_data,
                "immediate_action_required": True,
            },
        )

    def _get_recommended_action(self, quota_data: dict[str, Any]) -> str:
        """Get recommended action based on quota usage"""
        percentage = quota_data.get("quota_percentage", 0)
        alert_level = quota_data.get("alert_level", "normal")

        if alert_level == "exceeded":
            return "Immediate rate limit increase required - service may be impacted"
        elif alert_level == "critical":
            return "Consider increasing rate limits - approaching maximum capacity"
        elif alert_level == "warning":
            return "Monitor usage patterns - may need rate limit adjustment"
        else:
            return "Usage within normal parameters"


# Global notification sender instance
notification_sender = WebhookNotificationSender()
