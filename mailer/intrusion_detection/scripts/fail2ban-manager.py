#!/usr/bin/env python3
"""
Fail2Ban Management Script
Provides advanced management and monitoring capabilities
"""

import argparse
import json
import logging
import os
import subprocess
from datetime import datetime
from typing import Any

import mysql.connector
import redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Fail2BanManager:
    """Advanced fail2ban management and integration"""

    def __init__(self):
        """Initialize manager with database connections"""
        self.redis_client = self._init_redis()
        self.mysql_conn = self._init_mysql()

    def _init_redis(self):
        """Initialize Redis connection for caching"""
        try:
            return redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD"),
                decode_responses=True,
            )
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            return None

    def _init_mysql(self):
        """Initialize MySQL connection for persistent storage"""
        try:
            return mysql.connector.connect(
                host=os.getenv("DB_HOST", "localhost"),
                user=os.getenv("DB_USER", "mailuser"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_NAME", "mailserver"),
            )
        except Exception as e:
            logger.warning(f"MySQL connection failed: {e}")
            return None

    def get_banned_ips(self) -> list[dict[str, Any]]:
        """Get list of currently banned IPs"""
        try:
            result = subprocess.run(
                ["fail2ban-client", "status"], capture_output=True, text=True, check=True
            )

            jails = []
            lines = result.stdout.split("\n")
            for line in lines:
                if "Jail list:" in line:
                    jail_names = line.split("Jail list:")[1].strip().split(",")
                    for jail_name in jail_names:
                        jail_name = jail_name.strip()
                        if jail_name:
                            banned_ips = self._get_jail_banned_ips(jail_name)
                            jails.append(
                                {
                                    "jail": jail_name,
                                    "banned_ips": banned_ips,
                                    "count": len(banned_ips),
                                }
                            )

            return jails

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get banned IPs: {e}")
            return []

    def _get_jail_banned_ips(self, jail_name: str) -> list[str]:
        """Get banned IPs for specific jail"""
        try:
            result = subprocess.run(
                ["fail2ban-client", "get", jail_name, "banip"],
                capture_output=True,
                text=True,
                check=True,
            )

            # Parse the output to extract IPs
            banned_ips = []
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith("The jail"):
                    banned_ips.append(line)

            return banned_ips

        except subprocess.CalledProcessError:
            return []

    def unban_ip(self, ip: str, jail_name: str = None) -> bool:
        """Unban an IP address from jail(s)"""
        try:
            if jail_name:
                # Unban from specific jail
                subprocess.run(["fail2ban-client", "set", jail_name, "unbanip", ip], check=True)
                logger.info(f"✅ Unbanned {ip} from jail {jail_name}")
            else:
                # Unban from all jails
                jails = self.get_banned_ips()
                for jail in jails:
                    if ip in jail["banned_ips"]:
                        subprocess.run(
                            ["fail2ban-client", "set", jail["jail"], "unbanip", ip], check=True
                        )
                        logger.info(f"✅ Unbanned {ip} from jail {jail['jail']}")

            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to unban {ip}: {e}")
            return False

    def whitelist_ip(self, ip: str) -> bool:
        """Add IP to permanent whitelist"""
        try:
            # Add to fail2ban ignore list
            config_file = "/etc/fail2ban/jail.local"

            # Read current config
            if os.path.exists(config_file):
                with open(config_file) as f:
                    content = f.read()
            else:
                content = "[DEFAULT]\n"

            # Add IP to ignoreip if not already present
            if "ignoreip = " in content:
                # Update existing ignoreip line
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if line.strip().startswith("ignoreip = "):
                        if ip not in line:
                            lines[i] = line + f" {ip}"
                        break
                content = "\n".join(lines)
            else:
                # Add new ignoreip line
                content += f"\nignoreip = 127.0.0.1/8 ::1 {ip}\n"

            # Write updated config
            with open(config_file, "w") as f:
                f.write(content)

            # Reload fail2ban
            subprocess.run(["fail2ban-client", "reload"], check=True)

            logger.info(f"✅ Added {ip} to whitelist")
            return True

        except Exception as e:
            logger.error(f"Failed to whitelist {ip}: {e}")
            return False

    def get_statistics(self) -> dict[str, Any]:
        """Get fail2ban statistics"""
        try:
            stats = {"total_banned_ips": 0, "jails": {}, "top_countries": {}, "recent_bans": []}

            # Get current bans
            jails = self.get_banned_ips()
            for jail in jails:
                stats["jails"][jail["jail"]] = jail["count"]
                stats["total_banned_ips"] += jail["count"]

            # Get recent bans from logs
            stats["recent_bans"] = self._get_recent_bans()

            return stats

        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {}

    def _get_recent_bans(self) -> list[dict[str, Any]]:
        """Get recent bans from fail2ban log"""
        try:
            recent_bans = []
            log_file = "/var/log/fail2ban.log"

            if os.path.exists(log_file):
                # Read last 100 lines
                result = subprocess.run(
                    ["tail", "-n", "100", log_file], capture_output=True, text=True
                )

                lines = result.stdout.split("\n")
                for line in lines:
                    if "Ban " in line and "[" in line:
                        # Parse ban entry
                        parts = line.split()
                        if len(parts) >= 6:
                            timestamp = " ".join(parts[:2])
                            jail = line.split("[")[1].split("]")[0]
                            ip = parts[-1] if parts[-1] != "already" else parts[-2]

                            recent_bans.append(
                                {"timestamp": timestamp, "jail": jail, "ip": ip, "action": "ban"}
                            )

            return recent_bans[-20:]  # Return last 20 bans

        except Exception as e:
            logger.error(f"Failed to get recent bans: {e}")
            return []

    def generate_report(self) -> str:
        """Generate comprehensive security report"""
        stats = self.get_statistics()

        report = f"""
📊 FAIL2BAN SECURITY REPORT
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

🚨 CURRENT STATUS:
- Total Banned IPs: {stats.get("total_banned_ips", 0)}
- Active Jails: {len(stats.get("jails", {}))}

📋 JAIL STATISTICS:
"""
        for jail, count in stats.get("jails", {}).items():
            report += f"  - {jail}: {count} banned IPs\n"

        report += "\n🕐 RECENT BANS (Last 20):\n"
        for ban in stats.get("recent_bans", [])[-10:]:
            report += f"  - {ban['timestamp']}: {ban['ip']} [{ban['jail']}]\n"

        return report


def main():
    """Main CLI interface"""
    parser = argparse.ArgumentParser(description="Fail2Ban Manager")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--unban", type=str, help="Unban IP address")
    parser.add_argument("--whitelist", type=str, help="Add IP to whitelist")
    parser.add_argument("--report", action="store_true", help="Generate security report")
    parser.add_argument("--jail", type=str, help="Specify jail for operations")

    args = parser.parse_args()

    manager = Fail2BanManager()

    if args.status:
        banned_ips = manager.get_banned_ips()
        print(json.dumps(banned_ips, indent=2))

    elif args.unban:
        success = manager.unban_ip(args.unban, args.jail)
        print(f"Unban {'successful' if success else 'failed'}")

    elif args.whitelist:
        success = manager.whitelist_ip(args.whitelist)
        print(f"Whitelist {'successful' if success else 'failed'}")

    elif args.report:
        report = manager.generate_report()
        print(report)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
