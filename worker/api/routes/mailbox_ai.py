"""
Maya consent, history, documents, and the newer AI endpoints.

Mounted at /api/v1/mailbox alongside routes/mailbox.py -- the same split
smtp_credential_reports makes from smtp_credentials: one prefix, two modules,
purely for size and ownership. No path collides: FastAPI path parameters
never match across "/", so /ai/summarize/thread/{id} here cannot be captured
by mailbox.py's /ai/summarize/{message_id}.

Everything is mailbox-scoped through the session, exactly like mailbox.py:
no handler takes an account id from the path or body, and the consent
document route scopes its lookup to the session's own mailbox in the WHERE
clause -- someone else's document id matches nothing rather than 403ing.

The gates (mobile v1 section 13) live in utils/ai_consent; the AI endpoints
here depend on require_maya, which resolves capabilities.ai -> org policy ->
entitlement -> consent in that order and 403s with the outermost failing
gate's error_code. The consent endpoints themselves require only the mailbox
session: a holder must be able to READ the terms, their state and their
history -- and withdraw -- even when a gate ahead of consent is closed.
"""

import json
import logging
import re
from html import unescape

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from utils.ai_consent import (
    build_consent_event,
    consent_payload,
    current_terms_version,
    document_url_for,
    history_rows,
    insert_consent_event,
    load_consent_state,
    load_document,
    load_terms,
    metered_ai_call,
    redact_for_ai,
    require_maya,
    terms_sha256,
    upsert_consent_state,
    utc_now,
)
from utils.auth import create_api_response
from utils.database import get_db_connection
from utils.mailbox_auth import require_mailbox

from routes.mailbox import AI_MAX_INPUT_CHARS, _ai_chat, _unavailable
from shared.imap_mail import (
    ImapUnavailableError,
    fetch_raw_message,
    imap_session,
    parse_message,
    split_message_id,
    thread_for,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# A conversation longer than this is summarised from its most recent
# messages. Twelve is well past the point where earlier turns are quoted
# inside later ones anyway, and it bounds both the IMAP fetches and the
# share of AI_MAX_INPUT_CHARS each message gets.
MAX_THREAD_MESSAGES = 12

# Classification labels are a closed vocabulary: the client renders them as
# chips/filters, so a free-text label from the model would be an unstyled
# surprise. Anything the model invents is coerced to "other".
AI_CLASSIFY_LABELS = (
    "primary",
    "newsletter",
    "promotion",
    "social",
    "notification",
    "finance",
    "travel",
    "action_required",
    "other",
)
MAX_CLASSIFY_ITEMS = 20
_CLASSIFY_SUBJECT_CHARS = 200
_CLASSIFY_PREVIEW_CHARS = 300


def _db():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )
    return conn, conn.cursor(dictionary=True)


# ---------------------------------------------------------------------------
# Terms + consent (13e)
# ---------------------------------------------------------------------------


class ConsentRequest(BaseModel):
    accept_assistant: bool
    accept_training: bool = False
    terms_version: str = Field(..., min_length=1, max_length=32)


@router.get(
    "/ai/terms",
    summary="The current AI assistant terms",
    description=(
        "The full text of the terms the holder would be accepting, with its "
        "version and SHA-256. The client shows this text before POST /ai/consent; "
        "consent to one version is never consent to the next."
    ),
)
def ai_terms(mailbox: dict = Depends(require_mailbox)):
    version = current_terms_version()
    text = load_terms(version)
    return create_api_response(
        "success",
        "AI terms",
        {"version": version, "text": text, "sha256": terms_sha256(text)},
    )


@router.get(
    "/ai/consent",
    summary="The mailbox holder's AI consent state",
    description=(
        "Current opt-in flags, the version accepted, when, the version currently "
        "in force, and the URL of the consent document. Deliberately available "
        "regardless of entitlement or org policy -- reading one's own consent "
        "state is never gated."
    ),
)
def ai_consent_show(mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    conn, cursor = _db()
    try:
        state = load_consent_state(cursor, mailbox["email_account_id"])
    finally:
        cursor.close()
        conn.close()
    return create_api_response("success", "AI consent state", consent_payload(state))


@router.post(
    "/ai/consent",
    summary="Accept the AI assistant terms",
    description=(
        "Records the holder's acceptance of the CURRENT terms version -- 409 with "
        "error_code terms_version_stale if the submitted version is not current, "
        "so a client holding yesterday's text can never bind the holder to words "
        "they did not see. On success the acceptance is written to an append-only "
        "ledger together with a sealed PDF record (mailbox, both decisions, the "
        "full terms text as agreed, timestamp, actor), and the response carries "
        "its consent_id and document_url."
    ),
)
def ai_consent_accept(body: ConsentRequest, mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    if not body.accept_assistant:
        raise HTTPException(
            status_code=422,
            detail=create_api_response(
                "error",
                "accept_assistant must be true; use DELETE /ai/consent to withdraw",
            ),
        )
    current = current_terms_version()
    if body.terms_version != current:
        raise HTTPException(
            status_code=409,
            detail=create_api_response(
                "error",
                "These terms are no longer the current version; review and accept the "
                "current terms",
                {"current_terms_version": current},
                error_code="terms_version_stale",
            ),
        )
    # 503s here, before anything is written, if the terms text is missing --
    # recording consent to words that cannot be embedded in the record would
    # be recording consent to nothing.
    load_terms(current)

    conn, cursor = _db()
    try:
        state = load_consent_state(cursor, mailbox["email_account_id"])
        now = utc_now()
        already_accepted = bool(
            state and state["ai_assistant_opt_in"] and state["ai_terms_version"] == current
        )
        # A holder who already accepted this version and is now adding the
        # training decision gets the dedicated history action; everything
        # else -- first acceptance, re-acceptance after withdrawal, a new
        # version -- is "accepted".
        action = (
            "training_accepted"
            if already_accepted and body.accept_training and not state["ai_training_opt_in"]
            else "accepted"
        )
        event = build_consent_event(
            mailbox=mailbox,
            action=action,
            assistant_opt_in=True,
            training_opt_in=body.accept_training,
            terms_version=current,
            actor=mailbox["email"],
            include_terms_text=True,
            now=now,
        )
        insert_consent_event(cursor, event)

        assistant_at = (
            state["ai_assistant_opted_in_at"]
            if already_accepted and state.get("ai_assistant_opted_in_at")
            else now
        )
        if not body.accept_training:
            training_at = None
        elif (
            already_accepted
            and state["ai_training_opt_in"]
            and state.get("ai_training_opted_in_at")
        ):
            training_at = state["ai_training_opted_in_at"]
        else:
            training_at = now
        upsert_consent_state(
            cursor,
            mailbox,
            assistant_opt_in=True,
            training_opt_in=body.accept_training,
            terms_version=current,
            assistant_opted_in_at=assistant_at,
            training_opted_in_at=training_at,
            consent_event_id=event["id"],
            now=now,
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return create_api_response(
        "success",
        "Consent recorded",
        {"document_url": document_url_for(event["id"]), "consent_id": event["id"]},
    )


@router.delete(
    "/ai/consent",
    summary="Withdraw AI consent entirely",
    description=(
        "Withdraws both the assistant and training decisions and writes a "
        "'revoked' ledger entry with its own receipt document. Idempotent: with "
        "nothing to withdraw it succeeds without writing anything."
    ),
)
def ai_consent_withdraw(mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    conn, cursor = _db()
    try:
        state = load_consent_state(cursor, mailbox["email_account_id"])
        if not state or not state["ai_assistant_opt_in"]:
            return create_api_response(
                "success", "No AI consent to withdraw", consent_payload(state)
            )
        now = utc_now()
        event = build_consent_event(
            mailbox=mailbox,
            action="revoked",
            assistant_opt_in=False,
            training_opt_in=False,
            terms_version=state["ai_terms_version"],
            actor=mailbox["email"],
            include_terms_text=False,
            now=now,
        )
        # On a withdrawal, included_training records what WAS withdrawn.
        event["included_training"] = state["ai_training_opt_in"]
        insert_consent_event(cursor, event)
        upsert_consent_state(
            cursor,
            mailbox,
            assistant_opt_in=False,
            training_opt_in=False,
            terms_version=None,
            assistant_opted_in_at=None,
            training_opted_in_at=None,
            consent_event_id=None,
            now=now,
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return create_api_response(
        "success",
        "Consent withdrawn",
        {"document_url": document_url_for(event["id"]), "consent_id": event["id"]},
    )


@router.delete(
    "/ai/consent/training",
    summary="Withdraw the training decision only",
    description=(
        "Turns off use of this mailbox's requests for model improvement while "
        "leaving the assistant itself consented. Idempotent."
    ),
)
def ai_consent_withdraw_training(mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    conn, cursor = _db()
    try:
        state = load_consent_state(cursor, mailbox["email_account_id"])
        if not state or not state["ai_training_opt_in"]:
            return create_api_response(
                "success", "Training was not enabled", consent_payload(state)
            )
        now = utc_now()
        event = build_consent_event(
            mailbox=mailbox,
            action="training_revoked",
            assistant_opt_in=state["ai_assistant_opt_in"],
            training_opt_in=False,
            terms_version=state["ai_terms_version"],
            actor=mailbox["email"],
            include_terms_text=False,
            now=now,
        )
        event["included_training"] = True
        insert_consent_event(cursor, event)
        upsert_consent_state(
            cursor,
            mailbox,
            assistant_opt_in=state["ai_assistant_opt_in"],
            training_opt_in=False,
            terms_version=state["ai_terms_version"],
            assistant_opted_in_at=state["ai_assistant_opted_in_at"],
            training_opted_in_at=None,
            consent_event_id=state["consent_event_id"],
            now=now,
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return create_api_response(
        "success",
        "Training consent withdrawn",
        {"document_url": document_url_for(event["id"]), "consent_id": event["id"]},
    )


@router.get(
    "/ai/consent/history",
    summary="The mailbox holder's consent history",
    description=(
        "Every acceptance and withdrawal, newest first, including any "
        "revoked_by_organisation entries written when an org admin set the AI "
        "policy to blocked. Each entry links its stored document."
    ),
)
def ai_consent_history(mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    conn, cursor = _db()
    try:
        rows = history_rows(cursor, mailbox["email_account_id"])
    finally:
        cursor.close()
        conn.close()
    return create_api_response("success", "Consent history", rows)


@router.get(
    "/ai/consent/{consent_id}/document",
    summary="Download one consent document",
    description=(
        "The stored PDF for one ledger entry -- own records only; another "
        "mailbox's id is a 404, not a 403."
    ),
)
def ai_consent_document(consent_id: str, mailbox: dict = Depends(require_mailbox)):  # noqa: B008
    conn, cursor = _db()
    try:
        pdf = load_document(cursor, mailbox["email_account_id"], consent_id)
    finally:
        cursor.close()
        conn.close()
    if pdf is None:
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Document not found")
        )
    safe_name = re.sub(r"[^0-9A-Za-z]", "", consent_id) or "record"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="maya-consent-{safe_name}.pdf"'},
    )


# ---------------------------------------------------------------------------
# Thread summary + classification (13d) -- fully gated, metered
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _text_of(message: dict) -> str:
    """Plain text of a parsed message, degrading from body_text to a rough
    de-tagging of body_html. Rough is fine: this feeds a summariser, not a
    renderer."""
    body = message.get("body_text")
    if not body and message.get("body_html"):
        body = unescape(_TAG_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", message["body_html"])))
    return re.sub(r"[ \t]+", " ", body or "").strip()


def _address_line(participants) -> str:
    parts = []
    for participant in participants or []:
        if isinstance(participant, dict):
            parts.append(participant.get("email") or participant.get("name") or "")
        else:
            parts.append(str(participant))
    return ", ".join(part for part in parts if part) or "unknown"


@router.post(
    "/ai/summarize/thread/{message_id}",
    summary="Summarise a whole conversation",
    description=(
        "Follows the message's RFC 5322 thread (shared/imap_mail.thread_for, "
        "read-only), stitches the most recent messages into one prompt, and "
        "summarises the conversation. Gated like every Maya endpoint; the "
        "response carries ai_calls_remaining."
    ),
)
def ai_summarize_thread(message_id: str, maya: dict = Depends(require_maya)):  # noqa: B008
    mailbox = maya["mailbox"]
    folder, uid = split_message_id(message_id)
    folder = folder or "INBOX"
    if not uid.isdigit():
        raise HTTPException(
            status_code=400, detail=create_api_response("error", "Malformed message id")
        )

    try:
        with imap_session(mailbox["email"]) as conn:
            thread = thread_for(conn, folder, uid)
            if thread:
                targets = []
                for summary in thread[-MAX_THREAD_MESSAGES:]:
                    t_folder, t_uid = split_message_id(summary["id"])
                    targets.append((t_folder or summary.get("folder") or folder, t_uid))
            else:
                # A message with no threading identifiers is a conversation
                # of one; summarise it rather than failing.
                targets = [(folder, uid)]

            messages = []
            for t_folder, t_uid in targets:
                fetched = fetch_raw_message(conn, t_folder, t_uid)
                if fetched is None:
                    continue
                messages.append(parse_message(fetched[0], t_folder, t_uid, fetched[1]))
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None

    if not messages:
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Message not found")
        )
    texts = [_text_of(message) for message in messages]
    if not any(texts):
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", "This conversation has no text to summarise"),
        )

    # Every message gets an equal share of the model's input budget, so one
    # newsletter in the middle of a thread cannot crowd out the replies.
    budget = max(400, AI_MAX_INPUT_CHARS // len(messages))
    parts = []
    for index, message in enumerate(messages):
        parts.append(
            f"Message {index + 1} of {len(messages)}\n"
            f"From: {_address_line(message.get('from'))}\n"
            f"Date: {message.get('received_at') or 'unknown'}\n"
            f"Subject: {message.get('subject') or '(no subject)'}\n\n"
            f"{texts[index][:budget]}"
        )
    stitched = redact_for_ai("\n\n---\n\n".join(parts))

    with metered_ai_call(maya) as meter:
        summary = _ai_chat(
            "Summarise this email conversation in at most five sentences: what it is "
            "about, where it stands, and any action the reader is being asked to "
            "take. Messages are in chronological order. Add nothing that is not in "
            "the messages.",
            stitched,
        )
    return create_api_response(
        "success",
        "Thread summary generated",
        {
            "summary": summary,
            "message_count": len(messages),
            "ai_calls_remaining": meter.remaining,
        },
    )


class ClassifyItem(BaseModel):
    id: str = Field(..., min_length=1, max_length=300)
    subject: str = Field("", max_length=1000)
    preview: str = Field("", max_length=2000)


class ClassifyRequest(BaseModel):
    items: list[ClassifyItem] = Field(..., min_length=1, max_length=MAX_CLASSIFY_ITEMS)


def _parse_classification(raw: str, count: int) -> dict[int, tuple[str, float]]:
    """The model's reply -> {item number: (label, confidence)}.

    Tolerates markdown fences and an {"items": [...]} wrapper because models
    produce both no matter how firmly told not to. Anything else unparseable
    returns {} and the caller answers 502 -- inventing labels client-side
    would defeat the point of asking."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return {}
    if isinstance(parsed, dict):
        parsed = parsed.get("items", [])
    if not isinstance(parsed, list):
        return {}
    labelled: dict[int, tuple[str, float]] = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= count:
            continue
        label = str(entry.get("label", "")).strip().lower().replace(" ", "_").replace("-", "_")
        if label not in AI_CLASSIFY_LABELS:
            label = "other"
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        labelled[number] = (label, min(1.0, max(0.0, confidence)))
    return labelled


@router.post(
    "/ai/classify",
    summary="Label a batch of messages",
    description=(
        "Classifies up to 20 messages by subject and preview into a closed label "
        "vocabulary with a confidence per item. One batch costs one call against "
        "the monthly allowance; the response carries ai_calls_remaining."
    ),
)
def ai_classify(body: ClassifyRequest, maya: dict = Depends(require_maya)):  # noqa: B008
    numbered = [
        {
            "n": index,
            "subject": redact_for_ai(item.subject[:_CLASSIFY_SUBJECT_CHARS]),
            "preview": redact_for_ai(item.preview[:_CLASSIFY_PREVIEW_CHARS]),
        }
        for index, item in enumerate(body.items, start=1)
    ]
    system = (
        "You label emails for an inbox. You receive a JSON array of items, each "
        '{"n", "subject", "preview"}. Reply with ONLY a JSON array of objects '
        '{"n": <the same number>, "label": <label>, "confidence": <number from 0 to 1>}, '
        "one per item, covering every item. label must be exactly one of: "
        + ", ".join(AI_CLASSIFY_LABELS)
        + ". No prose, no markdown fences."
    )

    with metered_ai_call(maya) as meter:
        raw = _ai_chat(system, json.dumps(numbered, ensure_ascii=True))
        labelled = _parse_classification(raw, len(numbered))
        if not labelled:
            logger.error("Unparseable AI classification response: %s", raw[:300])
            # Raising inside the metered block refunds the call -- the
            # holder got nothing for it.
            raise HTTPException(
                status_code=502,
                detail=create_api_response("error", "The AI service returned nothing usable"),
            )

    results = []
    for index, item in enumerate(body.items, start=1):
        label, confidence = labelled.get(index, ("other", 0.0))
        results.append({"id": item.id, "label": label, "confidence": confidence})
    return create_api_response(
        "success",
        "Messages classified",
        {
            "items": results,
            "labels": list(AI_CLASSIFY_LABELS),
            "ai_calls_remaining": meter.remaining,
        },
    )
