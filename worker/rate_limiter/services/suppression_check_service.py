#!/usr/bin/env python3
"""
Suppression Check Service - outbound recipient suppression lookups

The enforcement half of the suppression list. The tracking service and the
gateway API write email_suppressions rows (bounces, complaints, unsubscribes,
manual adds); until this service existed nothing on the outbound path ever
read them back, so a suppressed recipient kept receiving mail. The Postfix
policy bridge consults this (through app.py's /check_rate_limit and /policy
endpoints) once per outbound message at the DATA phase and turns a hit into
an SMTP 554 5.7.1 REJECT -- the sender is told, in session, exactly why the
message was refused. Mail is never silently dropped.

Contract:
- FAIL OPEN. A database outage, a missing pool, or any unexpected error
  answers "not suppressed" (logged). The suppression list must never be the
  reason mail stops flowing -- kin to the fail-open rule every other error
  path on the policy bridge already follows.
- Short in-process TTL cache so the mail path costs at most one DB query per
  (organization, recipient) per window. A freshly added suppression can
  therefore take up to CACHE_TTL seconds to start rejecting, and a freshly
  removed one can keep rejecting for the same window -- the same staleness
  budget get_smtp_credential already accepts on this path.
"""

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class SuppressionCheckService:
    """Read-only view over email_suppressions for the outbound mail path."""

    CACHE_TTL = 60  # seconds
    CACHE_MAX_ENTRIES = 5000

    def __init__(self, database_service):
        """database_service: RateLimitDatabaseService -- reused for its
        connection pool; this service issues one SELECT and owns no state
        of its own beyond the cache."""
        self.db = database_service
        # (organization_id, recipient.lower()) -> (expires_monotonic, result)
        self._cache: dict = {}

    def is_suppressed(self, organization_id: str, recipient: str) -> tuple[bool, str]:
        """Return (suppressed, suppression_type) for one org/recipient pair.

        suppression_type is '' when not suppressed or on any error --
        callers only need it to name the reason in the reject text.
        """
        if not organization_id or not recipient:
            return False, ""

        key = (organization_id, recipient.strip().lower())
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        result = self._lookup(organization_id, recipient.strip())

        if len(self._cache) >= self.CACHE_MAX_ENTRIES:
            # Wholesale eviction beats bookkeeping here: it is a cache in
            # front of one indexed SELECT, not a store, and the pathological
            # case (an org cycling >5000 distinct recipients per minute) just
            # degrades to one query per check.
            self._cache.clear()
        self._cache[key] = (now + self.CACHE_TTL, result)
        return result

    def _lookup(self, organization_id: str, recipient: str) -> tuple[bool, str]:
        """One SELECT against email_suppressions. Mirrors the tracking
        service's own is_suppressed predicate exactly (active, unexpired,
        same org) so the two sides of the feature can never disagree about
        what 'suppressed' means."""
        pool = getattr(self.db, "db_pool", None)
        if not pool:
            logger.warning("Suppression lookup skipped: no database pool (fail open)")
            return False, ""

        try:
            conn = pool.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT suppression_type
                    FROM email_suppressions
                    WHERE email = %s
                    AND organization_id = %s
                    AND (expires_at IS NULL OR expires_at > %s)
                    AND active = 1
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (recipient, organization_id, datetime.utcnow()),
                )
                row = cursor.fetchone()
                cursor.close()
            finally:
                conn.close()

            if row:
                return True, str(row[0] or "")
            return False, ""

        except Exception as e:
            logger.error(
                f"Suppression lookup failed for {recipient} (org {organization_id}): {e} "
                "(fail open)"
            )
            return False, ""
