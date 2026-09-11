"""
Background purge of expired idempotency keys (phase-04 task 4.2 retention).

Same lightweight daemon-thread pattern already used in this codebase for
periodic cleanup (worker/webhooks/services/cleanup_service.py) rather than a
new scheduler dependency -- kept intentionally simple since this table's
only job is bounding its own growth, not tracking delivery outcomes.
"""

import logging
import threading
import time

from .database import get_db_connection

logger = logging.getLogger(__name__)


def purge_expired_idempotency_keys():
    """Delete idempotency_keys rows past their expires_at. Returns rows deleted."""
    conn = get_db_connection()
    if not conn:
        logger.error("Idempotency cleanup: db connection failed, skipping this cycle")
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM idempotency_keys WHERE expires_at < NOW()")
        conn.commit()
        return cursor.rowcount
    except Exception as exc:
        logger.error(f"Idempotency cleanup failed: {exc}")
        return 0
    finally:
        conn.close()


def _cleanup_loop(interval_seconds):
    while True:
        time.sleep(interval_seconds)
        try:
            deleted = purge_expired_idempotency_keys()
            if deleted:
                logger.info(f"Idempotency cleanup: purged {deleted} expired key(s)")
        except Exception as exc:
            logger.error(f"Idempotency cleanup loop error: {exc}")


def start_idempotency_cleanup(interval_hours=1):
    """Start the daemon cleanup thread. Safe to call once at API startup."""
    thread = threading.Thread(
        target=_cleanup_loop,
        args=(interval_hours * 3600,),
        name="idempotency-cleanup-worker",
        daemon=True,
    )
    thread.start()
    logger.info(f"Idempotency cleanup worker started (every {interval_hours}h)")
