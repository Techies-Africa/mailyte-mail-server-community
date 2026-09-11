"""
Maya (the mailbox AI assistant): entitlement, org policy, consent and quota.

Mobile v1 section 13. One module owns everything that decides whether an AI
request from a mailbox holder may run, so the answer is the same on every
route that asks:

    capabilities.ai  ->  org policy  ->  entitlement  ->  consent  ->  quota

`resolve_maya()` walks those gates in that order and raises on the OUTERMOST
failing one -- a holder in an org that blocks AI is told that, not that they
have not consented to a thing they are not allowed to use anyway. The client
keys its UI off `error_code`, so the codes are a contract:

    503 ai_not_configured     no AI endpoint on this deployment (unchanged)
    403 org_policy_blocked    org policy is blocked, or never set
    403 not_entitled          org's plan does not include Maya
    403 consent_required      holder has not accepted the current terms
    403 terms_version_stale   holder accepted an older version
    429 ai_quota_exhausted    monthly per-mailbox allowance used up
    503 ai_quota_unavailable  the quota counter (Redis) cannot be reached

Storage. Entitlement and policy are columns on `organizations`, written by
PUT /api/v1/organizations/{id}/ai-settings -- Laravel flips paying orgs on
through that, staff through the console. Consent is a state row per mailbox
(`mailbox_ai_consents`) plus an append-only ledger (`mailbox_ai_consent_events`)
holding, for every acceptance and withdrawal, the canonical record, its
SHA-256 digest, an HMAC seal over that digest, and a PDF rendering. Migration
0022.

"Sealed", not "signed". The seal is an HMAC-SHA256 under a server-side key
(AI_CONSENT_SIGNING_KEY, falling back to ADMIN_TOKEN_SECRET). It proves the
record has not been altered since the server wrote it, to anyone who holds
the key. It is NOT an X.509 / PAdES digital signature: it carries no
certificate chain, no timestamp authority and no non-repudiation against the
server operator. Making that stronger is a product decision, not a code one.

Consent to one terms version is not consent to the next. The current version
is AI_TERMS_VERSION (default below) and its text lives in worker/api/ai_terms/.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, HTTPException

from shared.ulid_utils import generate_ulid

from .auth import create_api_response
from .database import get_db_connection
from .mailbox_auth import require_mailbox
from .pdf_minimal import build_pdf

logger = logging.getLogger(__name__)

ORG_POLICIES = ("unset", "allowed", "blocked", "accepted_for_all")
HISTORY_ACTIONS = (
    "accepted",
    "revoked",
    "training_accepted",
    "training_revoked",
    "revoked_by_organisation",
)

# What /capabilities reports when nothing is known -- a missing org row, a
# database error, or a deployment that has not run migration 0022 yet. The
# client treats absent entitlements as "nothing entitled", and so does this.
NO_ENTITLEMENTS = {"maya": False, "maya_plan": None, "maya_org_policy": "unset"}

TERMS_DIR = Path(__file__).resolve().parent.parent / "ai_terms"
DEFAULT_TERMS_VERSION = "2026-08-31.1"
_TERMS_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")

DEFAULT_MONTHLY_QUOTA = 500

# Redis keys live for a little over a month so a counter for the month that
# just ended is still readable for a few days if anyone asks.
_QUOTA_KEY_TTL = int(timedelta(days=40).total_seconds())

_terms_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def ai_configured() -> bool:
    """AI is optional and BYO-endpoint: the credential is the only thing that
    decides whether it exists. Single definition -- routes/mailbox.py's
    _ai_configured() delegates here so /capabilities and gate 1 cannot
    disagree."""
    return bool(os.getenv("AI_BASE_URL", "").strip() and os.getenv("AI_API_KEY", "").strip())


def current_terms_version() -> str:
    return os.getenv("AI_TERMS_VERSION", "").strip() or DEFAULT_TERMS_VERSION


def default_monthly_quota() -> int:
    try:
        return max(0, int(os.getenv("AI_MONTHLY_QUOTA_DEFAULT", str(DEFAULT_MONTHLY_QUOTA))))
    except ValueError:
        return DEFAULT_MONTHLY_QUOTA


def _signing_key() -> bytes | None:
    key = (
        os.getenv("AI_CONSENT_SIGNING_KEY", "").strip()
        or os.getenv("ADMIN_TOKEN_SECRET", "").strip()
    )
    return key.encode() if key else None


def public_api_base_url() -> str:
    """Prefix for document_url. Empty by default, which yields a path relative
    to whatever host the client already talks to -- correct behind the BFF and
    behind Traefik alike. Set PUBLIC_API_BASE_URL to emit absolute URLs."""
    return os.getenv("PUBLIC_API_BASE_URL", "").strip().rstrip("/")


def document_url_for(event_id: str | None) -> str | None:
    if not event_id:
        return None
    return f"{public_api_base_url()}/api/v1/mailbox/ai/consent/{event_id}/document"


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------


def load_terms(version: str) -> str:
    """The full text of one terms version, from worker/api/ai_terms/.

    Kept in-repo and versioned by filename so the text a holder agreed to is
    the text in the tag that was deployed -- and so a new version is a code
    review, not a config edit. Missing text is a 503: the consent flow cannot
    proceed without showing the words, and pretending otherwise would record
    consent to nothing.
    """
    if not _TERMS_VERSION_RE.match(version or ""):
        raise HTTPException(
            status_code=503,
            detail=create_api_response(
                "error", "AI terms version is malformed", error_code="ai_terms_unavailable"
            ),
        )
    cached = _terms_cache.get(version)
    if cached is not None:
        return cached
    path = TERMS_DIR / f"{version}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.error("AI terms text missing for version %s (%s)", version, path)
        raise HTTPException(
            status_code=503,
            detail=create_api_response(
                "error",
                "The AI terms for this deployment are not available",
                error_code="ai_terms_unavailable",
            ),
        ) from None
    _terms_cache[version] = text
    return text


def terms_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Best-effort redaction
# ---------------------------------------------------------------------------

# Digit runs of payment-card length, allowing the spaces/dashes people type
# between groups. Deliberately narrow: the terms promise best-effort masking
# of "obvious payment card numbers and similarly formatted identifiers", not
# PII detection, and a wider net would start eating order numbers and phone
# numbers the summary needs.
_CARD_LIKE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def redact_for_ai(text: str) -> str:
    """Mask card-like digit sequences before text leaves for the AI provider.

    Best-effort by design and documented as such in the terms (section 3).
    Nothing here should be read as a guarantee, and nothing here is a
    substitute for the consent the holder gave knowing that.
    """
    if not text:
        return text
    return _CARD_LIKE_RE.sub("[redacted-number]", text)


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------


def load_org_ai_settings(cursor, organization_id: str) -> dict:
    """The organization's Maya columns, normalised.

    An org row that does not exist -- or predates migration 0022 -- resolves
    to "not entitled, policy unset", which the gates treat as blocked.
    """
    cursor.execute(
        "SELECT ai_entitled, ai_plan, ai_org_policy, ai_monthly_quota "
        "FROM organizations WHERE id = %s",
        (organization_id,),
    )
    row = cursor.fetchone() or {}
    policy = row.get("ai_org_policy") or "unset"
    if policy not in ORG_POLICIES:
        policy = "unset"
    quota = row.get("ai_monthly_quota")
    return {
        "entitled": bool(row.get("ai_entitled")),
        "plan": row.get("ai_plan") or None,
        "policy": policy,
        "monthly_quota": int(quota) if quota is not None else default_monthly_quota(),
    }


def load_mailbox_ai_quota(cursor, email_account_id: str) -> int | None:
    """This mailbox's own Maya allowance, or None to use the org's.

    NULL is the normal state for a mailbox billing has never touched, and for
    every row that predates migration 0024 -- so the fallback is what keeps
    existing deployments behaving exactly as they did.
    """
    try:
        cursor.execute(
            "SELECT ai_monthly_quota FROM email_accounts WHERE id = %s",
            (email_account_id,),
        )
        row = cursor.fetchone() or {}
    except Exception:
        # A deployment that has not run 0024 yet has no such column. Falling
        # back to the org quota is correct there, and is strictly better than
        # failing a request the customer is entitled to make.
        return None

    quota = row.get("ai_monthly_quota")
    return int(quota) if quota is not None else None


def entitlements_payload(org: dict) -> dict:
    return {
        "maya": bool(org["entitled"]),
        "maya_plan": org["plan"],
        "maya_org_policy": org["policy"],
    }


def entitlements_for_mailbox(mailbox: dict) -> dict:
    """The `entitlements` block of GET /mailbox/capabilities.

    Never raises: /capabilities is the first thing the client loads after
    sign-in, and a database hiccup must degrade to "nothing entitled" rather
    than take the whole mailbox down with it.
    """
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return dict(NO_ENTITLEMENTS)
        cursor = conn.cursor(dictionary=True)
        try:
            return entitlements_payload(load_org_ai_settings(cursor, mailbox["organization_id"]))
        finally:
            cursor.close()
    except Exception as exc:
        logger.error("Entitlement lookup failed for %s: %s", mailbox.get("email"), exc)
        return dict(NO_ENTITLEMENTS)
    finally:
        if conn:
            with contextlib.suppress(Exception):
                conn.close()


def load_consent_state(cursor, email_account_id: str) -> dict | None:
    cursor.execute(
        "SELECT id, email_account_id, organization_id, ai_assistant_opt_in, "
        "ai_training_opt_in, ai_terms_version, ai_assistant_opted_in_at, "
        "ai_training_opted_in_at, consent_event_id, created_at, updated_at "
        "FROM mailbox_ai_consents WHERE email_account_id = %s",
        (email_account_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "email_account_id": row["email_account_id"],
        "organization_id": row["organization_id"],
        "ai_assistant_opt_in": bool(row["ai_assistant_opt_in"]),
        "ai_training_opt_in": bool(row["ai_training_opt_in"]),
        "ai_terms_version": row["ai_terms_version"],
        "ai_assistant_opted_in_at": row["ai_assistant_opted_in_at"],
        "ai_training_opted_in_at": row["ai_training_opted_in_at"],
        "consent_event_id": row["consent_event_id"],
    }


def iso_utc(moment: datetime | None) -> str | None:
    """DATETIME columns here hold UTC; say so in the wire format."""
    if moment is None:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.replace(microsecond=0).isoformat() + "Z"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def consent_payload(state: dict | None) -> dict:
    """GET /mailbox/ai/consent -- the exact shape the client was built against."""
    version = current_terms_version()
    if not state:
        return {
            "ai_assistant_opt_in": False,
            "ai_training_opt_in": False,
            "ai_terms_version": None,
            "ai_assistant_opted_in_at": None,
            "current_terms_version": version,
            "document_url": None,
        }
    return {
        "ai_assistant_opt_in": state["ai_assistant_opt_in"],
        "ai_training_opt_in": state["ai_training_opt_in"],
        "ai_terms_version": state["ai_terms_version"],
        "ai_assistant_opted_in_at": iso_utc(state["ai_assistant_opted_in_at"]),
        "current_terms_version": version,
        "document_url": document_url_for(state["consent_event_id"])
        if state["ai_assistant_opt_in"]
        else None,
    }


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


def _forbidden(message: str, error_code: str) -> HTTPException:
    return HTTPException(
        status_code=403, detail=create_api_response("error", message, error_code=error_code)
    )


def evaluate_gates(org: dict, consent: dict | None, terms_version: str) -> None:
    """Gates 2-4, in order, on already-loaded state. Pure: no I/O, so the
    ordering is unit-testable without a database.

    Org policy first because it is the broadest veto (13a-2): `unset` is
    treated as blocked, and `blocked` overrides an individual acceptance
    that already exists. `accepted_for_all` skips the individual ask but
    never the plan -- an org cannot accept its way into a feature it has not
    paid for.
    """
    policy = org["policy"]
    if policy in ("unset", "blocked"):
        raise _forbidden("Your organisation has not enabled the AI assistant", "org_policy_blocked")
    if not org["entitled"]:
        raise _forbidden("The AI assistant is not included in your plan", "not_entitled")
    if policy == "accepted_for_all":
        return
    if not consent or not consent["ai_assistant_opt_in"]:
        raise _forbidden("Accept the AI assistant terms to continue", "consent_required")
    if consent["ai_terms_version"] != terms_version:
        raise _forbidden(
            "The AI assistant terms have changed; please review and accept them again",
            "terms_version_stale",
        )


def resolve_maya(mailbox: dict) -> dict:
    """All four gates for one mailbox session. Returns the context the
    handlers need afterwards (quota, org settings, consent), or raises."""
    if not ai_configured():
        raise HTTPException(
            status_code=503,
            detail=create_api_response(
                "error",
                "No AI endpoint is configured for this deployment",
                error_code="ai_not_configured",
            ),
        )

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    cursor = conn.cursor(dictionary=True)
    try:
        org = load_org_ai_settings(cursor, mailbox["organization_id"])
        consent = load_consent_state(cursor, mailbox["email_account_id"])
        mailbox_quota = load_mailbox_ai_quota(cursor, mailbox["email_account_id"])
    finally:
        cursor.close()
        conn.close()

    version = current_terms_version()
    evaluate_gates(org, consent, version)
    return {
        "mailbox": mailbox,
        "org": org,
        "consent": consent,
        "terms_version": version,
        # Per-mailbox when billing has set one, falling back to the org's
        # value. Tiers are sold per mailbox, so 3 Standard and 4 Pro mailboxes
        # in one organization must not share a single allowance.
        "quota": mailbox_quota if mailbox_quota is not None else org["monthly_quota"],
    }


def require_maya(mailbox: dict = Depends(require_mailbox)) -> dict:  # noqa: B008
    """FastAPI dependency: mailbox session plus every Maya gate.

    `def`, not `async def` -- it does blocking DB work and must run in the
    threadpool (the ~128-endpoint event-loop finding applies here too).
    """
    return resolve_maya(mailbox)


# ---------------------------------------------------------------------------
# Monthly quota (Redis)
# ---------------------------------------------------------------------------

_redis_client = None


def _redis():
    """Lazy, module-cached client. Import inside so a deployment with no
    Redis library still imports this module (and every route that uses it)."""
    global _redis_client
    if _redis_client is None:
        import redis as redis_lib

        url = os.getenv("REDIS_URL", "").strip()
        if url:
            _redis_client = redis_lib.from_url(url, socket_timeout=3, decode_responses=True)
        else:
            _redis_client = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "redis"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                socket_timeout=3,
                decode_responses=True,
            )
    return _redis_client


def quota_key(email_account_id: str, moment: datetime | None = None) -> str:
    moment = moment or datetime.now(UTC)
    return f"mailyte:ai:calls:{email_account_id}:{moment:%Y%m}"


def _quota_exhausted() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=create_api_response(
            "error",
            "You have used this month's AI assistant allowance",
            {"ai_calls_remaining": 0},
            error_code="ai_quota_exhausted",
        ),
    )


def _quota_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=create_api_response(
            "error",
            "The AI assistant is temporarily unavailable",
            error_code="ai_quota_unavailable",
        ),
    )


def consume_ai_call(email_account_id: str, quota: int) -> int:
    """Charge one call against this mailbox's month. Returns calls remaining.

    INCR first, then compare: the increment is atomic, so two concurrent
    requests at the boundary cannot both pass. The one that overshoots gives
    its unit back and is refused.

    Fails CLOSED when Redis is unreachable. Maya is a metered, paid feature;
    running it unmetered during a cache outage would be a billing hole that
    nobody would notice until the provider invoice arrived.
    """
    if quota <= 0:
        raise _quota_exhausted()
    key = quota_key(email_account_id)
    try:
        client = _redis()
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, _QUOTA_KEY_TTL)
    except Exception as exc:
        logger.error("AI quota counter unavailable: %s", exc)
        raise _quota_unavailable() from None
    if count > quota:
        with contextlib.suppress(Exception):
            client.decr(key)
        raise _quota_exhausted()
    return quota - count


def refund_ai_call(email_account_id: str) -> None:
    """Give a unit back when the provider failed to answer. Best effort."""
    try:
        client = _redis()
        key = quota_key(email_account_id)
        if int(client.decr(key)) < 0:
            client.set(key, 0, keepttl=True)
    except Exception as exc:
        logger.warning("AI quota refund failed: %s", exc)


def ai_calls_remaining(email_account_id: str, quota: int) -> int:
    """Read-only view for informational responses. Unknown -> 0, never a guess."""
    try:
        used = int(_redis().get(quota_key(email_account_id)) or 0)
    except Exception:
        return 0
    return max(0, quota - used)


class AiMeter:
    remaining: int = 0


@contextlib.contextmanager
def metered_ai_call(maya: dict) -> Iterator[AiMeter]:
    """Wrap exactly the provider call: charges on entry, refunds if the body
    raises. A 502 from the model, an IMAP failure, a 422 for an empty message
    -- none of them delivered an answer, so none of them should cost one."""
    account_id = maya["mailbox"]["email_account_id"]
    meter = AiMeter()
    meter.remaining = consume_ai_call(account_id, maya["quota"])
    try:
        yield meter
    except BaseException:
        refund_ai_call(account_id)
        raise


# ---------------------------------------------------------------------------
# Consent events: canonical record, seal, PDF
# ---------------------------------------------------------------------------


class SessionCursor:
    """Adapts a SQLAlchemy Connection to the dictionary-cursor shape the
    helpers below expect, so organizations.py (ORM session) and mailbox_ai.py
    (mysql.connector) can share one implementation of "revoke every holder
    in this org" inside their own transactions."""

    def __init__(self, connection):
        self._connection = connection
        self._result = None

    def execute(self, sql: str, params=()) -> None:
        self._result = self._connection.exec_driver_sql(sql, tuple(params))

    def fetchone(self) -> dict | None:
        row = self._result.mappings().first() if self._result is not None else None
        return dict(row) if row else None

    def fetchall(self) -> list[dict]:
        if self._result is None:
            return []
        return [dict(row) for row in self._result.mappings().all()]

    @property
    def rowcount(self) -> int:
        return self._result.rowcount if self._result is not None else 0

    def close(self) -> None:
        return None


def canonical_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def seal_digest(digest: str) -> str | None:
    key = _signing_key()
    if not key:
        return None
    return hmac.new(key, digest.encode(), hashlib.sha256).hexdigest()


def verify_event(record_json: str, digest: str, seal: str | None) -> dict:
    """Recompute the digest and seal for a stored event. For support tooling:
    a record whose digest no longer matches its canonical JSON was altered
    after the fact; a seal that fails means either alteration or a rotated
    key (the two are indistinguishable by design -- rotate deliberately)."""
    expected_digest = hashlib.sha256(record_json.encode()).hexdigest()
    expected_seal = seal_digest(expected_digest)
    return {
        "digest_ok": hmac.compare_digest(expected_digest, digest or ""),
        "seal_ok": bool(seal)
        and bool(expected_seal)
        and hmac.compare_digest(expected_seal, seal or ""),
        "sealed": bool(seal),
    }


def _decision(flag: bool) -> str:
    return "ACCEPTED" if flag else "DECLINED"


def _render_document(record: dict, digest: str, seal: str | None, terms_text: str | None) -> bytes:
    action = record["action"]
    headline = {
        "accepted": "Consent record",
        "training_accepted": "Consent record (training added)",
        "revoked": "Withdrawal record",
        "training_revoked": "Withdrawal record (training only)",
        "revoked_by_organisation": "Revocation record (by organisation)",
    }.get(action, "Consent record")

    blocks: list[tuple[str, str]] = [
        ("h1", f"Mailyte AI Assistant (Maya) - {headline}"),
        ("blank", ""),
        ("body", f"Record ID: {record['consent_id']}"),
        ("body", f"Mailbox: {record['mailbox']}"),
        ("body", f"Organisation ID: {record['organization_id']}"),
        ("body", f"Recorded at: {record['recorded_at']}"),
        ("body", f"Recorded by: {record['actor']}"),
        ("body", f"Action: {action}"),
        ("blank", ""),
        ("h2", "Decisions"),
        ("body", f"Use of the AI assistant: {_decision(record['assistant_opt_in'])}"),
        (
            "body",
            "Use of my mail to improve AI models (training): "
            f"{_decision(record['training_opt_in'])}",
        ),
        ("blank", ""),
        ("h2", "Terms"),
        ("body", f"Terms version: {record['terms_version'] or 'n/a'}"),
        ("mono", f"Terms SHA-256: {record['terms_sha256'] or 'n/a'}"),
        ("blank", ""),
        ("h2", "Integrity"),
        ("mono", f"Record digest (SHA-256): {digest}"),
        (
            "mono",
            f"Integrity seal (HMAC-SHA256): {seal}"
            if seal
            else "Integrity seal: not sealed (no signing key configured on the server)",
        ),
        (
            "body",
            "This document is integrity-sealed, not digitally signed. The digest is the "
            "SHA-256 of the canonical JSON record Mailyte stored; the seal is an HMAC over "
            "that digest under a key held by the server. Together they show the record has "
            "not been altered since it was written. They are not an X.509 or PAdES signature "
            "and carry no certificate chain or third-party timestamp.",
        ),
    ]
    if terms_text:
        blocks += [
            ("blank", ""),
            ("h2", f"Terms as agreed (version {record['terms_version']}, full text)"),
            ("blank", ""),
        ]
        for paragraph in terms_text.split("\n\n"):
            stripped = paragraph.strip()
            if not stripped:
                continue
            if stripped.startswith("# "):
                blocks.append(("h2", stripped[2:]))
            elif stripped.startswith("## "):
                blocks.append(("h2", stripped[3:]))
            else:
                blocks.append(("body", stripped))
            blocks.append(("blank", ""))

    return build_pdf(
        blocks,
        title=f"Maya {headline} - {record['mailbox']}",
        subject="Mailyte AI assistant consent record",
        keywords=f"sha256={digest}",
        created_at=datetime.fromisoformat(record["recorded_at"].replace("Z", "+00:00")),
    )


def build_consent_event(
    *,
    mailbox: dict,
    action: str,
    assistant_opt_in: bool,
    training_opt_in: bool,
    terms_version: str | None,
    actor: str,
    include_terms_text: bool,
    now: datetime | None = None,
) -> dict:
    """One ledger row, fully formed: canonical record, digest, seal, PDF.

    Acceptances embed the FULL terms text in the PDF -- "what did I agree to"
    has to be answerable from the document alone, years later, without the
    repo. Withdrawals reference the version and its hash instead; there is
    nothing being agreed to.
    """
    if action not in HISTORY_ACTIONS:
        raise ValueError(f"Unknown consent action: {action}")
    now = now or utc_now()
    event_id = generate_ulid()
    terms_text: str | None = None
    terms_hash: str | None = None
    if terms_version and include_terms_text:
        terms_text = load_terms(terms_version)
        terms_hash = terms_sha256(terms_text)
    elif terms_version:
        # Withdrawals still pin the version's hash so the record says WHICH
        # text is being withdrawn from, even after it is superseded. Missing
        # text must not block a withdrawal, though -- hence the suppress.
        with contextlib.suppress(HTTPException):
            terms_hash = terms_sha256(load_terms(terms_version))

    record = {
        "consent_id": event_id,
        "schema": "mailyte.ai_consent.v1",
        "mailbox": mailbox["email"],
        "email_account_id": mailbox["email_account_id"],
        "organization_id": mailbox["organization_id"],
        "action": action,
        "assistant_opt_in": bool(assistant_opt_in),
        "training_opt_in": bool(training_opt_in),
        "terms_version": terms_version,
        "terms_sha256": terms_hash,
        "recorded_at": iso_utc(now),
        "actor": actor,
    }
    record_json = canonical_json(record)
    digest = hashlib.sha256(record_json.encode()).hexdigest()
    seal = seal_digest(digest)
    pdf = _render_document(record, digest, seal, terms_text)

    return {
        "id": event_id,
        "email_account_id": mailbox["email_account_id"],
        "organization_id": mailbox["organization_id"],
        "email": mailbox["email"],
        "action": action,
        "terms_version": terms_version,
        "included_training": bool(training_opt_in),
        "actor": actor,
        "record_json": record_json,
        "digest": digest,
        "seal": seal,
        "pdf": pdf,
        "created_at": now,
    }


def insert_consent_event(cursor, event: dict) -> None:
    cursor.execute(
        "INSERT INTO mailbox_ai_consent_events "
        "(id, email_account_id, organization_id, email, action, terms_version, "
        " included_training, actor, record_json, record_sha256, record_hmac, "
        " document_pdf, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            event["id"],
            event["email_account_id"],
            event["organization_id"],
            event["email"],
            event["action"],
            event["terms_version"],
            1 if event["included_training"] else 0,
            event["actor"],
            event["record_json"],
            event["digest"],
            event["seal"],
            event["pdf"],
            event["created_at"],
        ),
    )


def upsert_consent_state(
    cursor,
    mailbox: dict,
    *,
    assistant_opt_in: bool,
    training_opt_in: bool,
    terms_version: str | None,
    assistant_opted_in_at: datetime | None,
    training_opted_in_at: datetime | None,
    consent_event_id: str | None,
    now: datetime,
) -> None:
    """One row per mailbox, written in full every time. Portable upsert (no
    VALUES() -- deprecated in MySQL 8.0.20 and absent in MariaDB's dialect
    notes), so the same statement works on either server."""
    values = (
        1 if assistant_opt_in else 0,
        1 if training_opt_in else 0,
        terms_version,
        assistant_opted_in_at,
        training_opted_in_at,
        consent_event_id,
        now,
    )
    cursor.execute(
        "INSERT INTO mailbox_ai_consents "
        "(id, email_account_id, organization_id, ai_assistant_opt_in, ai_training_opt_in, "
        " ai_terms_version, ai_assistant_opted_in_at, ai_training_opted_in_at, "
        " consent_event_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE ai_assistant_opt_in = %s, ai_training_opt_in = %s, "
        " ai_terms_version = %s, ai_assistant_opted_in_at = %s, ai_training_opted_in_at = %s, "
        " consent_event_id = %s, updated_at = %s",
        (generate_ulid(), mailbox["email_account_id"], mailbox["organization_id"])
        + values
        + (now,)
        + values,
    )


def revoke_consents_for_organization(cursor, organization_id: str, actor: str) -> int:
    """Org policy -> blocked: every holder with a live acceptance gets a
    revoked_by_organisation ledger entry and their state row cleared (13a-2).

    Cleared, not merely vetoed: when the org later un-blocks, holders are
    asked again rather than silently resuming under a consent they may have
    forgotten they gave. Actor is the admin identity that set the policy.
    Returns how many holders were affected.
    """
    cursor.execute(
        "SELECT c.email_account_id, c.organization_id, a.email, c.ai_training_opt_in, "
        " c.ai_terms_version "
        "FROM mailbox_ai_consents c JOIN email_accounts a ON a.id = c.email_account_id "
        "WHERE c.organization_id = %s AND c.ai_assistant_opt_in = 1",
        (organization_id,),
    )
    holders = cursor.fetchall() or []
    now = utc_now()
    for holder in holders:
        mailbox = {
            "email": holder["email"],
            "email_account_id": holder["email_account_id"],
            "organization_id": holder["organization_id"],
        }
        event = build_consent_event(
            mailbox=mailbox,
            action="revoked_by_organisation",
            assistant_opt_in=False,
            training_opt_in=False,
            terms_version=holder.get("ai_terms_version"),
            actor=actor,
            include_terms_text=False,
            now=now,
        )
        # included_training on a revocation records what was withdrawn.
        event["included_training"] = bool(holder.get("ai_training_opt_in"))
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
    return len(holders)


def history_rows(cursor, email_account_id: str, limit: int = 100) -> list[dict]:
    cursor.execute(
        "SELECT id, action, terms_version, included_training, actor, created_at "
        "FROM mailbox_ai_consent_events WHERE email_account_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (email_account_id, int(limit)),
    )
    return [
        {
            "id": row["id"],
            "action": row["action"],
            "at": iso_utc(row["created_at"]),
            "terms_version": row["terms_version"],
            "included_training": bool(row["included_training"]),
            "actor": row["actor"],
            "document_url": document_url_for(row["id"]),
        }
        for row in cursor.fetchall() or []
    ]


def load_document(cursor, email_account_id: str, event_id: str) -> bytes | None:
    """The PDF for one ledger entry -- scoped to the session's own mailbox in
    the WHERE clause, so another holder's id matches nothing (not 403)."""
    cursor.execute(
        "SELECT document_pdf FROM mailbox_ai_consent_events "
        "WHERE id = %s AND email_account_id = %s",
        (event_id, email_account_id),
    )
    row = cursor.fetchone()
    if not row or row.get("document_pdf") is None:
        return None
    return bytes(row["document_pdf"])
