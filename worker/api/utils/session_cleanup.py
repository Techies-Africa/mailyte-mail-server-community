#!/usr/bin/env python3
"""
Session Cleanup — periodic purge of expired/revoked web_sessions (phase-03 task 3.6).

Mirrors worker/webhooks/services/cleanup_service.py's background-thread
pattern rather than pulling in a new scheduler dependency for one query.
"""

import logging
import threading
import time

from utils.database import get_db_connection

logger = logging.getLogger(__name__)

_PURGE_SQL = """
    DELETE FROM web_sessions
     WHERE (expires_at < NOW() OR absolute_expiry < NOW() OR revoked_at IS NOT NULL)
       AND created_at < NOW() - INTERVAL 7 DAY
"""

_running = False


def purge_expired_sessions() -> int:
    """Delete web_sessions rows that are expired/revoked AND at least 7 days
    old. The 7-day grace period keeps a recently-revoked session's row
    around briefly for incident review before it's gone for good."""
    conn = get_db_connection()
    if not conn:
        logger.warning("Session cleanup skipped: database connection failed")
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(_PURGE_SQL)
        conn.commit()
        deleted = cursor.rowcount
        cursor.close()
        if deleted:
            logger.info(f"Session cleanup: purged {deleted} expired/revoked web_sessions rows")
        return deleted
    except Exception as e:
        logger.error(f"Session cleanup failed: {e}")
        return 0
    finally:
        conn.close()


def _cleanup_loop(interval_seconds: int) -> None:
    while True:
        for _ in range(interval_seconds):
            time.sleep(1)
        try:
            purge_expired_sessions()
        except Exception as e:
            logger.error(f"Session cleanup loop error: {e}", exc_info=True)


def start_session_cleanup(interval_hours: int = 6) -> None:
    """Start the background purge thread once per process. Non-fatal by
    design -- a failure here must never crash the API service."""
    global _running
    if _running:
        return
    _running = True
    thread = threading.Thread(
        target=_cleanup_loop,
        args=(interval_hours * 3600,),
        name="web-session-cleanup",
        daemon=True,
    )
    thread.start()
    logger.info(f"Session cleanup thread started (every {interval_hours}h)")
