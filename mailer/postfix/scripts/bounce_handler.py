#!/usr/bin/env python3
"""
Postfix Bounce Handler - Processes bounced emails and updates tracking

This script processes bounced emails from Postfix and categorizes them
as hard bounces, soft bounces, or blocks for proper handling.
"""

import logging
import os
import re
import sys
from datetime import datetime
from email import message_from_string
from pathlib import Path

import requests

# Add project root to path for shared imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.webhook_dispatcher import Events, dispatch_event

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BounceHandler:
    """Handle bounced email processing and tracking updates."""

    def __init__(self):
        self.tracking_service_url = os.getenv("TRACKING_SERVICE_URL", "http://tracking:8086")
        self.webhook_service_url = os.getenv("WEBHOOK_SERVICE_URL", "http://webhooks:8081")

        # Bounce classification patterns
        self.hard_bounce_patterns = [
            r"user unknown",
            r"no such user",
            r"recipient rejected",
            r"mailbox unavailable",
            r"account disabled",
            r"domain not found",
            r"permanent failure",
            r"550",
            r"551",
            r"553",
            r"554",
        ]

        self.soft_bounce_patterns = [
            r"mailbox full",
            r"over quota",
            r"temporary failure",
            r"try again later",
            r"deferred",
            r"421",
            r"422",
            r"450",
            r"451",
            r"452",
        ]

        self.block_patterns = [
            r"spam",
            r"blocked",
            r"blacklist",
            r"reputation",
            r"policy",
            r"content rejected",
        ]

    def process_bounce(self, email_content: str):
        """Process bounced email and extract bounce information."""
        try:
            msg = message_from_string(email_content)

            # Extract original recipient from bounce message
            original_recipient = self._extract_original_recipient(msg)
            if not original_recipient:
                logger.warning("Could not extract original recipient from bounce")
                return

            # Extract bounce reason and classify
            bounce_reason = self._extract_bounce_reason(msg)
            bounce_type = self._classify_bounce(bounce_reason)

            # Extract tracking information if present
            tracking_info = self._extract_tracking_info(msg)

            # Create bounce event data
            bounce_data = {
                "recipient": original_recipient,
                "bounce_type": bounce_type,
                "bounce_reason": bounce_reason,
                "timestamp": datetime.utcnow().isoformat(),
                "tracking_info": tracking_info,
            }

            # Send to tracking service
            self._report_bounce(bounce_data)

            # Dispatch via global webhook dispatcher
            event_type = (
                Events.DELIVERY_BOUNCE_HARD
                if bounce_type == "HARD"
                else Events.DELIVERY_BOUNCE_SOFT
            )
            dispatch_event(
                event_type,
                data={
                    "recipient": original_recipient,
                    "bounce_type": bounce_type,
                    "bounce_reason": bounce_reason,
                    "message_id": tracking_info.get("email_id", ""),
                    "tracking_id": tracking_info.get("tracking_id", ""),
                },
                domain=original_recipient.split("@")[-1] if "@" in original_recipient else None,
                source_service="postfix",
                use_redis=True,
            )

            logger.info(f"Processed {bounce_type} bounce for {original_recipient}")

        except Exception as e:
            logger.error(f"Error processing bounce: {e}")

    def _extract_original_recipient(self, msg) -> str:
        """Extract original recipient from bounce message."""
        # Check various headers and body content for recipient
        headers_to_check = ["X-Failed-Recipients", "X-Original-To", "To"]

        for header in headers_to_check:
            if header in msg:
                return msg[header]

        # Parse from message body
        body = str(msg.get_payload())
        recipient_match = re.search(r"<([^>]+@[^>]+)>", body)
        if recipient_match:
            return recipient_match.group(1)

        return None

    def _extract_bounce_reason(self, msg) -> str:
        """Extract bounce reason from message."""
        # Check various sources for bounce reason
        if "X-Postfix-Sender" in msg:
            return msg.get("X-Postfix-Sender", "")

        body = str(msg.get_payload())

        # Look for diagnostic codes
        diagnostic_match = re.search(r"Diagnostic-Code:\s*(.+)", body, re.IGNORECASE)
        if diagnostic_match:
            return diagnostic_match.group(1).strip()

        # Look for status codes
        status_match = re.search(r"Status:\s*(\d+\.\d+\.\d+)", body)
        if status_match:
            return f"Status: {status_match.group(1)}"

        # Return first line of body as fallback
        lines = body.split("\n")
        for line in lines:
            if line.strip():
                return line.strip()[:500]

        return "Unknown bounce reason"

    def _classify_bounce(self, bounce_reason: str) -> str:
        """Classify bounce as hard, soft, or block."""
        reason_lower = bounce_reason.lower()

        for pattern in self.hard_bounce_patterns:
            if re.search(pattern, reason_lower):
                return "HARD"

        for pattern in self.soft_bounce_patterns:
            if re.search(pattern, reason_lower):
                return "SOFT"

        for pattern in self.block_patterns:
            if re.search(pattern, reason_lower):
                return "BLOCK"

        # Default to soft bounce for safety
        return "SOFT"

    def _extract_tracking_info(self, msg) -> dict:
        """Extract tracking information from bounce message."""
        tracking_info = {}

        # Look for tracking headers
        if "X-Tracking-ID" in msg:
            tracking_info["tracking_id"] = msg["X-Tracking-ID"]

        if "X-Email-ID" in msg:
            tracking_info["email_id"] = msg["X-Email-ID"]

        return tracking_info

    def _report_bounce(self, bounce_data: dict):
        """Report bounce to tracking service."""
        try:
            response = requests.post(
                f"{self.tracking_service_url}/api/tracking/bounce", json=bounce_data, timeout=10
            )
            response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to report bounce to tracking service: {e}")

    def _send_bounce_webhook(self, bounce_data: dict):
        """Send bounce webhook notification."""
        try:
            webhook_data = {
                "event": "email.bounced",
                "data": bounce_data,
                "timestamp": datetime.utcnow().isoformat(),
            }

            response = requests.post(
                f"{self.webhook_service_url}/webhook/send", json=webhook_data, timeout=10
            )
            response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to send bounce webhook: {e}")


if __name__ == "__main__":
    handler = BounceHandler()

    # Read email from stdin
    email_content = sys.stdin.read()

    if email_content:
        handler.process_bounce(email_content)
    else:
        logger.error("No email content received")
        sys.exit(1)
