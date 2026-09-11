#!/usr/bin/env python3
"""
Fail2Ban Webhook Notification Script
Sends security alerts when IPs are banned/unbanned
"""

import logging
import os
import sys
from datetime import datetime

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("/var/log/fail2ban-webhook.log"), logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


class Fail2BanWebhook:
    """Send webhook notifications for fail2ban events"""

    def __init__(self):
        """Initialize webhook configuration"""
        self.webhook_url = os.getenv(
            "SECURITY_WEBHOOK_URL", "http://webhooks:8081/security-webhook"
        )
        self.webhook_secret = os.getenv("SECURITY_WEBHOOK_SECRET", "fail2ban_secret_change_this")
        self.timeout = int(os.getenv("WEBHOOK_TIMEOUT", "10"))

    def send_notification(self, action: str, ip: str, jail_name: str, ban_time: str = None):
        """
        Send security notification via webhook

        Args:
            action: 'ban' or 'unban'
            ip: IP address that was banned/unbanned
            jail_name: Name of the fail2ban jail
            ban_time: Duration of ban (for ban actions)
        """
        try:
            # Prepare webhook payload
            payload = {
                "event_type": f"security.{action}",
                "timestamp": datetime.now().isoformat(),
                "data": {
                    "ip_address": ip,
                    "jail_name": jail_name,
                    "action": action,
                    "ban_time": ban_time,
                    "server_hostname": os.getenv("HOSTNAME", "mail-server"),
                    "severity": "HIGH" if action == "ban" else "MEDIUM",
                },
                "metadata": {
                    "source": "fail2ban",
                    "version": "1.0",
                    "server_ip": self._get_server_ip(),
                },
            }

            # Prepare headers
            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Secret": self.webhook_secret,
                "X-Fail2Ban-Action": action,
                "X-Fail2Ban-Jail": jail_name,
            }

            # Send webhook
            response = requests.post(
                self.webhook_url, json=payload, headers=headers, timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info(
                    f"✅ Webhook sent successfully for {action} of {ip} in jail {jail_name}"
                )
            else:
                logger.error(
                    f"❌ Webhook failed with status {response.status_code}: {response.text}"
                )

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Webhook request failed: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error sending webhook: {e}")

    def _get_server_ip(self) -> str:
        """Get server's public IP address"""
        try:
            response = requests.get("https://api.ipify.org", timeout=5)
            return response.text.strip()
        except:
            return "unknown"

    def log_geolocation(self, ip: str):
        """Log geolocation information for banned IP"""
        try:
            # Simple geolocation lookup (optional)
            response = requests.get(f"http://ip-api.com/json/{ip}", timeout=5)
            if response.status_code == 200:
                geo_data = response.json()
                logger.info(
                    f"🌍 IP {ip} geolocation: {geo_data.get('city', 'Unknown')}, "
                    f"{geo_data.get('country', 'Unknown')} ({geo_data.get('isp', 'Unknown ISP')})"
                )
        except:
            logger.debug(f"Could not fetch geolocation for {ip}")


def main():
    """Main function to handle fail2ban webhook notifications"""
    if len(sys.argv) < 4:
        logger.error("Usage: fail2ban-webhook.py <action> <ip> <jail_name> [ban_time]")
        sys.exit(1)

    action = sys.argv[1]
    ip = sys.argv[2]
    jail_name = sys.argv[3]
    ban_time = sys.argv[4] if len(sys.argv) > 4 else None

    # Validate action
    if action not in ["ban", "unban"]:
        logger.error(f"Invalid action: {action}. Must be 'ban' or 'unban'")
        sys.exit(1)

    # Initialize webhook sender
    webhook = Fail2BanWebhook()

    # Log the security event
    if action == "ban":
        logger.warning(
            f"🚨 SECURITY ALERT: IP {ip} has been BANNED by jail '{jail_name}' for {ban_time} seconds"
        )
        webhook.log_geolocation(ip)
    else:
        logger.info(f"✅ IP {ip} has been UNBANNED from jail '{jail_name}'")

    # Send webhook notification
    webhook.send_notification(action, ip, jail_name, ban_time)


if __name__ == "__main__":
    main()
