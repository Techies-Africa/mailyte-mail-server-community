"""
IMAP access for mailbox-holder surfaces.

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 2. Lives in shared/
rather than inside one service because two services need it: worker/api's
/api/v1/mailbox/* (this phase) and worker/jmap (which today carries its own
copy of the same logic in app.py).

**Why the api service talks to Dovecot directly instead of calling the jmap
service over HTTP.** jmap's Bearer path resolves an API key to a single
organization and refuses any mailbox outside it, so one internal key cannot
serve a webmail that hosts every organization -- the same single-key scoping
problem already recorded against MAIL_SERVER_API_KEY. The alternatives were
inventing a new trusted-service auth mode in jmap (new security surface, on
the service Laravel currently depends on) or going straight to Dovecot the
way worker/api/utils/managesieve.py already does. This is the latter.

**Authentication is the Dovecot master user**, `<mailbox>*<master>`, exactly
as jmap does for Bearer requests and managesieve does for every request.
email_accounts.password is a one-way bcrypt hash, so there is nothing to
recover even if we wanted to -- and the mailbox holder's own password is
therefore never stored anywhere by this stack, not even encrypted for the
life of a session. That is strictly better than the webmail's previous
backend, which kept a reversible copy in mailbox_sessions.smtp_credential.

The caller is responsible for having authenticated the mailbox holder first
(utils/mailbox_auth.require_mailbox). This module trusts the address it is
given; it performs no authorization of its own.

TRACKED DEBT: worker/jmap/app.py still has its own copy of the mailbox
listing and message shaping below. Deliberate -- refactoring a live service
that Laravel depends on, during the migration away from Laravel, is the wrong
order. Once /api/v1/mailbox/* is the only consumer, jmap should import from
here and its duplicates should go.
"""

import base64
import binascii
import contextlib
import hashlib
import imaplib
import logging
import os
import re
import smtplib
import threading
from datetime import UTC
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import SMTP as SMTP_POLICY
from email.utils import (
    formataddr,
    formatdate,
    getaddresses,
    make_msgid,
    parseaddr,
    parsedate_to_datetime,
)
from html import unescape

logger = logging.getLogger(__name__)

IMAP_HOST = os.getenv("IMAP_HOST", "dovecot")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_MASTER_USER = os.getenv("IMAP_MASTER_USER", "")
IMAP_MASTER_PASSWORD = os.getenv("IMAP_MASTER_PASSWORD", "")
IMAP_TIMEOUT = int(os.getenv("IMAP_TIMEOUT", "20"))
# How long to wait for a concurrency slot before giving up. Comfortably
# longer than a normal operation, short enough that a wedged mailbox
# surfaces as an error rather than a hung page.
IMAP_SLOT_WAIT = int(os.getenv("IMAP_SLOT_WAIT", "15"))

# Dovecot's special-use folders, mapped to the roles the webmail renders
# icons and ordering from. Spam and Junk both appear because different
# provisioning paths have created each.
ROLE_MAP = {
    "INBOX": "inbox",
    "Drafts": "drafts",
    "Sent": "sent",
    "Junk": "junk",
    "Spam": "junk",
    "Trash": "trash",
    "Archive": "archive",
}

# SPECIAL-USE (RFC 6154) attributes Dovecot returns in LIST, mapped to the
# same roles. Consulted BEFORE the name table: a mailbox migrated from another
# system carries the attribute on a folder called "Sent Messages" or "Deleted
# Items", and the attribute -- not the name -- is what Dovecot itself files
# into, so it is the truth about which folder plays which part.
_SPECIAL_USE_ROLES = {
    "\\drafts": "drafts",
    "\\sent": "sent",
    "\\junk": "junk",
    "\\trash": "trash",
    "\\archive": "archive",
}

# Every SPECIAL-USE attribute, including the ones with no role of their own.
# A folder carrying any of them is structural and is never renamed or deleted
# from here.
_SPECIAL_USE_ATTRIBUTES = set(_SPECIAL_USE_ROLES) | {"\\all", "\\flagged", "\\important"}

# Well-known names, lowercased, protected even when the server reports no
# attribute for them -- the ROLE_MAP names plus the ones dovecot.conf and the
# common desktop clients create. Compared against the WHOLE folder name, not
# its last segment, so a user's own "Projects/Archive" is still theirs to
# rename.
_WELL_KNOWN_SPECIAL_NAMES = {name.lower() for name in ROLE_MAP} | {
    "sent messages",
    "sent items",
    "deleted items",
    "deleted messages",
    "junk e-mail",
    "junk email",
    "archives",
    "all mail",
}

# IMAP LIST response: (\flags) delimiter name
#
# The delimiter is always quoted by Dovecot but a bare name like INBOX is
# NOT, so splitting on the literal `' "'` mis-parses every unquoted name --
# `(\HasNoChildren) "/" INBOX` yields `/" INBOX` rather than `INBOX`, which
# then breaks the STATUS call and every later SELECT for that mailbox. Found
# live during phase-11 webmail verification; this regex handles both forms.
_LIST_RESPONSE_RE = re.compile(r"^\(([^)]*)\)\s+(\S+)\s+(.+)$")


# Dovecot enforces mail_max_userip_connections (default 10) per user+IP pair.
# Every request here opens its own connection, and the api service is a single
# IP, so a mailbox with several requests in flight -- which is the normal
# webmail load, since opening a folder fires the folder list, the message list
# and the capability manifest together -- runs straight into it.
#
# Dovecot does not refuse the excess connections. It holds them, so the
# symptom is not a clear error but "The read operation timed out" on a
# fraction of requests, with nothing at all in the mail server's log.
# Measured: 12 concurrent requests against the default limit of 10 failed 9
# of 24 times.
#
# Staying under the server's limit is the client's job. This bounds our own
# concurrency per mailbox so requests queue for a slot -- each one holds it
# for well under a second -- instead of being started and then timing out.
_MAX_CONCURRENT_PER_MAILBOX = int(os.getenv("IMAP_MAX_CONCURRENT_PER_MAILBOX", "6"))
_slot_lock = threading.Lock()
_mailbox_slots: dict[str, threading.BoundedSemaphore] = {}


def _slot_for(email_address: str) -> threading.BoundedSemaphore:
    with _slot_lock:
        slot = _mailbox_slots.get(email_address)
        if slot is None:
            slot = threading.BoundedSemaphore(_MAX_CONCURRENT_PER_MAILBOX)
            _mailbox_slots[email_address] = slot
        return slot


class ImapUnavailableError(Exception):
    """Dovecot could not be reached or refused the master credential.

    Distinct from "the mailbox holder got something wrong": this is always a
    502, never a 4xx, because nothing the caller sends can fix it.
    """


class FolderProtectedError(ValueError):
    """INBOX or a special-use folder.

    Renaming or deleting one would break the mailbox for every client on it
    -- Dovecot files into these by attribute, and the webmail's delete button
    depends on Trash existing. Subclasses ValueError so a caller that only
    knows about the generic refusal still handles it.
    """


class FolderNotEmptyError(ValueError):
    """The folder still holds messages or subfolders.

    `reason` is "messages" or "children". Deliberately not deleted anyway:
    IMAP DELETE on a non-empty folder destroys its contents with no Trash
    step, which is the one thing PRD 01 section 7 forbids.
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _require_master() -> None:
    if not IMAP_MASTER_USER or not IMAP_MASTER_PASSWORD:
        raise ImapUnavailableError(
            "IMAP_MASTER_USER/IMAP_MASTER_PASSWORD are not configured; "
            "the mailbox surface cannot open any mailbox without them"
        )


def imap_connect(email_address: str) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP session for a mailbox, as the master user.

    Callers must close it -- use imap_session() unless there is a reason not
    to.
    """
    _require_master()
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
    except Exception as exc:
        logger.error("IMAP connection failed for %s: %s", email_address, exc)
        raise ImapUnavailableError("Mail server unavailable") from None

    try:
        conn.login(f"{email_address}*{IMAP_MASTER_USER}", IMAP_MASTER_PASSWORD)
    except Exception as exc:
        # A master login that fails is a server-side misconfiguration, not a
        # bad password -- the holder's password was never involved.
        logger.error("IMAP master login failed for %s: %s", email_address, exc)
        with contextlib.suppress(Exception):
            conn.logout()
        raise ImapUnavailableError("Mail server rejected the service credential") from None

    return conn


class imap_session:  # noqa: N801 -- context manager used as a lowercase verb
    """`with imap_session(addr) as conn:` -- closes even on error.

    imaplib raises from logout() when the connection is already broken, which
    would otherwise mask the real exception on the way out.
    """

    def __init__(self, email_address: str):
        self.email_address = email_address
        self.conn: imaplib.IMAP4_SSL | None = None
        self._slot: threading.BoundedSemaphore | None = None

    def __enter__(self) -> imaplib.IMAP4_SSL:
        # Take a slot BEFORE connecting, so we never open a connection Dovecot
        # will not serve. Bounded wait rather than forever: a caller blocked
        # here for a minute should get a clean 502, not hang the request.
        slot = _slot_for(self.email_address)
        if not slot.acquire(timeout=IMAP_SLOT_WAIT):
            raise ImapUnavailableError("Mailbox is busy, try again")
        self._slot = slot
        try:
            self.conn = imap_connect(self.email_address)
        except Exception:
            self._release()
            raise
        return self.conn

    def _release(self) -> None:
        if self._slot is not None:
            with contextlib.suppress(ValueError):
                self._slot.release()
            self._slot = None

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self.conn is not None:
                with contextlib.suppress(Exception):
                    self.conn.logout()
        finally:
            # Released only after logout, so the slot really does correspond
            # to a connection Dovecot has finished with.
            self._release()
        return False


def folder_id(mailbox_name: str) -> str:
    """Stable id for a folder.

    md5 of the name, matching worker/jmap so the same folder carries the same
    id whichever service answered. Not a security boundary -- it is a cache
    key the client round-trips, and the name is public to its own owner.
    """
    return hashlib.md5(mailbox_name.encode(), usedforsecurity=False).hexdigest()


def _decode(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _status_counts(conn: imaplib.IMAP4_SSL, mailbox_name: str) -> dict:
    """MESSAGES/UNSEEN/UIDNEXT/UIDVALIDITY for one folder.

    STATUS rather than SELECT + SEARCH UNSEEN: one command instead of two, it
    does not disturb the connection's selected mailbox, and it yields the UID
    tokens that make a cheap change signal possible -- a client can poll the
    folder list and know whether anything arrived without fetching a single
    message (PRD P2/P4).
    """
    counts = {"total": 0, "unread": 0, "uid_next": 0, "uid_validity": 0}
    try:
        st, data = conn.status(f'"{mailbox_name}"', "(MESSAGES UNSEEN UIDNEXT UIDVALIDITY)")
    except Exception as exc:
        logger.warning("STATUS failed for %s: %s", mailbox_name, exc)
        return counts

    if st != "OK" or not data:
        return counts

    text = _decode(data[0])
    for key, target in (
        ("MESSAGES", "total"),
        ("UNSEEN", "unread"),
        ("UIDNEXT", "uid_next"),
        ("UIDVALIDITY", "uid_validity"),
    ):
        found = re.search(rf"\b{key}\s+(\d+)", text)
        if found:
            counts[target] = int(found.group(1))
    return counts


def _list_entries(conn: imaplib.IMAP4_SSL) -> list[dict]:
    """Raw LIST rows as {flags, delimiter, name}, \\Noselect rows included.

    `flags` is a lowercased set (`\\sent`, `\\hasnochildren`, ...) and
    `delimiter` is the server's hierarchy character, or None when the server
    reports NIL (a flat namespace). list_folders and the rename/delete
    helpers all read from this one parser so a LIST quirk is fixed once.
    """
    try:
        status, data = conn.list()
    except Exception as exc:
        logger.error("IMAP LIST failed: %s", exc)
        raise ImapUnavailableError("Could not list folders") from None

    if status != "OK":
        return []

    entries = []
    for item in data or []:
        if item is None:
            continue
        # A name the server sends as a literal arrives from imaplib as a
        # (prefix, name) tuple; the prefix then ends in the `{size}` marker
        # where the name would be.
        if isinstance(item, tuple):
            text = re.sub(r"\{\d+\}\s*$", "", _decode(item[0])) + _decode(item[1])
        else:
            text = _decode(item)
        match = _LIST_RESPONSE_RE.match(text)
        if not match:
            continue

        name = match.group(3).strip().strip('"')
        if not name:
            continue
        delimiter = match.group(2).strip('"')
        entries.append(
            {
                "flags": {f.lower() for f in match.group(1).split()},
                "delimiter": None if delimiter.upper() == "NIL" else delimiter,
                "name": name,
            }
        )
    return entries


def _role_of(name: str, flags: set[str]) -> str | None:
    for flag in flags:
        role = _SPECIAL_USE_ROLES.get(flag)
        if role:
            return role
    return ROLE_MAP.get(name)


def is_protected_folder(name: str, flags: set[str] | None = None) -> bool:
    """Whether a folder is INBOX or special-use -- by attribute OR by name.

    Both checks, because each misses cases the other catches: Dovecot only
    reports the attribute for folders in its own special_use config, and a
    folder named "Sent" with no attribute is still the one every other client
    files sent mail into.
    """
    if name.upper() == "INBOX":
        return True
    if flags and any(flag in _SPECIAL_USE_ATTRIBUTES for flag in flags):
        return True
    return name.lower() in _WELL_KNOWN_SPECIAL_NAMES


def list_folders(conn: imaplib.IMAP4_SSL) -> list[dict]:
    """Every folder in the mailbox, with counts and IMAP change tokens.

    Returned in the wire shape /api/v1/mailbox/folders serves, so the route
    is a pass-through and there is only one place to look when a field is
    wrong.
    """
    folders = []
    for entry in _list_entries(conn):
        # \Noselect folders are hierarchy placeholders with no messages; the
        # webmail would render an entry that cannot be opened.
        if "\\noselect" in entry["flags"]:
            continue

        name = entry["name"]
        counts = _status_counts(conn, name)
        folders.append(
            {
                "id": folder_id(name),
                "name": name,
                "role": _role_of(name, entry["flags"]),
                "total": counts["total"],
                "unread": counts["unread"],
                "uid_next": counts["uid_next"],
                "uid_validity": counts["uid_validity"],
            }
        )

    # INBOX first, then the other special-use folders, then everything else
    # alphabetically -- the order a mail user expects, decided here so every
    # client does not have to re-derive it.
    role_order = {"inbox": 0, "drafts": 1, "sent": 2, "junk": 3, "trash": 4, "archive": 5}
    folders.sort(key=lambda f: (role_order.get(f["role"], 99), f["name"].lower()))
    return folders


def create_folder(conn: imaplib.IMAP4_SSL, name: str) -> None:
    """Create a folder. A webmail 'label' IS an IMAP folder (PRD S5)."""
    try:
        status, data = conn.create(f'"{name}"')
    except Exception as exc:
        logger.error("IMAP CREATE failed for %s: %s", name, exc)
        raise ImapUnavailableError("Could not create folder") from None

    if status != "OK":
        raise ValueError(_decode(data[0]) if data else "Could not create folder")

    # Subscribe as well: an unsubscribed folder is invisible to clients that
    # LSUB rather than LIST, which would make a folder created here appear in
    # the webmail and nowhere else.
    try:
        conn.subscribe(f'"{name}"')
    except Exception as exc:
        logger.warning("SUBSCRIBE failed for %s (folder still created): %s", name, exc)


def hierarchy_delimiter(conn: imaplib.IMAP4_SSL, entries: list[dict] | None = None) -> str:
    """The server's hierarchy separator.

    Read off the LIST rows already fetched when the caller has them, else
    asked for with `LIST "" ""`, which every server answers with the
    delimiter alone (RFC 3501 6.3.8). Falls back to "/" -- what dovecot.conf
    configures here -- rather than failing a rename over a lookup miss.
    """
    for entry in entries or []:
        if entry["delimiter"]:
            return entry["delimiter"]
    try:
        st, data = conn.list('""', '""')
        if st == "OK":
            for item in data or []:
                match = _LIST_RESPONSE_RE.match(_decode(item)) if item is not None else None
                if match:
                    delimiter = match.group(2).strip('"')
                    if delimiter and delimiter.upper() != "NIL":
                        return delimiter
    except Exception as exc:
        logger.debug("LIST for delimiter failed: %s", exc)
    return "/"


def _entry_named(entries: list[dict], name: str) -> dict | None:
    return next((e for e in entries if e["name"] == name), None)


def rename_folder(conn: imaplib.IMAP4_SSL, name: str, new_leaf: str) -> str:
    """Rename a folder in place and return its new full name.

    `new_leaf` is the last segment only. A nested folder keeps its parent:
    renaming "Clients/Acme" to "Acme Ltd" yields "Clients/Acme Ltd". RFC 3501
    RENAME moves any subfolders along with it.

    Refuses INBOX and special-use folders (FolderProtectedError): RENAME on
    INBOX has the unique semantics of moving its messages into a new folder
    and leaving INBOX empty, and renaming Sent or Trash orphans every client
    that files into them by name.
    """
    entries = _list_entries(conn)
    entry = _entry_named(entries, name)
    if entry is None:
        raise ValueError("Folder not found")
    if is_protected_folder(name, entry["flags"]):
        raise FolderProtectedError("INBOX and system folders cannot be renamed")

    delimiter = entry["delimiter"] or hierarchy_delimiter(conn, entries)
    new_leaf = new_leaf.strip()
    if not new_leaf:
        raise ValueError("Folder name is required")
    if delimiter in new_leaf:
        raise ValueError(f"Folder names cannot contain {delimiter!r}")

    parent, sep, _old_leaf = name.rpartition(delimiter)
    target = f"{parent}{delimiter}{new_leaf}" if sep else new_leaf
    if target == name:
        return name
    if _entry_named(entries, target) is not None:
        raise ValueError("A folder with that name already exists")
    if is_protected_folder(target):
        raise ValueError("That name is reserved for a system folder")

    try:
        st, data = conn.rename(_imap_quote(name), _imap_quote(target))
    except Exception as exc:
        logger.error("IMAP RENAME %s -> %s failed: %s", name, target, exc)
        raise ImapUnavailableError("Could not rename folder") from None
    if st != "OK":
        raise ValueError(_decode(data[0]) if data else "Could not rename folder")

    # RENAME does not carry the subscription across (RFC 3501 6.3.5 leaves it
    # to the client), and an unsubscribed folder disappears from every LSUB
    # client. Best effort on both: the rename itself has already happened.
    with contextlib.suppress(Exception):
        conn.unsubscribe(_imap_quote(name))
    with contextlib.suppress(Exception):
        conn.subscribe(_imap_quote(target))
    return target


def delete_folder(conn: imaplib.IMAP4_SSL, name: str) -> None:
    """Delete an EMPTY, non-special folder.

    Refuses when the folder still holds messages or subfolders
    (FolderNotEmptyError, reason "messages" / "children"). IMAP DELETE would
    destroy the messages outright, skipping Trash -- the caller is expected
    to have the holder empty the folder first, deliberately. Subfolders are
    refused rather than recursed for the same reason.
    """
    entries = _list_entries(conn)
    entry = _entry_named(entries, name)
    if entry is None:
        raise ValueError("Folder not found")
    if is_protected_folder(name, entry["flags"]):
        raise FolderProtectedError("INBOX and system folders cannot be deleted")

    delimiter = entry["delimiter"] or hierarchy_delimiter(conn, entries)
    prefix = name + delimiter
    if any(e["name"].startswith(prefix) for e in entries):
        raise FolderNotEmptyError("Delete or move its subfolders first", "children")

    # Strict count, not _status_counts: that helper answers 0 when STATUS
    # fails, and "STATUS failed" must not read as "empty" on the one path
    # that destroys a folder.
    try:
        st, data = conn.status(_imap_quote(name), "(MESSAGES)")
    except Exception as exc:
        logger.error("STATUS before DELETE failed for %s: %s", name, exc)
        raise ImapUnavailableError("Could not inspect folder") from None
    count = re.search(rb"MESSAGES\s+(\d+)", data[0] if data and isinstance(data[0], bytes) else b"")
    if st != "OK" or count is None:
        raise ImapUnavailableError("Could not inspect folder")
    if int(count.group(1)) > 0:
        raise FolderNotEmptyError("Move or delete its messages first", "messages")

    # Unsubscribe first: a subscription that outlives its folder is a
    # phantom entry in every LSUB client until someone cleans it by hand.
    with contextlib.suppress(Exception):
        conn.unsubscribe(_imap_quote(name))
    try:
        st, data = conn.delete(_imap_quote(name))
    except Exception as exc:
        logger.error("IMAP DELETE %s failed: %s", name, exc)
        raise ImapUnavailableError("Could not delete folder") from None
    if st != "OK":
        raise ValueError(_decode(data[0]) if data else "Could not delete folder")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

# A message id carries the folder that holds it. IMAP UIDs are only unique
# within a mailbox, so a bare UID is ambiguous the moment a client holds
# messages from two folders at once -- and resolving it would mean SELECTing
# every folder in turn. Same scheme worker/jmap uses.
EMAIL_ID_SEPARATOR = ":"

# Headers worth pulling for a list row. Deliberately not the whole header
# block: a mailing-list message can carry several KB of Received/DKIM lines
# that no list row displays.
_SUMMARY_HEADERS = "SUBJECT FROM TO CC BCC REPLY-TO DATE MESSAGE-ID IN-REPLY-TO REFERENCES"

# How much body text to pull for the preview line. Enough for the ~200
# characters a row shows even after quoted-printable expansion, small enough
# that a page of 50 costs one round trip and not a download.
_PREVIEW_BYTES = 2048


def _unfold(value) -> str | None:
    """A header value with its folding whitespace collapsed, or None if blank.

    The compat32 parser both shapers use returns a folded header VERBATIM,
    CRLF and tab included, so a References line Gmail wrapped came out as
    `<a@x>\\r\\n\\t<b@x>` -- a value no client could compare against anything.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _message_id_tokens(value: str | None) -> list[str]:
    """Every <...> token in a References / In-Reply-To header."""
    if not value:
        return []
    return re.findall(r"<[^>]+>", value)


def thread_root(
    message_id: str | None, in_reply_to: str | None, references: str | None
) -> str | None:
    """The identifier of the conversation this message belongs to.

    The root of the References chain: RFC 5322 lists References oldest
    first, so its first token is the message that started everything. A
    reply whose client sent only In-Reply-To roots at that; a message that
    is neither roots at itself. Deterministic from the message's own
    headers alone, so a list row and the detail view compute the same value
    without a second fetch, and every member of a well-formed thread shares
    it. thread_for() searches on the same identifiers.

    None only when the message carries no identifier at all -- then there
    is nothing to group on, and inventing a key would put it in a thread of
    one that changes with every fetch.
    """
    for value in (references, in_reply_to):
        tokens = _message_id_tokens(value)
        if tokens:
            return tokens[0]
    tokens = _message_id_tokens(message_id)
    if tokens:
        return tokens[0]
    return _unfold(message_id)


def _threading_fields(headers) -> dict:
    """message_id / in_reply_to / references / thread_id, one way for both shapers."""
    message_id = _unfold(headers.get("Message-ID"))
    in_reply_to = _unfold(headers.get("In-Reply-To"))
    references = _unfold(headers.get("References"))
    return {
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "thread_id": thread_root(message_id, in_reply_to, references),
    }


def compose_message_id(folder: str, uid: str | int) -> str:
    return f"{folder}{EMAIL_ID_SEPARATOR}{uid}"


def split_message_id(message_id: str) -> tuple[str | None, str]:
    """(folder, uid) for a scoped id; (None, raw) for a bare UID.

    Tolerates the bare form because ids issued before scoping existed may
    still be held by a client.
    """
    if EMAIL_ID_SEPARATOR in message_id:
        folder, _, uid = message_id.rpartition(EMAIL_ID_SEPARATOR)
        if uid.isdigit() and folder:
            return folder, uid
    return None, message_id


def _imap_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_uids(
    conn: imaplib.IMAP4_SSL,
    folder: str,
    *,
    query: str | None = None,
    unread_only: bool = False,
    starred_only: bool = False,
) -> list[str]:
    """Newest-first UIDs matching the filter, resolved by the server.

    IMAP SEARCH is answered from Dovecot's own indexes, including full-text
    body search, without transferring a single message. This is what makes
    webmail search real (PRD P5): searching client-side over a preview string
    cannot find a word that appears on line 40 of an email.
    """
    criteria: list[str] = []
    if query:
        # TEXT covers headers and body, which is what someone typing into a
        # single search box means.
        criteria += ["TEXT", _imap_quote(query)]
    if unread_only:
        criteria.append("UNSEEN")
    if starred_only:
        criteria.append("FLAGGED")
    if not criteria:
        criteria = ["ALL"]

    try:
        st, _ = conn.select(_imap_quote(folder), readonly=True)
        if st != "OK":
            return []
    except Exception as exc:
        logger.error("SELECT %s failed: %s", folder, exc)
        raise ImapUnavailableError("Could not open folder") from None

    # SORT (RFC 5256) gives a genuinely date-ordered result, which UID order
    # only approximates -- a message delivered late but dated earlier sorts
    # correctly here and wrongly under UID order. Dovecot supports SORT; the
    # SEARCH fallback exists for servers that do not.
    try:
        st, data = conn.uid("SORT", "(REVERSE DATE)", "UTF-8", *criteria)
        if st == "OK" and data and data[0] is not None:
            return [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
    except Exception as exc:
        logger.debug("SORT unavailable in %s (%s); falling back to SEARCH", folder, exc)

    try:
        st, data = conn.uid("SEARCH", None, *criteria)
    except Exception as exc:
        logger.error("SEARCH in %s failed: %s", folder, exc)
        raise ImapUnavailableError("Could not search folder") from None

    if st != "OK" or not data or data[0] is None:
        return []
    uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
    # Newest first, matching what SORT would have returned.
    return list(reversed(uids))


def _decode_header_value(raw: str | None) -> str:
    """RFC 2047 -> text. `=?UTF-8?B?...?=` is what a subject line really is."""
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
    except Exception:
        return " ".join(raw.split())
    out = []
    for value, charset in parts:
        if isinstance(value, bytes):
            try:
                out.append(value.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                # decode_header labels raw 8-bit header bytes "unknown-8bit",
                # which is not a codec Python has -- and senders also invent
                # charset names outright. Without this, ONE such subject line
                # raised LookupError out of every list or detail request that
                # touched the message.
                out.append(value.decode("utf-8", errors="replace"))
        else:
            out.append(value)
    # Unfolded on the way out. The compat32 parser hands a folded header
    # over VERBATIM, so a subject some client wrapped mid-line arrives as
    # `...Dedicated IP for\r\n mailyte.com...`. This value goes into JSON,
    # into filenames -- and, when the reader replies, straight back into
    # the reply's Subject, where an embedded CRLF is a header no policy
    # will store. One folded subject, one failed send.
    return " ".join("".join(out).split())


def _participants(header_value: str | None) -> list[dict]:
    """An address header as [{name, email}].

    getaddresses handles the group syntax and quoted display names that a
    naive split on "," mangles -- `"Doe, John" <j@x>` is one address, not two.
    """
    if not header_value:
        return []
    people = []
    for name, addr in getaddresses([header_value]):
        if not addr or "@" not in addr:
            continue
        people.append({"name": _decode_header_value(name) or None, "email": addr.strip()})
    return people


def _received_at(raw_date: str | None) -> str | None:
    """Date header -> ISO 8601, or None.

    None rather than a fabricated timestamp when the header is missing or
    unparseable: the webmail renders an absent date as absent, and inventing
    "now" would sort a decade-old message to the top of the list.
    """
    if not raw_date:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


_MIME_BOUNDARY_RE = re.compile(r"^--\S+\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Elements whose CONTENT is code, not prose. Stripping tags alone leaves the
# CSS or JS behind, which is how previews came to read
# "@media only screen { html { min-height: 100%; background: #fff }".
_HTML_CODE_BLOCK_RE = re.compile(
    r"<(style|script|head|title)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_LOOKS_LIKE_HTML_RE = re.compile(r"<\s*(html|body|table|div|p|br|a|img|span)\b", re.IGNORECASE)
# Quoted-printable hex escapes. A preview is a slice of an undecoded body, so
# "=3D" shows up literally -- as seen in `<html lang=3D"en">`.
_QP_HEX_RE = re.compile(r"=([0-9A-Fa-f]{2})")


def _text_from_html(fragment: str) -> str:
    """Readable text out of an HTML body.

    Order matters: code blocks must go before tags are stripped, or their
    contents survive as text.
    """
    text = _HTML_COMMENT_RE.sub(" ", fragment)
    text = _HTML_CODE_BLOCK_RE.sub(" ", text)
    # An unclosed <style> is routine here, because the preview is a 2 KB slice
    # that often ends mid-document. Its rules would otherwise leak into the
    # row. Stop at the next structural tag rather than running to the end:
    # consuming everything after it discarded the real text whenever the
    # closing tag was merely missing rather than genuinely truncated.
    text = re.sub(
        r"<(style|script)\b[^>]*>(?:(?!<\s*/?\s*(?:body|div|table|p|h1|h2)\b).)*",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = _HTML_TAG_RE.sub(" ", text)
    # A tag left open by the 2 KB cut.
    #
    # _HTML_TAG_RE needs a closing bracket, so a tag the slice ends inside
    # survives whole. That is routine rather than rare: one <img> with a
    # base64 data URI can be longer than the entire slice, which is how a row
    # came to read `<img style="display:block;" height=35
    # src="data:image/png;base64,iVBORw0KGgo...`.
    text = re.sub(r"<[^>]*$", " ", text)
    return unescape(text)


def _decode_qp_preview(text: str) -> str:
    """Undo quoted-printable escapes for display only.

    Decodes CONSECUTIVE escapes as one byte string, which is the whole point:
    a single character is often several bytes. Decoding each `=XX` on its own
    turned every multi-byte character into one replacement char per byte, so
    a tea emoji (=F0=9F=8E=89, four bytes) rendered as four black diamonds and
    a superscript two (=C2=B2) as two. That is what put garbage in the preview
    for any sender using an emoji or an accented letter.

    Deliberately lossy and non-strict: this runs on an arbitrary 2 KB slice
    that may end mid-sequence, so anything undecodable becomes U+FFFD rather
    than raising. Never use this to reconstruct a message.
    """
    text = text.replace("=\r\n", "").replace("=\n", "")

    def _run(match: re.Match) -> str:
        raw = bytes(int(pair, 16) for pair in re.findall(r"=([0-9A-Fa-f]{2})", match.group(0)))
        return raw.decode("utf-8", errors="replace")

    # One or more escapes in a row are one unit -- see above.
    return re.sub(r"(?:=[0-9A-Fa-f]{2})+", _run, text)


# Markup that is structure rather than prose, in a part that claims to be
# plain text. Two unrelated sources produce it and both landed in the row:
# senders whose "plain text" alternative is really Markdown, and the DSN
# templates postfix generates, which box their warnings inside a rule of "#".
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Anchored to line start so an order number like "Ref #1234" keeps its hash.
_MD_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
# A whole line of one repeated character: a Markdown rule, a setext underline
# ("Welcome" over "======="), or a DSN divider.
_MD_RULE_LINE_RE = re.compile(r"^[ \t]*([-=*_#])\1{2,}[ \t]*$", re.MULTILINE)
# The same run when it is not alone on its line, which is what survives once a
# hard-wrapped divider has been folded.
_DIVIDER_RUN_RE = re.compile(r"([-=*_#])\1{3,}")
# Paired emphasis only, so a lone asterisk or an identifier with underscores is
# left alone.
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
# Leading quote and list markers, which read as noise once lines are collapsed
# into one.
_MD_LINE_PREFIX_RE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?|[-*+][ \t]+)", re.MULTILINE)


def _strip_markdown_noise(text: str) -> str:
    """Drop Markdown decoration from a preview, keeping the words.

    Runs on plain-text parts, after quoted-printable is decoded and before
    lines are folded into one -- several of these are anchored to line starts
    and would stop matching once the newlines are gone.

    Link syntax keeps its label and discards the target: `[ View in browser
    ](https://5qu8o.r.ag.d.sendibm3.com/mk/mr/sh/7nVTPd...)` spent the entire
    preview on a tracking URL nobody can read.
    """
    text = _MD_IMAGE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_RULE_LINE_RE.sub(" ", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_LINE_PREFIX_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub(r"\2", text)
    text = _DIVIDER_RUN_RE.sub(" ", text)
    return text


def _first_text_part(body: str) -> str:
    """The readable text out of a raw MIME body.

    BODY[TEXT] returns the message body verbatim, which for a multipart
    message is boundaries and per-part headers -- not prose. Previews for
    those rendered as
    `--===============064...=Content-Type: text/plain; charset="utf-8"...`,
    which is every message this server sends, since build_message() always
    produces multipart/alternative.

    Walks the parts and returns the first text/plain one, falling back to
    text/html with tags stripped. Base64 parts are skipped: decoding a slice
    of a partial fetch yields either an exception or garbage, and a preview is
    not worth either.
    """
    if not _MIME_BOUNDARY_RE.search(body):
        # Single-part, but not necessarily prose: a text/html-only message has
        # no boundary either, and returning it verbatim put raw markup in the
        # row -- `<html lang=3D"en"> <head> <title>Namecheap</title> ...`.
        return _text_from_html(body) if _LOOKS_LIKE_HTML_RE.search(body) else body

    html_fallback = ""
    for chunk in _MIME_BOUNDARY_RE.split(body)[1:]:
        head, sep, content = chunk.partition("\n\n")
        if not sep:
            head, sep, content = chunk.partition("\r\n\r\n")
        if not sep:
            continue
        headers = head.lower()
        if "base64" in headers:
            continue
        # `and content.strip()` matters: a message composed as HTML still
        # carries a text/plain part, and it is often empty. Returning it
        # unconditionally gave those messages a blank preview -- correct by
        # the letter of "prefer text/plain", useless to read.
        if "text/plain" in headers and content.strip():
            # Some senders label HTML as text/plain. Cheap to check, and the
            # alternative is markup in the row.
            return _text_from_html(content) if _LOOKS_LIKE_HTML_RE.search(content) else content
        if "text/html" in headers and not html_fallback:
            html_fallback = _text_from_html(content)
        # A part with no Content-Type defaults to text/plain per RFC 2045.
        if "content-type:" not in headers and content.strip():
            return content

    return html_fallback


def _preview_from(text: bytes | None) -> str | None:
    """A one-line preview from the first slice of the body.

    Returns None, never "", when there is nothing to show -- the webmail
    renders an absent preview as no preview rather than as invented text.
    """
    if not text:
        return None
    body = text.decode("utf-8", errors="replace")

    # Pick the text part BEFORE unfolding quoted-printable, not after.
    #
    # Python's email library generates boundaries that END in "=", e.g.
    # "--===============0646090633780080066=". Stripping "=\n" first therefore
    # welds the boundary onto the header line below it, destroying the very
    # line the part-splitter looks for -- which is how a preview came to read
    # `--===============064...=Content-Type: text/plain; charset="utf-8"`.
    body = _first_text_part(body)

    # Quoted-printable survives a partial fetch undecoded: soft line breaks as
    # a literal "=\n", and every escaped byte as "=3D". Both showed up in the
    # row. Safe here: this is one part's content, with no boundaries left in
    # it, so decoding cannot damage a delimiter.
    body = _decode_qp_preview(body)

    # Before the fold, not after: the heading, rule and list patterns are
    # anchored to line starts, which stop existing once lines are joined.
    body = _strip_markdown_noise(body)

    collapsed = " ".join(body.split())
    return collapsed[:200] or None


_HAS_ATTACHMENT_RE = re.compile(rb'"(?:attachment|inline)"', re.IGNORECASE)


def _has_attachment(bodystructure: bytes | None) -> bool:
    """Whether BODYSTRUCTURE mentions a non-body part.

    Read off the structure rather than by downloading the message: the whole
    point of BODYSTRUCTURE is that the server describes the shape for free.
    Inline images count -- a message whose only 'attachment' is a signature
    logo shows the paperclip in every other mail client too.
    """
    if not bodystructure:
        return False
    return bool(_HAS_ATTACHMENT_RE.search(bodystructure))


# The metadata prefix imaplib puts before each literal. Used to tell where one
# message's FETCH response ends and the next begins.
_FETCH_UID_RE = re.compile(rb"UID\s+(\d+)")
_FETCH_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")
_FETCH_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")
# Which literal a payload belongs to has to be read off the section spec
# immediately before its {size} marker.
#
# Two traps here, both of which produce silently-empty fields rather than an
# error:
#
# 1. Searching the whole prefix for "RFC822" files an RFC822 fetch under the
#    wrong key, because the prefix also contains "RFC822.SIZE".
# 2. The spec is NOT a single whitespace-free token. `BODY[HEADER.FIELDS
#    (SUBJECT FROM TO ...)]` contains spaces, so a `(\S+)` match captures only
#    the trailing `REFERENCES)]` fragment, which matches no known section --
#    every header literal is then dropped and every message comes back with an
#    empty subject, no sender and no date. Match the whole bracketed spec.
_SECTION_MARKER_RE = re.compile(rb"(BODY\[[^\]]*\](?:<[^>]*>)?|RFC822(?:\.[A-Z]+)?)\s*\{\d+\}\s*$")


def _flags_of(meta: bytes) -> set[str]:
    match = _FETCH_FLAGS_RE.search(meta)
    if not match:
        return set()
    return {f.decode(errors="replace").lower() for f in match.group(1).split()}


def fetch_summaries(conn: imaplib.IMAP4_SSL, folder: str, uids: list[str]) -> list[dict]:
    """Build list rows for these UIDs, in the order given.

    One UID FETCH for the whole page, asking for flags, size, structure, the
    handful of headers a row displays, and the first slice of body text for
    the preview -- everything a row needs in a single round trip, and no whole
    message downloaded.

    BODY.PEEK, not BODY: fetching a preview must not mark mail as read. Using
    BODY here would mean opening a folder silently marked every message on the
    page as seen.
    """
    if not uids:
        return []

    try:
        st, _ = conn.select(_imap_quote(folder), readonly=True)
        if st != "OK":
            return []
        st, data = conn.uid(
            "FETCH",
            ",".join(uids),
            f"(UID FLAGS RFC822.SIZE BODYSTRUCTURE "
            f"BODY.PEEK[HEADER.FIELDS ({_SUMMARY_HEADERS})] "
            f"BODY.PEEK[TEXT]<0.{_PREVIEW_BYTES}>)",
        )
    except Exception as exc:
        logger.error("FETCH in %s failed: %s", folder, exc)
        raise ImapUnavailableError("Could not read messages") from None

    if st != "OK" or not data:
        return []

    # Regroup imaplib's flat response. A FETCH asking for several literals
    # comes back as one tuple per literal plus a lone b')' terminator per
    # message, with UID/FLAGS/SIZE only on the first tuple's prefix -- walking
    # it blindly pairs the wrong body with the wrong message.
    grouped: dict[str, dict] = {}
    current_uid: str | None = None

    for item in data:
        if not isinstance(item, tuple):
            continue
        prefix, payload = item[0], item[1]
        if not isinstance(prefix, bytes):
            prefix = str(prefix).encode()

        uid_match = _FETCH_UID_RE.search(prefix)
        if uid_match:
            current_uid = uid_match.group(1).decode()
            entry = grouped.setdefault(current_uid, {"meta": b"", "header": None, "text": None})
            # Metadata can arrive on any of the literals for this message;
            # keep the longest prefix seen, which is the one carrying FLAGS
            # and RFC822.SIZE.
            if len(prefix) > len(entry["meta"]):
                entry["meta"] = prefix
        elif current_uid is None:
            continue
        else:
            entry = grouped[current_uid]

        marker = _SECTION_MARKER_RE.search(prefix)
        token = marker.group(1) if marker else b""
        if token.startswith(b"BODY[HEADER.FIELDS") or b"HEADER.FIELDS" in token:
            entry["header"] = payload
        elif token.startswith(b"BODY[TEXT]"):
            entry["text"] = payload

    parser = BytesParser()
    summaries = []
    for uid in uids:
        entry = grouped.get(uid)
        if not entry:
            continue
        headers = parser.parsebytes(entry["header"] or b"", headersonly=True)
        flags = _flags_of(entry["meta"])
        size_match = _FETCH_SIZE_RE.search(entry["meta"])

        summaries.append(
            {
                "id": compose_message_id(folder, uid),
                "folder": folder,
                "subject": _decode_header_value(headers.get("Subject")),
                "from": _participants(headers.get("From")),
                "to": _participants(headers.get("To")),
                "cc": _participants(headers.get("Cc")),
                "bcc": _participants(headers.get("Bcc")),
                "reply_to": _participants(headers.get("Reply-To")),
                "received_at": _received_at(headers.get("Date")),
                "size": int(size_match.group(1)) if size_match else 0,
                "has_attachment": _has_attachment(entry["meta"]),
                "is_read": "\\seen" in flags,
                "is_starred": "\\flagged" in flags,
                "is_answered": "\\answered" in flags,
                "is_draft": "\\draft" in flags,
                "preview": _preview_from(entry["text"]),
                # Threading identifiers, unfolded, plus the thread root they
                # imply -- the same derivation parse_message uses, so a row
                # and its detail never disagree about which thread it is in.
                **_threading_fields(headers),
            }
        )
    return summaries


# ---- Provenance: who really sent it, and how it got here -------------------
#
# Read from the headers the receiving side stamped (Return-Path, Received,
# Authentication-Results, DKIM-Signature), never from anything the sender
# could have written for display. Detail view only: every one of these means
# reading the whole header block, which a list row deliberately does not.

# Authentication-Results method verdicts we report (RFC 8601 section 2.7).
# Anything else a resolver emits ("policy", a vendor extension) becomes null
# rather than a value the client has no rendering for.
_AUTH_VERDICTS = {"pass", "fail", "softfail", "neutral", "none", "temperror", "permerror"}
# `spf=pass`, `dkim=fail`, ... -- the lookbehind stops `smtp.mailfrom=` style
# property names and `header.d=` from being read as a method.
_AUTH_RESULT_RE = re.compile(r"(?<![\w.-])(spf|dkim|dmarc)\s*=\s*([A-Za-z]+)", re.IGNORECASE)
# The signing domain of a DKIM result that passed: `dkim=pass header.d=x.com`
# (`header.i=@x.com` on older resolvers).
_DKIM_PASS_DOMAIN_RE = re.compile(
    r"dkim\s*=\s*pass[^;]*?header\.[di]\s*=\s*@?([A-Za-z0-9.-]+)", re.IGNORECASE
)
# The d= tag of a DKIM-Signature header. Anchored to a tag boundary so the
# `d` in `bh=`/`hd=`-style tags cannot match.
_DKIM_D_TAG_RE = re.compile(r"(?:^|;)\s*d\s*=\s*([^;\s]+)", re.IGNORECASE)
# A Received hop that was encrypted on the wire: Postfix writes `with ESMTPS`
# (or ESMTPSA when authenticated too) and, verbosely, `(using TLSv1.3 ...)`.
_RECEIVED_TLS_RE = re.compile(
    r"\bwith\s+(?:E|UTF8)?SMTPSA?\b|\busing\s+TLS|\bTLSv?1(?:\.\d)?\b|\(version=TLS",
    re.IGNORECASE,
)
# Hops that say nothing about the outside world: Dovecot's own LMTP delivery
# and Postfix's loopback re-injection after the tracking filter. Both sit
# ABOVE the real receiving hop, since Received headers are prepended.
_RECEIVED_INTERNAL_RE = re.compile(
    r"\bwith\s+LMTPS?\b|\bwith\s+local\b|\[127\.0\.0\.1\]|\[::1\]|\(localhost\b",
    re.IGNORECASE,
)


def _return_path_domain(message) -> str | None:
    value = _unfold(message.get("Return-Path"))
    if not value:
        return None
    _name, address = parseaddr(value)
    if "@" not in address:
        # `<>` -- the null sender a bounce carries. There is no domain.
        return None
    return address.rsplit("@", 1)[1].lower() or None


def _dkim_signer(message, auth_results: list[str]) -> str | None:
    signers = []
    for signature in message.get_all("DKIM-Signature") or []:
        match = _DKIM_D_TAG_RE.search(_unfold(signature) or "")
        if match:
            signers.append(match.group(1).lower())
    if not signers:
        return None
    passed = {
        domain.lower()
        for results in auth_results
        for domain in _DKIM_PASS_DOMAIN_RE.findall(results)
    }
    # Prefer the signature the receiving side verified; a message signed by
    # both a sending service and the author's own domain is "signed by" the
    # one that actually checked out.
    return next((d for d in signers if d in passed), signers[0])


def _transport_security(message) -> str | None:
    hops = [_unfold(h) or "" for h in (message.get_all("Received") or [])]
    if not hops:
        # A message with no Received line was never received -- a draft, or
        # the copy this server filed in Sent. Nothing to report.
        return None
    external = [h for h in hops if not _RECEIVED_INTERNAL_RE.search(h)]
    hop = external[0] if external else hops[0]
    return "tls" if _RECEIVED_TLS_RE.search(hop) else "none"


def _list_unsubscribe(message) -> dict | None:
    value = _unfold(message.get("List-Unsubscribe"))
    if not value:
        return None
    targets = re.findall(r"<([^>]+)>", value) or [v.strip() for v in value.split(",")]
    mailto = next((t for t in targets if t.lower().startswith("mailto:")), None)
    url = next((t for t in targets if t.lower().startswith(("https://", "http://"))), None)
    post = (_unfold(message.get("List-Unsubscribe-Post")) or "").lower()
    return {
        "mailto": mailto,
        "url": url,
        # RFC 8058: one-click needs BOTH the POST header and an https target.
        "one_click": bool(url and url.lower().startswith("https://"))
        and "list-unsubscribe=one-click" in post.replace(" ", ""),
    }


def _authentication(auth_results: list[str]) -> dict:
    verdicts: dict[str, str | None] = {"spf": None, "dkim": None, "dmarc": None}
    seen: set[str] = set()
    # Headers are in prepend order, so the first is the one THIS server's
    # resolver wrote at ingress; a forwarder's older verdicts come after and
    # only fill in methods ours did not report.
    for results in auth_results:
        for method, verdict in _AUTH_RESULT_RE.findall(results):
            method = method.lower()
            if method in seen:
                continue
            seen.add(method)
            verdict = verdict.lower()
            verdicts[method] = verdict if verdict in _AUTH_VERDICTS else None
    return verdicts


def message_provenance(message) -> dict:
    """mailed_by / signed_by / security / list_unsubscribe / authentication.

    Every key is always present and null when the message does not carry
    the header it comes from. The webmail and the mobile app render the
    "mailed by / signed by / security" line from this, the way Gmail's
    message details do, and offer one-click unsubscribe when the sender
    supports it.
    """
    auth_results = [_unfold(h) or "" for h in (message.get_all("Authentication-Results") or [])]
    return {
        "mailed_by": _return_path_domain(message),
        "signed_by": _dkim_signer(message, auth_results),
        "security": _transport_security(message),
        "list_unsubscribe": _list_unsubscribe(message),
        "authentication": _authentication(auth_results),
    }


def _part_filename(part) -> str | None:
    """Attachment filename, RFC 2231 / RFC 2047 decoded.

    get_filename() already handles the RFC 2231 continuation form
    (`name*0*=utf-8''...`); what it does not do is decode an RFC 2047 encoded
    word, which is what most clients actually send for a non-ASCII name.
    """
    raw = part.get_filename()
    if not raw:
        return None
    return _decode_header_value(raw) or None


def _is_attachment(part) -> bool:
    """Whether a MIME part is an attachment rather than the message body.

    Content-Disposition is authoritative when present. When it is absent, a
    part still counts as an attachment if it carries a filename -- plenty of
    senders omit the disposition header entirely.
    """
    disposition = (part.get_content_disposition() or "").lower()
    if disposition in ("attachment", "inline"):
        return True
    return bool(part.get_filename())


def parse_message(raw: bytes, folder: str, uid: str, flags: set[str]) -> dict:
    """Full message -> the detail shape the webmail renders.

    Body selection follows what a mail client does, not what the MIME tree
    literally says: the LAST text/plain and text/html body parts win, because
    multipart/alternative orders parts least-rich first and the richest
    representation is the one a reader should see.
    """
    message = BytesParser().parsebytes(raw)

    body_text: str | None = None
    body_html: str | None = None
    attachments: list[dict] = []

    for index, part in enumerate(message.walk()):
        if part.get_content_maintype() == "multipart":
            continue

        if _is_attachment(part):
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {
                    # Position in walk() order. The download route re-walks the
                    # same message and counts the same way, so this index is
                    # only meaningful together with the message id -- which is
                    # exactly how the webmail uses it.
                    "index": index,
                    "name": _part_filename(part) or f"attachment-{index}",
                    "type": part.get_content_type(),
                    "size": len(payload),
                    "is_inline": (part.get_content_disposition() or "").lower() == "inline",
                    "content_id": (part.get("Content-ID") or "").strip("<>") or None,
                }
            )
            continue

        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            # An unknown or misspelled charset label is common in real mail;
            # utf-8 with replacement shows something readable rather than
            # failing the whole message.
            text = payload.decode("utf-8", errors="replace")

        if content_type == "text/plain":
            body_text = text
        else:
            body_html = text

    return {
        "id": compose_message_id(folder, uid),
        "folder": folder,
        "subject": _decode_header_value(message.get("Subject")),
        "from": _participants(message.get("From")),
        "to": _participants(message.get("To")),
        "cc": _participants(message.get("Cc")),
        "bcc": _participants(message.get("Bcc")),
        "reply_to": _participants(message.get("Reply-To")),
        "received_at": _received_at(message.get("Date")),
        "size": len(raw),
        "has_attachment": bool(attachments),
        "is_read": "\\seen" in flags,
        "is_starred": "\\flagged" in flags,
        "is_answered": "\\answered" in flags,
        "is_draft": "\\draft" in flags,
        "preview": _preview_from((body_text or "").encode() if body_text else None),
        **_threading_fields(message),
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
        # Detail only. These read the whole header block, which the list
        # shaper deliberately never fetches.
        **message_provenance(message),
    }


def fetch_raw_message(
    conn: imaplib.IMAP4_SSL, folder: str, uid: str
) -> tuple[bytes, set[str]] | None:
    """The whole message plus its flags, or None if the UID is gone.

    BODY.PEEK[], not BODY[]: reading a message must not be what marks it read.
    The webmail decides that explicitly via /mark-read, which is what lets
    "mark as unread" mean anything at all.
    """
    try:
        st, _ = conn.select(_imap_quote(folder), readonly=True)
        if st != "OK":
            return None
        st, data = conn.uid("FETCH", str(uid), "(UID FLAGS BODY.PEEK[])")
    except Exception as exc:
        logger.error("FETCH %s in %s failed: %s", uid, folder, exc)
        raise ImapUnavailableError("Could not read message") from None

    if st != "OK" or not data:
        return None

    raw = b""
    meta = b""
    for item in data:
        if isinstance(item, tuple):
            prefix, payload = item[0], item[1]
            if isinstance(prefix, bytes) and len(prefix) > len(meta):
                meta = prefix
            if payload:
                raw = payload
        elif isinstance(item, bytes) and len(item) > len(meta):
            meta = item

    if not raw:
        return None
    return raw, _flags_of(meta)


def raw_download_name(raw: bytes, uid: str | int) -> str:
    """A safe `.eml` filename for a raw download.

    The subject reduced to ASCII `[A-Za-z0-9._-]` and capped, plus the UID so
    two messages with one subject do not collide on disk. Nothing from the
    message reaches the header unsanitised: a subject is sender-controlled.
    """
    head = BytesParser().parsebytes(raw, headersonly=True)
    subject = _decode_header_value(head.get("Subject"))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", subject).strip("-.")[:60] or "message"
    return f"{stem}-{uid}.eml"


# Webmail-facing flag names mapped to IMAP system flags. Only these four are
# settable -- an arbitrary keyword from a client would be stored by Dovecot
# and rendered by nothing.
_SETTABLE_FLAGS = {
    "read": "\\Seen",
    "starred": "\\Flagged",
    "answered": "\\Answered",
    "draft": "\\Draft",
}


def set_flag(conn: imaplib.IMAP4_SSL, folder: str, uid: str, flag: str, value: bool) -> None:
    """Add or remove one system flag on one message."""
    imap_flag = _SETTABLE_FLAGS.get(flag)
    if imap_flag is None:
        raise ValueError(f"Unknown flag: {flag}")

    try:
        st, _ = conn.select(_imap_quote(folder))
        if st != "OK":
            raise ValueError("Folder not found")
        st, _ = conn.uid("STORE", str(uid), "+FLAGS" if value else "-FLAGS", f"({imap_flag})")
    except ValueError:
        raise
    except Exception as exc:
        logger.error("STORE %s on %s failed: %s", flag, uid, exc)
        raise ImapUnavailableError("Could not update the message") from None

    if st != "OK":
        raise ValueError("Could not update the message")


def mark_answered(
    conn: imaplib.IMAP4_SSL,
    message_id: str,
    folders: list[str],
    hint: tuple[str, str] | None = None,
) -> str | None:
    """Set \\Answered on the message a reply was to. Returns its id, or None.

    Only the server can do this: a client sends the reply through this API
    and never touches the original, so unless the send path flags it the
    mailbox never records anything as answered and is_answered is false
    forever -- in every other client too, since the flag is what they all
    read.

    `hint` is the (folder, uid) the client says it replied to. It is checked
    against the Message-ID before being trusted, so a stale or wrong hint
    falls back to the search rather than flagging the wrong message. The
    search is a HEADER Message-ID lookup folder by folder, first hit wins.

    Best effort by contract: the reply has already left, so nothing here may
    fail the request. Errors are logged at debug and answered with None.
    """
    token = (message_id or "").strip()
    if not token:
        return None
    if not token.startswith("<") and " " not in token:
        token = f"<{token}>"
    quoted = _imap_quote(token)

    def _flag(folder: str, uid: str) -> str | None:
        st, _ = conn.uid("STORE", uid, "+FLAGS", "(\\Answered)")
        return compose_message_id(folder, uid) if st == "OK" else None

    if hint:
        folder, uid = hint
        try:
            st, _ = conn.select(_imap_quote(folder))
            if st == "OK":
                st, data = conn.uid("SEARCH", None, "UID", uid, "HEADER", "Message-ID", quoted)
                if st == "OK" and data and data[0] and uid.encode() in data[0].split():
                    return _flag(folder, uid)
        except Exception as exc:
            logger.debug("Answered hint %s:%s not usable: %s", folder, uid, exc)

    for folder in folders:
        try:
            st, _ = conn.select(_imap_quote(folder))
            if st != "OK":
                continue
            st, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", quoted)
            if st != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()
            if uids:
                return _flag(folder, uids[0].decode())
        except Exception as exc:
            logger.debug("Answered search in %s failed: %s", folder, exc)
    return None


def move_message(conn: imaplib.IMAP4_SSL, folder: str, uid: str, target: str) -> str:
    """Move a message and return its new id.

    Prefers RFC 6851 UID MOVE, which is atomic. The COPY + \\Deleted + EXPUNGE
    fallback is the pre-6851 idiom and is not: an interruption between the
    steps leaves the message in both folders, or deleted from neither. Dovecot
    supports MOVE, so the fallback should not normally run.
    """
    try:
        st, _ = conn.select(_imap_quote(folder))
        if st != "OK":
            raise ValueError("Folder not found")

        try:
            st, data = conn.uid("MOVE", str(uid), _imap_quote(target))
            if st == "OK":
                return compose_message_id(target, uid)
        except Exception as exc:
            logger.debug("UID MOVE unavailable (%s); falling back to COPY", exc)

        st, _ = conn.uid("COPY", str(uid), _imap_quote(target))
        if st != "OK":
            raise ValueError("Could not move the message")
        conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        conn.expunge()
        return compose_message_id(target, uid)
    except ValueError:
        raise
    except Exception as exc:
        logger.error("MOVE %s -> %s failed: %s", uid, target, exc)
        raise ImapUnavailableError("Could not move the message") from None


def expunge_message(conn: imaplib.IMAP4_SSL, folder: str, uid: str) -> None:
    """Permanently destroy one message.

    The caller MUST have established that the message is already in Trash.
    PRD 01 section 7 is binding: nothing outside Trash is ever destroyed, so
    the webmail's delete button moves to Trash and only a second delete from
    inside Trash reaches this function.
    """
    try:
        st, _ = conn.select(_imap_quote(folder))
        if st != "OK":
            raise ValueError("Folder not found")
        conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        conn.expunge()
    except ValueError:
        raise
    except Exception as exc:
        logger.error("EXPUNGE %s in %s failed: %s", uid, folder, exc)
        raise ImapUnavailableError("Could not delete the message") from None


def append_message(
    conn: imaplib.IMAP4_SSL, folder: str, raw: bytes, flags: str = "(\\Draft \\Seen)"
) -> str | None:
    """Append a message and return its new UID if the server reports one.

    Dovecot answers APPEND with UIDPLUS (RFC 4315) `[APPENDUID <validity>
    <uid>]`, which is the only way to learn the id without a follow-up SEARCH
    that could match the wrong message when two drafts are saved in the same
    second.
    """
    try:
        st, data = conn.append(_imap_quote(folder), flags, None, raw)
    except Exception as exc:
        logger.error("APPEND to %s failed: %s", folder, exc)
        raise ImapUnavailableError("Could not save the message") from None

    if st != "OK":
        raise ValueError("Could not save the message")

    for item in data or []:
        match = re.search(rb"APPENDUID\s+\d+\s+(\d+)", item if isinstance(item, bytes) else b"")
        if match:
            return match.group(1).decode()
    return None


def extract_attachment(raw: bytes, index: int) -> dict | None:
    """One attachment part's decoded bytes, by its walk() index.

    Re-walks the message and counts identically to parse_message, so an index
    handed out there resolves to the same part here. Returns None rather than
    raising when the index is not an attachment -- a stale index from a
    reloaded client is a 404, not a server error.
    """
    message = BytesParser().parsebytes(raw)
    for position, part in enumerate(message.walk()):
        if position != index:
            continue
        if part.get_content_maintype() == "multipart" or not _is_attachment(part):
            return None
        return {
            "name": _part_filename(part) or f"attachment-{index}",
            "type": part.get_content_type(),
            "content": part.get_payload(decode=True) or b"",
            # So a forwarded inline image can be re-attached under the same
            # Content-ID the forwarded HTML still references.
            "content_id": (part.get("Content-ID") or "").strip().strip("<>") or None,
            "is_inline": (part.get_content_disposition() or "").lower() == "inline",
        }
    return None


def thread_for(conn: imaplib.IMAP4_SSL, folder: str, uid: str) -> list[dict]:
    """The conversation this message belongs to, oldest first.

    Threading follows RFC 5322 identifiers -- Message-ID, In-Reply-To and
    References -- not subject lines. Subject matching is what makes unrelated
    "Re: hello" messages collapse into one conversation, and it breaks the
    moment someone renames a reply.

    Scoped to the message's own folder plus Sent, because a conversation a
    mailbox holder took part in lives in exactly those two places; searching
    every folder would multiply the cost for replies that are not there.
    """
    fetched = fetch_raw_message(conn, folder, uid)
    if fetched is None:
        return []

    head = BytesParser().parsebytes(fetched[0], headersonly=True)
    own_id = (head.get("Message-ID") or "").strip()
    # The full identifier set for this conversation: everything the message
    # references, plus itself.
    tokens = set(_message_id_tokens(head.get("References")))
    tokens |= set(_message_id_tokens(head.get("In-Reply-To")))
    if own_id:
        tokens.add(own_id)
    if not tokens:
        return []

    seen: dict[str, dict] = {}
    for search_folder in dict.fromkeys([folder, "Sent"]):
        for token in tokens:
            for criteria in (
                ["HEADER", "Message-ID", token],
                ["HEADER", "References", token],
                ["HEADER", "In-Reply-To", token],
            ):
                try:
                    st, _ = conn.select(_imap_quote(search_folder), readonly=True)
                    if st != "OK":
                        break
                    st, data = conn.uid("SEARCH", None, *criteria)
                except Exception as exc:
                    logger.debug("Thread search failed in %s: %s", search_folder, exc)
                    continue
                if st != "OK" or not data or data[0] is None:
                    continue
                found = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
                for summary in fetch_summaries(conn, search_folder, found):
                    seen[summary["id"]] = summary

    # Oldest first: a conversation reads top to bottom.
    return sorted(seen.values(), key=lambda m: m["received_at"] or "")


def harvest_contacts(
    conn: imaplib.IMAP4_SSL, folders: list[str], scan_limit: int = 300
) -> list[dict]:
    """Addresses this mailbox actually corresponds with, most-used first.

    Harvested from message headers rather than kept in an address book,
    because an address book nobody fills in stays empty while the mailbox
    already knows who its owner writes to.

    Recipients are taken from Sent and senders from everything else -- the
    people worth autocompleting are those written TO, and those who wrote in.
    """
    counts: dict[str, dict] = {}

    for folder in folders:
        try:
            st, _ = conn.select(_imap_quote(folder), readonly=True)
            if st != "OK":
                continue
            st, data = conn.uid("SEARCH", None, "ALL")
            if st != "OK" or not data or data[0] is None:
                continue
        except Exception as exc:
            logger.debug("Contact harvest skipped %s: %s", folder, exc)
            continue

        uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
        # Newest first, capped: recent correspondents are the useful ones and
        # scanning a 40,000-message archive to autocomplete a name is not.
        for summary in fetch_summaries(conn, folder, list(reversed(uids))[:scan_limit]):
            sources = summary["to"] + summary["cc"] if folder == "Sent" else summary["from"]
            for person in sources:
                key = person["email"].lower()
                entry = counts.setdefault(
                    key, {"name": person["name"], "email": person["email"], "count": 0}
                )
                entry["count"] += 1
                # Prefer a real display name over a bare address if any
                # message ever supplied one.
                if not entry["name"] and person["name"]:
                    entry["name"] = person["name"]

    return sorted(counts.values(), key=lambda c: (-c["count"], c["email"]))


# ---------------------------------------------------------------------------
# Composition and submission (PRD Phase 3)
# ---------------------------------------------------------------------------

# Where outbound mail is submitted.
#
# MAIL_SUBMIT_* rather than SMTP_*: the api service already sets SMTP_HOST /
# SMTP_PORT for ALERT delivery, and compose keeps the LAST value when a key
# repeats -- so writing submission settings into SMTP_PORT silently lost to
# the alert one (and, the other way round, would have redirected alerts).
# Two different jobs, two different variables.
#
# The default port is Postfix's internal submission listener (master.cf 10587),
# which carries the tracking content_filter. Port 25 deliberately does not:
# it also receives all inbound mail, and tracking must never rewrite messages
# arriving for our users.
SMTP_HOST = os.getenv("MAIL_SUBMIT_HOST") or os.getenv("SMTP_HOST", "postfix")
SMTP_PORT = int(os.getenv("MAIL_SUBMIT_PORT") or os.getenv("SMTP_PORT", "25"))
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "30"))

# Matches the limits the Laravel route enforced, so a message that was
# accepted before is still accepted. Both are below Postfix's own
# message_size_limit deliberately: refusing a 30MB attachment before it is
# base64-expanded gives a real message instead of a mail-server rejection
# after the upload has already happened.
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024


def message_bytes(message: EmailMessage) -> bytes:
    """Serialise with CRLF line endings.

    EmailMessage.as_bytes() emits bare LF, which both protocols this feeds
    reject or mangle: Postfix answers `5.5.2 bare <LF> received` and refuses
    the message outright, and RFC 3501 requires CRLF for IMAP APPEND too. One
    helper for both so a message cannot be built correctly for one path and
    wrongly for the other.
    """
    return message.as_bytes(policy=SMTP_POLICY)


# An <img> whose src is a base64 raster image -- the shape the webmail's editor
# produces for an inserted or pasted picture, and the one shape of data: URI
# the signature sanitiser lets through. Group 1 is everything up to the
# opening quote of the value, so the rewrite keeps every other attribute.
_DATA_IMAGE_SRC_RE = re.compile(
    r"(<img\b[^>]*?\bsrc=)([\"'])data:image/(png|jpe?g|gif|webp);base64,([a-z0-9+/=\s]*)\2",
    re.IGNORECASE,
)


def embed_data_images(html: str) -> tuple[str, list[dict]]:
    """Turn data: images in HTML into Content-ID references plus their parts.

    A picture the editor embedded as `<img src="data:image/png;base64,...">`
    displays in the webmail's own reading pane and in Apple Mail, but Gmail
    and Outlook refuse data: URIs in received mail outright -- the recipient
    sees a blank where the signature banner should be. A part in a
    multipart/related container referenced by `cid:` is what every client has
    rendered for decades, so that is what goes out.

    Returns the rewritten HTML and the parts to add, each
    `{"cid", "subtype", "name", "content"}`. The same bytes pasted twice (a
    logo in the body and again in the signature) share one part: matched on
    a digest of the decoded image, not on the text of the URI.

    A URI whose base64 does not decode is left exactly as it was rather than
    dropped -- refusing to send over one malformed image would lose the
    message, and leaving it in costs the recipient one broken picture.
    """
    parts: list[dict] = []
    cid_by_digest: dict[str, str] = {}

    def swap(match: re.Match) -> str:
        prefix, quote, subtype, payload = match.groups()
        try:
            content = base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
        except (binascii.Error, ValueError):
            return match.group(0)
        if not content:
            return match.group(0)

        digest = hashlib.sha256(content).hexdigest()
        cid = cid_by_digest.get(digest)
        if cid is None:
            subtype = "jpeg" if subtype.lower() == "jpg" else subtype.lower()
            cid = f"{digest[:32]}@inline.mailyte"
            cid_by_digest[digest] = cid
            parts.append(
                {
                    "cid": cid,
                    "subtype": subtype,
                    "name": f"image{len(parts) + 1}.{'jpg' if subtype == 'jpeg' else subtype}",
                    "content": content,
                }
            )
        return f"{prefix}{quote}cid:{cid}{quote}"

    return _DATA_IMAGE_SRC_RE.sub(swap, html), parts


def build_message(
    *,
    from_address: str,
    from_name: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
    inline_images: bool = False,
    inline_parts: list[dict] | None = None,
) -> EmailMessage:
    """Assemble a message. The SAME builder serves drafts and sends.

    Deliberately one function: a draft assembled by a second, nearly-identical
    builder is a draft that turns into a subtly different email when sent.

    Tolerates the partial fields a draft legitimately has -- no recipients, no
    subject -- so autosave never fails on a half-typed address. Validation
    that a message is complete enough to send belongs at send time.

    `inline_images` is the one deliberate difference between the two callers.
    On SEND, data: images in the HTML become Content-ID parts (see
    embed_data_images) so Gmail and Outlook show them. A DRAFT keeps them as
    data: URIs on purpose: the compose window resumes a draft from its stored
    HTML, and an editor can re-open a data: image but not a cid: reference
    whose part it never receives -- converting drafts would make every
    resumed draft lose its pictures.

    `inline_parts` are ready-made Content-ID parts -- `{"cid", "type",
    "name", "content"}` -- that the HTML already references by `cid:`. This
    is how a forwarded message keeps its inline images: the parts are lifted
    from the original and re-attached under the same ids. With no HTML body
    to reference them they are filed as ordinary attachments instead of
    being dropped.
    """
    message = EmailMessage()

    # Every caller-supplied value is _unfold()ed before it becomes a header.
    # A reply echoes the original's subject, and a subject that arrived
    # folded keeps its CRLF through RFC 2047 decoding on old parses;
    # EmailMessage's policy then refuses the store ("Header values may not
    # contain linefeed or carriage return characters") and the send 500s --
    # one long Namecheap subject proved it. The same stroke is the header-
    # injection guard: a CRLF smuggled into any of these fields becomes a
    # space in one header, never a second header.
    from_name = _unfold(from_name)
    message["From"] = formataddr((from_name, from_address)) if from_name else from_address
    for header, value in (("To", to), ("Cc", cc), ("Bcc", bcc)):
        cleaned = [addr for addr in (_unfold(a) for a in value or []) if addr]
        if cleaned:
            message[header] = ", ".join(cleaned)

    message["Subject"] = _unfold(subject) or ""
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()

    # Threading headers, so a reply lands inside its conversation in every
    # client rather than starting a new one. A msg-id is bracketed on the
    # wire (RFC 5322 3.6.4); a client that hands over the bare id gets the
    # brackets added so other clients' threading still matches on it.
    if in_reply_to:
        value = _unfold(in_reply_to) or ""
        if value and not value.startswith("<") and " " not in value:
            value = f"<{value}>"
        message["In-Reply-To"] = value
    if references:
        message["References"] = _unfold(references)

    related: list[dict] = []
    if body_html and inline_images:
        body_html, related = embed_data_images(body_html)
    attachments = list(attachments or [])
    for part in inline_parts or []:
        maintype, _, subtype = (part.get("type") or "application/octet-stream").partition("/")
        entry = {
            "cid": part["cid"],
            "maintype": maintype or "application",
            "subtype": subtype or "octet-stream",
            "name": part.get("name") or "inline",
            "content": part["content"],
        }
        if body_html:
            related.append(entry)
        else:
            attachments.append(
                {"name": entry["name"], "type": part.get("type"), "content": part["content"]}
            )

    message.set_content(body_text if body_text is not None else "")
    if body_html:
        message.add_alternative(body_html, subtype="html")
        if related:
            # The HTML alternative is the last part added. add_related() on it
            # turns THAT part into multipart/related with the HTML first and
            # the images after, which is the structure clients expect --
            # mixed( alternative( text, related( html, image... ) ), files ).
            # Adding the images to the top-level message instead would make
            # them ordinary attachments a reader has to open by hand.
            html_part = message.get_payload()[-1]
            for image in related:
                html_part.add_related(
                    image["content"],
                    maintype=image.get("maintype", "image"),
                    subtype=image["subtype"],
                    cid=f"<{image['cid']}>",
                    disposition="inline",
                    filename=_unfold(image["name"]) or "inline",
                )

    for item in attachments:
        maintype, _, subtype = (item.get("type") or "application/octet-stream").partition("/")
        message.add_attachment(
            item["content"],
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            # basename(): the filename comes from the sender's own machine and
            # is untrusted. Keeping a path out of the MIME filename parameter
            # stops "../../x" reaching anything that later writes it to disk.
            # EmailMessage handles the RFC 2231 encoding itself.
            filename=_unfold(os.path.basename(item.get("name") or "attachment")) or "attachment",
        )

    return message


def submit_message(envelope_from: str, recipients: list[str], raw: bytes) -> None:
    """Hand a message to Postfix for delivery.

    Submission goes to port 25 as a trusted client (the api service is inside
    Postfix's mynetworks), not to 587 with SASL -- there is no mailbox password
    to authenticate with, by design.

    **That makes pinning the sender this code's job, not Postfix's.**
    smtpd_sender_restrictions begins `permit_mynetworks`, which short-circuits
    before `reject_sender_login_mismatch` ever runs, so Postfix will accept any
    From we present. The caller must pass the session's own mailbox as
    envelope_from and must have built the From header to match; nothing
    downstream will catch it if they do not.
    """
    if not recipients:
        raise ValueError("A message needs at least one recipient")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.sendmail(envelope_from, recipients, raw)
    except smtplib.SMTPRecipientsRefused as exc:
        refused = ", ".join(sorted(exc.recipients)) or "the recipients"
        raise ValueError(f"The mail server refused {refused}") from None
    except smtplib.SMTPResponseException as exc:
        # A 5xx is the message's fault and worth repeating to the sender; a
        # 4xx is the server's and is not.
        if 500 <= exc.smtp_code < 600:
            detail = exc.smtp_error
            text = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
            raise ValueError(f"The mail server rejected this message: {text}") from None
        logger.error("SMTP %s during submission: %s", exc.smtp_code, exc.smtp_error)
        raise ImapUnavailableError("Could not send the message") from None
    except Exception as exc:
        logger.error("SMTP submission failed: %s", exc)
        raise ImapUnavailableError("Could not send the message") from None


def quota(conn: imaplib.IMAP4_SSL) -> dict:
    """Real storage usage from IMAP QUOTA (RFC 2087).

    Asking the server beats summing message sizes: the sum ignores index and
    cache overhead, and would mean fetching every folder to answer a number
    shown on a settings page.

    Returns zeros when the server reports no quota root. A mailbox with no
    quota genuinely has no limit, and the caller renders "unlimited" rather
    than a fabricated ceiling -- inventing one would show a usage percentage
    that means nothing.
    """
    used_kb = limit_kb = 0
    messages = 0
    try:
        st, data = conn.getquotaroot("INBOX")
    except Exception as exc:
        logger.debug("QUOTA unsupported or failed: %s", exc)
        return {
            "used_mb": 0,
            "quota_mb": 0,
            "percentage": None,
            "used_bytes": 0,
            "quota_bytes": 0,
            "messages": 0,
        }

    if st != "OK":
        return {
            "used_mb": 0,
            "quota_mb": 0,
            "percentage": None,
            "used_bytes": 0,
            "quota_bytes": 0,
            "messages": 0,
        }

    # Response is [[quotaroot lines], [quota lines]]; the numbers live in
    # `STORAGE <used> <limit>` inside the second group.
    for group in data or []:
        for item in group if isinstance(group, list) else [group]:
            raw = item if isinstance(item, bytes) else b""
            match = re.search(rb"STORAGE\s+(\d+)\s+(\d+)", raw)
            if match:
                used_kb, limit_kb = int(match.group(1)), int(match.group(2))
            # MESSAGE rides along in the same response. Free here; counting it
            # any other way means a SELECT per folder.
            msg_match = re.search(rb"MESSAGE\s+(\d+)", raw)
            if msg_match:
                messages = int(msg_match.group(1))

    used_mb = round(used_kb / 1024, 1)
    quota_mb = round(limit_kb / 1024, 1)
    return {
        "used_mb": used_mb,
        "quota_mb": quota_mb,
        "percentage": round(used_kb / limit_kb * 100, 1) if limit_kb else None,
        # Exact values beside the rounded ones. used_mb is rounded to one
        # decimal for display, so deriving bytes from it quantises everything
        # to 100 KB -- a 214 KB mailbox becomes "0.2 MB", then 209,715 bytes.
        # Anything storing a figure wants these.
        "used_bytes": used_kb * 1024,
        "quota_bytes": limit_kb * 1024,
        "messages": messages,
    }
