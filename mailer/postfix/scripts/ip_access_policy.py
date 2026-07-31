#!/usr/bin/env python3
"""
Per-Organization IP Access Policy Server for Postfix

Enforces IP whitelists configured by organizations.
When an organization enables IP restrictions, only whitelisted IPs can send
mail via authenticated SMTP for that organization's domains.

This runs as a Postfix policy delegation service (RFC 5321 appendix).
Postfix connects via unix socket and sends key=value pairs.

Policy flow:
  1. Extract sender domain and client IP from Postfix policy request
  2. Look up the organization for the sender domain
  3. If the org has IP restrictions enabled, check if client IP is allowed
  4. Return DUNNO (allow) or REJECT (deny)

Database schema uses ip_access_rules table:
  - organization_id, rule_type (whitelist/blacklist), ip_address (CIDR), description

If an organization has NO ip_access_rules, all IPs are allowed (default open).
If an organization HAS whitelist rules, ONLY those IPs are allowed.
"""

import ipaddress
import json
import logging
import os
import sys
from pathlib import Path

import mysql.connector
import redis

# Add project root for shared imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from shared.webhook_dispatcher import Events, dispatch_event

    _dispatcher_available = True
except ImportError:
    _dispatcher_available = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/var/log/postfix/ip_access_policy.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("ip_access_policy")


class IPAccessPolicy:
    def __init__(self):
        self.db_config = {
            "host": os.environ.get("DB_HOST", "mysql"),
            "port": int(os.environ.get("DB_PORT", "3306")),
            "database": os.environ.get("DB_NAME", "mailserver"),
            "user": os.environ.get("DB_USER", "mailuser"),
            "password": os.environ.get("DB_PASSWORD", "mailpassword"),
        }
        self._db_conn = None

        try:
            self._redis = redis.Redis(
                host=os.environ.get("REDIS_HOST", "redis"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                db=0,
                decode_responses=True,
                socket_timeout=2,
            )
        except Exception:
            self._redis = None

        self.cache_ttl = 300  # 5 minutes

    @property
    def db_connection(self):
        if self._db_conn is None or not self._db_conn.is_connected():
            try:
                self._db_conn = mysql.connector.connect(**self.db_config)
            except Exception as e:
                logger.error(f"DB connection failed: {e}")
                self._db_conn = None
        return self._db_conn

    def _get_cached(self, key):
        if not self._redis:
            return None
        try:
            val = self._redis.get(key)
            return json.loads(val) if val else None
        except Exception:
            return None

    def _set_cached(self, key, value, ttl=None):
        if not self._redis:
            return
        try:
            self._redis.setex(key, ttl or self.cache_ttl, json.dumps(value))
        except Exception:
            pass

    def check_access(self, sender_domain, client_ip):
        """Check if client_ip is allowed to send for sender_domain's organization."""
        if not sender_domain or not client_ip:
            return "DUNNO"

        cache_key = f"ip_access:{sender_domain}:{client_ip}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached["action"]

        conn = self.db_connection
        if not conn:
            return "DUNNO"  # Fail open on DB error

        try:
            cursor = conn.cursor(dictionary=True)

            # Look up organization for this domain
            cursor.execute(
                """
                SELECT o.id as org_id, o.settings
                FROM organizations o
                JOIN domains d ON d.organization_id = o.id
                WHERE d.domain = %s AND d.active = 1 AND o.status = 'active'
                LIMIT 1
            """,
                (sender_domain,),
            )

            org = cursor.fetchone()
            if not org:
                cursor.close()
                self._set_cached(cache_key, {"action": "DUNNO"})
                return "DUNNO"

            org_id = org["org_id"]

            # Check if this org has IP restrictions
            cursor.execute(
                """
                SELECT rule_type, ip_address
                FROM ip_access_rules
                WHERE organization_id = %s AND active = 1
                ORDER BY rule_type ASC
            """,
                (org_id,),
            )

            rules = cursor.fetchall()
            cursor.close()

            if not rules:
                # No rules = all IPs allowed
                self._set_cached(cache_key, {"action": "DUNNO"})
                return "DUNNO"

            # Parse rules
            whitelists = [r["ip_address"] for r in rules if r["rule_type"] == "whitelist"]
            blacklists = [r["ip_address"] for r in rules if r["rule_type"] == "blacklist"]

            try:
                client = ipaddress.ip_address(client_ip)
            except ValueError:
                self._set_cached(cache_key, {"action": "DUNNO"})
                return "DUNNO"

            # Check blacklist first
            for cidr in blacklists:
                try:
                    if client in ipaddress.ip_network(cidr, strict=False):
                        action = f"REJECT 5.7.1 Access denied: IP {client_ip} is blacklisted for this organization"
                        logger.warning(
                            f"Blacklisted IP {client_ip} for org {org_id} domain {sender_domain}"
                        )
                        self._set_cached(cache_key, {"action": action})
                        if _dispatcher_available:
                            try:
                                dispatch_event(
                                    Events.SECURITY_IP_BLOCKED,
                                    data={
                                        "ip_address": client_ip,
                                        "domain": sender_domain,
                                        "rule_type": "blacklist",
                                        "cidr": cidr,
                                    },
                                    org_id=org_id,
                                    domain=sender_domain,
                                    source_service="postfix",
                                    use_redis=True,
                                )
                            except Exception:
                                pass
                        return action
                except ValueError:
                    continue

            # If whitelists exist, IP must match at least one
            if whitelists:
                for cidr in whitelists:
                    try:
                        if client in ipaddress.ip_network(cidr, strict=False):
                            self._set_cached(cache_key, {"action": "DUNNO"})
                            return "DUNNO"
                    except ValueError:
                        continue

                # IP not in any whitelist
                action = f"REJECT 5.7.1 Access denied: IP {client_ip} is not whitelisted for this organization"
                logger.warning(
                    f"Non-whitelisted IP {client_ip} for org {org_id} domain {sender_domain}"
                )
                self._set_cached(cache_key, {"action": action})
                if _dispatcher_available:
                    try:
                        dispatch_event(
                            Events.SECURITY_IP_BLOCKED,
                            data={
                                "ip_address": client_ip,
                                "domain": sender_domain,
                                "rule_type": "whitelist_miss",
                            },
                            org_id=org_id,
                            domain=sender_domain,
                            source_service="postfix",
                            use_redis=True,
                        )
                    except Exception:
                        pass
                return action

            self._set_cached(cache_key, {"action": "DUNNO"})
            return "DUNNO"

        except Exception as e:
            logger.error(f"IP access check failed: {e}")
            return "DUNNO"  # Fail open


def main():
    policy = IPAccessPolicy()

    # Postfix policy protocol: read key=value pairs, blank line terminates
    while True:
        attrs = {}
        while True:
            line = sys.stdin.readline()
            if not line:
                return  # EOF
            line = line.strip()
            if not line:
                break  # End of request
            if "=" in line:
                key, value = line.split("=", 1)
                attrs[key] = value

        if not attrs:
            continue

        sender = attrs.get("sender", "")
        client_ip = attrs.get("client_address", "")
        sasl_username = attrs.get("sasl_username", "")

        # Only enforce for authenticated senders (submission/smtps)
        if not sasl_username:
            action = "DUNNO"
        else:
            sender_domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
            action = policy.check_access(sender_domain, client_ip)

        sys.stdout.write(f"action={action}\n\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
