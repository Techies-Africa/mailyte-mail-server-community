#!/usr/bin/env python3
"""
Email Tracking Service - Encrypted Webhook Notifications

This service handles secure webhook notifications with:
- AES-256 encryption for all payload data
- Multiple webhook endpoint support with failover
- Event-based routing and filtering
- Production-grade security and monitoring

All webhook operations are encrypted and distributed across multiple
endpoints for reliability and performance optimization.
"""

import json
import hashlib
import hmac
import time
import base64
import secrets
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from config import config_manager
import sys
import os

# Add database models to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from database.models import WebhookURL

logger = logging.getLogger(__name__)


class WebhookEncryption:
    """Handles AES-256 encryption/decryption for webhook payloads"""

    @staticmethod
    def generate_key_from_password(password: str, salt: bytes = None) -> Tuple[bytes, bytes]:
        """Generate encryption key from password using PBKDF2"""
        if salt is None:
            salt = secrets.token_bytes(32)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key, salt

    @staticmethod
    def encrypt_payload(payload: Dict[str, Any], encryption_key: str) -> Dict[str, str]:
        """Encrypt webhook payload using AES-256"""
        try:
            # Generate key from password
            key, salt = WebhookEncryption.generate_key_from_password(encryption_key)
            fernet = Fernet(key)

            # Serialize and encrypt payload
            payload_json = json.dumps(payload, default=str)
            encrypted_data = fernet.encrypt(payload_json.encode())

            return {
                "encrypted_payload": base64.b64encode(encrypted_data).decode(),
                "salt": base64.b64encode(salt).decode(),
                "encryption_algorithm": "AES-256-Fernet",
                "encrypted_at": datetime.utcnow().isoformat() + "Z",
            }
        except Exception as e:
            logger.error(f"Payload encryption failed: {e}")
            raise

    @staticmethod
    def decrypt_payload(encrypted_data: Dict[str, str], encryption_key: str) -> Dict[str, Any]:
        """Decrypt webhook payload (for testing/verification)"""
        try:
            salt = base64.b64decode(encrypted_data["salt"])
            key, _ = WebhookEncryption.generate_key_from_password(encryption_key, salt)
            fernet = Fernet(key)

            encrypted_payload = base64.b64decode(encrypted_data["encrypted_payload"])
            decrypted_data = fernet.decrypt(encrypted_payload)

            return json.loads(decrypted_data.decode())
        except Exception as e:
            logger.error(f"Payload decryption failed: {e}")
            raise


class EnterpriseWebhookService:
    """
    Webhook service with encryption, multiple endpoints, and failover.

    Features:
    - AES-256 payload encryption
    - Multiple webhook URL support
    - Event-based routing and filtering
    - Priority-based delivery
    - Automatic failover and retry logic
    - Performance monitoring and health tracking
    """

    def __init__(self):
        """Initialize the webhook service."""
        self.config = config_manager.config
        self.webhook_config = config_manager.get_webhook_config()

        # Database connection for dynamic webhook management
        self.db_url = config_manager.get_database_url()
        self.engine = create_engine(self.db_url, pool_size=5, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Thread pool for async webhook delivery
        self.executor = ThreadPoolExecutor(max_workers=10)

        # Webhook cache for performance
        self._webhook_cache = {}
        self._cache_timestamp = 0
        self._cache_ttl = 300  # 5 minutes

        logger.info("EnterpriseWebhookService initialized with encryption support")

    def get_webhooks_for_event(
        self, event_type: str, service_type: str, tenant_id: str = None, domain_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve webhook URLs configured for specific event and service types.

        Args:
            event_type: Type of event (e.g., 'email.opened', 'smtp.delivered')
            service_type: Service generating the event (e.g., 'tracking', 'postfix')
            tenant_id: Optional tenant filter
            domain_id: Optional domain filter

        Returns:
            List of webhook configurations matching the criteria
        """
        # Check cache first
        cache_key = f"{event_type}:{service_type}:{tenant_id}:{domain_id}"
        current_time = time.time()

        if (
            cache_key in self._webhook_cache
            and current_time - self._cache_timestamp < self._cache_ttl
        ):
            return self._webhook_cache[cache_key]

        try:
            session = self.SessionLocal()

            # Build query for active webhooks
            query = session.query(WebhookURL).filter(WebhookURL.active == True)

            # Apply organization filter if specified
            if tenant_id:
                query = query.filter(
                    text("(organization_id IS NULL OR organization_id = :org_id)")
                ).params(org_id=tenant_id)

            # Filter by event type (JSON contains)
            query = query.filter(text("JSON_CONTAINS(event_types, :event_type)")).params(
                event_type=f'"{event_type}"'
            )

            # Filter by service type (JSON contains)
            query = query.filter(text("JSON_CONTAINS(service_types, :service_type)")).params(
                service_type=f'"{service_type}"'
            )

            # Apply tenant filter if specified
            if tenant_id:
                query = query.filter(
                    text("(tenant_filter IS NULL OR JSON_CONTAINS(tenant_filter, :tenant_id))")
                ).params(tenant_id=f'"{tenant_id}"')

            # Apply domain filter if specified
            if domain_id:
                query = query.filter(
                    text("(domain_filter IS NULL OR JSON_CONTAINS(domain_filter, :domain_id))")
                ).params(domain_id=f'"{domain_id}"')

            # Order by priority (1 = highest priority)
            webhooks = query.order_by(WebhookURL.priority).all()

            # Convert to dictionaries
            webhook_configs = []
            for webhook in webhooks:
                webhook_configs.append(
                    {
                        "id": webhook.id,
                        "name": webhook.name,
                        "url": webhook.url,
                        "encryption_key": webhook.encryption_key,
                        "webhook_secret": webhook.webhook_secret,
                        "timeout_seconds": webhook.timeout_seconds,
                        "retry_attempts": webhook.retry_attempts,
                        "retry_delay_seconds": webhook.retry_delay_seconds,
                        "custom_headers": webhook.custom_headers or {},
                        "auth_type": webhook.auth_type,
                        "auth_credentials": webhook.auth_credentials,
                        "priority": webhook.priority,
                    }
                )

            # Cache the results
            self._webhook_cache[cache_key] = webhook_configs
            self._cache_timestamp = current_time

            session.close()
            return webhook_configs

        except Exception as e:
            logger.error(f"Failed to retrieve webhooks for event {event_type}: {e}")
            return []

    def send_tracking_webhook(
        self,
        event_type: str,
        tracking_data: Dict[str, str],
        request_info: Dict[str, Any],
        additional_data: Dict[str, Any] = None,
    ):
        """
        Send encrypted webhook notification for a tracking event to all configured endpoints.

        Args:
            event_type: Type of tracking event ('opened', 'clicked', etc.)
            tracking_data: Decoded tracking information
            request_info: Request metadata
            additional_data: Optional additional event data
        """
        # Get webhooks for this tracking event
        webhooks = self.get_webhooks_for_event(
            f"email.{event_type}",
            "tracking",
            tracking_data.get("tenant_id"),
            tracking_data.get("domain_id"),
        )

        if not webhooks:
            logger.debug(f"No webhooks configured for tracking event: {event_type}")
            return

        # Create base payload
        payload = self._create_tracking_payload(
            event_type, tracking_data, request_info, additional_data
        )

        # Send to all configured webhooks
        self._send_to_multiple_webhooks(webhooks, payload, f"tracking.{event_type}")

    def send_smtp_webhook(self, event_type: str, smtp_data: Dict[str, Any]):
        """
        Send encrypted webhook notification for SMTP events.

        Args:
            event_type: SMTP event type ('delivered', 'bounced', 'deferred', etc.)
            smtp_data: SMTP event data including sender, recipient, status, etc.
        """
        # Get webhooks for this SMTP event
        webhooks = self.get_webhooks_for_event(
            f"smtp.{event_type}", "postfix", smtp_data.get("tenant_id"), smtp_data.get("domain_id")
        )

        if not webhooks:
            logger.debug(f"No webhooks configured for SMTP event: {event_type}")
            return

        # Create SMTP payload
        payload = {
            "event": f"smtp.{event_type}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": "postfix",
            "data": smtp_data,
            "event_id": secrets.token_urlsafe(16),
        }

        # Send to all configured webhooks
        self._send_to_multiple_webhooks(webhooks, payload, f"smtp.{event_type}")

    def _create_tracking_payload(
        self,
        event_type: str,
        tracking_data: Dict[str, str],
        request_info: Dict[str, Any],
        additional_data: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Create tracking webhook payload with privacy filtering."""
        # Filter request info based on privacy settings
        filtered_request_info = {}
        if self.config.track_ip_address:
            ip_address = request_info.get("ip_address", "")
            if self.config.anonymize_ip:
                ip_address = config_manager.anonymize_ip_address(ip_address)
            filtered_request_info["ip_address"] = ip_address

        if self.config.track_user_agent:
            filtered_request_info["user_agent"] = request_info.get("user_agent", "")

        if self.config.track_referrer:
            filtered_request_info["referer"] = request_info.get("referer", "")

        if self.config.track_geolocation:
            for field in ["country", "region", "city"]:
                if field in request_info:
                    filtered_request_info[field] = request_info[field]

        # Create payload
        payload = {
            "event": f"email.{event_type}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": "tracking",
            "tracking_data": {
                "email_id": tracking_data.get("email_id"),
                "recipient": tracking_data.get("recipient"),
                "tenant_id": tracking_data.get("tenant_id"),
                "domain_id": tracking_data.get("domain_id"),
                "timestamp": tracking_data.get("timestamp"),
            },
            "request_info": filtered_request_info,
            "event_id": secrets.token_urlsafe(16),
        }

        # Add additional data if provided
        if additional_data:
            payload["additional_data"] = additional_data

        return payload

    def _send_to_multiple_webhooks(
        self, webhooks: List[Dict[str, Any]], payload: Dict[str, Any], event_identifier: str
    ):
        """Send payload to multiple webhook endpoints with encryption and failover."""
        if not webhooks:
            return

        # Submit webhook delivery tasks to thread pool
        futures = []
        for webhook in webhooks:
            future = self.executor.submit(
                self._send_single_webhook, webhook, payload, event_identifier
            )
            futures.append((future, webhook))

        # Monitor delivery results
        successful_deliveries = 0
        failed_deliveries = 0

        for future, webhook in futures:
            try:
                success = future.result(timeout=webhook.get("timeout_seconds", 30))
                if success:
                    successful_deliveries += 1
                    self._update_webhook_stats(webhook["id"], success=True)
                else:
                    failed_deliveries += 1
                    self._update_webhook_stats(webhook["id"], success=False)
            except Exception as e:
                failed_deliveries += 1
                self._update_webhook_stats(webhook["id"], success=False)
                logger.error(f"Webhook delivery failed for {webhook['name']}: {e}")

        logger.info(
            f"Webhook delivery for {event_identifier}: "
            f"{successful_deliveries} successful, {failed_deliveries} failed"
        )

    def _send_single_webhook(
        self, webhook: Dict[str, Any], payload: Dict[str, Any], event_identifier: str
    ) -> bool:
        """Send payload to a single webhook endpoint with encryption and retry logic."""
        webhook_url = webhook["url"]
        max_retries = webhook.get("retry_attempts", 3)
        retry_delay = webhook.get("retry_delay_seconds", 5)

        for attempt in range(max_retries + 1):
            try:
                # Encrypt payload
                encrypted_payload = WebhookEncryption.encrypt_payload(
                    payload, webhook["encryption_key"]
                )

                # Create headers
                headers = {
                    "Content-Type": "application/json",
                    "X-Webhook-Source": "mailyte-mail-server",
                    "X-Webhook-Event": event_identifier,
                    "X-Webhook-ID": webhook["name"],
                    "X-Webhook-Signature": self._generate_signature(
                        encrypted_payload, webhook["webhook_secret"]
                    ),
                    "X-Webhook-Timestamp": str(int(time.time())),
                    "X-Encryption-Algorithm": "AES-256-Fernet",
                }

                # Add custom headers
                if webhook.get("custom_headers"):
                    headers.update(webhook["custom_headers"])

                # Add authentication if configured
                if webhook.get("auth_type") and webhook.get("auth_credentials"):
                    self._add_authentication(
                        headers, webhook["auth_type"], webhook["auth_credentials"]
                    )

                # Send webhook
                response = requests.post(
                    webhook_url,
                    json=encrypted_payload,
                    headers=headers,
                    timeout=webhook.get("timeout_seconds", 30),
                )

                if response.status_code in [200, 201, 202]:
                    logger.debug(f"Webhook delivered successfully to {webhook['name']}")
                    return True
                else:
                    logger.warning(
                        f"Webhook delivery failed to {webhook['name']}: HTTP {response.status_code}"
                    )

            except requests.exceptions.Timeout:
                logger.warning(f"Webhook timeout to {webhook['name']} (attempt {attempt + 1})")
            except requests.exceptions.ConnectionError:
                logger.warning(f"Connection error to {webhook['name']} (attempt {attempt + 1})")
            except Exception as e:
                logger.error(f"Webhook error to {webhook['name']} (attempt {attempt + 1}): {e}")

            # Wait before retry (except on last attempt)
            if attempt < max_retries:
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff

        logger.error(
            f"Webhook delivery failed to {webhook['name']} after {max_retries + 1} attempts"
        )
        return False

    def _generate_signature(self, payload: Dict[str, Any], secret: str) -> str:
        """Generate HMAC signature for webhook validation."""
        payload_json = json.dumps(payload, sort_keys=True)
        signature = hmac.new(
            secret.encode("utf-8"), payload_json.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    def _add_authentication(self, headers: Dict[str, str], auth_type: str, auth_credentials: str):
        """Add authentication headers based on configuration."""
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_credentials}"
        elif auth_type == "basic":
            headers["Authorization"] = f"Basic {auth_credentials}"
        elif auth_type == "api_key":
            headers["X-API-Key"] = auth_credentials

    def _update_webhook_stats(self, webhook_id: int, success: bool):
        """Update webhook delivery statistics."""
        try:
            session = self.SessionLocal()

            webhook = session.query(WebhookURL).filter(WebhookURL.id == webhook_id).first()
            if webhook:
                if success:
                    webhook.success_count += 1
                    webhook.last_success = datetime.utcnow()
                else:
                    webhook.failure_count += 1
                    webhook.last_failure = datetime.utcnow()

                session.commit()

            session.close()

        except Exception as e:
            logger.error(f"Failed to update webhook stats for ID {webhook_id}: {e}")

    def get_webhook_health_status(self) -> Dict[str, Any]:
        """Get health status of all configured webhooks."""
        try:
            session = self.SessionLocal()

            webhooks = session.query(WebhookURL).filter(WebhookURL.active == True).all()

            health_status = {
                "total_webhooks": len(webhooks),
                "healthy_webhooks": 0,
                "unhealthy_webhooks": 0,
                "webhook_details": [],
            }

            for webhook in webhooks:
                # Calculate health based on success rate and recent activity
                total_attempts = webhook.success_count + webhook.failure_count
                success_rate = (
                    (webhook.success_count / total_attempts * 100) if total_attempts > 0 else 0
                )

                # Consider healthy if success rate > 90% and recent success
                is_healthy = (
                    success_rate >= 90.0
                    and webhook.last_success
                    and (datetime.utcnow() - webhook.last_success).total_seconds() < 3600
                )

                if is_healthy:
                    health_status["healthy_webhooks"] += 1
                else:
                    health_status["unhealthy_webhooks"] += 1

                health_status["webhook_details"].append(
                    {
                        "id": webhook.id,
                        "name": webhook.name,
                        "url": webhook.url[:50] + "..." if len(webhook.url) > 50 else webhook.url,
                        "success_rate": round(success_rate, 2),
                        "total_attempts": total_attempts,
                        "last_success": webhook.last_success.isoformat()
                        if webhook.last_success
                        else None,
                        "last_failure": webhook.last_failure.isoformat()
                        if webhook.last_failure
                        else None,
                        "is_healthy": is_healthy,
                    }
                )

            session.close()
            return health_status

        except Exception as e:
            logger.error(f"Failed to get webhook health status: {e}")
            return {"error": str(e)}


# Global service instance
webhook_service = EnterpriseWebhookService()
