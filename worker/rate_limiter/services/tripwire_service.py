"""Bounce-rate tripwire: auto-pause an organization's outbound sending.

One bad list can burn the shared IPs' reputation for every customer in
minutes, long before a human reads a dashboard. This watches the only
production-fed signal that exists -- mail_logs, written by the log ingestor
from Postfix's own log -- and when an organization's recent bounce rate
crosses the threshold, sets a Redis pause key the policy path consults on
every message. (delivery_optimizer keeps its own reputation counters, but
nothing feeds them in production, so they are deliberately not used here.)

The split matters: EVALUATION (the background scan) runs only when
TRIPWIRE_ENABLED is on, but ENFORCEMENT is keyed purely on the pause key's
presence -- an operator-set or leftover pause holds even with the flag off,
and flipping the flag on never retroactively pauses anyone until a scan
actually trips. A pause becomes a DEFER at the Postfix bridge, not a
bounce -- reversible by design -- and the key carries its own TTL so a
forgotten pause self-heals instead of silencing a customer forever.

Same two rules as send_credit_service, learned the same expensive way:
consulted only from the existing policy consultation point, and every
infrastructure failure fails OPEN. A broken Redis or MySQL must never
stop mail; the worst case of failing open is a few more minutes of a bad
send, the worst case of failing closed is an outage for everyone.

Disabled by default (TRIPWIRE_ENABLED=false) so deploying this code changes
nothing until someone has watched the thresholds against real traffic.
"""

import logging
import os
import threading

from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class TripwireService:
    """Windowed bounce/complaint-rate scan with a Redis-keyed pause."""

    def __init__(self, redis_client, config_service):
        self.redis = redis_client
        self.config_service = config_service

        # Read once at init: these shape a background scan, not a request
        # path, so a restart to re-tune them is acceptable and cheaper than
        # re-parsing the environment every 60 seconds.
        self.enabled = os.getenv("TRIPWIRE_ENABLED", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self.window_minutes = _env_int("TRIPWIRE_WINDOW_MINUTES", 60)
        self.min_messages = _env_int("TRIPWIRE_MIN_MESSAGES", 50)
        self.bounce_rate_threshold = _env_float("TRIPWIRE_BOUNCE_RATE", 0.05)
        self.complaint_rate_threshold = _env_float("TRIPWIRE_COMPLAINT_RATE", 0.003)
        self.pause_ttl_seconds = _env_int("TRIPWIRE_PAUSE_TTL_SECONDS", 21600)

        # Orgs THIS process paused, so the recovery pass announces
        # ORG_ACTIVATED exactly once per pause instead of guessing from key
        # absence alone (every never-paused org has an absent key).
        self._paused_by_us: set[str] = set()
        self._lock = threading.Lock()

    # -- keys -------------------------------------------------------------

    @staticmethod
    def _pause_key(organization_id: str) -> str:
        return f"org_send_paused:{organization_id}"

    # -- enforcement read (per message, must be cheap and unbreakable) ----

    def is_paused(self, organization_id: str) -> str | None:
        """The pause reason, or None when sending is allowed.

        Consulted on the live policy path for every message, so this is one
        Redis GET and nothing else. On ANY error it returns None: a broken
        Redis must never block mail.
        """
        if not organization_id or organization_id == "unknown":
            return None
        try:
            return self.redis.get(self._pause_key(organization_id))
        except Exception as exc:
            logger.error(f"tripwire: pause read failed for {organization_id}: {exc}")
            return None

    # -- pause / release --------------------------------------------------

    def pause(self, organization_id: str, reason: str, observed_rate: float | None = None):
        """Pause an organization's outbound sending for the configured TTL."""
        try:
            self.redis.set(self._pause_key(organization_id), reason, ex=self.pause_ttl_seconds)
        except Exception as exc:
            # No key, no pause: don't announce a suspension that isn't
            # actually being enforced.
            logger.error(f"tripwire: pause write failed for {organization_id}: {exc}")
            return

        with self._lock:
            self._paused_by_us.add(organization_id)

        logger.warning(
            f"tripwire: paused outbound sending for org {organization_id} "
            f"({self.pause_ttl_seconds}s): {reason}"
        )

        try:
            dispatch_event(
                Events.ORG_SUSPENDED,
                data={
                    "organization_id": organization_id,
                    "reason": reason,
                    "bounce_rate": observed_rate,
                    "window_minutes": self.window_minutes,
                    "source": "tripwire",
                },
                source_service="rate_limiter",
            )
        except Exception as exc:
            # The pause itself already holds; a lost notification is the
            # acceptable half of that failure.
            logger.error(f"tripwire: suspend event dispatch failed for {organization_id}: {exc}")

    def release(self, organization_id: str, reason: str = "manual release") -> bool:
        """Manually un-pause an organization (the DELETE route).

        Returns whether a pause actually existed. If the window is still
        bad, the next evaluation pass will simply trip again -- a manual
        release is an override of the TTL, not of the thresholds.
        """
        removed = False
        try:
            removed = bool(self.redis.delete(self._pause_key(organization_id)))
        except Exception as exc:
            logger.error(f"tripwire: pause delete failed for {organization_id}: {exc}")

        # Untrack either way so the background pass can't later announce a
        # second activation for a pause an operator already lifted.
        with self._lock:
            self._paused_by_us.discard(organization_id)

        if removed:
            logger.warning(f"tripwire: released pause for org {organization_id}: {reason}")
            self._dispatch_activated(organization_id, reason)
        return removed

    def _dispatch_activated(self, organization_id: str, reason: str):
        try:
            dispatch_event(
                Events.ORG_ACTIVATED,
                data={
                    "organization_id": organization_id,
                    "reason": reason,
                    "window_minutes": self.window_minutes,
                    "source": "tripwire",
                },
                source_service="rate_limiter",
            )
        except Exception as exc:
            logger.error(f"tripwire: activate event dispatch failed for {organization_id}: {exc}")

    # -- the scan ---------------------------------------------------------

    def evaluate(self):
        """One evaluation pass; called every 60s from the background thread.

        Nothing may escape into the caller: a failed pass is a logged line
        and a quiet minute, never a crashed thread or a blocked request.
        """
        if not self.enabled:
            return

        try:
            stats = self._window_stats()
        except Exception as exc:
            # Without fresh numbers there is nothing safe to decide in
            # EITHER direction -- no new pauses, and no releases based on a
            # window we couldn't actually read.
            logger.error(f"tripwire: evaluation pass failed: {exc}")
            return

        try:
            for organization_id, row in stats.items():
                total = row["total"]
                if total < self.min_messages:
                    # Below the floor a single unlucky bounce dominates the
                    # ratio; small senders are what the floor protects.
                    continue

                bounce_rate = row["bounced"] / total
                complaint_rate = row["complaints"] / total
                if bounce_rate < self.bounce_rate_threshold and (
                    complaint_rate < self.complaint_rate_threshold
                ):
                    continue

                # An existing pause is left untouched rather than re-set:
                # refreshing the TTL every scan would make a still-bouncing
                # org's pause effectively permanent, and re-dispatching
                # ORG_SUSPENDED each minute would bury the one event that
                # matters. If the window is still bad when the TTL lapses,
                # the very next pass trips it again -- that IS the retry.
                if self.is_paused(organization_id) is not None:
                    continue

                if bounce_rate >= self.bounce_rate_threshold:
                    self.pause(
                        organization_id,
                        f"bounce rate {bounce_rate:.1%} over the last "
                        f"{self.window_minutes}m ({row['bounced']}/{total}) breached "
                        f"the {self.bounce_rate_threshold:.1%} tripwire",
                        observed_rate=round(bounce_rate, 4),
                    )
                else:
                    self.pause(
                        organization_id,
                        f"complaint rate {complaint_rate:.2%} over the last "
                        f"{self.window_minutes}m ({row['complaints']}/{total}) breached "
                        f"the {self.complaint_rate_threshold:.2%} tripwire",
                        observed_rate=round(complaint_rate, 4),
                    )

            self._release_recovered(stats)
        except Exception as exc:
            logger.error(f"tripwire: evaluation pass failed: {exc}")

    def _release_recovered(self, stats: dict[str, dict]):
        """Announce recovery for orgs whose pause expired AND window healed.

        Runs after the pause pass, so an org that is unpaused-but-still-bad
        has just been re-paused and is skipped by the is_paused check here.
        """
        with self._lock:
            tracked = set(self._paused_by_us)

        for organization_id in tracked:
            try:
                if self.is_paused(organization_id) is not None:
                    continue

                row = stats.get(organization_id)
                # No rows (went quiet) or below the floor both count as
                # healthy: there is no longer evidence of a problem.
                healthy = (
                    row is None
                    or row["total"] < self.min_messages
                    or (
                        row["bounced"] / row["total"] < self.bounce_rate_threshold
                        and row["complaints"] / row["total"] < self.complaint_rate_threshold
                    )
                )
                if healthy:
                    logger.warning(
                        f"tripwire: pause expired and window healthy for org "
                        f"{organization_id}; announcing reactivation"
                    )
                    self._dispatch_activated(organization_id, "pause expired; window healthy")
                    with self._lock:
                        self._paused_by_us.discard(organization_id)
            except Exception as exc:
                logger.error(f"tripwire: recovery check failed for {organization_id}: {exc}")

    # -- window stats -----------------------------------------------------

    def _window_stats(self, organization_id: str | None = None) -> dict[str, dict]:
        """Per-org totals for the configured window, from mail_logs.

        Returns {organization_id: {total, bounced, complaints}}. Raises on
        database unavailability -- evaluate() treats that as "no opinion"
        rather than "everyone is healthy".
        """
        db_pool = self.config_service.db_pool
        if not db_pool:
            raise RuntimeError("database not available")

        conn = db_pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # status enum (0001_baseline): 'queued','sending','sent',
            # 'delivered','bounced','rejected','deferred'. Only 'bounced'
            # counts against the tripwire -- 'rejected' is our OWN policy
            # refusing mail (often this very tripwire deferring), and
            # counting it would let a pause feed itself.
            query = """
                SELECT organization_id,
                       COUNT(*) AS total,
                       COALESCE(SUM(status = 'bounced'), 0) AS bounced
                FROM mail_logs
                WHERE `timestamp` >= NOW() - INTERVAL %s MINUTE
                  AND organization_id IS NOT NULL
            """
            params: list = [self.window_minutes]
            if organization_id is not None:
                query += " AND organization_id = %s"
                params.append(organization_id)
            query += " GROUP BY organization_id"
            if organization_id is None:
                # The floor is applied in SQL for the sweep so a busy server
                # doesn't return a row per one-message org every minute; the
                # single-org snapshot skips it because a status page should
                # show the real numbers even under the floor.
                query += " HAVING total >= %s"
                params.append(self.min_messages)

            cursor.execute(query, params)
            stats = {
                row["organization_id"]: {
                    "total": int(row["total"]),
                    "bounced": int(row["bounced"]),
                    "complaints": 0,
                }
                for row in cursor.fetchall()
            }

            # Complaint leg. email_tracking's event_type enum includes
            # 'complained' and POST /api/tracking/complaint writes it, but
            # nothing feeds that endpoint in production yet -- no FBL is
            # registered with any ISP, so this returns no rows today. The
            # producer arrives with phase-05 feedback loops
            # (plans/07-smtp-send/phase-05-feedback.md); the threshold is
            # configured now so turning that on needs no change here.
            query = """
                SELECT organization_id, COUNT(*) AS complaints
                FROM email_tracking
                WHERE event_type = 'complained'
                  AND `timestamp` >= NOW() - INTERVAL %s MINUTE
            """
            params = [self.window_minutes]
            if organization_id is not None:
                query += " AND organization_id = %s"
                params.append(organization_id)
            query += " GROUP BY organization_id"

            cursor.execute(query, params)
            for row in cursor.fetchall():
                # Complaints only make a RATE against sends we can count;
                # an org with complaints but no mail_logs rows in the window
                # has no denominator and is left to the next window.
                if row["organization_id"] in stats:
                    stats[row["organization_id"]]["complaints"] = int(row["complaints"])

            cursor.close()
            return stats
        finally:
            conn.close()

    # -- status -----------------------------------------------------------

    def snapshot(self, organization_id: str) -> dict:
        """Pause state plus current window numbers, for the status route."""
        data = {
            "organization_id": organization_id,
            "evaluation_enabled": self.enabled,
            "paused_reason": self.is_paused(organization_id),
            "window_minutes": self.window_minutes,
            "min_messages": self.min_messages,
            "bounce_rate_threshold": self.bounce_rate_threshold,
            "complaint_rate_threshold": self.complaint_rate_threshold,
            "pause_ttl_seconds": self.pause_ttl_seconds,
        }
        try:
            row = self._window_stats(organization_id).get(organization_id)
        except Exception as exc:
            logger.error(f"tripwire: window stats failed for {organization_id}: {exc}")
            row = None

        total = row["total"] if row else 0
        bounced = row["bounced"] if row else 0
        complaints = row["complaints"] if row else 0
        data.update(
            {
                "window_total": total,
                "window_bounced": bounced,
                "window_complaints": complaints,
                "bounce_rate": round(bounced / total, 4) if total else 0.0,
                "complaint_rate": round(complaints / total, 4) if total else 0.0,
            }
        )
        return data
