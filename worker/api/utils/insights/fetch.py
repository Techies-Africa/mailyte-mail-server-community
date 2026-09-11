"""
IMAP side of the insights engine: which folders, which UIDs, batched
header-only fetches -- and nothing else.

Bounded on three axes, because a statistics page must never be the reason
webmail slows down:

* **Messages.** At most INSIGHTS_MAX_PER_FOLDER (20,000) per folder per
  window, newest first, so a cut drops the oldest mail. The count actually
  read is reported in window.messages_considered.
* **Time.** A wall-clock deadline shared across folders; when it passes, the
  remaining batches are skipped and window.truncated is set. Stale-but-labelled
  beats a request that never returns.
* **Connections.** The caller opens ONE session through shared.imap_mail's
  imap_session, which takes one of the mailbox's concurrency slots exactly like
  a folder listing does. The other slots stay free for the webmail.

Every fetch is header-only (BODY.PEEK[HEADER.FIELDS (...)]), so a rollup over
20,000 messages moves a few MB, not the mailbox.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from shared.imap_mail import ImapUnavailableError

from .records import FETCH_ITEMS, HeaderRecord, parse_fetch_response, parse_internaldate

logger = logging.getLogger(__name__)

MAX_PER_FOLDER = int(os.getenv("INSIGHTS_MAX_PER_FOLDER", "20000"))
FETCH_BATCH = int(os.getenv("INSIGHTS_FETCH_BATCH", "500"))
# Wall-clock budget for the whole IMAP phase of one rollup.
TIME_BUDGET_SECONDS = float(os.getenv("INSIGHTS_TIME_BUDGET_SECONDS", "45"))
# Correspondent lookups for the context endpoint are per address, so far
# smaller; a separate, tighter cap keeps a message open from costing a rollup.
CONTEXT_MAX_PER_FOLDER = int(os.getenv("INSIGHTS_CONTEXT_MAX_PER_FOLDER", "1000"))
CONTEXT_TIME_BUDGET_SECONDS = float(os.getenv("INSIGHTS_CONTEXT_TIME_BUDGET_SECONDS", "15"))
# Deep threads reference dozens of ids; searching for each one is 3 SEARCHes
# per folder. The most recent ones are the ones still in play.
THREAD_MAX_TOKENS = 12

# IMAP LIST response: (\flags) delimiter name. Same expression shared/imap_mail
# uses, kept locally so this module does not depend on its private names.
_LIST_RE = re.compile(r"^\(([^)]*)\)\s+(\S+)\s+(.+)$")
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Conventional names, consulted only when LIST carries no special-use flags
# (RFC 6154). Dovecot on this deployment flags Sent, "Sent Messages" and
# Drafts (mailer/dovecot/config/dovecot.conf), so this is the fallback for a
# server that does not.
_SENT_NAMES = ("Sent", "Sent Messages", "Sent Items", "Sent Mail")
_DRAFTS_NAMES = ("Drafts",)

Roles = dict[str, list[str]]


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _text(item) -> str:
    if isinstance(item, bytes):
        return item.decode("utf-8", errors="replace")
    return str(item)


def imap_date(when: datetime) -> str:
    """`1-Jun-2026` -- the only date form IMAP SEARCH accepts, English months."""
    utc = when.astimezone(UTC)
    return f"{utc.day}-{_MONTH_NAMES[utc.month - 1]}-{utc.year}"


# ---------------------------------------------------------------------------
# Folder roles
# ---------------------------------------------------------------------------


def resolve_roles(conn) -> Roles:
    """{'inbox': [...], 'sent': [...], 'drafts': [...]} for this mailbox.

    Special-use flags first: they are what the server itself says a folder is
    for, and a migrated mailbox may keep its mail in "Sent Messages" rather
    than "Sent" -- both flagged \\Sent here, and both scanned. Names are the
    fallback for a server that does not advertise RFC 6154.
    """
    try:
        status, data = conn.list()
    except Exception as exc:
        logger.error("IMAP LIST failed: %s", exc)
        raise ImapUnavailableError("Could not list folders") from None

    roles: Roles = {"inbox": ["INBOX"], "sent": [], "drafts": []}
    if status != "OK":
        return roles

    names: list[str] = []
    for item in data or []:
        if item is None:
            continue
        if isinstance(item, tuple):
            # A name sent as a literal: (b'(\\flags) "/" {n}', b'name').
            match = _LIST_RE.match(_text(item[0]) + " x")
            flags = match.group(1).lower() if match else ""
            name = _text(item[1]).strip()
        else:
            match = _LIST_RE.match(_text(item))
            if not match:
                continue
            flags = match.group(1).lower()
            name = match.group(3).strip().strip('"')
        if not name or "\\noselect" in flags:
            continue
        names.append(name)
        if "\\sent" in flags:
            roles["sent"].append(name)
        if "\\drafts" in flags:
            roles["drafts"].append(name)

    if not roles["sent"]:
        roles["sent"] = [n for n in _SENT_NAMES if n in names]
    if not roles["drafts"]:
        roles["drafts"] = [n for n in _DRAFTS_NAMES if n in names]
    return roles


def role_for(folder: str, roles: Roles) -> str:
    if folder.upper() == "INBOX":
        return "inbox"
    if folder in roles.get("sent", ()):
        return "sent"
    if folder in roles.get("drafts", ()):
        return "drafts"
    return "other"


# ---------------------------------------------------------------------------
# UID search and batched header fetch
# ---------------------------------------------------------------------------


def _select(conn, folder: str) -> bool:
    try:
        status, _ = conn.select(_quote(folder), readonly=True)
    except Exception as exc:
        logger.error("SELECT %s failed: %s", folder, exc)
        raise ImapUnavailableError("Could not open folder") from None
    return status == "OK"


def _uid_search(conn, folder: str, *criteria: str) -> list[str]:
    """UIDs matching `criteria`, ascending, in an already-selected folder."""
    try:
        status, data = conn.uid("SEARCH", None, *criteria)
    except Exception as exc:
        logger.error("SEARCH in %s failed: %s", folder, exc)
        raise ImapUnavailableError("Could not search folder") from None
    if status != "OK" or not data or data[0] is None:
        return []
    return [_text(u) for u in data[0].split()]


@dataclass
class FetchResult:
    records: list[HeaderRecord] = field(default_factory=list)
    skipped: int = 0
    truncated: bool = False


def fetch_records(
    conn,
    folder: str,
    role: str,
    uids: list[str],
    deadline: float,
    cap: int = MAX_PER_FOLDER,
) -> FetchResult:
    """Header records for `uids` (ascending as SEARCH returned them), newest first.

    Fetched newest-first in batches so that both the per-folder cap and the
    time budget drop the OLDEST mail, which is the least informative.
    """
    result = FetchResult()
    if not uids:
        return result
    if len(uids) > cap:
        uids = uids[-cap:]
        result.truncated = True
    newest_first = list(reversed(uids))

    for start in range(0, len(newest_first), FETCH_BATCH):
        if time.monotonic() > deadline:
            result.truncated = True
            break
        batch = newest_first[start : start + FETCH_BATCH]
        try:
            status, data = conn.uid("FETCH", ",".join(batch), FETCH_ITEMS)
        except Exception as exc:
            logger.error("FETCH in %s failed: %s", folder, exc)
            raise ImapUnavailableError("Could not read messages") from None
        if status != "OK":
            continue
        records, skipped = parse_fetch_response(data, folder, role)
        result.records.extend(records)
        result.skipped += skipped
    return result


@dataclass
class Collected:
    records: list[HeaderRecord] = field(default_factory=list)
    roles: Roles = field(default_factory=dict)
    folders: dict[str, int] = field(default_factory=dict)
    skipped: int = 0
    truncated: bool = False

    @property
    def considered(self) -> int:
        return len(self.records)


def collect_window(conn, since: datetime, deadline: float) -> Collected:
    """Every INBOX + Sent message since `since`, plus ALL drafts, as records.

    Drafts are not windowed: "stale drafts" is a question about the drafts
    that exist, and the oldest is the one that matters. They are few, and the
    per-folder cap still applies.
    """
    roles = resolve_roles(conn)
    collected = Collected(roles=roles)
    for role in ("inbox", "sent", "drafts"):
        for folder in roles.get(role, []):
            if time.monotonic() > deadline:
                collected.truncated = True
                break
            if not _select(conn, folder):
                continue
            criteria = ("ALL",) if role == "drafts" else ("SINCE", imap_date(since))
            uids = _uid_search(conn, folder, *criteria)
            result = fetch_records(conn, folder, role, uids, deadline)
            collected.records.extend(result.records)
            collected.folders[folder] = collected.folders.get(folder, 0) + len(result.records)
            collected.skipped += result.skipped
            collected.truncated = collected.truncated or result.truncated
    return collected


def oldest_unread_at(conn, folder: str = "INBOX", probe: int = 20) -> datetime | None:
    """INTERNALDATE of the oldest unread message in `folder`, or None.

    Asked directly rather than read off the window, because the oldest unread
    is very often OLDER than any window -- that is what makes it worth showing.
    UIDs are assigned in arrival order, so the lowest few are the oldest; a
    handful are checked rather than one in case of a re-delivered message.
    """
    try:
        if not _select(conn, folder):
            return None
        uids = _uid_search(conn, folder, "UNSEEN", "UNDELETED")
        if not uids:
            return None
        lowest = sorted(uids, key=int)[:probe]
        status, data = conn.uid("FETCH", ",".join(lowest), "(UID INTERNALDATE)")
    except ImapUnavailableError:
        raise
    except Exception as exc:
        logger.debug("Oldest-unread probe failed in %s: %s", folder, exc)
        return None
    if status != "OK":
        return None

    oldest: datetime | None = None
    for item in data or []:
        raw = item[0] if isinstance(item, tuple) else item
        if not isinstance(raw, bytes):
            continue
        match = re.search(rb'INTERNALDATE\s+"([^"]+)"', raw)
        when = parse_internaldate(match.group(1)) if match else None
        if when and (oldest is None or when < oldest):
            oldest = when
    return oldest


# ---------------------------------------------------------------------------
# Targeted lookups for /messages/{id}/context
# ---------------------------------------------------------------------------


def fetch_one(conn, folder: str, role: str, uid: str) -> HeaderRecord | None:
    if not _select(conn, folder):
        return None
    result = fetch_records(conn, folder, role, [uid], time.monotonic() + 10)
    return result.records[0] if result.records else None


def collect_correspondent(
    conn, roles: Roles, address: str, deadline: float, cap: int = CONTEXT_MAX_PER_FOLDER
) -> list[HeaderRecord]:
    """Mail exchanged with one address: FROM it in INBOX, TO/CC it in Sent.

    IMAP FROM/TO/CC are substring matches on the header, so `a@x.com` also
    finds `a@x.com.au`; the engine filters on the exact address afterwards.
    """
    records: list[HeaderRecord] = []
    for folder in roles.get("inbox", []):
        if time.monotonic() > deadline or not _select(conn, folder):
            continue
        uids = _uid_search(conn, folder, "FROM", _quote(address))
        records += fetch_records(conn, folder, "inbox", uids, deadline, cap=cap).records
    for folder in roles.get("sent", []):
        if time.monotonic() > deadline or not _select(conn, folder):
            continue
        uids = _uid_search(conn, folder, "OR", "TO", _quote(address), "CC", _quote(address))
        records += fetch_records(conn, folder, "sent", uids, deadline, cap=cap).records
    return records


def collect_thread(conn, roles: Roles, seed: HeaderRecord, deadline: float) -> list[HeaderRecord]:
    """Every message sharing an identifier with `seed`, across its own folder,
    INBOX and Sent -- RFC 5322 threading, never subject lines."""
    tokens: list[str] = []
    for token in (seed.message_id, *seed.in_reply_to, *reversed(seed.references)):
        if token and token not in tokens:
            tokens.append(token)
    tokens = tokens[:THREAD_MAX_TOKENS]
    if not tokens:
        return [seed]

    folders = list(dict.fromkeys([seed.folder, *roles.get("inbox", []), *roles.get("sent", [])]))
    found: dict[str, HeaderRecord] = {seed.id: seed}
    for folder in folders:
        if time.monotonic() > deadline or not _select(conn, folder):
            continue
        role = role_for(folder, roles) if folder != seed.folder else seed.role
        uids: set[str] = set()
        for token in tokens:
            for header in ("Message-ID", "References", "In-Reply-To"):
                if time.monotonic() > deadline:
                    break
                try:
                    uids.update(_uid_search(conn, folder, "HEADER", header, _quote(token)))
                except ImapUnavailableError:
                    raise
                except Exception as exc:
                    logger.debug("Thread search failed in %s: %s", folder, exc)
        if not uids:
            continue
        # SEARCH per token, so the same message can be found several times;
        # fetch each UID once.
        result = fetch_records(conn, folder, role, sorted(uids, key=int), deadline)
        for record in result.records:
            found.setdefault(record.id, record)
    return list(found.values())
