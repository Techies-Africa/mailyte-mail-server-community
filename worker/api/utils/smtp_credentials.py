#!/usr/bin/env python3
"""
Shared helpers for the SMTP API-key lifecycle routes
(routes/smtp_credentials.py and routes/smtp_credential_reports.py).

Secret generation lives here on the mail server -- not in Laravel -- because
the console consumes this API directly and CE deployments have no Laravel at
all (00-PRD-smtp-api-keys section 2). The plaintext secret exists only in the
create/rotate HTTP response; only the bcrypt hash and a short display prefix
are ever stored.
"""

import base64
import ipaddress
import logging
import os
import re
import secrets
from datetime import UTC, datetime

import requests
from fastapi import Request

from database.models.core import SmtpCredential, SmtpCredentialEvent

logger = logging.getLogger(__name__)

# Long enough that the slug survives truncation next to the random suffix.
USERNAME_MAX = 255
SECRET_BYTES = 30  # token_urlsafe(30) -> 40 chars
PREFIX_LEN = 8


def generate_secret() -> tuple[str, str]:
    """Return (plaintext_secret, display_prefix)."""
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return secret, secret[:PREFIX_LEN]


def generate_username(session, domain_name: str) -> str:
    """`{domain-slug}-smtp-{random}` -- same shape Laravel's phase-09 flow
    used, so keys minted before and after the authority move look alike.
    Collision-checked because the column is UNIQUE and a duplicate would
    surface as an opaque IntegrityError."""
    slug = re.sub(r"[^a-z0-9]+", "-", domain_name.lower()).strip("-")[:64]
    for _ in range(5):
        candidate = f"{slug}-smtp-{secrets.token_hex(4)}"
        if not session.query(SmtpCredential).filter_by(username=candidate).first():
            return candidate
    # 5 collisions on 8 hex chars means the RNG is broken, not the data.
    raise RuntimeError("could not generate a unique SMTP credential username")


def validate_allowlist(ips: list[str]) -> str | None:
    """Entries may be single IPs or CIDR networks (v4 or v6) -- both are
    valid Dovecot `allow_nets` values. Returns an error message or None."""
    if not isinstance(ips, list):
        return "allowed_ips must be a list"
    for entry in ips:
        if not isinstance(entry, str) or not entry:
            return "allowed_ips entries must be non-empty strings"
        try:
            if "/" in entry:
                ipaddress.ip_network(entry, strict=False)
            else:
                ipaddress.ip_address(entry)
        except ValueError:
            return f"'{entry}' is not a valid IP address or CIDR network"
    return None


def parse_expires_at(value):
    """ISO-8601 string -> naive UTC datetime (the schema stores naive UTC,
    matching every other DateTime in this database). Returns (datetime|None,
    error|None); the empty string / None clears the expiry."""
    if value in (None, ""):
        return None, None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None, f"'{value}' is not a valid ISO-8601 timestamp"
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    if parsed <= datetime.utcnow():
        return None, "expires_at must be in the future"
    return parsed, None


def resolve_actor(request: Request) -> str:
    """Human-attributable actor string for the audit trail. Operator sessions
    carry an operator id; API keys are attributed to their organization."""
    ctx = getattr(request.state, "auth_context", None) or {}
    if ctx.get("operator_id"):
        return f"operator:{ctx['operator_id']}"
    if ctx.get("scope") == "platform":
        return "platform-api-key"
    if ctx.get("organization_id"):
        return f"api-key:{ctx['organization_id']}"
    return "unknown"


def source_ip_of(request: Request) -> str | None:
    """Client IP for the audit trail. Traefik terminates TLS in front of this
    API, so the first X-Forwarded-For hop is the real client when present."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def org_scope_filter(query, request: Request):
    """Apply tenant isolation to a SmtpCredential/event query. Organization
    scope is always forced to the caller's own org; platform scope sees all
    (optionally narrowed by an ?organization_id= param in the route)."""
    ctx = getattr(request.state, "auth_context", None) or {}
    if ctx.get("scope") == "organization":
        return query.filter_by(organization_id=ctx.get("organization_id"))
    return query


def fetch_scoped_credential(session, request: Request, credential_id: str):
    """Fetch a credential the caller is allowed to see, or None. Cross-org
    access returns None -> the route answers 404, never 403 (conventions
    section 8: do not leak existence of other tenants' resources)."""
    query = session.query(SmtpCredential).filter_by(id=credential_id)
    return org_scope_filter(query, request).first()


def flush_auth_cache(username: str) -> bool:
    """Flush Dovecot's auth cache for one username via the doveadm HTTP API.

    Verified live on Dovecot 2.3.16: a `nocache` passdb extra field is NOT
    honoured, so without this call a revoked/rotated/expired key keeps
    authenticating from cache for up to auth_cache_ttl (1 hour). Called
    after the DB commit -- the mutation is already durable, so a flush
    failure degrades to the cache-TTL window and is reported to the caller
    rather than failing the request."""
    api_key = os.getenv("DOVEADM_API_KEY", "")
    url = os.getenv("DOVEADM_URL", "http://dovecot:24180")
    if not api_key:
        logger.warning("DOVEADM_API_KEY unset -- auth cache not flushed for %s", username)
        return False
    token = base64.b64encode(api_key.encode()).decode()
    body = [["authCacheFlush", {"user": [username]}, "flush"]]
    for attempt in (1, 2):
        try:
            # Scheme is X-Dovecot-API (the WWW-Authenticate value the
            # listener advertises), not X-Dovecot-API-Key as the setting
            # name suggests -- the latter gets a bare 401.
            resp = requests.post(
                f"{url}/doveadm/v1",
                json=body,
                headers={"Authorization": f"X-Dovecot-API {token}"},
                timeout=5,
            )
            if resp.status_code == 200 and resp.json()[0][0] == "doveadmResponse":
                return True
            logger.error(
                "doveadm authCacheFlush for %s returned %s: %s",
                username,
                resp.status_code,
                resp.text[:200],
            )
            return False
        except requests.RequestException as e:
            if attempt == 2:
                logger.error("doveadm authCacheFlush for %s failed: %s", username, e)
    return False


def record_event(
    session,
    credential: SmtpCredential,
    event: str,
    request: Request,
    detail: dict | None = None,
) -> None:
    """Append an audit row in the caller's transaction -- committed (or
    rolled back) atomically with the change it describes."""
    session.add(
        SmtpCredentialEvent(
            credential_id=credential.id,
            organization_id=credential.organization_id,
            username=credential.username,
            event=event,
            actor=resolve_actor(request),
            source_ip=source_ip_of(request),
            detail=detail,
        )
    )
