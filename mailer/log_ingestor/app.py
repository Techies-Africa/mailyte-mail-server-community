#!/usr/bin/env python3
"""
Postfix log ingestor — the missing producer for mail_logs and delivery events.

Why this exists
---------------
`mail_logs` is read by the platform API (worker/api/routes/message_trace.py,
analytics.py) and `delivery_events` is read by mailyte-api's EmailLogService,
but nothing in the stack ever wrote a per-message row to either. Confirmed on
production 2026-08-22: mail_logs, email_tracking, bounce_events, mail_queue,
email_archive and analytics_data were all at 0 rows while mail flowed normally,
so every "Email Logs" surface was correctly rendering an empty set.

The pieces that looked like they might be the producer were not:
  * mailer/log_analyzer/ writes aggregate `mail_analysis_reports`, never
    per-message rows -- and is not in any compose file, so no container exists.
  * webhook_sender.py IS defined in master.cf as a `webhook-filter` transport,
    but no `content_filter` ever references it. `tracking-filter` occupies that
    slot on both submission and smtps, so webhook-filter is dead config.
  * tracking_injector.py has _send_webhook_notification(), but it returns
    immediately unless TRACKING_WEBHOOK_URL is set, which it is not.

Postfix's own log is the right source: it is the authoritative record of every
delivery attempt regardless of how the message was submitted (SMTP submission,
webmail, LMTP, or the API), and MailLog's columns -- relay, delays, dsn, status
-- map onto a Postfix delivery line one-for-one, which is what that schema was
plainly designed around.

Design notes
------------
Idempotent by construction: each row's primary key is derived deterministically
from (queue id, recipient, status, timestamp) and inserted with INSERT IGNORE.
Re-reading a log region -- after a crash, a bad offset, or an operator replay --
cannot create duplicates. That matters more than it sounds: the offset file is
the kind of state that goes stale exactly when you are least able to notice.

Log rotation is detected by inode, not by size alone. A rotated-then-smaller
file would otherwise look like a truncation and be re-read from 0 against the
wrong file.
"""

import hashlib
import logging
import os
import re
import signal
import sys
import time
from base64 import b32encode
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector

sys.path.append(str(Path(__file__).parent))

from shared.webhook_dispatcher import Events, dispatch_event  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - log_ingestor - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# This service makes one outbound HTTPS call per ingested message, and at DEBUG
# urllib3 narrates every connection -- which would bury the actual ingest lines
# and grow the log faster than the mail log it is reading. Production currently
# resolves LOG_LEVEL to DEBUG (the .env defines it twice, INFO then DEBUG, and
# the later wins), so pin the HTTP libraries up regardless of the root level.
for noisy in ("urllib3", "requests", "urllib3.connectionpool"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

LOG_PATH = os.getenv("POSTFIX_LOG_PATH", "/var/log/postfix/mail.log")
STATE_PATH = os.getenv("INGESTOR_STATE_PATH", "/var/lib/log_ingestor/state")
POLL_SECONDS = float(os.getenv("INGESTOR_POLL_SECONDS", "2"))
# How many queue-ids to remember while waiting for their delivery lines. A
# message's from=/message-id= lines arrive before its to= lines, sometimes
# seconds apart under load, so this has to outlive that gap without growing
# without bound on a busy server.
QID_CACHE_SIZE = int(os.getenv("INGESTOR_QID_CACHE", "20000"))

# Internal content_filter hops, not deliveries. Postfix logs a full
# "status=sent" line when it hands a message to a pipe transport and another
# when the reinjected copy is actually delivered, so counting both would show
# every message twice -- once relay=tracking-filter, once relay=dovecot. Only
# the second is a delivery anyone means.
INTERNAL_RELAYS = {
    r.strip().lower()
    for r in os.getenv("INGESTOR_INTERNAL_RELAYS", "tracking-filter,webhook-filter").split(",")
    if r.strip()
}

DB = {
    "host": os.getenv("DB_HOST", "mysql"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "database": os.getenv("DB_NAME", "mailserver"),
    "user": os.getenv("DB_USER", "mailuser"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# --- SMTP API keys (00-PRD-smtp-api-keys K2) -------------------------------
# Auto-suspension of abusive keys. Disabled by default deliberately: the
# thresholds need observing against real traffic before this is allowed to
# turn keys off on its own (PRD section 8). When enabled, a key whose recent
# outbound mail is mostly bouncing/rejected is set inactive, an audit event
# is recorded, and its Dovecot auth-cache entry is flushed.
AUTO_SUSPEND_ENABLED = os.getenv("AUTO_SUSPEND_ENABLED", "false").lower() == "true"
AUTO_SUSPEND_MIN_MESSAGES = int(os.getenv("AUTO_SUSPEND_MIN_MESSAGES", "20"))
AUTO_SUSPEND_FAILURE_RATIO = float(os.getenv("AUTO_SUSPEND_FAILURE_RATIO", "0.5"))
AUTO_SUSPEND_WINDOW_HOURS = int(os.getenv("AUTO_SUSPEND_WINDOW_HOURS", "1"))
DOVEADM_URL = os.getenv("DOVEADM_URL", "http://dovecot:24180")
DOVEADM_API_KEY = os.getenv("DOVEADM_API_KEY", "")
# last_used_at write throttle -- one UPDATE per key per interval, not one
# per delivered message.
LAST_USED_THROTTLE_SECONDS = int(os.getenv("LAST_USED_THROTTLE_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Postfix line parsing
# ---------------------------------------------------------------------------
# "Aug 22 08:05:35 courier postfix/lmtp[1210]: B49AF1425B4: to=<...>, ..."
# The submission/smtps services set `-o syslog_name=postfix/submission`, so
# their smtpd logs as the THREE-part "postfix/submission/smtpd[375]" -- the
# optional middle segment is what lets the authenticated client= lines (the
# only lines carrying sasl_username) match at all.
LINE_RE = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"\S+\s+postfix/(?:[a-z0-9-]+/)?(?P<comp>[a-z]+)(?:\[\d+\])?:\s+(?P<rest>.*)$"
)
QID_RE = re.compile(r"^(?P<qid>[A-F0-9]{6,20}):\s+(?P<body>.*)$")
MSGID_RE = re.compile(r"message-id=<?(?P<mid>[^>\s]+)>?")
FROM_RE = re.compile(r"from=<(?P<sender>[^>]*)>")
SIZE_RE = re.compile(r"size=(?P<size>\d+)")
TO_RE = re.compile(r"to=<(?P<rcpt>[^>]*)>")
RELAY_RE = re.compile(r"relay=(?P<relay>[^,]+)")
DELAYS_RE = re.compile(r"delays=(?P<delays>[^,]+)")
DSN_RE = re.compile(r"dsn=(?P<dsn>[^,\s]+)")
STATUS_RE = re.compile(r"status=(?P<status>\w+)(?:\s+\((?P<detail>.*)\))?")
# "NOQUEUE: reject: RCPT from x[1.2.3.4]: 553 5.7.1 <a@b>: ...; from=<a@b> to=<c@d> ..."
REJECT_RE = re.compile(r"NOQUEUE:\s+reject:\s+(?P<detail>.*)$")
# First 3-digit SMTP reply code in a reject line, e.g. "... : 450 4.3.2 ..."
REPLY_CODE_RE = re.compile(r"\b(?P<code>[45]\d{2})\b")
# Subject, echoed into the log by the `/^Subject:/ WARN` header_checks rule.
# Postfix renders it as:
#   <qid>: warning: header Subject: <value> from <client>; from=<..> to=<..> ...
# The value can itself contain " from " and "; ", so trim the known suffixes
# off the end rather than trying to match the subject non-greedily.
SUBJECT_WARN_RE = re.compile(r"warning:\s+header\s+Subject:\s*(?P<rest>.*)$", re.I)
# "<qid>: client=host[1.2.3.4], sasl_method=PLAIN, sasl_username=user" --
# smtpd logs the authenticated identity once per queue id; it is the only
# line that ties a delivery to the SMTP API key (or mailbox) that sent it.
SASL_RE = re.compile(r"sasl_username=(?P<sasl>\S+)")

MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}

# Postfix status -> mail_logs.status enum
# 'sent' is Postfix's word for "handed off successfully", which for both a
# local LMTP save and a remote 250 is what an operator reads as delivered --
# the same meaning Mailgun/Postmark put behind that word. The enum keeps 'sent'
# available, but splitting the two here would mean a "delivered" filter
# silently excluded all outbound mail, which is the opposite of useful.
STATUS_MAP = {
    "sent": "delivered",
    "bounced": "bounced",
    "deferred": "deferred",
    "expired": "bounced",
}
EVENT_MAP = {
    "delivered": Events.EMAIL_DELIVERED,
    "bounced": Events.EMAIL_BOUNCED,
    "deferred": Events.EMAIL_DEFERRED,
    "rejected": Events.EMAIL_REJECTED,
}


def parse_timestamp(mon: str, day: str, tod: str, now: datetime) -> datetime:
    """
    Postfix logs carry no year. Assume the current one, but if that lands more
    than a day in the future we have just crossed New Year reading December
    lines, so fall back a year rather than stamping them ahead of now.
    """
    try:
        ts = datetime(now.year, MONTHS[mon], int(day), *[int(p) for p in tod.split(":")])
    except (KeyError, ValueError):
        return now
    if ts - now > timedelta(days=1):
        ts = ts.replace(year=now.year - 1)
    return ts


def extract_subject(rest: str) -> str:
    """
    Pull the subject out of a header_checks WARN line.

    Postfix appends its own context after the header value:
        <subject> from <client>; from=<..> to=<..> proto=.. helo=<..>: <warn text>
    A subject may legitimately contain " from " or ";", so peel the known
    trailing context off the right rather than matching the subject itself
    non-greedily -- the latter truncates any subject containing " from ".
    """
    value = rest
    marker = value.find("; from=<")
    if marker != -1:
        value = value[:marker]
    # What remains is "<subject> from <client>"; the client is the last token
    # and has no spaces, so only strip when that shape actually holds.
    head, sep, tail = value.rpartition(" from ")
    if sep and tail and " " not in tail.strip():
        value = head
    return value.strip()[:1000]


def row_id(qid: str, recipient: str, status: str, ts: datetime) -> str:
    """
    Deterministic 26-char id so replaying a log region is a no-op rather than a
    duplicate. char(26) matches the ULID column width the rest of the schema
    uses; base32 keeps it in the same alphabet family.
    """
    digest = hashlib.sha256(
        f"{qid}|{recipient}|{status}|{ts.isoformat()}".encode()
    ).digest()
    return b32encode(digest).decode("ascii")[:26].upper()


class Ingestor:
    def __init__(self):
        self.qids: OrderedDict[str, dict] = OrderedDict()
        self._org_cache: dict[str, str | None] = {}
        self._last_used_writes: dict[str, datetime] = {}
        # message-id -> sasl_username. The tracking content_filter reinjects
        # outbound mail under a NEW queue id, and only the original qid's
        # smtpd line carries sasl_username -- the Message-ID header survives
        # the hop, so it is the bridge that keeps the delivered copy
        # attributed to the key that sent it.
        self._msgid_sasl: OrderedDict[str, str] = OrderedDict()
        self._conn = None
        self._running = True

    def _map_msgid_sasl(self, message_id: str | None, sasl_username: str | None):
        if not message_id or not sasl_username:
            return
        self._msgid_sasl[message_id] = sasl_username
        self._msgid_sasl.move_to_end(message_id)
        while len(self._msgid_sasl) > QID_CACHE_SIZE:
            self._msgid_sasl.popitem(last=False)

    # -- database ----------------------------------------------------------
    def conn(self):
        if self._conn is not None:
            try:
                self._conn.ping(reconnect=True, attempts=2, delay=1)
                return self._conn
            except mysql.connector.Error:
                self._conn = None
        self._conn = mysql.connector.connect(**DB, autocommit=True)
        return self._conn

    def org_for_domain(self, domain: str) -> str | None:
        if not domain:
            return None
        domain = domain.lower()
        if domain in self._org_cache:
            return self._org_cache[domain]
        org = None
        try:
            cur = self.conn().cursor()
            cur.execute("SELECT organization_id FROM domains WHERE domain = %s LIMIT 1", (domain,))
            row = cur.fetchone()
            cur.close()
            org = row[0] if row else None
        except mysql.connector.Error as exc:
            logger.warning("org lookup failed for %s: %s", domain, exc)
            return None  # don't cache a lookup that failed for infrastructure reasons
        self._org_cache[domain] = org
        return org

    def body_for(self, message_id: str | None) -> dict:
        """
        Fetch the captured body for a message, if tracking_injector stored one.

        Only outbound mail passes through that filter, so inbound messages
        legitimately have no row here and render without a body -- the same way
        a provider shows content for what you sent, not for what arrived.
        """
        if not message_id:
            return {}
        try:
            cur = self.conn().cursor(dictionary=True)
            cur.execute(
                "SELECT html, text, subject FROM email_bodies "
                "WHERE message_id = %s ORDER BY captured_at DESC LIMIT 1",
                (message_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row or {}
        except mysql.connector.Error as exc:
            logger.warning("body lookup failed for %s: %s", message_id, exc)
            return {}

    def prune_bodies(self):
        """
        Enforce the retention window on captured bodies.

        Done here rather than in a cron because this process is already running
        and already holds a connection; a retention policy that depends on a
        separate scheduled job is one nobody notices has stopped.
        """
        try:
            cur = self.conn().cursor()
            cur.execute("DELETE FROM email_bodies WHERE expires_at IS NOT NULL AND expires_at < NOW()")
            if cur.rowcount:
                logger.info("pruned %d expired message bodies", cur.rowcount)
            cur.close()
        except mysql.connector.Error as exc:
            logger.warning("body prune failed: %s", exc)

    @staticmethod
    def domain_of(address: str) -> str:
        return address.rsplit("@", 1)[-1].lower() if address and "@" in address else ""

    def resolve_org(self, sender: str, recipient: str) -> tuple[str | None, str]:
        """
        Attribute the row to whichever side of the message we actually host:
        sender for outbound, recipient for inbound. Returns (org_id, domain).
        """
        for addr in (sender, recipient):
            dom = self.domain_of(addr)
            org = self.org_for_domain(dom)
            if org:
                return org, dom
        return None, self.domain_of(sender) or self.domain_of(recipient)

    # -- writing -----------------------------------------------------------
    def touch_credential(self, username: str, ts: datetime):
        """Maintain smtp_credentials.last_used_at from the ingest path --
        the auth path deliberately never writes it (an UPDATE per AUTH
        would serialize logins on row locks). Throttled per key."""
        last = self._last_used_writes.get(username)
        if last and (ts - last).total_seconds() < LAST_USED_THROTTLE_SECONDS:
            return
        try:
            cur = self.conn().cursor()
            cur.execute(
                "UPDATE smtp_credentials SET last_used_at = %s WHERE username = %s",
                (ts, username),
            )
            cur.close()
            self._last_used_writes[username] = ts
        except mysql.connector.Error as exc:
            logger.error("last_used_at update failed for %s: %s", username, exc)

    def maybe_auto_suspend(self, username: str, ts: datetime):
        """Suspend a key whose recent outbound mail is mostly failing.
        Gated off by default (AUTO_SUSPEND_ENABLED); see the config note."""
        if not AUTO_SUSPEND_ENABLED or "@" in username:
            return
        try:
            cur = self.conn().cursor()
            cur.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(status IN ('bounced', 'rejected')) AS failed
                FROM mail_logs
                WHERE sasl_username = %s AND timestamp >= %s
                """,
                (username, ts - timedelta(hours=AUTO_SUSPEND_WINDOW_HOURS)),
            )
            total, failed = cur.fetchone()
            total, failed = int(total or 0), int(failed or 0)
            if total < AUTO_SUSPEND_MIN_MESSAGES or failed / total < AUTO_SUSPEND_FAILURE_RATIO:
                cur.close()
                return
            cur.execute(
                "UPDATE smtp_credentials SET active = 0 WHERE username = %s AND active = 1",
                (username,),
            )
            if cur.rowcount:
                cur.execute(
                    """
                    INSERT INTO smtp_credential_events
                        (id, credential_id, organization_id, username, event,
                         actor, detail, created_at)
                    SELECT %s, id, organization_id, username, 'suspended',
                           'log_ingestor:auto-suspend',
                           JSON_OBJECT('failed', %s, 'total', %s,
                                       'window_hours', %s), %s
                    FROM smtp_credentials WHERE username = %s
                    """,
                    (
                        os.urandom(13).hex().upper()[:26],
                        failed,
                        total,
                        AUTO_SUSPEND_WINDOW_HOURS,
                        ts,
                        username,
                    ),
                )
                logger.warning(
                    "auto-suspended SMTP credential %s: %d/%d recent messages failed",
                    username, failed, total,
                )
                self._flush_dovecot_cache(username)
            cur.close()
        except mysql.connector.Error as exc:
            logger.error("auto-suspend check failed for %s: %s", username, exc)

    def _flush_dovecot_cache(self, username: str):
        """A suspension is only real once the auth-cache entry is gone --
        same doveadm HTTP call the platform API makes (K1)."""
        if not DOVEADM_API_KEY:
            logger.warning("DOVEADM_API_KEY unset -- cache not flushed for %s", username)
            return
        try:
            import base64

            import requests

            token = base64.b64encode(DOVEADM_API_KEY.encode()).decode()
            requests.post(
                f"{DOVEADM_URL}/doveadm/v1",
                json=[["authCacheFlush", {"user": [username]}, "suspend"]],
                headers={"Authorization": f"X-Dovecot-API {token}"},
                timeout=5,
            )
        except Exception as exc:
            logger.error("doveadm cache flush failed for %s: %s", username, exc)

    def record(self, *, qid, ts, sender, recipient, status, message_id,
               size, relay, delays, dsn, detail, subject=None, sasl_username=None):
        org_id, domain = self.resolve_org(sender, recipient)
        rid = row_id(qid, recipient, status, ts)
        bounce_reason = detail if status in ("bounced", "deferred", "rejected") else None

        try:
            cur = self.conn().cursor()
            cur.execute(
                """
                INSERT IGNORE INTO mail_logs
                    (id, timestamp, sender, recipient, organization_id, status,
                     message_id, size, relay, delays, dsn, bounce_reason, subject,
                     sasl_username)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (rid, ts, sender or "", recipient or "", org_id, status,
                 message_id, size, relay, delays, dsn, bounce_reason, subject,
                 sasl_username),
            )
            inserted = cur.rowcount > 0
            cur.close()
        except mysql.connector.Error as exc:
            logger.error("mail_logs insert failed (%s -> %s): %s", sender, recipient, exc)
            return

        if not inserted:
            return  # already ingested; don't re-fire the webhook either

        if sasl_username and status in ("bounced", "rejected"):
            self.maybe_auto_suspend(sasl_username, ts)

        logger.info("logged %s %s -> %s (%s)", status, sender, recipient, qid)

        # Feed the tenant-facing surface too. mailyte-api resolves org/domain/
        # recipient from exactly these payload keys (WebhookProcessingService::
        # resolveContext), so send them explicitly rather than relying on its
        # EmailAccount fallback.
        event = EVENT_MAP.get(status)
        if not event:
            return

        # EmailLogController::show reads content.html/.text off the payload, so
        # the body has to travel with the event rather than be looked up later
        # -- mailyte-api has no route back into this database.
        body = self.body_for(message_id)
        try:
            dispatch_event(
                event_type=event,
                data={
                    "message_id": message_id,
                    "queue_id": qid,
                    "sender": sender,
                    "from": sender,
                    # EmailLogResource surfaces content.subject from the stored
                    # payload, so this is what fills the dashboard's Subject
                    # column and the "Email details" modal heading.
                    "subject": subject or body.get("subject"),
                    "html": body.get("html"),
                    "text": body.get("text"),
                    "recipient": recipient,
                    "to": recipient,
                    "domain": domain,
                    "organization_id": org_id,
                    "status": status,
                    "relay": relay,
                    "dsn": dsn,
                    "size": size,
                    "detail": detail,
                    "timestamp": ts.isoformat(),
                },
                domain=domain or None,
                source_service="log_ingestor",
            )
        except Exception as exc:  # never let webhook trouble stall ingestion
            logger.error("dispatch_event failed for %s: %s", qid, exc)

    # -- parsing -----------------------------------------------------------
    def remember(self, qid: str, **fields):
        entry = self.qids.get(qid) or {}
        entry.update({k: v for k, v in fields.items() if v is not None})
        self.qids[qid] = entry
        self.qids.move_to_end(qid)
        while len(self.qids) > QID_CACHE_SIZE:
            self.qids.popitem(last=False)

    def handle_line(self, line: str, now: datetime):
        m = LINE_RE.match(line)
        if not m:
            return
        ts = parse_timestamp(m["mon"], m["day"], m["time"], now)
        rest = m["rest"]

        rej = REJECT_RE.search(rest)
        if rej:
            detail = rej["detail"]
            sender_m, rcpt_m = FROM_RE.search(detail), TO_RE.search(detail)
            if rcpt_m:
                # Split on the SMTP reply class, not on the word "reject".
                # postscreen's deep-protocol test and rspamd greylisting both
                # log as rejects while returning 4xx, and the sender retries
                # and gets through -- recording those as failures would report
                # ordinary greylisting as lost mail.
                code = REPLY_CODE_RE.search(detail)
                temporary = bool(code) and code["code"].startswith("4")
                self.record(
                    qid="NOQUEUE", ts=ts,
                    sender=sender_m["sender"] if sender_m else "",
                    recipient=rcpt_m["rcpt"],
                    status="deferred" if temporary else "rejected",
                    message_id=None,
                    size=None, relay=None, delays=None, dsn=None, detail=detail[:2000],
                )
            return

        q = QID_RE.match(rest)
        if not q:
            return
        qid, body = q["qid"], q["body"]

        # smtpd's connection line: remember the authenticated identity for
        # this queue id so the delivery rows can be attributed to it, and
        # keep the key's last_used_at current (throttled).
        if body.startswith("client=") and "sasl_username=" in body:
            sasl = SASL_RE.search(body)
            if sasl:
                username = sasl["sasl"][:255]
                self.remember(qid, sasl_username=username)
                self._map_msgid_sasl(self.qids.get(qid, {}).get("message_id"), username)
                if "@" not in username:  # SMTP API keys only, not mailboxes
                    self.touch_credential(username, ts)
            return

        subj = SUBJECT_WARN_RE.search(body)
        if subj:
            value = extract_subject(subj["rest"])
            if value:
                self.remember(qid, subject=value)
            return

        mid = MSGID_RE.search(body)
        if mid:
            self.remember(qid, message_id=mid["mid"][:255])
            self._map_msgid_sasl(mid["mid"][:255], self.qids.get(qid, {}).get("sasl_username"))

        # from=/size= also appear on delivery lines; only the qmgr/cleanup form
        # carries the envelope sender for the whole message.
        if "from=<" in body and "to=<" not in body:
            f, s = FROM_RE.search(body), SIZE_RE.search(body)
            self.remember(
                qid,
                sender=f["sender"] if f else None,
                size=int(s["size"]) if s else None,
            )

        st = STATUS_RE.search(body)
        to = TO_RE.search(body)
        if not (st and to):
            return

        mapped = STATUS_MAP.get(st["status"])
        if not mapped:
            return

        cached = self.qids.get(qid, {})
        relay = RELAY_RE.search(body)

        # Drop the content_filter handoff so a message is not logged twice.
        # The relay reads as a bare transport name ("tracking-filter") rather
        # than the host[ip]:port form a real delivery carries.
        if relay:
            relay_name = relay["relay"].split("[", 1)[0].strip().lower()
            if relay_name in INTERNAL_RELAYS:
                return
        delays = DELAYS_RE.search(body)
        dsn = DSN_RE.search(body)
        self.record(
            qid=qid, ts=ts,
            sender=cached.get("sender", ""),
            recipient=to["rcpt"],
            status=mapped,
            message_id=cached.get("message_id"),
            size=cached.get("size"),
            relay=relay["relay"][:255] if relay else None,
            delays=delays["delays"][:100] if delays else None,
            dsn=dsn["dsn"][:10] if dsn else None,
            detail=(st["detail"] or "")[:2000],
            subject=cached.get("subject"),
            # Direct hit for the original queue id; the message-id bridge
            # covers the content_filter's reinjected copy (new qid).
            sasl_username=cached.get("sasl_username")
            or self._msgid_sasl.get(cached.get("message_id") or ""),
        )

    # -- tailing -----------------------------------------------------------
    def load_state(self) -> tuple[int, int]:
        try:
            inode, offset = Path(STATE_PATH).read_text().split(":")
            return int(inode), int(offset)
        except (OSError, ValueError):
            return 0, 0

    def save_state(self, inode: int, offset: int):
        try:
            p = Path(STATE_PATH)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"{inode}:{offset}")
        except OSError as exc:
            logger.warning("could not persist state: %s", exc)

    def run(self):
        logger.info("ingesting %s -> mail_logs (state: %s)", LOG_PATH, STATE_PATH)
        known_inode, offset = self.load_state()
        last_prune = 0.0

        while self._running:
            # Hourly is plenty for a daily-granularity retention window, and
            # keeps the DELETE off the hot path of tailing the log.
            if time.time() - last_prune > 3600:
                self.prune_bodies()
                last_prune = time.time()

            try:
                stat = os.stat(LOG_PATH)
            except FileNotFoundError:
                time.sleep(POLL_SECONDS)
                continue

            # Rotation/truncation: a new inode means a new file (start at 0);
            # a shrunken file with the same inode was truncated in place.
            if stat.st_ino != known_inode:
                logger.info("new log file (inode %s), reading from start", stat.st_ino)
                known_inode, offset = stat.st_ino, 0
            elif stat.st_size < offset:
                logger.info("log truncated, rewinding")
                offset = 0

            if stat.st_size > offset:
                now = datetime.now()
                with open(LOG_PATH, encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    for line in fh:
                        if not line.endswith("\n"):
                            break  # partial trailing write; pick it up next pass
                        try:
                            self.handle_line(line.rstrip("\n"), now)
                        except Exception as exc:
                            logger.error("line failed: %s (%s)", exc, line[:200])
                        offset += len(line.encode("utf-8"))
                self.save_state(known_inode, offset)

            time.sleep(POLL_SECONDS)

    def stop(self, *_):
        logger.info("shutting down")
        self._running = False


if __name__ == "__main__":
    ing = Ingestor()
    signal.signal(signal.SIGTERM, ing.stop)
    signal.signal(signal.SIGINT, ing.stop)
    ing.run()
