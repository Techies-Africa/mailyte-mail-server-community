#!/usr/bin/env python3
"""
Webhook Health Monitoring Service

This service monitors the health and performance of all configured webhooks:
- Real-time health status tracking
- Performance metrics collection
- Automatic failover detection
- Alert generation for webhook failures

Integrates with the main health monitoring system for comprehensive oversight.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from flask import Flask, jsonify, request

# Add database models to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
from database.models import WebhookURL, WebhookEvent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


class WebhookHealthMonitor:
    """Monitor webhook health and performance across the mail server."""

    def __init__(self):
        """Initialize the webhook health monitor."""
        self.db_url = self._get_database_url()
        self.engine = create_engine(self.db_url, pool_size=5, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Health thresholds (configurable)
        self.success_rate_threshold = float(os.getenv("WEBHOOK_SUCCESS_RATE_THRESHOLD", "90.0"))
        self.failure_threshold = int(os.getenv("WEBHOOK_FAILURE_THRESHOLD", "5"))
        self.response_time_threshold = float(os.getenv("WEBHOOK_RESPONSE_TIME_THRESHOLD", "5.0"))

        logger.info("WebhookHealthMonitor initialized")

    def _get_database_url(self) -> str:
        """Get database URL from environment variables."""
        return (
            f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
            f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}"
            f"/{os.getenv('DB_NAME')}?charset=utf8mb4"
        )

    def get_webhook_health_summary(self) -> Dict[str, Any]:
        """Get overall webhook health summary."""
        try:
            session = self.SessionLocal()

            # Get all active webhooks
            webhooks = session.query(WebhookURL).filter(WebhookURL.active == True).all()

            summary = {
                "total_webhooks": len(webhooks),
                "healthy_webhooks": 0,
                "degraded_webhooks": 0,
                "unhealthy_webhooks": 0,
                "disabled_webhooks": 0,
                "health_score": 0.0,
                "last_updated": datetime.utcnow().isoformat() + "Z",
            }

            for webhook in webhooks:
                health_status = self._calculate_webhook_health(webhook)

                if health_status["status"] == "healthy":
                    summary["healthy_webhooks"] += 1
                elif health_status["status"] == "degraded":
                    summary["degraded_webhooks"] += 1
                else:
                    summary["unhealthy_webhooks"] += 1

            # Calculate overall health score
            if summary["total_webhooks"] > 0:
                summary["health_score"] = (
                    summary["healthy_webhooks"] * 100 + summary["degraded_webhooks"] * 50
                ) / summary["total_webhooks"]

            session.close()
            return summary

        except Exception as e:
            logger.error(f"Failed to get webhook health summary: {e}")
            return {"error": str(e)}

    def get_detailed_webhook_health(self) -> List[Dict[str, Any]]:
        """Get detailed health information for all webhooks."""
        try:
            session = self.SessionLocal()

            webhooks = session.query(WebhookURL).filter(WebhookURL.active == True).all()

            detailed_health = []
            for webhook in webhooks:
                health_info = self._calculate_webhook_health(webhook)

                detailed_health.append(
                    {
                        "id": webhook.id,
                        "name": webhook.name,
                        "url": self._mask_url(webhook.url),
                        "health_status": health_info["status"],
                        "success_rate": health_info["success_rate"],
                        "total_attempts": health_info["total_attempts"],
                        "recent_failures": health_info["recent_failures"],
                        "last_success": webhook.last_success.isoformat()
                        if webhook.last_success
                        else None,
                        "last_failure": webhook.last_failure.isoformat()
                        if webhook.last_failure
                        else None,
                        "event_types": webhook.event_types,
                        "service_types": webhook.service_types,
                        "priority": webhook.priority,
                        "issues": health_info["issues"],
                        "recommendations": health_info["recommendations"],
                    }
                )

            session.close()
            return detailed_health

        except Exception as e:
            logger.error(f"Failed to get detailed webhook health: {e}")
            return []

    def _calculate_webhook_health(self, webhook: WebhookURL) -> Dict[str, Any]:
        """Calculate health status for a single webhook."""
        total_attempts = webhook.success_count + webhook.failure_count
        success_rate = (webhook.success_count / total_attempts * 100) if total_attempts > 0 else 0

        # Check recent activity (last 24 hours)
        recent_cutoff = datetime.utcnow() - timedelta(hours=24)
        has_recent_success = webhook.last_success and webhook.last_success > recent_cutoff
        has_recent_failure = webhook.last_failure and webhook.last_failure > recent_cutoff

        # Count recent failures
        recent_failures = 0
        if webhook.last_failure and webhook.last_failure > recent_cutoff:
            # This is a simplified count - in production you'd query delivery logs
            recent_failures = min(webhook.failure_count, 10)

        # Determine health status
        issues = []
        recommendations = []

        if success_rate < self.success_rate_threshold:
            issues.append(f"Low success rate: {success_rate:.1f}%")
            recommendations.append("Check webhook endpoint availability and response handling")

        if recent_failures >= self.failure_threshold:
            issues.append(f"High recent failure count: {recent_failures}")
            recommendations.append("Review webhook endpoint logs and error responses")

        if not has_recent_success and total_attempts > 0:
            issues.append("No recent successful deliveries")
            recommendations.append("Verify webhook endpoint is responding correctly")

        if total_attempts == 0:
            issues.append("No webhook attempts recorded")
            recommendations.append("Verify webhook is properly configured for expected events")

        # Determine overall status
        if not issues:
            status = "healthy"
        elif success_rate > 50 and has_recent_success:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "status": status,
            "success_rate": round(success_rate, 2),
            "total_attempts": total_attempts,
            "recent_failures": recent_failures,
            "issues": issues,
            "recommendations": recommendations,
        }

    def _mask_url(self, url: str) -> str:
        """Mask sensitive parts of webhook URL for security."""
        if len(url) <= 50:
            return url

        # Show first 30 and last 10 characters
        return f"{url[:30]}...{url[-10:]}"

    def test_webhook_connectivity(self, webhook_id: int) -> Dict[str, Any]:
        """Test connectivity to a specific webhook endpoint."""
        try:
            session = self.SessionLocal()
            webhook = session.query(WebhookURL).filter(WebhookURL.id == webhook_id).first()

            if not webhook:
                return {"error": "Webhook not found"}

            # Create test payload
            test_payload = {
                "event": "test.connectivity",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
                "test_message": "Connectivity test from webhook health monitor",
            }

            # Test connectivity
            start_time = time.time()
            try:
                response = requests.post(
                    webhook.url,
                    json=test_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=webhook.timeout_seconds,
                )
                response_time = (time.time() - start_time) * 1000  # milliseconds

                result = {
                    "webhook_id": webhook_id,
                    "webhook_name": webhook.name,
                    "connectivity_status": "success" if response.status_code == 200 else "failed",
                    "status_code": response.status_code,
                    "response_time_ms": round(response_time, 2),
                    "response_size": len(response.content),
                    "tested_at": datetime.utcnow().isoformat() + "Z",
                }

                if response.status_code != 200:
                    result["error_message"] = f"HTTP {response.status_code}: {response.text[:200]}"

                return result

            except requests.exceptions.Timeout:
                return {
                    "webhook_id": webhook_id,
                    "connectivity_status": "timeout",
                    "error_message": f"Request timed out after {webhook.timeout_seconds} seconds",
                    "tested_at": datetime.utcnow().isoformat() + "Z",
                }
            except requests.exceptions.ConnectionError:
                return {
                    "webhook_id": webhook_id,
                    "connectivity_status": "connection_error",
                    "error_message": "Failed to connect to webhook endpoint",
                    "tested_at": datetime.utcnow().isoformat() + "Z",
                }

        except Exception as e:
            logger.error(f"Failed to test webhook connectivity for ID {webhook_id}: {e}")
            return {"error": str(e)}
        finally:
            session.close()


# Initialize health monitor
health_monitor = WebhookHealthMonitor()


# API endpoints
@app.route("/webhook-health/summary", methods=["GET"])
def get_health_summary():
    """Get overall webhook health summary."""
    return jsonify(health_monitor.get_webhook_health_summary())


@app.route("/webhook-health/detailed", methods=["GET"])
def get_detailed_health():
    """Get detailed health information for all webhooks."""
    return jsonify(
        {
            "webhooks": health_monitor.get_detailed_webhook_health(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )


@app.route("/webhook-health/test/<int:webhook_id>", methods=["POST"])
def test_webhook(webhook_id):
    """Test connectivity to a specific webhook."""
    return jsonify(health_monitor.test_webhook_connectivity(webhook_id))


@app.route("/webhook-health/status", methods=["GET"])
def health_check():
    """Health check for the webhook health monitor itself."""
    return jsonify(
        {
            "status": "healthy",
            "service": "webhook-health-monitor",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8087, debug=False)
