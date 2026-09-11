"""
Optional "topics" block: what the mailbox's conversations are about.

The only part of insights that is not header arithmetic, and the only part
that leaves the server: a sample of SUBJECT LINES (never bodies, never
addresses) goes to the deployment's OpenAI-compatible endpoint, the same one
routes/mailbox.py's compose/summarise features use. It exists only when

1. that endpoint is configured (AI_BASE_URL + AI_API_KEY -- the same check
   as mailbox.py's _ai_configured(), replicated rather than imported so this
   module has no dependency on a router), AND
2. the operator has opted the deployment in with INSIGHTS_TOPICS_ENABLED=true.

The second gate is deliberate and defaults OFF. Compose and summarise send
text to the AI at the holder's explicit click; a rollup runs on its own, and
shipping subject lines off-box without an opt-in is not a decision for this
module to make. Per-mailbox AI consent is being built elsewhere
(0022_ai_consent_entitlements); when it lands, the route can add it as a
third gate without touching anything here.

Failure of any kind -- endpoint down, bad JSON, empty answer -- returns None
and the block is omitted. Topics are a nicety; the page must not wait on them.
"""

from __future__ import annotations

import json
import logging
import os
import re

import requests

from .records import HeaderRecord

logger = logging.getLogger(__name__)

TOPICS_TIMEOUT_SECONDS = int(os.getenv("INSIGHTS_TOPICS_TIMEOUT", "20"))
SUBJECT_SAMPLE_SIZE = 60
MIN_SUBJECTS = 8
MAX_TOPICS = 6

_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fwd?|fw|aw|wg|sv|vs|tr|rv)\s*(?:\[\d+\])?\s*:\s*)+", re.I)
_TICKET_RE = re.compile(r"\[[^\]]{1,40}\]|#\d+")


def ai_configured() -> bool:
    return bool(os.getenv("AI_BASE_URL", "").strip() and os.getenv("AI_API_KEY", "").strip())


def topics_enabled() -> bool:
    flag = os.getenv("INSIGHTS_TOPICS_ENABLED", "").strip().lower()
    return flag in ("1", "true", "yes", "on") and ai_configured()


def normalise_subject(subject: str | None) -> str:
    """Strip reply/forward prefixes and ticket tags so one conversation is one line."""
    text = _PREFIX_RE.sub("", subject or "")
    text = _TICKET_RE.sub(" ", text)
    return " ".join(text.split())[:80]


def subject_sample(records: list[HeaderRecord], limit: int = SUBJECT_SAMPLE_SIZE) -> list[str]:
    """Distinct normalised subjects, most recent first."""
    dated = [r for r in records if r.effective_date is not None]
    dated.sort(key=lambda r: r.effective_date, reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for record in dated:
        subject = normalise_subject(record.subject)
        key = subject.lower()
        if len(subject) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(subject)
        if len(out) >= limit:
            break
    return out


def _chat(system: str, user: str) -> str | None:
    base = os.getenv("AI_BASE_URL", "").strip().rstrip("/")
    key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
    try:
        response = requests.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "api-key": key,  # Azure OpenAI reads this header instead.
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
            },
            timeout=TOPICS_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Insights topics: AI endpoint unreachable: %s", exc)
        return None
    if response.status_code != 200:
        logger.warning("Insights topics: AI endpoint returned %s", response.status_code)
        return None
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_topics(text: str | None) -> list[dict] | None:
    """Model output -> [{label, share}] or None. Tolerates a fenced block."""
    if not text:
        return None
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    start = body.find("[")
    end = body.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(body[start : end + 1])
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None

    topics: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        label = str(item.get("topic") or item.get("label") or "").strip()
        try:
            share = float(item.get("share", 0))
        except (TypeError, ValueError):
            continue
        if not label or share <= 0:
            continue
        topics.append({"label": label[:60], "share": min(share, 1.0)})
    if not topics:
        return None

    topics.sort(key=lambda t: -t["share"])
    topics = topics[:MAX_TOPICS]
    total = sum(t["share"] for t in topics)
    if total > 1.0:
        for topic in topics:
            topic["share"] = topic["share"] / total
    for topic in topics:
        topic["share"] = round(topic["share"], 3)
    return topics


def compute_topics(records: list[HeaderRecord]) -> dict | None:
    """The topics block, or None when it cannot or should not be produced."""
    if not topics_enabled():
        return None
    subjects = subject_sample(records)
    if len(subjects) < MIN_SUBJECTS:
        return None
    answer = _chat(
        "You group email subject lines into topics. Reply with JSON only: a list of "
        f'at most {MAX_TOPICS} objects with keys "topic" (2-4 words) and "share" '
        "(fraction of the subjects that belong to it, between 0 and 1). No prose.",
        "\n".join(f"- {s}" for s in subjects),
    )
    topics = parse_topics(answer)
    if not topics:
        return None
    return {"topics": topics, "subjects_sampled": len(subjects), "source": "ai"}
