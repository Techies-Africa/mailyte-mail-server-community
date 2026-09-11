#!/usr/bin/env python3
"""
Unit tests for the mailbox insights engine and its routes
(worker/api/utils/insights/*, worker/api/routes/mailbox_insights.py).

Everything runs over synthetic HeaderRecords and synthetic imaplib FETCH
data -- no IMAP, no database, no Redis (a 20-line MiniRedis stands in). The
engine is pure by design, so exact numbers are asserted, not shapes.
"""

import itertools
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from routes import mailbox_insights  # noqa: E402
from utils.insights import cache as insights_cache  # noqa: E402
from utils.insights import engine, topics  # noqa: E402
from utils.insights.fetch import imap_date, resolve_roles, role_for  # noqa: E402
from utils.insights.records import (  # noqa: E402
    HeaderRecord,
    is_attachment_bearing,
    is_automated,
    is_bounce,
    is_noreply_address,
    parse_fetch_response,
    parse_internaldate,
)

from shared.imap_mail import ImapUnavailableError  # noqa: E402

HOLDER = "me@corp.ng"
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
WINDOW_FROM = NOW - timedelta(days=90)
LAGOS = ZoneInfo("Africa/Lagos")  # UTC+1, no DST

_uids = itertools.count(1)


def rec(
    role="inbox",
    *,
    at,
    mid=None,
    irt=(),
    refs=(),
    sender="alice@ext.com",
    to=(HOLDER,),
    seen=True,
    answered=False,
    subject="Hello",
    size=1000,
    folder=None,
    **kwargs,
):
    """A HeaderRecord with the date in the slot its role reads it from."""
    uid = str(next(_uids))
    folder = folder or {"inbox": "INBOX", "sent": "Sent", "drafts": "Drafts"}.get(role, "Other")
    return HeaderRecord(
        folder=folder,
        uid=uid,
        role=role,
        message_id=mid if mid is not None else f"<m{uid}@test>",
        in_reply_to=tuple(irt),
        references=tuple(refs),
        date=at if role == "sent" else None,
        internal_date=None if role == "sent" else at,
        sender=sender,
        recipients=tuple(to),
        subject=subject,
        size=size,
        seen=seen,
        answered=answered,
        **kwargs,
    )


def prepare(records, window_from=WINDOW_FROM):
    return engine.prepare(records, HOLDER, NOW, window_from)


# ---------------------------------------------------------------------------
# records.py -- parsing
# ---------------------------------------------------------------------------


class TestInternaldate:
    def test_offset_is_converted_to_utc(self):
        parsed = parse_internaldate("01-Jan-2026 10:00:00 +0200")
        assert parsed == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)

    def test_dovecot_space_padded_day(self):
        parsed = parse_internaldate(" 7-Jul-2026 02:44:25 -0700")
        assert parsed == datetime(2026, 7, 7, 9, 44, 25, tzinfo=UTC)

    def test_garbage_is_none(self):
        assert parse_internaldate("not a date") is None
        assert parse_internaldate(None) is None
        assert parse_internaldate("40-Jan-2026 10:00:00 +0000") is None


HEADER_BLOB = (
    b"Subject: =?utf-8?B?VMOpc3Q=?=\r\n"
    b"From: Alice <ALICE@Ext.com>\r\n"
    b"To: Me <me@corp.ng>\r\n"
    b"Cc: bob@ext.com\r\n"
    b"Date: Mon, 03 Aug 2026 09:00:00 +0000\r\n"
    b"Message-ID: <a1@x>\r\n"
    b"In-Reply-To: <a0@x>\r\n"
    b"References: <a-1@x> <a0@x>\r\n"
    b"Return-Path: <bounce@relay.example>\r\n"
    b"Content-Type: multipart/mixed; boundary=xyz\r\n"
    b"\r\n"
)


class TestFetchParsing:
    def test_full_record(self):
        prefix = (
            b"1 (UID 42 FLAGS (\\Seen \\Answered) "
            b'INTERNALDATE " 3-Aug-2026 10:00:00 +0100" RFC822.SIZE 2048 '
            b"BODY[HEADER.FIELDS (SUBJECT FROM TO)] {%d}" % len(HEADER_BLOB)
        )
        records, skipped = parse_fetch_response([(prefix, HEADER_BLOB), b")"], "INBOX", "inbox")
        assert skipped == 0
        assert len(records) == 1
        record = records[0]
        assert record.uid == "42"
        assert record.id == "INBOX:42"
        assert record.subject == "Tést"
        assert record.sender == "alice@ext.com"
        assert record.recipients == ("me@corp.ng", "bob@ext.com")
        assert record.seen and record.answered and not record.flagged
        assert record.size == 2048
        assert record.internal_date == datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        assert record.date == datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        assert record.message_id == "<a1@x>"
        assert record.in_reply_to == ("<a0@x>",)
        assert record.references == ("<a-1@x>", "<a0@x>")
        assert record.return_path == "bounce@relay.example"
        assert record.content_type == "multipart/mixed"

    def test_flags_after_literal_are_still_read(self):
        prefix = b"1 (UID 7 BODY[HEADER.FIELDS (SUBJECT)] {%d}" % len(HEADER_BLOB)
        records, _ = parse_fetch_response(
            [(prefix, HEADER_BLOB), b" FLAGS (\\Seen))"], "INBOX", "inbox"
        )
        assert records[0].seen is True

    def test_odd_message_is_skipped_and_counted(self, monkeypatch):
        from utils.insights import records as records_module

        def boom(*args, **kwargs):
            raise ValueError("odd message")

        monkeypatch.setattr(records_module, "record_from_parts", boom)
        records, skipped = parse_fetch_response(
            [(b"1 (UID 9 BODY[HEADER.FIELDS (SUBJECT)] {2}", b"x\n"), b")"], "INBOX", "inbox"
        )
        assert records == []
        assert skipped == 1

    def test_empty_response(self):
        assert parse_fetch_response(None, "INBOX", "inbox") == ([], 0)
        assert parse_fetch_response([b"1 (FLAGS ())"], "INBOX", "inbox") == ([], 0)


class TestClassifiers:
    def test_bounce_by_empty_return_path(self):
        assert is_bounce(rec(at=NOW, return_path=""))

    def test_bounce_by_mailer_daemon(self):
        assert is_bounce(rec(at=NOW, sender="MAILER-DAEMON@mail.example".lower()))
        assert is_bounce(rec(at=NOW, sender="MAILER-DAEMON@mail.example"))

    def test_bounce_by_delivery_status_report(self):
        record = rec(at=NOW, content_type="multipart/report", report_type="delivery-status")
        assert is_bounce(record)
        # multipart/report alone (an MDN, say) is not a bounce.
        assert not is_bounce(rec(at=NOW, content_type="multipart/report"))

    def test_ordinary_mail_is_not_a_bounce(self):
        assert not is_bounce(rec(at=NOW, return_path="alice@ext.com"))
        assert not is_bounce(rec(at=NOW))  # Return-Path absent entirely

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"list_id": True},
            {"list_unsubscribe": True},
            {"precedence": "bulk"},
            {"precedence": "list"},
            {"auto_submitted": "auto-generated"},
            {"auto_response_suppress": True},
            {"sender": "noreply@shop.example"},
            {"sender": "no-reply@shop.example"},
            {"sender": "donotreply@shop.example"},
            {"sender": "notifications@github.example"},
            {"sender": "bounces+42@sendgrid.example"},
            {"sender": "billing-noreply@cloud.example"},
            {"return_path": ""},
        ],
    )
    def test_automated_signals(self, kwargs):
        assert is_automated(rec(at=NOW, **kwargs))

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"auto_submitted": "no"},
            {"sender": "support@vendor.example"},
            {"sender": "info@vendor.example"},
            {"sender": "news@vendor.example"},
        ],
    )
    def test_human_mail_is_not_automated(self, kwargs):
        assert not is_automated(rec(at=NOW, **kwargs))

    def test_noreply_address(self):
        assert is_noreply_address("no-reply@x.example")
        assert not is_noreply_address("joel@x.example")
        assert not is_noreply_address(None)

    def test_attachment_bearing(self):
        assert is_attachment_bearing(rec(at=NOW, content_type="multipart/mixed"))
        assert is_attachment_bearing(rec(at=NOW, size=600_000))
        assert not is_attachment_bearing(rec(at=NOW, content_type="text/plain", size=4000))


# ---------------------------------------------------------------------------
# engine.py -- threading and reply matching
# ---------------------------------------------------------------------------


class TestThreadingAndReplies:
    def test_reply_chain_threads_and_samples(self):
        a = rec("inbox", at=NOW - timedelta(days=3), mid="<a@x>", seen=True)
        b = rec(
            "sent",
            at=NOW - timedelta(days=3) + timedelta(minutes=120),
            mid="<b@x>",
            irt=("<a@x>",),
            refs=("<a@x>",),
            sender=HOLDER,
            to=("alice@ext.com",),
        )
        c = rec(
            "inbox",
            at=NOW - timedelta(days=2),
            mid="<c@x>",
            irt=("<b@x>",),
            refs=(
                "<a@x>",
                "<b@x>",
            ),
        )
        prep = prepare([a, b, c])

        assert len(prep.threads) == 1
        assert prep.is_answered(a)
        assert not prep.is_answered(c)
        assert [round(s.minutes) for s in prep.your_replies] == [120]
        # Their reply time: c answers b, one day minus nothing = 1320 minutes
        # after b (22h).
        assert len(prep.their_replies) == 1
        assert prep.their_replies[0].original.id == b.id

    def test_duplicate_message_id_prefers_sent_copy(self):
        sent = rec("sent", at=NOW - timedelta(days=1), mid="<dup@x>", sender=HOLDER)
        echo = rec("inbox", at=NOW - timedelta(days=1), mid="<dup@x>", sender=HOLDER)
        prep = prepare([echo, sent])
        assert len(prep.outbound) == 1
        assert prep.outbound[0].id == sent.id
        assert prep.inbound == []

    def test_self_copy_in_inbox_is_set_aside(self):
        prep = prepare([rec("inbox", at=NOW - timedelta(days=1), sender=HOLDER)])
        assert prep.inbound == [] and prep.outbound == []

    def test_answered_flag_counts_as_answered(self):
        record = rec("inbox", at=NOW - timedelta(days=5), answered=True)
        prep = prepare([record])
        assert prep.is_answered(record)

    def test_follow_up_to_own_message_is_not_a_reply_sample(self):
        first = rec("sent", at=NOW - timedelta(days=2), mid="<s1@x>", sender=HOLDER)
        nudge = rec(
            "sent",
            at=NOW - timedelta(days=1),
            mid="<s2@x>",
            irt=("<s1@x>",),
            sender=HOLDER,
        )
        prep = prepare([first, nudge])
        assert prep.your_replies == []

    def test_window_filter_and_drafts_exemption(self):
        old = rec("inbox", at=NOW - timedelta(days=120))
        draft = rec("drafts", at=NOW - timedelta(days=200))
        prep = prepare([old, draft])
        assert prep.inbound == []
        assert len(prep.drafts) == 1


class TestAttentionBlock:
    def test_rates_and_awaiting(self):
        answered_in = rec("inbox", at=NOW - timedelta(days=20), mid="<a@x>")
        reply = rec(
            "sent",
            at=NOW - timedelta(days=20) + timedelta(minutes=30),
            irt=("<a@x>",),
            sender=HOLDER,
            to=("alice@ext.com",),
        )
        ignored = rec(
            "inbox",
            at=NOW - timedelta(days=10),
            mid="<b@x>",
            sender="bob@ext.com",
            subject="Ping",
            seen=True,
        )
        newsletter = rec(
            "inbox", at=NOW - timedelta(days=3), list_id=True, sender="deals@shop.example"
        )
        fresh = rec("inbox", at=NOW - timedelta(hours=2), mid="<f@x>", sender="eve@ext.com")

        prep = prepare([answered_in, reply, ignored, newsletter, fresh])
        block = engine.attention_block(prep, oldest_unread=NOW - timedelta(days=40))

        # 3 human inbound (newsletter excluded); 1 answered.
        assert block["response_rate"] == round(1 / 3, 3)
        assert block["read_never_answered"] == 1  # `ignored` (fresh is seen+young but unanswered)
        assert block["oldest_unread_days"] == 40

        waiting = {item["from"]: item for item in block["awaiting_you"]}
        assert "bob@ext.com" in waiting
        assert waiting["bob@ext.com"]["waiting_days"] == 10
        assert waiting["bob@ext.com"]["subject"] == "Ping"
        # Newsletter (automated) and 2h-old message are not "awaiting".
        assert "deals@shop.example" not in waiting
        assert "eve@ext.com" not in waiting

    def test_awaiting_them(self):
        unanswered_out = rec(
            "sent", at=NOW - timedelta(days=5), sender=HOLDER, to=("bob@ext.com",), subject="Offer"
        )
        noreply_out = rec(
            "sent", at=NOW - timedelta(days=5), sender=HOLDER, to=("no-reply@x.example",)
        )
        prep = prepare([unanswered_out, noreply_out])
        block = engine.attention_block(prep, oldest_unread=None)
        assert block["oldest_unread_days"] is None
        assert len(block["awaiting_them"]) == 1
        item = block["awaiting_them"][0]
        assert item["to"] == "bob@ext.com"
        assert item["waiting_days"] == 5
        assert item["subject"] == "Offer"

    def test_countered_outbound_is_not_awaiting(self):
        out = rec(
            "sent", at=NOW - timedelta(days=5), mid="<o@x>", sender=HOLDER, to=("bob@ext.com",)
        )
        counter = rec("inbox", at=NOW - timedelta(days=4), irt=("<o@x>",), sender="bob@ext.com")
        prep = prepare([out, counter])
        block = engine.attention_block(prep, oldest_unread=None)
        assert block["awaiting_them"] == []

    def test_backlog_growing(self):
        records = [rec("inbox", at=NOW - timedelta(days=80), sender="a@x.example")]
        records += [
            rec("inbox", at=NOW - timedelta(days=30, hours=i), sender=f"s{i}@x.example")
            for i in range(5)
        ]
        block = engine.attention_block(prepare(records), oldest_unread=None)
        assert block["backlog_trend"] == "growing"

    def test_backlog_shrinking(self):
        records = [
            rec(
                "inbox",
                at=NOW - timedelta(days=80, hours=i),
                mid=f"<sh{i}@x>",
                refs=("<sh0@x>",),
            )
            for i in range(6)
        ]
        records.append(
            rec(
                "sent",
                at=NOW - timedelta(days=10),
                irt=("<sh5@x>",),
                refs=("<sh0@x>",),
                sender=HOLDER,
                to=("alice@ext.com",),
            )
        )
        block = engine.attention_block(prepare(records), oldest_unread=None)
        assert block["backlog_trend"] == "shrinking"

    def test_backlog_flat_when_small(self):
        block = engine.attention_block(
            prepare([rec("inbox", at=NOW - timedelta(days=50))]), oldest_unread=None
        )
        assert block["backlog_trend"] == "flat"

    def test_block_omitted_when_empty(self):
        assert engine.attention_block(prepare([]), oldest_unread=None) is None


class TestResponseTimeBlock:
    def _pair(self, day, minutes, sender="alice@ext.com", n=None):
        mid = f"<q{next(_uids)}@x>"
        inbound = rec("inbox", at=NOW - timedelta(days=day), mid=mid, sender=sender)
        reply = rec(
            "sent",
            at=NOW - timedelta(days=day) + timedelta(minutes=minutes),
            irt=(mid,),
            sender=HOLDER,
            to=(sender,),
        )
        return [inbound, reply]

    def test_median_and_p90(self):
        records = []
        for minutes in (10, 20, 30, 40, 100):
            records += self._pair(30, minutes)
        block = engine.response_time_block(prepare(records))
        assert block["yours_median_minutes"] == 30
        assert block["yours_p90_minutes"] == 100
        assert block["trend"] == "flat"  # all samples in one half

    def test_trend_improving(self):
        records = []
        for i in range(3):
            records += self._pair(85 - i, 100)  # first half of a 90d window
        for i in range(3):
            records += self._pair(10 + i, 50)  # second half
        block = engine.response_time_block(prepare(records))
        assert block["trend"] == "improving"

    def test_their_leaderboards_require_two_samples(self):
        records = []
        # bob replies twice (fast), carol replies twice (slow), dave once.
        for day, minutes, sender in (
            (40, 5, "bob@x.example"),
            (30, 15, "bob@x.example"),
            (40, 300, "carol@x.example"),
            (30, 400, "carol@x.example"),
            (20, 60, "dave@x.example"),
        ):
            mid = f"<t{next(_uids)}@x>"
            records.append(
                rec("sent", at=NOW - timedelta(days=day), mid=mid, sender=HOLDER, to=(sender,))
            )
            records.append(
                rec(
                    "inbox",
                    at=NOW - timedelta(days=day) + timedelta(minutes=minutes),
                    irt=(mid,),
                    sender=sender,
                )
            )
        block = engine.response_time_block(prepare(records))
        emails = [item["email"] for item in block["fastest"]]
        assert emails[0] == "bob@x.example"
        assert "dave@x.example" not in emails
        assert block["slowest"][0]["email"] == "carol@x.example"
        assert block["theirs_median_minutes"] is not None

    def test_block_omitted_without_samples(self):
        assert engine.response_time_block(prepare([rec("inbox", at=NOW)])) is None


class TestRhythmBlock:
    def test_timezone_bucketing(self):
        # Sunday 23:30 UTC is Monday 00:30 in Lagos (UTC+1).
        arrival = rec("inbox", at=datetime(2026, 8, 30, 23, 30, tzinfo=UTC))
        block = engine.rhythm_block(prepare([arrival]), LAGOS, "Africa/Lagos", "deployment_default")
        assert block["arrivals_by_weekday_hour"][0][0] == 1  # Monday, 00:xx
        assert block["arrivals_by_weekday_hour"][6][23] == 0
        assert block["busiest_hour"] == "00:00"
        assert block["timezone"] == "Africa/Lagos"
        assert block["timezone_source"] == "deployment_default"

    def test_after_hours_share(self):
        weekend = rec(
            "sent",
            at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
            sender=HOLDER,
            to=("a@x.example",),
        )  # Saturday
        weekday = rec(
            "sent",
            at=datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
            sender=HOLDER,
            to=("a@x.example",),
        )  # Tuesday 10:00 Lagos
        block = engine.rhythm_block(
            prepare([weekend, weekday]), LAGOS, "Africa/Lagos", "deployment_default"
        )
        assert block["after_hours_send_share"] == 0.5

    def test_block_omitted_when_empty(self):
        assert engine.rhythm_block(prepare([]), LAGOS, "Africa/Lagos", "x") is None


class TestRelationshipsBlock:
    def test_one_way_dormant_internal_and_top(self):
        records = []
        # Three one-way messages from a sender never written to.
        records += [
            rec("inbox", at=NOW - timedelta(days=10 + i), sender="news@vendor.example")
            for i in range(3)
        ]
        # A dormant two-way correspondent: last contact 65 days ago.
        records.append(rec("inbox", at=NOW - timedelta(days=70), sender="old@friend.example"))
        records.append(rec("inbox", at=NOW - timedelta(days=68), sender="old@friend.example"))
        records.append(
            rec("sent", at=NOW - timedelta(days=65), sender=HOLDER, to=("old@friend.example",))
        )
        # An internal colleague.
        records.append(rec("inbox", at=NOW - timedelta(days=2), sender="bob@corp.ng"))

        block = engine.relationships_block(prepare(records))

        assert block["one_way_senders"] == [
            {"email": "news@vendor.example", "received": 3, "you_sent": 0}
        ]
        assert block["dormant"] == [
            {"email": "old@friend.example", "exchanged": 3, "months_since": 2}
        ]
        # Inbound: 6 total, 3 internal? No: internal = bob only (1 of 6);
        # outbound: 1, to friend.example (not internal). 1 of 7.
        assert block["internal_share"] == round(1 / 7, 3)
        top = block["top_correspondents"]
        assert len(top) == 1 and top[0]["email"] == "old@friend.example"
        assert top[0]["sent"] == 1 and top[0]["received"] == 2

    def test_block_omitted_when_empty(self):
        assert engine.relationships_block(prepare([])) is None


class TestHygieneBlock:
    def test_all_fields(self):
        records = [
            rec("inbox", at=NOW - timedelta(days=10), size=1_000_000),
            rec(  # a bounce, which is also automated
                "inbox",
                at=NOW - timedelta(days=9),
                size=48_576,
                return_path="",
                sender="mailer-daemon@relay.example",
            ),
            rec(
                "sent",
                at=NOW - timedelta(days=8),
                sender=HOLDER,
                size=2_000_000,
                to=("a@x.example",),
            ),
            rec("drafts", at=NOW - timedelta(days=10), size=100),
            rec("drafts", at=NOW - timedelta(days=200), size=100),
            rec("drafts", at=NOW - timedelta(days=2), size=100),
        ]
        # Two heavy multipart/mixed messages from one sender.
        records += [
            rec(
                "inbox",
                at=NOW - timedelta(days=5 + i),
                sender="heavy@x.example",
                content_type="multipart/mixed",
                size=600_000,
            )
            for i in range(2)
        ]

        block = engine.hygiene_block(prepare(records))

        total = 1_000_000 + 48_576 + 2_000_000 + 600_000 * 2
        assert block["monthly_growth_mb"] == round(total / 1_048_576 / (90 / 30.44), 1)
        assert block["bounces"] == 1
        assert block["stale_drafts"] == 2
        assert block["oldest_draft_days"] == 200
        # 4 inbound, 1 automated (the bounce).
        assert block["automated_share"] == 0.25
        assert block["attachment_heavy_senders"] == [
            {"email": "heavy@x.example", "mb": round(1_200_000 / 1_048_576, 1)}
        ]

    def test_block_omitted_when_empty(self):
        assert engine.hygiene_block(prepare([])) is None


class TestBuildRollup:
    def _build(self, records, **kwargs):
        return engine.build_rollup(
            records,
            holder=HOLDER,
            now=NOW,
            window_from=WINDOW_FROM,
            tz=LAGOS,
            tz_name="Africa/Lagos",
            tz_source="deployment_default",
            **kwargs,
        )

    def test_window_block_and_sections(self):
        records = [
            rec("inbox", at=NOW - timedelta(days=10)),
            rec("sent", at=NOW - timedelta(days=9), sender=HOLDER, to=("a@x.example",)),
        ]
        payload = self._build(records, window_meta={"label": "90d", "cached": False})
        assert payload["window"]["from"] == WINDOW_FROM.isoformat()
        assert payload["window"]["to"] == NOW.isoformat()
        assert payload["window"]["messages_considered"] == 2
        assert payload["window"]["label"] == "90d"
        for key in ("attention", "rhythm", "relationships", "hygiene"):
            assert key in payload
        assert "topics" not in payload

    def test_failed_block_is_omitted_not_fatal(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("surprise mailbox")

        monkeypatch.setattr(engine, "attention_block", boom)
        payload = self._build([rec("inbox", at=NOW - timedelta(days=1))])
        assert "attention" not in payload
        assert "hygiene" in payload

    def test_empty_mailbox_is_window_only(self):
        payload = self._build([])
        assert set(payload.keys()) == {"window"}


class TestContextSummaries:
    def test_other_party(self):
        outbound = rec("sent", at=NOW, sender=HOLDER, to=(HOLDER, "bob@x.example"))
        assert engine.other_party(outbound, HOLDER) == "bob@x.example"
        inbound = rec("inbox", at=NOW, sender="alice@x.example")
        assert engine.other_party(inbound, HOLDER) == "alice@x.example"
        self_only = rec("sent", at=NOW, sender=HOLDER, to=(HOLDER,))
        assert engine.other_party(self_only, HOLDER) is None

    def test_correspondent_summary(self):
        first = rec("inbox", at=NOW - timedelta(days=20), mid="<c1@x>", sender="alice@x.example")
        reply = rec(
            "sent",
            at=NOW - timedelta(days=20) + timedelta(minutes=60),
            irt=("<c1@x>",),
            sender=HOLDER,
            to=("alice@x.example",),
        )
        latest = rec("inbox", at=NOW - timedelta(days=5), mid="<c2@x>", sender="alice@x.example")
        summary = engine.correspondent_summary(
            [first, reply, latest], HOLDER, "alice@x.example", NOW
        )
        assert summary["messages_exchanged"] == 3
        assert summary["first_contact"] == first.internal_date.isoformat()
        assert summary["last_contact"] == latest.internal_date.isoformat()
        assert summary["you_replied_to_last"] is False
        assert summary["unanswered_from_them"] == 1
        assert summary["median_reply_minutes"] == 60

    def test_correspondent_summary_none_without_history(self):
        assert engine.correspondent_summary([], HOLDER, "x@y.example", NOW) is None

    def test_thread_summary(self):
        first = rec("inbox", at=NOW - timedelta(days=20), mid="<t1@x>")
        reply = rec(
            "sent",
            at=NOW - timedelta(days=20) + timedelta(minutes=60),
            irt=("<t1@x>",),
            sender=HOLDER,
            to=("alice@ext.com",),
        )
        latest = rec("inbox", at=NOW - timedelta(days=5), irt=(reply.message_id,))
        summary = engine.thread_summary([first, reply, latest], HOLDER, NOW)
        assert summary["messages"] == 3
        assert summary["spans_days"] == 15
        assert summary["your_last_reply"] == reply.date.isoformat()

    def test_thread_summary_dedupes_copies(self):
        sent = rec("sent", at=NOW - timedelta(days=1), mid="<dup2@x>", sender=HOLDER)
        echo = rec("inbox", at=NOW - timedelta(days=1), mid="<dup2@x>", sender=HOLDER)
        summary = engine.thread_summary([sent, echo], HOLDER, NOW)
        assert summary["messages"] == 1


# ---------------------------------------------------------------------------
# fetch.py -- pure pieces
# ---------------------------------------------------------------------------


class FakeListConn:
    def __init__(self, lines):
        self.lines = lines

    def list(self):
        return "OK", self.lines


class TestFolderRoles:
    def test_special_use_flags_win(self):
        conn = FakeListConn(
            [
                b'(\\HasNoChildren) "/" INBOX',
                b'(\\HasNoChildren \\Sent) "/" "Sent Messages"',
                b'(\\HasNoChildren \\Sent) "/" Sent',
                b'(\\HasNoChildren \\Drafts) "/" Drafts',
                b'(\\Noselect \\HasChildren) "/" Projects',
                b'(\\HasNoChildren) "/" "Projects/2026"',
            ]
        )
        roles = resolve_roles(conn)
        assert roles["inbox"] == ["INBOX"]
        assert roles["sent"] == ["Sent Messages", "Sent"]
        assert roles["drafts"] == ["Drafts"]

    def test_name_fallback_without_flags(self):
        conn = FakeListConn(
            [
                b'(\\HasNoChildren) "/" INBOX',
                b'(\\HasNoChildren) "/" Sent',
                b'(\\HasNoChildren) "/" Drafts',
            ]
        )
        roles = resolve_roles(conn)
        assert roles["sent"] == ["Sent"]
        assert roles["drafts"] == ["Drafts"]

    def test_role_for(self):
        roles = {"inbox": ["INBOX"], "sent": ["Sent"], "drafts": ["Drafts"]}
        assert role_for("INBOX", roles) == "inbox"
        assert role_for("Sent", roles) == "sent"
        assert role_for("Archive", roles) == "other"

    def test_imap_date(self):
        assert imap_date(datetime(2026, 6, 1, 5, 0, tzinfo=UTC)) == "1-Jun-2026"


# ---------------------------------------------------------------------------
# cache.py
# ---------------------------------------------------------------------------


class MiniRedis:
    """Just the redis surface cache.py touches."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def exists(self, key):
        return 1 if key in self.store else 0

    def delete(self, key):
        self.store.pop(key, None)

    def ttl(self, key):
        return 100 if key in self.store else -2

    def ping(self):
        return True


class BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis is down")

        return fail


@pytest.fixture
def mini_redis():
    client = MiniRedis()
    insights_cache.use_client(client)
    yield client
    insights_cache.use_client(MiniRedis())


class TestCache:
    def test_roundtrip(self, mini_redis):
        insights_cache.set_json("k", {"a": 1}, 60)
        assert insights_cache.get_json("k") == {"a": 1}
        assert insights_cache.get_json("missing") is None

    def test_try_acquire_is_once(self, mini_redis):
        assert insights_cache.try_acquire("lock", 60) is True
        assert insights_cache.try_acquire("lock", 60) is False
        insights_cache.release("lock")
        assert insights_cache.try_acquire("lock", 60) is True

    def test_degrades_when_redis_breaks(self, mini_redis):
        insights_cache.use_client(BrokenRedis())
        assert insights_cache.get_json("k") is None
        # Marked unavailable: further calls answer as an empty cache without
        # touching the broken client again (it is discarded).
        insights_cache.set_json("k", {"a": 1}, 60)
        assert insights_cache.get_json("k") is None
        assert insights_cache.try_acquire("lock", 60) is True

    def test_rollup_slot_busy(self, monkeypatch):
        monkeypatch.setattr(insights_cache, "ROLLUP_SLOT_WAIT_SECONDS", 0.01)
        taken = []
        try:
            for _ in range(insights_cache.MAX_CONCURRENT_ROLLUPS):
                assert insights_cache._rollup_slots.acquire(timeout=1)
                taken.append(1)
            with pytest.raises(insights_cache.InsightsBusyError):
                with insights_cache.rollup_slot():
                    pass
        finally:
            for _ in taken:
                insights_cache._rollup_slots.release()
        with insights_cache.rollup_slot():
            pass


# ---------------------------------------------------------------------------
# topics.py
# ---------------------------------------------------------------------------


class TestTopics:
    def test_normalise_subject(self):
        assert topics.normalise_subject("Re: Re: Fwd: Budget 2026") == "Budget 2026"
        assert topics.normalise_subject("[jira] #142 Deploy pipeline") == "Deploy pipeline"
        assert topics.normalise_subject(None) == ""

    def test_subject_sample_dedupes_recent_first(self):
        records = [
            rec("inbox", at=NOW - timedelta(days=3), subject="Weekly sync"),
            rec("inbox", at=NOW - timedelta(days=2), subject="Re: Weekly sync"),
            rec("inbox", at=NOW - timedelta(days=1), subject="Invoice 42"),
        ]
        assert topics.subject_sample(records) == ["Invoice 42", "Weekly sync"]

    def test_parse_topics(self):
        text = (
            '```json\n[{"topic": "Invoices", "share": 0.5}, {"topic": "Hiring", "share": 0.3}]```'
        )
        parsed = topics.parse_topics(text)
        assert parsed == [
            {"label": "Invoices", "share": 0.5},
            {"label": "Hiring", "share": 0.3},
        ]
        assert topics.parse_topics("no json here") is None
        assert topics.parse_topics(None) is None

    def test_parse_topics_normalises_excess_shares(self):
        parsed = topics.parse_topics('[{"topic":"A","share":1.5},{"topic":"B","share":0.5}]')
        assert sum(t["share"] for t in parsed) == pytest.approx(1.0, abs=0.01)

    def test_disabled_without_opt_in(self, monkeypatch):
        monkeypatch.setenv("AI_BASE_URL", "https://ai.example")
        monkeypatch.setenv("AI_API_KEY", "k")
        monkeypatch.delenv("INSIGHTS_TOPICS_ENABLED", raising=False)
        assert topics.ai_configured() is True
        assert topics.topics_enabled() is False
        assert topics.compute_topics([]) is None

    def test_enabled_with_both_gates(self, monkeypatch):
        monkeypatch.setenv("AI_BASE_URL", "https://ai.example")
        monkeypatch.setenv("AI_API_KEY", "k")
        monkeypatch.setenv("INSIGHTS_TOPICS_ENABLED", "true")
        assert topics.topics_enabled() is True
        monkeypatch.setenv("AI_BASE_URL", "")
        assert topics.topics_enabled() is False


# ---------------------------------------------------------------------------
# routes/mailbox_insights.py
# ---------------------------------------------------------------------------


MAILBOX = {
    "email": HOLDER,
    "email_account_id": "01MBOXULID00000000000000AA",
    "organization_id": "01ORGULID0000000000000000A",
    "session_id": "01SESSULID00000000000000AA",
}


class TestInsightsRoute:
    def test_compute_then_cache(self, mini_redis, monkeypatch):
        calls = []

        def fake_compute(mailbox, window, days):
            calls.append((window, days))
            return {"window": {"label": window, "cached": False}, "hygiene": {"bounces": 0}}

        monkeypatch.setattr(mailbox_insights, "_compute_rollup", fake_compute)

        first = mailbox_insights.insights(window="90d", refresh=False, mailbox=MAILBOX)
        assert first["type"] == "success"
        assert first["data"]["window"]["cached"] is False
        assert calls == [("90d", 90)]

        second = mailbox_insights.insights(window="90d", refresh=False, mailbox=MAILBOX)
        assert second["data"]["window"]["cached"] is True
        assert second["data"]["hygiene"] == {"bounces": 0}
        assert len(calls) == 1  # served from cache

        # A different window is its own cache entry.
        mailbox_insights.insights(window="30d", refresh=False, mailbox=MAILBOX)
        assert calls[-1] == ("30d", 30)

    def test_refresh_is_throttled_per_mailbox(self, mini_redis, monkeypatch):
        calls = []

        def fake_compute(mailbox, window, days):
            calls.append(window)
            return {"window": {"cached": False}}

        monkeypatch.setattr(mailbox_insights, "_compute_rollup", fake_compute)

        mailbox_insights.insights(window="90d", refresh=True, mailbox=MAILBOX)
        assert len(calls) == 1
        throttled = mailbox_insights.insights(window="90d", refresh=True, mailbox=MAILBOX)
        assert len(calls) == 1  # second refresh inside the interval: cache served
        assert throttled["data"]["window"]["cached"] is True

    def test_imap_down_is_502(self, mini_redis, monkeypatch):
        def fake_compute(mailbox, window, days):
            raise ImapUnavailableError("Mail server unavailable")

        monkeypatch.setattr(mailbox_insights, "_compute_rollup", fake_compute)
        with pytest.raises(HTTPException) as excinfo:
            mailbox_insights.insights(window="90d", refresh=False, mailbox=MAILBOX)
        assert excinfo.value.status_code == 502

    def test_busy_is_503(self, mini_redis, monkeypatch):
        def fake_compute(mailbox, window, days):
            raise insights_cache.InsightsBusyError()

        monkeypatch.setattr(mailbox_insights, "_compute_rollup", fake_compute)
        with pytest.raises(HTTPException) as excinfo:
            mailbox_insights.insights(window="90d", refresh=False, mailbox=MAILBOX)
        assert excinfo.value.status_code == 503


class _FakeImapSession:
    def __init__(self, email_address):
        pass

    def __enter__(self):
        return object()

    def __exit__(self, *args):
        return False


class TestContextRoute:
    def test_context_payload(self, mini_redis, monkeypatch):
        seed = rec(
            "inbox",
            at=NOW - timedelta(days=3),
            mid="<seed@x>",
            sender="alice@ext.com",
            folder="INBOX",
        )
        older = rec("inbox", at=NOW - timedelta(days=20), mid="<h1@x>", sender="alice@ext.com")
        reply = rec(
            "sent",
            at=NOW - timedelta(days=20) + timedelta(minutes=45),
            irt=("<h1@x>",),
            sender=HOLDER,
            to=("alice@ext.com",),
        )

        monkeypatch.setattr(mailbox_insights, "imap_session", _FakeImapSession)
        monkeypatch.setattr(
            mailbox_insights.fetch,
            "resolve_roles",
            lambda conn: {"inbox": ["INBOX"], "sent": ["Sent"], "drafts": ["Drafts"]},
        )
        monkeypatch.setattr(
            mailbox_insights.fetch, "fetch_one", lambda conn, folder, role, uid: seed
        )
        monkeypatch.setattr(
            mailbox_insights.fetch,
            "collect_correspondent",
            lambda conn, roles, address, deadline: [older, reply],
        )
        monkeypatch.setattr(
            mailbox_insights.fetch,
            "collect_thread",
            lambda conn, roles, s, deadline: [seed],
        )

        response = mailbox_insights.message_context(f"INBOX:{seed.uid}", mailbox=MAILBOX)
        data = response["data"]
        assert data["message_id"] == seed.id
        correspondent = data["correspondent"]
        assert correspondent["email"] == "alice@ext.com"
        assert correspondent["messages_exchanged"] == 3
        assert correspondent["median_reply_minutes"] == 45
        assert correspondent["you_replied_to_last"] is False
        thread = data["thread"]
        assert thread["messages"] == 1
        assert thread["your_last_reply"] is None
        # No "ai" key: gating is owned elsewhere and these blocks never wait on it.
        assert "ai" not in data

    def test_malformed_id_is_400(self, mini_redis):
        with pytest.raises(HTTPException) as excinfo:
            mailbox_insights.message_context("INBOX:notanumber", mailbox=MAILBOX)
        assert excinfo.value.status_code == 400

    def test_missing_message_is_404(self, mini_redis, monkeypatch):
        monkeypatch.setattr(mailbox_insights, "imap_session", _FakeImapSession)
        monkeypatch.setattr(
            mailbox_insights.fetch, "resolve_roles", lambda conn: {"inbox": ["INBOX"]}
        )
        monkeypatch.setattr(
            mailbox_insights.fetch, "fetch_one", lambda conn, folder, role, uid: None
        )
        with pytest.raises(HTTPException) as excinfo:
            mailbox_insights.message_context("INBOX:999", mailbox=MAILBOX)
        assert excinfo.value.status_code == 404
