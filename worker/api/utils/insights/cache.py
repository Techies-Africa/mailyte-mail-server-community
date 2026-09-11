"""
Redis-backed rollup cache, refresh throttle and stampede guard for insights.

Spec 18d allows a stale-but-labelled rollup, and that is what makes this page
affordable: a rollup is computed at most once per (mailbox, window) per
INSIGHTS_CACHE_TTL_SECONDS (6h) and every read in between is one Redis GET.
window.to carries the compute time, so the client can say "as of 09:14".

Redis is the same instance the api service already reaches for transport
rules (routes/transport_rules.py) and the rate limiter; the connection
settings come from shared.config.RedisConfig so REDIS_URL / REDIS_PASSWORD are
honoured, not just host and port.

**Redis being down never breaks insights.** Every operation here swallows its
error and answers as though the cache were empty; the rollup is then simply
computed on each request until Redis returns. A short backoff stops a dead
Redis from adding two connect timeouts to every call in the meantime.

No table, no migration: nothing here is a record anyone needs to keep.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time

from shared.config import RedisConfig

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = int(os.getenv("INSIGHTS_CACHE_TTL_SECONDS", str(6 * 3600)))
CONTEXT_TTL_SECONDS = int(os.getenv("INSIGHTS_CONTEXT_TTL_SECONDS", "300"))
# A forced refresh (?refresh=true) recomputes at most this often per mailbox.
REFRESH_MIN_INTERVAL_SECONDS = int(os.getenv("INSIGHTS_REFRESH_MIN_INTERVAL_SECONDS", "300"))
# Held while one process computes a rollup so a burst of cold requests for
# the same mailbox waits for the first result instead of each opening IMAP.
LOCK_TTL_SECONDS = int(os.getenv("INSIGHTS_LOCK_TTL_SECONDS", "90"))
LOCK_WAIT_SECONDS = float(os.getenv("INSIGHTS_LOCK_WAIT_SECONDS", "20"))
# Process-wide ceiling on rollups computing at once. FastAPI's threadpool is
# shared with every other sync route in this service; a rollup holds a worker
# for tens of seconds, and forty of them at once would be a webmail outage.
MAX_CONCURRENT_ROLLUPS = int(os.getenv("INSIGHTS_MAX_CONCURRENT", "3"))
ROLLUP_SLOT_WAIT_SECONDS = float(os.getenv("INSIGHTS_SLOT_WAIT_SECONDS", "10"))

_UNAVAILABLE_BACKOFF_SECONDS = 60
_KEY_PREFIX = "mailyte:insights"
# Bump when the payload shape changes, so a deploy never serves a cached
# rollup in yesterday's shape.
SCHEMA_VERSION = "v1"

_client = None
_client_lock = threading.Lock()
_unavailable_until = 0.0

_rollup_slots = threading.BoundedSemaphore(MAX_CONCURRENT_ROLLUPS)


class InsightsBusyError(Exception):
    """Every rollup slot in this process is taken. A 503 with Retry-After."""


def use_client(client) -> None:
    """Inject a client (tests pass a fakeredis instance)."""
    global _client, _unavailable_until
    with _client_lock:
        _client = client
        _unavailable_until = 0.0


def redis_client():
    """Lazily-built shared client, or None while Redis is known to be down."""
    global _client, _unavailable_until
    if _client is not None:
        return _client
    if time.monotonic() < _unavailable_until:
        return None
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import redis

            client = redis.Redis.from_url(
                RedisConfig().url,
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True,
            )
            client.ping()
            _client = client
        except Exception as exc:
            logger.warning("Insights cache unavailable (%s); computing without it", exc)
            _unavailable_until = time.monotonic() + _UNAVAILABLE_BACKOFF_SECONDS
            return None
    return _client


def _mark_unavailable(exc: Exception) -> None:
    global _client, _unavailable_until
    logger.warning("Insights cache error (%s); computing without it", exc)
    with _client_lock:
        _client = None
        _unavailable_until = time.monotonic() + _UNAVAILABLE_BACKOFF_SECONDS


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def rollup_key(email: str, window: str) -> str:
    return f"{_KEY_PREFIX}:rollup:{SCHEMA_VERSION}:{email.lower()}:{window}"


def context_key(email: str, message_id: str) -> str:
    return f"{_KEY_PREFIX}:context:{SCHEMA_VERSION}:{email.lower()}:{message_id}"


def refresh_key(email: str) -> str:
    return f"{_KEY_PREFIX}:refresh:{email.lower()}"


def lock_key(email: str, window: str) -> str:
    return f"{_KEY_PREFIX}:lock:{email.lower()}:{window}"


# ---------------------------------------------------------------------------
# Operations -- each degrades to "no cache" on any error
# ---------------------------------------------------------------------------


def get_json(key: str) -> dict | None:
    client = redis_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:
        _mark_unavailable(exc)
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def set_json(key: str, value: dict, ttl_seconds: int) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, separators=(",", ":")), ex=max(1, ttl_seconds))
    except Exception as exc:
        _mark_unavailable(exc)


def try_acquire(key: str, ttl_seconds: int) -> bool:
    """SET NX EX. True when this caller now holds the key.

    Without Redis every caller "acquires", so a refresh is never throttled and
    a stampede is not guarded -- the process-wide slot semaphore still bounds
    the damage.
    """
    client = redis_client()
    if client is None:
        return True
    try:
        return bool(client.set(key, str(int(time.time())), nx=True, ex=max(1, ttl_seconds)))
    except Exception as exc:
        _mark_unavailable(exc)
        return True


def exists(key: str) -> bool:
    client = redis_client()
    if client is None:
        return False
    try:
        return bool(client.exists(key))
    except Exception as exc:
        _mark_unavailable(exc)
        return False


def ttl(key: str) -> int | None:
    client = redis_client()
    if client is None:
        return None
    try:
        value = client.ttl(key)
    except Exception as exc:
        _mark_unavailable(exc)
        return None
    return int(value) if value is not None and value >= 0 else None


def release(key: str) -> None:
    client = redis_client()
    if client is None:
        return
    with contextlib.suppress(Exception):
        client.delete(key)


def wait_for(key: str, timeout: float = LOCK_WAIT_SECONDS, interval: float = 0.5) -> dict | None:
    """Poll for another worker's result to land. None if it does not in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        value = get_json(key)
        if value is not None:
            return value
        if redis_client() is None:
            return None
    return None


class rollup_slot:  # noqa: N801 -- context manager used as a lowercase verb
    """`with rollup_slot():` -- one of the process's concurrent-rollup slots."""

    def __enter__(self):
        if not _rollup_slots.acquire(timeout=ROLLUP_SLOT_WAIT_SECONDS):
            raise InsightsBusyError("Too many insight rollups in progress")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        with contextlib.suppress(ValueError):
            _rollup_slots.release()
        return False
