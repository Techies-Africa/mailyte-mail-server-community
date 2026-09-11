"""
Mailbox insights -- server-side aggregation over a mailbox's own headers.

Layout:

* records.py  -- HeaderRecord, FETCH response parsing, per-message classifiers
                 (bounce / automated / attachment-bearing). Pure; no IMAP.
* fetch.py    -- folder roles, windowed UID search, batched header-only fetch,
                 targeted lookups for the context endpoint. The only IMAP user.
* engine.py   -- threading, reply matching, and the six blocks. Pure.
* cache.py    -- Redis rollup cache, refresh throttle, stampede guard,
                 process-wide rollup slots.
* topics.py   -- the optional, opt-in AI topics block.

routes/mailbox_insights.py composes them. Nothing here takes an account id
from a caller: the route passes the session's mailbox, exactly as every other
/api/v1/mailbox route does.
"""

from .engine import (
    build_rollup,
    correspondent_summary,
    other_party,
    thread_summary,
)
from .records import (
    HeaderRecord,
    is_attachment_bearing,
    is_automated,
    is_bounce,
    parse_fetch_response,
)

__all__ = [
    "HeaderRecord",
    "build_rollup",
    "correspondent_summary",
    "is_attachment_bearing",
    "is_automated",
    "is_bounce",
    "other_party",
    "parse_fetch_response",
    "thread_summary",
]
