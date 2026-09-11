"""Send-credit and app-sending-pool enforcement.

Laravel sells prepaid credits and a monthly app-sending allowance; this is
where they stop meaning nothing. Both are enforced ONLY for key-authenticated
traffic (an SMTP credential, whose SASL username carries no '@') -- a person
sending from their own mailbox is governed by their per-mailbox daily limit
and must never be blocked because the company's API allowance ran out.

Two hard rules, both learned the expensive way:

* This is consulted from the ONE existing policy consultation point, never a
  new one. Adding a second Postfix consultation double-counts every message
  and deferred essentially all mail on 2026-08-27.

* Every failure path fails OPEN. A database blip must not stop a customer's
  mail; the worst case of failing open is a small amount of unbilled sending,
  and the worst case of failing closed is an outage.

Disabled by default (SEND_CREDIT_ENFORCEMENT=false) so deploying this code
REFUSES nothing until someone has watched it against real traffic. The flag
gates refusals only: counting (record()) always runs, because the watching
itself needs moving counters -- with metering behind the same flag, every
credential send went through unmetered, app_send_used sat at 0 forever, the
reconcile loop had nothing to book, and the Usage page showed a full pool no
matter how much a customer sent.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def enforcement_enabled() -> bool:
    """Off unless explicitly switched on.

    The default is deliberately the safe one: shipping the code and enabling
    it are two separate decisions, and the second needs someone watching.
    """
    return os.getenv("SEND_CREDIT_ENFORCEMENT", "false").strip().lower() in ("1", "true", "yes")


class SendCreditService:
    """Counts app sending against the monthly pool, then against credits."""

    def __init__(self, redis_client, config_service):
        self.redis = redis_client
        self.config_service = config_service

    # -- keys -------------------------------------------------------------

    @staticmethod
    def _pool_key(organization_id: str) -> str:
        """Monthly counter, keyed by calendar month so it resets on its own
        rather than needing a job to clear it."""
        month = datetime.now(timezone.utc).strftime("%Y%m")
        return f"app_send:{organization_id}:{month}"

    @staticmethod
    def _dedicated_ip_key(organization_id: str) -> str:
        """Whether this organization sends from its own IP, as pushed by
        Laravel. Above the shared-pool ceiling it is the difference between
        sending and not."""
        return f"dedicated_ip:{organization_id}"

    @staticmethod
    def _balance_key(organization_id: str) -> str:
        """Laravel is the ledger of record; this is the enforced copy it
        pushes down, and what the send path can read fast enough to consult
        on every message."""
        return f"send_credits:{organization_id}"

    # -- reads ------------------------------------------------------------

    def pool_used(self, organization_id: str) -> int:
        try:
            value = self.redis.get(self._pool_key(organization_id))
            return int(value) if value else 0
        except Exception as exc:
            logger.error(f"send-credit: pool read failed for {organization_id}: {exc}")
            return 0

    def balance(self, organization_id: str) -> int | None:
        """Enforced credit balance, or None when we have never been told one.

        None means "no opinion" and is treated as allow: an organization whose
        balance has not been pushed yet must not be cut off by our own missing
        data.
        """
        try:
            value = self.redis.get(self._balance_key(organization_id))
            return int(value) if value is not None else None
        except Exception as exc:
            logger.error(f"send-credit: balance read failed for {organization_id}: {exc}")
            return None

    def has_dedicated_ip(self, organization_id: str) -> bool:
        try:
            return self.redis.get(self._dedicated_ip_key(organization_id)) == "1"
        except Exception as exc:
            logger.error(f"send-credit: dedicated-ip read failed for {organization_id}: {exc}")
            # Assume they have one: refusing a large sender because we could
            # not read a flag is the worse mistake.
            return True

    def shared_pool_ceiling(self) -> int:
        """Monthly volume a sender may put through the SHARED pool.

        Past this a broadcast has to move to its own IP. The limit is not
        commercial -- bandwidth is not the constraint -- it is that one bad
        list at this volume damages deliverability for every other customer
        on the shared address.
        """
        try:
            return int(os.getenv("BROADCAST_SHARED_CEILING", "50000"))
        except ValueError:
            return 50000

    def pool_allowance(self, organization_id: str) -> int:
        """The included monthly app-sending allowance, as pushed by Laravel."""
        try:
            value = self.redis.get(f"app_send_allowance:{organization_id}")
            return int(value) if value else 0
        except Exception as exc:
            logger.error(f"send-credit: allowance read failed for {organization_id}: {exc}")
            return 0

    # -- the check --------------------------------------------------------

    def check(self, organization_id: str) -> tuple[bool, str]:
        """May this organization send one more app message?

        Returns (allowed, reason). Consulted once per message from the policy
        endpoint, and only for key-authenticated senders.
        """
        if not enforcement_enabled():
            return True, "enforcement disabled"

        if not organization_id or organization_id == "unknown":
            # We could not tell whose message this is. Refusing on that basis
            # would block mail for a data problem of our own making.
            return True, "no organization resolved"

        try:
            allowance = self.pool_allowance(organization_id)
            used = self.pool_used(organization_id)

            # The shared-pool ceiling is checked BEFORE the allowance and
            # before credits, because it is not about whether the sender has
            # paid -- a customer with a million credits still cannot put that
            # volume through a shared address. Credits buy sending; they do
            # not buy other customers' deliverability.
            ceiling = self.shared_pool_ceiling()
            if used >= ceiling and not self.has_dedicated_ip(organization_id):
                return (
                    False,
                    f"above the {ceiling:,}/month shared-pool limit; a dedicated IP is required",
                )

            # Inside the included allowance: nothing to charge.
            if used < allowance:
                return True, "within included allowance"

            balance = self.balance(organization_id)

            if balance is None:
                return True, "no balance pushed"

            if balance > 0:
                return True, "covered by credits"

            return False, "no sending credits remaining"
        except Exception as exc:
            logger.error(f"send-credit: check failed for {organization_id}: {exc}")
            return True, f"check failed: {exc}"

    def record(self, organization_id: str) -> None:
        """Count one delivered app message.

        Increments the monthly pool and, once past the allowance, decrements
        the enforced credit balance. Laravel reconciles its ledger against
        this; on a disagreement Laravel wins, because it is the record of what
        the customer actually bought.

        Deliberately NOT gated on enforcement_enabled(): that flag decides
        whether check() may refuse a message, not whether sending is counted.
        Metering has to run from day one or there is no usage to bill, no
        counter to reconcile, and no real-traffic behaviour to watch before
        the flag is ever flipped on.
        """
        if not organization_id or organization_id == "unknown":
            return

        try:
            pool_key = self._pool_key(organization_id)
            used = self.redis.incr(pool_key)

            # 70 days: comfortably longer than a month so a late reconciliation
            # can still read it, short enough not to accumulate forever.
            if used == 1:
                self.redis.expire(pool_key, 70 * 24 * 3600)

            if used > self.pool_allowance(organization_id):
                balance = self.balance(organization_id)
                if balance is not None and balance > 0:
                    self.redis.decr(self._balance_key(organization_id))
        except Exception as exc:
            # Never let accounting failure block a message that was already
            # judged allowed.
            logger.error(f"send-credit: record failed for {organization_id}: {exc}")

    # -- writes from Laravel ----------------------------------------------

    def set_balance(self, organization_id: str, balance: int) -> None:
        """Push the authoritative balance down from Laravel."""
        try:
            self.redis.set(self._balance_key(organization_id), max(0, int(balance)))
        except Exception as exc:
            logger.error(f"send-credit: balance write failed for {organization_id}: {exc}")

    def set_dedicated_ip(self, organization_id: str, has_ip: bool) -> None:
        try:
            self.redis.set(self._dedicated_ip_key(organization_id), "1" if has_ip else "0")
        except Exception as exc:
            logger.error(f"send-credit: dedicated-ip write failed for {organization_id}: {exc}")

    def set_pool_allowance(self, organization_id: str, allowance: int) -> None:
        try:
            self.redis.set(f"app_send_allowance:{organization_id}", max(0, int(allowance)))
        except Exception as exc:
            logger.error(f"send-credit: allowance write failed for {organization_id}: {exc}")

    def snapshot(self, organization_id: str) -> dict:
        """What the enforcement side currently believes, for reconciliation."""
        return {
            "organization_id": organization_id,
            "enforcement_enabled": enforcement_enabled(),
            "app_send_allowance": self.pool_allowance(organization_id),
            "app_send_used": self.pool_used(organization_id),
            "credit_balance": self.balance(organization_id),
            "dedicated_ip": self.has_dedicated_ip(organization_id),
            "shared_pool_ceiling": self.shared_pool_ceiling(),
        }
