"""
Header-only message records for the mailbox insights engine.

Everything the insights page needs is header arithmetic: who wrote to whom,
when, in reply to what, and a handful of hygiene signals. So the engine never
downloads a body. One UID FETCH per batch asks Dovecot for FLAGS, INTERNALDATE,
RFC822.SIZE and a fixed list of header fields (BODY.PEEK, so computing
statistics never marks anything read), and this module turns that response
into `HeaderRecord`s.

Deliberately self-contained: shared/imap_mail.py's summary shaper is being
reworked concurrently, and the insights engine must not inherit whatever it
decides about in_reply_to/thread_id. The only import from it is the id scheme,
so the ids handed back here resolve against every other /api/v1/mailbox route.

**Never raises on an odd message.** A header block that will not parse, a
Date that is nonsense, an address with no "@" -- the record is skipped and
counted, and the rollup carries on. One malformed newsletter must not take the
whole insights page down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from email.header import decode_header
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime

from shared.imap_mail import compose_message_id

# The header fields the engine reads. Fixed rather than "everything": a
# mailing-list message can carry several KB of Received/DKIM/ARC lines that no
# statistic here uses, and at 20,000 messages a folder that is the difference
# between a few MB and a few hundred.
INSIGHT_HEADERS = (
    "MESSAGE-ID IN-REPLY-TO REFERENCES DATE FROM TO CC SUBJECT RETURN-PATH "
    "CONTENT-TYPE LIST-ID LIST-UNSUBSCRIBE PRECEDENCE AUTO-SUBMITTED "
    "X-AUTO-RESPONSE-SUPPRESS"
)
FETCH_ITEMS = f"(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({INSIGHT_HEADERS})])"

_UID_RE = re.compile(rb"\bUID\s+(\d+)")
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")
_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')
_MSGID_TOKEN_RE = re.compile(r"<[^<>\s]+>")
_INTERNALDATE_VALUE_RE = re.compile(
    r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+([+-])(\d{2})(\d{2})\s*$"
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

# Local parts that are machines, not people. Anchored and specific on purpose:
# "info@" and "support@" are read by humans and answered, and calling them
# automated would hide exactly the correspondence a small business cares
# about most.
_NOREPLY_LOCALPART_RE = re.compile(
    r"^(?:"
    r"no-?reply|do-?not-?reply|donotreply|no-?response|"
    r"no-?reply[-_.+].*|.*[-_.+]no-?reply|"
    r"notifications?|notify|alerts?|mailer|mailer-daemon|postmaster|"
    r"bounces?|bounces?[-_.+].*|.*[-_.+]bounces?|"
    r"newsletters?|digest|automated|auto-?mailer|robot"
    r")$",
    re.IGNORECASE,
)

# A message this large, or structured as multipart/mixed, carries more than
# prose. Content-Type is the cheap signal (BODYSTRUCTURE would be exact but
# costs Dovecot a parse per message it has not cached); the size floor catches
# senders whose clients wrap a single attachment some other way.
ATTACHMENT_BEARING_BYTES = 500_000

# Oldest Date header worth believing. Spam and broken clients emit 1970 and
# 2001 routinely; a message "from" before this is undated, not ancient.
_MIN_PLAUSIBLE_YEAR = 1995


@dataclass(slots=True)
class HeaderRecord:
    """One message, reduced to the headers the engine reads.

    `role` is the folder's function -- inbox | sent | drafts | other -- and is
    what decides direction: a message in a Sent folder is outbound whatever its
    From says, and a message in INBOX is inbound (unless it is From the holder
    themself, which the engine sets aside as a self-copy or a spoof).
    """

    folder: str
    uid: str
    role: str
    message_id: str | None = None
    in_reply_to: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    date: datetime | None = None
    internal_date: datetime | None = None
    sender: str | None = None
    recipients: tuple[str, ...] = ()
    subject: str = ""
    size: int = 0
    seen: bool = False
    answered: bool = False
    flagged: bool = False
    draft: bool = False
    deleted: bool = False
    # "" when the header is the empty reverse-path `<>`, the address when
    # present, None when the header is absent. The distinction matters: `<>`
    # is the strongest bounce signal there is.
    return_path: str | None = None
    content_type: str = "text/plain"
    report_type: str | None = None
    list_id: bool = False
    list_unsubscribe: bool = False
    precedence: str | None = None
    auto_submitted: str | None = None
    auto_response_suppress: bool = False
    id: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.id = compose_message_id(self.folder, self.uid)

    @property
    def effective_date(self) -> datetime | None:
        """The instant this message counts at.

        Inbound and drafts use INTERNALDATE: the moment it landed on THIS
        server, which is when the holder could first have seen it, and which
        a sender's wrong clock cannot skew. Outbound uses the Date header: the
        moment it was written, since a Sent copy filed late (or migrated)
        carries an INTERNALDATE that says nothing about when it was sent.
        Each falls back to the other when its preferred source is missing.
        """
        if self.role == "sent":
            return self.date or self.internal_date
        return self.internal_date or self.date

    @property
    def sender_domain(self) -> str | None:
        return domain_of(self.sender)

    @property
    def sender_localpart(self) -> str:
        return (self.sender or "").rpartition("@")[0]


# ---------------------------------------------------------------------------
# Field parsers -- each returns a safe default instead of raising
# ---------------------------------------------------------------------------


def domain_of(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    return address.rpartition("@")[2].lower() or None


def parse_internaldate(raw: str | bytes | None) -> datetime | None:
    """IMAP INTERNALDATE (`17-Jul-1996 02:44:25 -0700`) -> aware UTC datetime.

    Hand-parsed rather than strptime'd: %b is locale dependent, and the
    leading space Dovecot pads single-digit days with is a trap for both.
    """
    if not raw:
        return None
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    match = _INTERNALDATE_VALUE_RE.match(text)
    if not match:
        return None
    day, mon, year, hh, mm, ss, sign, tzh, tzm = match.groups()
    month = _MONTHS.get(mon.lower())
    if not month:
        return None
    try:
        offset = timedelta(hours=int(tzh), minutes=int(tzm))
        if sign == "-":
            offset = -offset
        local = datetime(
            int(year), month, int(day), int(hh), int(mm), int(ss), tzinfo=timezone(offset)
        )
    except ValueError:
        return None
    return local.astimezone(UTC)


def parse_rfc_date(raw: object) -> datetime | None:
    """Date header -> aware UTC datetime, or None for anything implausible."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(str(raw))
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed.year < _MIN_PLAUSIBLE_YEAR:
        return None
    return parsed


def decode_words(raw: object) -> str:
    """RFC 2047 encoded words -> text, whitespace collapsed. Never raises."""
    if not raw:
        return ""
    text = str(raw)
    try:
        parts = decode_header(text)
    except Exception:
        return " ".join(text.split())
    out: list[str] = []
    for value, charset in parts:
        if isinstance(value, bytes):
            try:
                out.append(value.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(value.decode("utf-8", errors="replace"))
        else:
            out.append(value)
    return " ".join("".join(out).split())


def message_id_tokens(raw: object) -> tuple[str, ...]:
    """Every `<...>` token in a Message-ID / In-Reply-To / References value."""
    if not raw:
        return ()
    return tuple(_MSGID_TOKEN_RE.findall(str(raw)))


def parse_addresses(values: list[object] | object) -> tuple[str, ...]:
    """Bare, lower-cased addresses out of one or more address headers.

    getaddresses handles quoted display names and groups; a naive split on
    "," turns `"Doe, John" <j@x>` into two non-addresses.
    """
    if values is None:
        return ()
    if not isinstance(values, list):
        values = [values]
    seen: set[str] = set()
    out: list[str] = []
    try:
        pairs = getaddresses([str(v) for v in values if v is not None])
    except Exception:
        return ()
    for _name, addr in pairs:
        addr = (addr or "").strip().strip("<>").lower()
        if "@" not in addr or addr in seen:
            continue
        seen.add(addr)
        out.append(addr)
    return tuple(out)


def _flags(meta: bytes) -> set[str]:
    match = _FLAGS_RE.search(meta)
    if not match:
        return set()
    return {f.decode(errors="replace").lower() for f in match.group(1).split()}


def _return_path(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "<>"):
        return ""
    addresses = parse_addresses(text)
    return addresses[0] if addresses else text.strip("<>").lower()


def _first_token(raw: object) -> str | None:
    """`auto-replied; owner-email=...` -> `auto-replied`."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    return text.split(";", 1)[0].strip() or None


def record_from_parts(
    folder: str, role: str, uid: str, meta: bytes, header_bytes: bytes | None
) -> HeaderRecord:
    """Build a record from one FETCH response's metadata prefix and header literal.

    Raises on genuinely broken input -- the caller (parse_fetch_response)
    counts and skips. Individual field parsers inside are already lenient, so
    what reaches the except is the truly odd message, not an unusual one.
    """
    headers = BytesHeaderParser().parsebytes(header_bytes or b"")
    flags = _flags(meta)
    size_match = _SIZE_RE.search(meta)
    internal_match = _INTERNALDATE_RE.search(meta)

    own_tokens = message_id_tokens(headers.get("Message-ID"))
    message_id = own_tokens[0] if own_tokens else None
    if message_id is None:
        bare = (str(headers.get("Message-ID") or "")).strip()
        # A Message-ID without angle brackets is a client bug, but the value
        # still identifies the message; other clients' References will carry
        # it bracketed, so normalise to that form.
        message_id = f"<{bare}>" if bare and " " not in bare else None

    report_type = headers.get_param("report-type")
    if isinstance(report_type, tuple):
        report_type = report_type[2]

    senders = parse_addresses(headers.get("From"))
    recipients = parse_addresses(headers.get_all("To", []) + headers.get_all("Cc", []))
    references: list[str] = []
    for value in headers.get_all("References", []):
        references.extend(message_id_tokens(value))

    return HeaderRecord(
        folder=folder,
        uid=uid,
        role=role,
        message_id=message_id,
        in_reply_to=message_id_tokens(headers.get("In-Reply-To")),
        references=tuple(dict.fromkeys(references)),
        date=parse_rfc_date(headers.get("Date")),
        internal_date=parse_internaldate(internal_match.group(1) if internal_match else None),
        sender=senders[0] if senders else None,
        recipients=recipients,
        subject=decode_words(headers.get("Subject"))[:200],
        size=int(size_match.group(1)) if size_match else 0,
        seen="\\seen" in flags,
        answered="\\answered" in flags,
        flagged="\\flagged" in flags,
        draft="\\draft" in flags,
        deleted="\\deleted" in flags,
        return_path=_return_path(headers.get("Return-Path")),
        content_type=(headers.get_content_type() or "text/plain").lower(),
        report_type=str(report_type).lower() if report_type else None,
        list_id=headers.get("List-Id") is not None,
        list_unsubscribe=headers.get("List-Unsubscribe") is not None,
        precedence=_first_token(headers.get("Precedence")),
        auto_submitted=_first_token(headers.get("Auto-Submitted")),
        auto_response_suppress=headers.get("X-Auto-Response-Suppress") is not None,
    )


def parse_fetch_response(
    data: list | None, folder: str, role: str
) -> tuple[list[HeaderRecord], int]:
    """imaplib UID FETCH data -> (records, skipped).

    imaplib flattens a FETCH into one tuple per literal plus a lone b')' per
    message, with UID/FLAGS/INTERNALDATE/SIZE in the tuple's prefix. This
    request asks for exactly one literal (the header fields), so each tuple is
    one message -- but the regrouping below keys on the UID in the prefix
    rather than assuming that, so a server that splits differently still
    pairs the right headers with the right flags. Trailing bytes items (the
    closing paren, or FLAGS a server appends after the literal) are folded
    into the current message's metadata.
    """
    grouped: dict[str, dict[str, bytes]] = {}
    current: str | None = None

    for item in data or []:
        if isinstance(item, tuple):
            prefix, payload = item[0], item[1] if len(item) > 1 else b""
            if not isinstance(prefix, bytes):
                prefix = str(prefix).encode()
            uid_match = _UID_RE.search(prefix)
            if uid_match:
                current = uid_match.group(1).decode()
                entry = grouped.setdefault(current, {"meta": b"", "header": b""})
            elif current is None:
                continue
            else:
                entry = grouped[current]
            entry["meta"] += b" " + prefix
            if isinstance(payload, bytes) and b"HEADER" in prefix.upper():
                entry["header"] = payload
        elif isinstance(item, bytes) and current is not None:
            grouped[current]["meta"] += b" " + item

    records: list[HeaderRecord] = []
    skipped = 0
    for uid, entry in grouped.items():
        try:
            records.append(record_from_parts(folder, role, uid, entry["meta"], entry["header"]))
        except Exception:
            skipped += 1
    return records, skipped


# ---------------------------------------------------------------------------
# Per-message classifiers
# ---------------------------------------------------------------------------


def is_bounce(record: HeaderRecord) -> bool:
    """A delivery failure report, by any of three independent signals.

    1. Return-Path is the empty reverse-path `<>` -- RFC 5321 requires it on
       every DSN precisely so bounces cannot bounce.
    2. From MAILER-DAEMON@... -- what Postfix, Exim and Sendmail put on the
       report they generate.
    3. Content-Type multipart/report; report-type=delivery-status -- RFC 3464,
       the structured DSN Exchange, Gmail and every modern MTA emit.

    Any one is enough. Not used: subject-line matching ("Undeliverable:"), which
    is language- and vendor-specific and which humans forward.
    """
    if record.return_path == "":
        return True
    if record.sender_localpart.lower() == "mailer-daemon":
        return True
    return record.content_type == "multipart/report" and record.report_type == "delivery-status"


def is_automated(record: HeaderRecord) -> bool:
    """Machine-sent mail: lists, notifications, auto-replies, bounces.

    Signals, any one sufficient: List-Id or List-Unsubscribe (RFC 2919 / 2369
    -- every list manager and bulk sender sets one), Precedence bulk|list|junk
    (the older convention), Auto-Submitted other than "no" (RFC 3834 -- vacation
    responders and system notices), X-Auto-Response-Suppress (Exchange's
    marker on generated mail), a no-reply style local part, or a bounce.
    """
    if is_bounce(record):
        return True
    if record.list_id or record.list_unsubscribe or record.auto_response_suppress:
        return True
    if record.precedence in ("bulk", "list", "junk"):
        return True
    if record.auto_submitted and record.auto_submitted != "no":
        return True
    return bool(_NOREPLY_LOCALPART_RE.match(record.sender_localpart))


def is_noreply_address(address: str | None) -> bool:
    """Whether an address is one nobody reads -- so mail sent TO it is not
    'awaiting a reply'."""
    if not address or "@" not in address:
        return False
    return bool(_NOREPLY_LOCALPART_RE.match(address.rpartition("@")[0]))


def is_attachment_bearing(record: HeaderRecord) -> bool:
    return record.content_type == "multipart/mixed" or record.size >= ATTACHMENT_BEARING_BYTES
