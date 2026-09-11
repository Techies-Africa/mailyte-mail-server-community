#!/usr/bin/env python3
"""
Rspamd Settings Sync — Per-Organization Spam Policies

Reads organization spam settings from MySQL and syncs them to Redis
where Rspamd's settings module reads them at scan time.

Each organization can configure:
  - spam_threshold: Score at which email is marked as spam (default: 6)
  - reject_threshold: Score at which email is rejected (default: 15)
  - quarantine_enabled: Whether to quarantine instead of deliver spam
  - sender_whitelist: List of trusted sender addresses
  - sender_blacklist: List of blocked sender addresses
  - domain_whitelist: List of trusted sending domains

Usage:
    python3 sync_rspamd_settings.py              # One-time sync
    python3 sync_rspamd_settings.py --watch       # Continuous sync every 60s
    python3 sync_rspamd_settings.py --org <id>    # Sync specific org
"""

import argparse
import json
import logging
import os
import sys
import time

import mysql.connector
import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [rspamd-sync] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("rspamd-sync")

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# The multimap sender/domain lists MUST land in the Redis db the rspamd
# multimap module reads them from: mailer/rspamd/config/local.d/multimap.conf
# maps are "redis://redis:6379/3/org_sender_whitelist_${rcpt_domain}" etc.
# The settings module (settings.conf) uses the default db 0. Writing both to
# db 0 (the old behaviour) meant per-org sender lists never matched anything.
MULTIMAP_REDIS_DB = 3

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_NAME = os.getenv("DB_NAME", "mailserver")
DB_USER = os.getenv("DB_USER", "mailuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mailpassword")

SYNC_INTERVAL = int(os.getenv("RSPAMD_SYNC_INTERVAL", 60))

# Default thresholds (used when org has no custom settings)
DEFAULT_SPAM_THRESHOLD = 6
DEFAULT_REJECT_THRESHOLD = 15
DEFAULT_GREYLIST_THRESHOLD = 4
DEFAULT_REWRITE_THRESHOLD = 10


def get_redis(db: int = 0):
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=db, decode_responses=True)


def get_db():
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )


def sync_all_orgs(r, r_multimap, conn):
    """Sync settings for all active organizations.

    r writes the settings-module keys (db 0); r_multimap writes the
    sender/domain list keys the multimap module reads (db 3)."""
    cursor = conn.cursor(dictionary=True)

    # Get all active organizations with their domains. organizations has an
    # `active` tinyint, not a `status` column (0001_baseline) -- the old
    # `o.status = 'active'` filter crashed the whole sync with
    # "Unknown column" before anything was written.
    cursor.execute("""
        SELECT o.id AS org_id, o.name AS org_name, o.settings AS org_settings,
               GROUP_CONCAT(d.domain) AS domains
        FROM organizations o
        JOIN domains d ON d.organization_id = o.id AND d.active = 1
        WHERE o.active = 1
        GROUP BY o.id
    """)

    orgs = cursor.fetchall()
    synced = 0

    # Track which settings keys we write (to clean up stale ones)
    active_keys = set()

    for org in orgs:
        org_id = org["org_id"]
        domains = org["domains"].split(",") if org["domains"] else []
        org_settings = {}

        if org["org_settings"]:
            try:
                org_settings = (
                    json.loads(org["org_settings"])
                    if isinstance(org["org_settings"], str)
                    else org["org_settings"]
                )
            except (json.JSONDecodeError, TypeError):
                org_settings = {}

        # Extract spam policy settings
        spam_settings = org_settings.get("spam_policy", {})
        spam_threshold = spam_settings.get("spam_threshold", DEFAULT_SPAM_THRESHOLD)
        reject_threshold = spam_settings.get("reject_threshold", DEFAULT_REJECT_THRESHOLD)
        greylist_threshold = spam_settings.get("greylist_threshold", DEFAULT_GREYLIST_THRESHOLD)
        rewrite_threshold = spam_settings.get("rewrite_threshold", DEFAULT_REWRITE_THRESHOLD)
        quarantine_enabled = spam_settings.get("quarantine_enabled", False)

        # Build Rspamd settings object for this org
        for domain in domains:
            setting_id = f"org_{org_id}_{domain}"

            rspamd_setting = {
                "id": setting_id,
                "priority": 10,
                "rcpt": domain,
                "apply": {
                    "actions": {
                        "reject": reject_threshold,
                        "add header": spam_threshold,
                        "greylist": greylist_threshold,
                        "rewrite subject": rewrite_threshold,
                    }
                },
            }

            # If quarantine is enabled, rewrite subject instead of just adding header
            if quarantine_enabled:
                rspamd_setting["apply"]["actions"]["quarantine"] = spam_threshold

            # Write to Redis
            settings_key = f"rspamd_settings:{setting_id}"
            r.set(settings_key, json.dumps(rspamd_setting))
            active_keys.add(settings_key)
            synced += 1

        # Sync sender whitelists/blacklists to Redis multimap keys -- in the
        # db multimap.conf actually reads (see MULTIMAP_REDIS_DB above).
        sender_whitelist = spam_settings.get("sender_whitelist", [])
        sender_blacklist = spam_settings.get("sender_blacklist", [])
        domain_whitelist = spam_settings.get("domain_whitelist", [])

        for domain in domains:
            # Sender whitelist
            wl_key = f"org_sender_whitelist_{domain}"
            if sender_whitelist:
                r_multimap.delete(wl_key)
                for addr in sender_whitelist:
                    r_multimap.sadd(wl_key, addr)
            else:
                r_multimap.delete(wl_key)

            # Sender blacklist
            bl_key = f"org_sender_blacklist_{domain}"
            if sender_blacklist:
                r_multimap.delete(bl_key)
                for addr in sender_blacklist:
                    r_multimap.sadd(bl_key, addr)
            else:
                r_multimap.delete(bl_key)

            # Domain whitelist
            dwl_key = f"org_domain_whitelist_{domain}"
            if domain_whitelist:
                r_multimap.delete(dwl_key)
                for d in domain_whitelist:
                    r_multimap.sadd(dwl_key, d)
            else:
                r_multimap.delete(dwl_key)

    cursor.close()
    return synced


def sync_single_org(r, r_multimap, conn, org_id):
    """Sync settings for a single organization."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT o.id AS org_id, o.name AS org_name, o.settings AS org_settings,
               GROUP_CONCAT(d.domain) AS domains
        FROM organizations o
        JOIN domains d ON d.organization_id = o.id AND d.active = 1
        WHERE o.id = %s AND o.active = 1
        GROUP BY o.id
    """,
        (org_id,),
    )

    org = cursor.fetchone()
    cursor.close()

    if not org:
        logger.warning(f"Organization {org_id} not found or not active")
        return 0

    # Delegate to full sync logic (it handles a list, so wrap in one)
    return sync_all_orgs(r, r_multimap, conn)


def main():
    parser = argparse.ArgumentParser(
        description="Sync organization spam policies to Rspamd via Redis"
    )
    parser.add_argument("--watch", action="store_true", help="Continuously sync every N seconds")
    parser.add_argument("--org", type=str, help="Sync specific organization by ID")
    parser.add_argument(
        "--interval", type=int, default=SYNC_INTERVAL, help="Sync interval in seconds (default: 60)"
    )
    args = parser.parse_args()

    logger.info(
        f"Connecting to Redis {REDIS_HOST}:{REDIS_PORT} and MySQL {DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    if args.watch:
        logger.info(f"Starting continuous sync every {args.interval}s")
        while True:
            try:
                r = get_redis()
                r_multimap = get_redis(MULTIMAP_REDIS_DB)
                conn = get_db()
                count = sync_all_orgs(r, r_multimap, conn)
                conn.close()
                logger.info(f"Synced {count} organization settings to Rspamd")
            except Exception as e:
                logger.error(f"Sync failed: {e}")
            time.sleep(args.interval)
    else:
        r = get_redis()
        r_multimap = get_redis(MULTIMAP_REDIS_DB)
        conn = get_db()
        if args.org:
            count = sync_single_org(r, r_multimap, conn, args.org)
        else:
            count = sync_all_orgs(r, r_multimap, conn)
        conn.close()
        logger.info(f"Synced {count} organization settings to Rspamd")


if __name__ == "__main__":
    main()
