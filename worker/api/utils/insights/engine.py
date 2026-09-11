"""
The insights aggregation engine: HeaderRecords in, spec blocks out.

Pure functions over parsed records -- no IMAP, no database, no clock reads.
The route fetches and passes `now`; the tests hand in synthetic records and
exact expectations.

Threading is RFC 5322 only: two messages share a conversation when they share
any Message-ID / In-Reply-To / References token, connected transitively
(union-find). Never subjects -- subject matching is what collapses unrelated
"Re: hello" messages into one thread, the same stance thread_for takes.

**Every block is independent.** build_rollup computes each inside its own
guard; a block that fails (or has nothing to say) is OMITTED, never
null-filled, and the client hides that section. One surprising mailbox must
not blank the whole page.

Thresholds live at the top of this file and are documented in
docs/api/mailbox-insights.md; change them in both places.
"""

from __future__ import annotations

import logging
import math
import statistics
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

from .records import (
    HeaderRecord,
    is_attachment_bearing,
    is_automated,
    is_bounce,
    is_noreply_address,
)

logger = logging.getLogger(__name__)

# --- Thresholds (documented in docs/api/mailbox-insights.md) ---------------

# A message is "awaiting" only once it has waited a full day; three hours of
# silence is not a backlog item.
AWAITING_MIN_HOURS = 24
# Backlog snapshots ignore mail younger than this: recent messages have not
# had a fair chance to be answered, and counting them calls every busy
# morning "growing".
BACKLOG_GRACE_HOURS = 48
# Trends need a >=15% change AND a >=3-message absolute change to leave "flat".
TREND_RATIO = 0.15
TREND_MIN_ABS = 3
# A reply-time trend needs at least this many samples in EACH half.
TREND_MIN_SAMPLES = 3
# A reply more than 30 days after the message it answers is a revived thread,
# not a response time; it would dominate the p90 while describing nothing.
REPLY_SAMPLE_MAX_MINUTES = 30 * 24 * 60
# Business hours for after_hours_send_share: Mon-Fri, 08:00-17:59 local.
BUSINESS_START_HOUR = 8
BUSINESS_END_HOUR = 18
STALE_DRAFT_DAYS = 7
DORMANT_MIN_MONTHS = 2
DORMANT_MIN_EXCHANGED = 3
ONE_WAY_MIN_RECEIVED = 3
LIST_LIMIT = 10
LEADERBOARD_LIMIT = 5
LEADERBOARD_MIN_SAMPLES = 2
ATTACHMENT_HEAVY_MIN_MB = 1.0

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# When one Message-ID shows up in several folders (a message sent to yourself,
# a list echoing your own post back), the Sent copy wins: it is the one that
# says what the holder DID.
_ROLE_PREFERENCE = {"sent": 0, "inbox": 1, "other": 2, "drafts": 3}


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------


def median_minutes(samples: list[float]) -> int | None:
    return round(statistics.median(samples)) if samples else None


def percentile_minutes(samples: list[float], p: float) -> int | None:
    """Nearest-rank percentile -- an actual observed value, not an interpolation."""
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(0, math.ceil(p * len(ordered)) - 1)
    return round(ordered[rank])


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _days_between(older: datetime, newer: datetime) -> int:
    return max(0, (newer - older).days)


# ---------------------------------------------------------------------------
# Preparation: dedupe, classify, thread, match replies
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        root = self.parent.setdefault(item, item)
        if root != item:
            root = self.find(root)
            self.parent[item] = root
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class ReplySample:
    reply: HeaderRecord
    original: HeaderRecord
    minutes: float


@dataclass
class Prepared:
    holder: str
    domain: str
    now: datetime
    window_from: datetime | None
    inbound: list[HeaderRecord] = field(default_factory=list)
    human_inbound: list[HeaderRecord] = field(default_factory=list)
    outbound: list[HeaderRecord] = field(default_factory=list)
    drafts: list[HeaderRecord] = field(default_factory=list)
    threads: dict[str, list[HeaderRecord]] = field(default_factory=dict)
    thread_of: dict[str, str] = field(default_factory=dict)
    # inbound record id -> the first outbound message dated after it in its
    # thread. Presence means "the holder replied to this".
    answered_by: dict[str, HeaderRecord] = field(default_factory=dict)
    # outbound record id -> the first HUMAN inbound dated after it in its
    # thread. Presence means "they came back after this".
    countered_by: dict[str, HeaderRecord] = field(default_factory=dict)
    your_replies: list[ReplySample] = field(default_factory=list)
    their_replies: list[ReplySample] = field(default_factory=list)

    def is_answered(self, record: HeaderRecord) -> bool:
        # The \Answered flag covers replies sent from OTHER clients, whose
        # copies may not be in this server's Sent at all.
        return record.answered or record.id in self.answered_by


def _dedupe(records: list[HeaderRecord]) -> list[HeaderRecord]:
    seen: set[str] = set()
    out: list[HeaderRecord] = []
    for record in sorted(records, key=lambda r: _ROLE_PREFERENCE.get(r.role, 9)):
        if record.message_id:
            if record.message_id in seen:
                continue
            seen.add(record.message_id)
        out.append(record)
    return out


def _reply_original(
    reply: HeaderRecord,
    thread: list[HeaderRecord],
    by_message_id: dict[str, HeaderRecord],
    target_ids: set[str],
) -> HeaderRecord | None:
    """The message `reply` answers, if that message is in `target_ids`.

    In-Reply-To is authoritative when it resolves: if it points at a known
    message that is NOT a target (a follow-up to one's own message, say), the
    answer is None -- falling through to the thread would misattribute it.
    Only when In-Reply-To is absent or unresolvable do the nearest References
    token and finally "latest target earlier in the thread" stand in.
    """
    when = reply.effective_date
    if when is None:
        return None
    for token in reply.in_reply_to:
        candidate = by_message_id.get(token)
        if candidate is None:
            continue
        good = (
            candidate.id in target_ids
            and candidate.effective_date is not None
            and candidate.effective_date < when
        )
        return candidate if good else None
    for token in reversed(reply.references):
        candidate = by_message_id.get(token)
        if (
            candidate is not None
            and candidate.id in target_ids
            and candidate.effective_date is not None
            and candidate.effective_date < when
        ):
            return candidate
    prior = [
        m
        for m in thread
        if m.id in target_ids and m.effective_date is not None and m.effective_date < when
    ]
    return prior[-1] if prior else None


def _first_after(candidates: list[HeaderRecord], when: datetime) -> HeaderRecord | None:
    """First of `candidates` (sorted by effective_date) strictly after `when`."""
    dates = [c.effective_date for c in candidates]
    index = bisect_right(dates, when)
    return candidates[index] if index < len(candidates) else None


def prepare(
    records: list[HeaderRecord],
    holder: str,
    now: datetime,
    window_from: datetime | None,
) -> Prepared:
    holder = holder.lower()
    prep = Prepared(
        holder=holder, domain=holder.rpartition("@")[2], now=now, window_from=window_from
    )

    slop = now + timedelta(days=1)  # clock-skew tolerance; beyond it the date is garbage
    for record in _dedupe(records):
        when = record.effective_date
        if when is None or when > slop or record.deleted:
            continue
        if record.role == "drafts":
            prep.drafts.append(record)  # drafts are deliberately not windowed
            continue
        if window_from is not None and when < window_from:
            continue
        if record.role == "sent" or (record.role == "other" and record.sender == holder):
            prep.outbound.append(record)
        elif record.sender == holder:
            # In INBOX but From the holder: a self-copy, a list echo, or a
            # spoof. Neither the holder's sending behaviour nor mail awaiting
            # them -- set aside entirely.
            continue
        else:
            prep.inbound.append(record)

    prep.human_inbound = [r for r in prep.inbound if not is_automated(r)]

    # --- threads ---
    uf = _UnionFind()
    conversational = prep.inbound + prep.outbound
    for record in conversational:
        own = record.message_id or record.id
        for token in (*record.in_reply_to, *record.references):
            uf.union(own, token)
    threads: dict[str, list[HeaderRecord]] = defaultdict(list)
    for record in conversational:
        thread_id = uf.find(record.message_id or record.id)
        prep.thread_of[record.id] = thread_id
        threads[thread_id].append(record)
    for members in threads.values():
        members.sort(key=lambda r: r.effective_date)
    prep.threads = dict(threads)

    # --- reply matching ---
    by_message_id = {r.message_id: r for r in conversational if r.message_id}
    outbound_ids = {r.id for r in prep.outbound}
    human_inbound_ids = {r.id for r in prep.human_inbound}

    for thread in prep.threads.values():
        outs = [m for m in thread if m.id in outbound_ids]
        humans = [m for m in thread if m.id in human_inbound_ids]
        for member in thread:
            if member.id in outbound_ids:
                counter = _first_after(humans, member.effective_date)
                if counter is not None:
                    prep.countered_by[member.id] = counter
            else:
                answer = _first_after(outs, member.effective_date)
                if answer is not None:
                    prep.answered_by[member.id] = answer

        for out in outs:
            original = _reply_original(out, thread, by_message_id, human_inbound_ids)
            if original is None:
                continue
            minutes = (out.effective_date - original.effective_date).total_seconds() / 60
            if 0 < minutes <= REPLY_SAMPLE_MAX_MINUTES:
                prep.your_replies.append(ReplySample(out, original, minutes))
        for human in humans:
            original = _reply_original(human, thread, by_message_id, outbound_ids)
            if original is None:
                continue
            minutes = (human.effective_date - original.effective_date).total_seconds() / 60
            if 0 < minutes <= REPLY_SAMPLE_MAX_MINUTES:
                prep.their_replies.append(ReplySample(human, original, minutes))

    return prep


# ---------------------------------------------------------------------------
# Blocks -- each returns None to mean "omit my section"
# ---------------------------------------------------------------------------


def _trend(first: int, second: int, *, growing: str, shrinking: str) -> str:
    if abs(second - first) < TREND_MIN_ABS:
        return "flat"
    if second > first * (1 + TREND_RATIO):
        return growing
    if second < first * (1 - TREND_RATIO):
        return shrinking
    return "flat"


def attention_block(prep: Prepared, oldest_unread: datetime | None) -> dict | None:
    population = prep.human_inbound
    if not population and not prep.outbound:
        return None

    answered = sum(1 for r in population if prep.is_answered(r))
    # Same freshness cutoff as the awaiting lists: a message read two hours
    # ago is not yet "never answered" -- it joins the guilt pile only once
    # it has sat unanswered past the awaiting window.
    _guilt_cutoff = prep.now - timedelta(hours=AWAITING_MIN_HOURS)
    read_never_answered = sum(
        1
        for r in population
        if r.seen and not prep.is_answered(r) and r.effective_date <= _guilt_cutoff
    )

    # Backlog at time t: human inbound that had arrived (with 48h grace) and
    # was not yet answered at t. Reconstructable because each answer's own
    # date is known; comparing mid-window to now says which way it moves.
    grace = timedelta(hours=BACKLOG_GRACE_HOURS)

    def backlog_at(when: datetime) -> int:
        count = 0
        for record in population:
            if record.effective_date > when - grace:
                continue
            if record.answered:  # flag carries no date; treat as always answered
                continue
            answer = prep.answered_by.get(record.id)
            if answer is None or answer.effective_date > when:
                count += 1
        return count

    if prep.window_from is not None:
        midpoint = prep.window_from + (prep.now - prep.window_from) / 2
        backlog_trend = _trend(
            backlog_at(midpoint), backlog_at(prep.now), growing="growing", shrinking="shrinking"
        )
    else:
        backlog_trend = "flat"

    awaiting_cutoff = prep.now - timedelta(hours=AWAITING_MIN_HOURS)

    awaiting_you: list[dict] = []
    awaiting_them: list[dict] = []
    human_inbound_ids = {r.id for r in prep.human_inbound}
    outbound_ids = {r.id for r in prep.outbound}
    for thread in prep.threads.values():
        open_inbound = [
            m
            for m in thread
            if m.id in human_inbound_ids
            and not prep.is_answered(m)
            and m.effective_date <= awaiting_cutoff
        ]
        if open_inbound:
            newest = open_inbound[-1]
            awaiting_you.append(
                {
                    "message_id": newest.id,
                    "from": newest.sender,
                    "subject": newest.subject or None,
                    # Waiting since the FIRST unanswered message, shown on the
                    # latest one -- that is how long they have actually waited.
                    "waiting_days": _days_between(open_inbound[0].effective_date, prep.now),
                }
            )

        open_outbound = [
            m
            for m in thread
            if m.id in outbound_ids
            and m.id not in prep.countered_by
            and m.effective_date <= awaiting_cutoff
        ]
        # Only the LAST message of the thread being outbound means the ball is
        # in their court.
        if open_outbound and thread[-1].id == open_outbound[-1].id:
            newest = open_outbound[-1]
            recipients = [
                a for a in newest.recipients if a != prep.holder and not is_noreply_address(a)
            ]
            if recipients:
                awaiting_them.append(
                    {
                        "message_id": newest.id,
                        "to": recipients[0],
                        "subject": newest.subject or None,
                        "waiting_days": _days_between(newest.effective_date, prep.now),
                    }
                )

    awaiting_you.sort(key=lambda i: -i["waiting_days"])
    awaiting_them.sort(key=lambda i: -i["waiting_days"])

    return {
        "response_rate": _share(answered, len(population)),
        "read_never_answered": read_never_answered,
        "oldest_unread_days": (
            _days_between(oldest_unread, prep.now) if oldest_unread is not None else None
        ),
        "backlog_trend": backlog_trend,
        "awaiting_you": awaiting_you[:LIST_LIMIT],
        "awaiting_them": awaiting_them[:LIST_LIMIT],
    }


def response_time_block(prep: Prepared) -> dict | None:
    yours = [s.minutes for s in prep.your_replies]
    theirs = [s.minutes for s in prep.their_replies]
    if not yours and not theirs:
        return None

    trend = "flat"
    if prep.window_from is not None and yours:
        midpoint = prep.window_from + (prep.now - prep.window_from) / 2
        first = [s.minutes for s in prep.your_replies if s.reply.effective_date < midpoint]
        second = [s.minutes for s in prep.your_replies if s.reply.effective_date >= midpoint]
        if len(first) >= TREND_MIN_SAMPLES and len(second) >= TREND_MIN_SAMPLES:
            first_median, second_median = statistics.median(first), statistics.median(second)
            if second_median < first_median * (1 - TREND_RATIO):
                trend = "improving"
            elif second_median > first_median * (1 + TREND_RATIO):
                trend = "worsening"

    by_correspondent: dict[str, list[float]] = defaultdict(list)
    for sample in prep.their_replies:
        if sample.reply.sender:
            by_correspondent[sample.reply.sender].append(sample.minutes)
    ranked = sorted(
        (
            {"email": email, "median_minutes": round(statistics.median(samples))}
            for email, samples in by_correspondent.items()
            if len(samples) >= LEADERBOARD_MIN_SAMPLES
        ),
        key=lambda item: item["median_minutes"],
    )

    return {
        "yours_median_minutes": median_minutes(yours),
        "yours_p90_minutes": percentile_minutes(yours, 0.9),
        "theirs_median_minutes": median_minutes(theirs),
        "trend": trend,
        "fastest": ranked[:LEADERBOARD_LIMIT],
        "slowest": ranked[::-1][:LEADERBOARD_LIMIT],
    }


def rhythm_block(prep: Prepared, tz: tzinfo, tz_name: str, tz_source: str) -> dict | None:
    if not prep.inbound and not prep.outbound:
        return None

    grid = [[0] * 24 for _ in range(7)]
    for record in prep.inbound:  # every arrival, automated included -- rhythm is volume
        local = record.effective_date.astimezone(tz)
        grid[local.weekday()][local.hour] += 1

    after_hours = 0
    for record in prep.outbound:
        local = record.effective_date.astimezone(tz)
        if local.weekday() >= 5 or not (BUSINESS_START_HOUR <= local.hour < BUSINESS_END_HOUR):
            after_hours += 1

    hour_totals = [sum(grid[day][hour] for day in range(7)) for hour in range(24)]
    busiest_hour = (
        f"{max(range(24), key=hour_totals.__getitem__):02d}:00" if any(hour_totals) else None
    )
    workday_totals = [sum(grid[day]) for day in range(5)]  # Mon-Fri: "weekday" means workday
    quietest_weekday = (
        _WEEKDAYS[min(range(5), key=workday_totals.__getitem__)] if prep.inbound else None
    )

    return {
        "arrivals_by_weekday_hour": grid,
        "after_hours_send_share": _share(after_hours, len(prep.outbound)),
        "busiest_hour": busiest_hour,
        "quietest_weekday": quietest_weekday,
        "timezone": tz_name,
        "timezone_source": tz_source,
    }


def relationships_block(prep: Prepared) -> dict | None:
    stats: dict[str, dict] = {}

    def entry(address: str) -> dict:
        return stats.setdefault(
            address,
            {"sent": 0, "received": 0, "first": None, "last": None, "reply_minutes": []},
        )

    def touch(item: dict, when: datetime) -> None:
        if item["first"] is None or when < item["first"]:
            item["first"] = when
        if item["last"] is None or when > item["last"]:
            item["last"] = when

    for record in prep.inbound:
        if not record.sender:
            continue
        item = entry(record.sender)
        item["received"] += 1
        touch(item, record.effective_date)
    for record in prep.outbound:
        for address in dict.fromkeys(record.recipients):
            if address == prep.holder:
                continue
            item = entry(address)
            item["sent"] += 1
            touch(item, record.effective_date)
    for sample in prep.your_replies:
        if sample.original.sender:
            entry(sample.original.sender)["reply_minutes"].append(sample.minutes)

    if not stats:
        return None

    one_way = sorted(
        (
            {"email": email, "received": item["received"], "you_sent": 0}
            for email, item in stats.items()
            if item["sent"] == 0 and item["received"] >= ONE_WAY_MIN_RECEIVED
        ),
        key=lambda i: -i["received"],
    )

    dormant = []
    for email, item in stats.items():
        exchanged = item["sent"] + item["received"]
        months_since = (prep.now - item["last"]).days // 30
        if (
            item["sent"] >= 1
            and item["received"] >= 1
            and exchanged >= DORMANT_MIN_EXCHANGED
            and months_since >= DORMANT_MIN_MONTHS
        ):
            dormant.append({"email": email, "exchanged": exchanged, "months_since": months_since})
    dormant.sort(key=lambda i: (-i["months_since"], -i["exchanged"]))

    internal = sum(1 for r in prep.inbound if r.sender_domain == prep.domain)
    outbound_counted = 0
    for record in prep.outbound:
        others = [a for a in record.recipients if a != prep.holder]
        if not others:
            continue
        outbound_counted += 1
        if all(a.rpartition("@")[2] == prep.domain for a in others):
            internal += 1

    top = sorted(
        (
            {
                "email": email,
                "sent": item["sent"],
                "received": item["received"],
                "median_reply_minutes": median_minutes(item["reply_minutes"]),
            }
            for email, item in stats.items()
            if item["sent"] >= 1 and item["received"] >= 1
        ),
        key=lambda i: -(i["sent"] + i["received"]),
    )

    return {
        "one_way_senders": one_way[:LIST_LIMIT],
        "dormant": dormant[:LIST_LIMIT],
        "internal_share": _share(internal, len(prep.inbound) + outbound_counted),
        "top_correspondents": top[:LIST_LIMIT],
    }


def hygiene_block(prep: Prepared) -> dict | None:
    windowed = prep.inbound + prep.outbound
    if not windowed and not prep.drafts:
        return None

    months = 1.0
    if prep.window_from is not None:
        months = max((prep.now - prep.window_from).days, 1) / 30.44
    total_bytes = sum(r.size for r in windowed)

    stale_cutoff = prep.now - timedelta(days=STALE_DRAFT_DAYS)
    stale_drafts = sum(1 for d in prep.drafts if d.effective_date <= stale_cutoff)
    oldest_draft = min((d.effective_date for d in prep.drafts), default=None)

    heavy: dict[str, int] = defaultdict(int)
    for record in prep.inbound:
        if record.sender and is_attachment_bearing(record):
            heavy[record.sender] += record.size
    attachment_heavy = sorted(
        (
            {"email": email, "mb": round(size / 1_048_576, 1)}
            for email, size in heavy.items()
            if size / 1_048_576 >= ATTACHMENT_HEAVY_MIN_MB
        ),
        key=lambda i: -i["mb"],
    )

    return {
        "monthly_growth_mb": round(total_bytes / 1_048_576 / months, 1),
        "bounces": sum(1 for r in prep.inbound if is_bounce(r)),
        "stale_drafts": stale_drafts,
        "oldest_draft_days": (
            _days_between(oldest_draft, prep.now) if oldest_draft is not None else None
        ),
        "automated_share": _share(len(prep.inbound) - len(prep.human_inbound), len(prep.inbound)),
        "attachment_heavy_senders": attachment_heavy[:LEADERBOARD_LIMIT],
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_rollup(
    records: list[HeaderRecord],
    *,
    holder: str,
    now: datetime,
    window_from: datetime,
    tz: tzinfo,
    tz_name: str,
    tz_source: str,
    oldest_unread: datetime | None = None,
    window_meta: dict | None = None,
) -> dict:
    """The full insights payload. Blocks that fail or are empty are omitted."""
    prep = prepare(records, holder, now, window_from)

    payload: dict = {
        "window": {
            "from": window_from.isoformat(),
            # Compute time, per spec 18d: a cached rollup shows how old it is.
            "to": now.isoformat(),
            "messages_considered": len(records),
            **(window_meta or {}),
        }
    }

    def guard(name: str, producer) -> None:
        try:
            block = producer()
        except Exception:
            logger.exception("Insights block %r failed; omitting it", name)
            return
        if block is not None:
            payload[name] = block

    guard("attention", lambda: attention_block(prep, oldest_unread))
    guard("response_time", lambda: response_time_block(prep))
    guard("rhythm", lambda: rhythm_block(prep, tz, tz_name, tz_source))
    guard("relationships", lambda: relationships_block(prep))
    guard("hygiene", lambda: hygiene_block(prep))
    # topics is appended by the route when (and only when) it is enabled,
    # configured and produced something -- see utils/insights/topics.py.
    return payload


# ---------------------------------------------------------------------------
# /messages/{id}/context -- the two header blocks
# ---------------------------------------------------------------------------


def other_party(record: HeaderRecord, holder: str) -> str | None:
    """The correspondent a message is 'with', from the holder's side."""
    holder = holder.lower()
    if record.role == "sent" or record.sender == holder:
        for address in record.recipients:
            if address != holder:
                return address
        return None
    return record.sender


def correspondent_summary(
    records: list[HeaderRecord], holder: str, address: str, now: datetime
) -> dict | None:
    """The correspondent block: history with one address, all headers."""
    address = address.lower()
    prep = prepare(records, holder, now, window_from=None)

    from_them = [r for r in prep.inbound if r.sender == address]
    to_them = [r for r in prep.outbound if address in r.recipients]
    if not from_them and not to_them:
        return None

    dates = [r.effective_date for r in from_them + to_them]
    last_from_them = max(from_them, key=lambda r: r.effective_date, default=None)
    reply_samples = [s.minutes for s in prep.your_replies if s.original.sender == address]

    return {
        "email": address,
        "messages_exchanged": len(from_them) + len(to_them),
        "first_contact": min(dates).isoformat(),
        "last_contact": max(dates).isoformat(),
        "you_replied_to_last": (
            prep.is_answered(last_from_them) if last_from_them is not None else None
        ),
        "unanswered_from_them": sum(1 for r in from_them if not prep.is_answered(r)),
        "median_reply_minutes": median_minutes(reply_samples),
    }


def thread_summary(records: list[HeaderRecord], holder: str, now: datetime) -> dict | None:
    """The thread block: shape of one conversation."""
    holder = holder.lower()
    dated = {r.id: r for r in records if r.effective_date is not None}
    if not dated:
        return None
    unique: dict[str, HeaderRecord] = {}
    for record in sorted(dated.values(), key=lambda r: _ROLE_PREFERENCE.get(r.role, 9)):
        unique.setdefault(record.message_id or record.id, record)

    members = sorted(unique.values(), key=lambda r: r.effective_date)
    yours = [m for m in members if m.role == "sent" or (m.role != "inbox" and m.sender == holder)]
    return {
        "messages": len(members),
        "spans_days": _days_between(members[0].effective_date, members[-1].effective_date),
        "your_last_reply": yours[-1].effective_date.isoformat() if yours else None,
    }
