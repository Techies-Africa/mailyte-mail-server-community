#!/usr/bin/env python3
"""
Domain Management API Routes
Production-grade domain management with comprehensive features and organization support.

This module provides API endpoints for:
- Domain CRUD operations with proper validation and relationships
- Domain quota and rate limit management
- Domain statistics and analytics with organization hierarchy
- Integration with the new organization/domain/email account structure

Key Features:
- Organization-aware domain management
- Comprehensive validation and error handling
- Quota and usage management
- Enhanced multi-tenant support
"""

import html as html_module
import ipaddress
import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


def sanitize_text(value):
    """Sanitize free-text input to prevent stored XSS. Strips all HTML tags."""
    if not value or not isinstance(value, str):
        return value
    import re as _re

    value = _re.sub(r"<[^>]+>", "", value)  # Strip HTML tags
    return html_module.escape(value, quote=True)


import base64
import os
import sys
from pathlib import Path

import dns.exception
import dns.resolver
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from utils import dkim_sync
from utils.auth import create_api_response, org_filter, require_api_key, verify_domain_scope
from utils.database import get_db_connection
from utils.smtp_credentials import flush_auth_cache

from database.models.certificates import DKIMKey
from database.models.core import Domain, EmailAccount, Organization, SmtpCredential
from shared.envelope_encryption import encrypt_private_key

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from schemas.common import ErrorResponse, SimpleMessageResponse
from schemas.domain import (
    DomainCreateResponse,
    DomainDeleteResponse,
    DomainDetailResponse,
    DomainEditResponse,
    DomainListResponse,
    DomainPolicyResponse,
    DomainQuotaResponse,
    DomainQuotaUpdateResponse,
    DomainStatsResponse,
    DomainVerifyDNSResponse,
    DomainWriteResponse,
)

from shared.webhook_dispatcher import Events, dispatch_event

# ---------------------------------------------------------------------------
# Pydantic request/response models for OpenAPI documentation
# ---------------------------------------------------------------------------


class DomainCreate(BaseModel):
    """Request body for creating a new mail domain."""

    domain: str = Field(
        ..., description="The domain name to add (e.g., example.com)", example="example.com"
    )
    organization_id: str = Field(
        ..., description="ID of the organization that owns this domain", example="org-123"
    )
    description: str | None = Field(
        None, description="Human-readable description of this domain", example="Main company domain"
    )
    dkim_enabled: bool = Field(
        True, description="Whether to enable DKIM signing for outbound email"
    )
    dkim_selector: str = Field(
        "default", description="DKIM selector name used in DNS record", example="default"
    )
    max_users: int = Field(1000, description="Maximum number of mailboxes allowed on this domain")
    max_quota: int = Field(10737418240, description="Maximum total storage in bytes (default 10GB)")
    active: bool = Field(True, description="Whether the domain is active and accepting mail")
    rate_limits: dict[str, Any] | None = Field(
        None, description="Rate-limiting rules for outbound email"
    )
    storage_quotas: dict[str, Any] | None = Field(
        None, description="Per-account default storage quota overrides"
    )
    external_id: str | None = Field(
        None, description="External system ID for integration", example="dom-ext-456"
    )


class DomainUpdate(BaseModel):
    """Request body for updating an existing domain."""

    description: str | None = Field(None, description="Updated description")
    active: bool | None = Field(None, description="Enable or disable the domain")
    max_users: int | None = Field(None, description="Updated max users limit")
    max_quota: int | None = Field(None, description="Updated max storage quota in bytes")
    dkim_enabled: bool | None = Field(None, description="Enable or disable DKIM signing")
    dkim_selector: str | None = Field(None, description="Updated DKIM selector name")
    rate_limits: dict[str, Any] | None = Field(None, description="Updated rate-limiting rules")
    storage_quotas: dict[str, Any] | None = Field(
        None, description="Updated storage quota overrides"
    )
    external_id: str | None = Field(None, description="Updated external system ID")


class DomainQuotaUpdate(BaseModel):
    """Request body for updating domain quota settings."""

    max_quota: int | None = Field(
        None, description="New total storage quota in bytes", example=21474836480
    )
    max_users: int | None = Field(None, description="New maximum mailbox count", example=500)
    rate_limits: dict[str, Any] | None = Field(None, description="Updated rate-limiting rules")
    storage_quotas: dict[str, Any] | None = Field(
        None, description="Updated per-account storage quota overrides"
    )


class DNSRecord(BaseModel):
    """A single DNS record that must be configured for the domain."""

    type: str = Field(..., description="DNS record type (MX, TXT, CNAME)", example="MX")
    name: str = Field(..., description="DNS record name", example="example.com")
    value: str = Field(..., description="DNS record value", example="mx.mailyte.com.")
    priority: int | None = Field(None, description="Priority (for MX records)", example=10)
    description: str = Field(..., description="Human-readable explanation of this record")


class LegacyDomainEdit(BaseModel):
    """Request body for the legacy (mailcow-compatible) domain edit endpoint."""

    items: list[str] = Field(
        ..., description="List of domain names or IDs to update", example=["example.com"]
    )
    attr: dict[str, Any] = Field(
        ...,
        description="Attribute key-value pairs to set on each domain",
        example={"active": 1, "maxquota": 10240},
    )


class DomainPolicyEdit(BaseModel):
    """Request body for editing domain spam/security policies."""

    domain: str = Field(
        ..., description="Domain name to update the policy for", example="example.com"
    )
    policy_bl_only: int = Field(0, description="Blacklist-only mode (0 or 1)")
    policy_reject_spam: int = Field(0, description="Reject detected spam (0 or 1)")
    policy_greylist: int = Field(1, description="Enable greylisting (0 or 1)")
    policy_rbl: int = Field(1, description="Enable RBL checks (0 or 1)")


# Allowed columns for the legacy edit_domain endpoint (mailcow-compatible field names)
ALLOWED_LEGACY_DOMAIN_EDIT_COLUMNS = {
    "description",
    "aliases",
    "mailboxes",
    "maxquota",
    "quota",
    "defquota",
    "transport",
    "backupmx",
    "active",
    "relay_all_recipients",
    "rl_value",
    "rl_frame",
    "gal",
}

logger = logging.getLogger(__name__)
router = APIRouter()


def get_db_session():
    """Get SQLAlchemy session"""
    engine = create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    Session = sessionmaker(bind=engine)
    return Session()


def validate_domain(domain):
    """Validate domain name format"""
    pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
    return re.match(pattern, domain) is not None


def validate_domain_data(data, is_update=False):
    """Validate domain data"""
    errors = []

    if not is_update and ("domain" not in data or not data["domain"]):
        errors.append("Domain name is required")
    elif "domain" in data and not validate_domain(data["domain"]):
        errors.append("Invalid domain name format")

    if not is_update and ("organization_id" not in data or not data["organization_id"]):
        errors.append("Organization ID is required")

    if "max_quota" in data:
        try:
            quota = int(data["max_quota"])
            if quota < 0:
                errors.append("Max quota must be non-negative")
        except (ValueError, TypeError):
            errors.append("Max quota must be a valid number")

    if "max_users" in data:
        try:
            users = int(data["max_users"])
            if users < 0:
                errors.append("Max users must be non-negative")
        except (ValueError, TypeError):
            errors.append("Max users must be a valid number")

    # The selector becomes one DNS label ({selector}._domainkey.{domain}) AND
    # one path component of rspamd's key file ({domain}.{selector}.key in the
    # shared key directory) -- a dot or slash in it is at best a broken DNS
    # name and at worst path traversal in a multi-tenant directory, so it is
    # rejected here rather than trusted downstream (utils/dkim_sync.py
    # independently refuses unsafe names as defence in depth).
    if data.get("dkim_selector") is not None and not re.match(
        r"^[a-zA-Z0-9]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9])?$", str(data["dkim_selector"])
    ):
        errors.append(
            "DKIM selector must be a single DNS label (letters, digits, '-' or '_'; "
            "no dots), at most 63 characters"
        )

    return errors


def generate_dkim_keypair():
    """Generate a 2048-bit RSA key pair for DKIM signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()

    public_key = private_key.public_key()
    public_der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_b64 = base64.b64encode(public_der).decode()

    return private_pem, public_b64


def get_server_hostname() -> str:
    """The public mail hostname to put in, and check DNS records against.

    Docker sets HOSTNAME to the container ID for any service whose compose
    block doesn't pass it explicitly -- which `api`'s didn't. That silently
    produced junk for every domain created since: records reading
    "MX c17a958e9f9b." and "v=spf1 include:spf.c17a958e9f9b", and an
    MX/SPF verification that compared live DNS against a container ID and so
    could never pass. A real mail hostname is always a FQDN, so a value with
    no dot in it is the container ID it looks like, not a hostname.
    """
    hostname = os.getenv("MAIL_HOSTNAME") or os.getenv("HOSTNAME", "")
    if "." not in hostname:
        return "mx.mailyte.com"
    return hostname


def get_spf_host() -> str:
    """The hostname customers `include:` in their SPF record.

    Deliberately separate from get_server_hostname(), because the two are
    independent facts that were previously forced to move together.

    SPF was derived as `spf.{server_hostname}`, so renaming the mail host
    silently repointed every customer's SPF at a hostname that had to be
    created before the rename could happen -- and if it wasn't, receivers get
    a permerror, which is worse than publishing no SPF at all. Worse, the
    verifier only checks that the string appears in the TXT record; it never
    resolves it. So the panel would go green while SPF authentication was
    actually broken.

    Keeping this configurable means the MX host can move to a new name while
    SPF keeps pointing at the record that genuinely exists and resolves.
    """
    explicit = os.getenv("MAIL_SPF_HOST", "").strip()
    if explicit:
        return explicit
    return f"spf.{get_server_hostname()}"


def generate_dns_records(
    domain: str,
    server_hostname: str,
    dkim_public_key: str = None,
    dkim_selector: str = "default",
):
    """Generate the DNS records a customer needs to configure.

    dkim_selector must be the domain's ACTUAL signing selector. This used to
    hardcode `default._domainkey`, which silently disagreed with the selector
    the verify-dns endpoint checks (domains.dkim_selector) for any domain on
    a non-default selector -- i.e. every domain that has been through the
    two-step rotate/activate flow: customers were told to publish the key at
    a name rspamd never signs with, and verification then failed forever.
    """
    records = [
        {
            "type": "MX",
            "name": domain,
            "value": f"{server_hostname}.",
            "priority": 10,
            "description": "Routes all email for this domain to Mailyte",
        },
        {
            "type": "TXT",
            "name": domain,
            "value": f"v=spf1 include:{get_spf_host()} ~all",
            "description": "Authorizes Mailyte servers to send email for this domain",
        },
        {
            "type": "TXT",
            "name": f"_dmarc.{domain}",
            # dmarc@ is the platform-wide convention (see autoconfig/app.py).
            "value": "v=DMARC1; p=quarantine; rua=mailto:dmarc@" + domain,
            "description": "Policy for handling emails that fail SPF/DKIM checks",
        },
    ]
    if dkim_public_key:
        records.append(
            {
                "type": "TXT",
                "name": f"{dkim_selector or 'default'}._domainkey.{domain}",
                "value": f"v=DKIM1; k=rsa; p={dkim_public_key}",
                "description": "Public key for DKIM signature verification",
            }
        )
    return records


# DNS verification must see what the WORLD sees, not the container's view.
# The default resolver is Docker's embedded DNS (127.0.0.11), which (a) resolves
# the mail server's own hostname to its INTERNAL container IP (e.g. courier ->
# 172.25.0.43), so the SPF authorization check compared a domain's SPF against
# the wrong IP and failed valid records, and (b) serves cached/stale answers, so
# a just-corrected record kept reading as its old value. Pin the verifier to
# public recursive resolvers instead (override with DNS_VERIFY_RESOLVERS).
_VERIFY_NAMESERVERS = [
    ip.strip()
    for ip in os.getenv("DNS_VERIFY_RESOLVERS", "8.8.8.8,1.1.1.1").split(",")
    if ip.strip()
]


def _build_verify_resolver() -> "dns.resolver.Resolver":
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = _VERIFY_NAMESERVERS
    resolver.timeout = 5
    resolver.lifetime = 10
    return resolver


_verify_resolver = _build_verify_resolver()


def check_dns_record(query_name, record_type):
    """Resolve a DNS record, returning dig-style text ("" when there's no answer).

    This used to shell out to `dig`, which is not installed in this image:
    subprocess raised FileNotFoundError, the bare `except` swallowed it, and
    every MX/SPF/DKIM/DMARC check therefore reported "not found" for domains
    that were in fact configured correctly. Resolving in-process removes that
    whole failure mode, and a lookup that genuinely fails is now logged
    instead of being indistinguishable from a missing record.

    Uses public resolvers (see _VERIFY_NAMESERVERS), never the container's own
    resolver, so verification reflects the public DNS view.
    """
    try:
        answers = _verify_resolver.resolve(query_name, record_type, lifetime=10)
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
    ):
        return ""
    except Exception as e:
        logger.warning(f"DNS lookup failed for {record_type} {query_name}: {e}")
        return ""

    values = []
    for rdata in answers:
        if record_type == "TXT":
            # TXT values over 255 bytes arrive as several chunks -- join them
            # so a split DKIM key reads back as the single string it is.
            values.append(b"".join(rdata.strings).decode("utf-8", "replace"))
        else:
            values.append(rdata.to_text())
    return "\n".join(values)


# RFC 7208 s4.6.4 caps a policy at ten DNS-querying mechanisms. Past that,
# receivers return permerror and the mail fails regardless of what the record
# says, so evaluating further would report a pass the real world rejects.
SPF_MAX_LOOKUPS = 10


def resolve_ips(name: str) -> list:
    """Every A/AAAA address for a name."""
    found = []
    for record_type in ("A", "AAAA"):
        for value in check_dns_record(name, record_type).split("\n"):
            value = value.strip()
            if not value:
                continue
            try:
                found.append(ipaddress.ip_address(value))
            except ValueError:
                continue
    return found


def find_spf_records(domain_name: str) -> list:
    """The domain's v=spf1 TXT records.

    Isolating them matters: check_dns_record joins every TXT record on the name
    with newlines, so a plain substring search ran across unrelated records --
    a site-verification string containing the SPF host would have passed the
    check on its own.
    """
    return [
        line.strip()
        for line in check_dns_record(domain_name, "TXT").split("\n")
        if line.strip().lower().startswith("v=spf1")
    ]


def spf_authorizes(domain_name: str, record: str, targets: list, budget: list, seen: set) -> bool:
    """Does this SPF record actually authorize any of `targets`?

    Evaluates the mechanisms rather than searching for a hostname, because the
    two questions have different answers in both directions. A domain that
    authorizes us with `ip4:` or `mx` is correctly configured and the textual
    check called it broken; a domain carrying `include:` for a host that does
    not resolve is broken and the textual check called it fine.

    Only `+` (the implicit default) authorizes -- `-include:` and `~mx` are
    refusals, and reading them as matches is how a checker green-lights a
    domain receivers will reject.
    """
    if record.lower() in seen:
        # An include loop is a permerror at the receiver, not an authorization.
        return False
    seen.add(record.lower())

    redirect = None

    for term in record.split()[1:]:
        if budget[0] <= 0:
            return False

        qualifier = "+"
        if term[:1] in "+-~?":
            qualifier, term = term[0], term[1:]
        lowered = term.lower()

        if lowered.startswith("redirect="):
            redirect = term.split("=", 1)[1]
            continue
        if lowered.startswith("exp=") or lowered == "all":
            continue

        matched = False

        if lowered.startswith(("ip4:", "ip6:")):
            try:
                network = ipaddress.ip_network(term.split(":", 1)[1], strict=False)
            except ValueError:
                continue
            matched = any(ip in network for ip in targets if ip.version == network.version)

        elif lowered == "a" or lowered.startswith("a:"):
            budget[0] -= 1
            host = term.split(":", 1)[1] if ":" in term else domain_name
            addresses = resolve_ips(host)
            matched = any(ip in addresses for ip in targets)

        elif lowered == "mx" or lowered.startswith("mx:"):
            budget[0] -= 1
            host = term.split(":", 1)[1] if ":" in term else domain_name
            addresses = []
            for line in check_dns_record(host, "MX").split("\n"):
                parts = line.split()
                if parts:
                    addresses.extend(resolve_ips(parts[-1].rstrip(".")))
            matched = any(ip in addresses for ip in targets)

        elif lowered.startswith("include:"):
            budget[0] -= 1
            included = term.split(":", 1)[1]
            for nested in find_spf_records(included):
                if spf_authorizes(included, nested, targets, budget, seen):
                    matched = True
                    break

        if matched and qualifier == "+":
            return True

    if redirect and budget[0] > 0:
        budget[0] -= 1
        for nested in find_spf_records(redirect):
            if spf_authorizes(redirect, nested, targets, budget, seen):
                return True

    return False


def check_spf(domain_name: str, server_hostname: str) -> dict:
    """Verify that the domain's SPF authorizes this mail server."""
    expected = f"include:{get_spf_host()}"
    records = find_spf_records(domain_name)

    if not records:
        return {
            "status": "fail",
            "found": False,
            "expected": expected,
            "message": "No SPF record published for this domain",
        }

    if len(records) > 1:
        return {
            "status": "fail",
            "found": True,
            "expected": expected,
            "actual": " | ".join(records),
            "message": (
                f"{len(records)} SPF records published; RFC 7208 allows one and "
                "receivers treat more as a permerror"
            ),
        }

    record = records[0]
    targets = resolve_ips(server_hostname)

    if not targets:
        # Our own hostname would not resolve. That is our problem, not the
        # customer's, so fall back to the textual check rather than reporting a
        # failure against them that we never actually established.
        included = get_spf_host() in record
        logger.warning(
            f"SPF check for {domain_name} fell back to a text match: "
            f"{server_hostname} did not resolve"
        )
        return {
            "status": "pass" if included else "fail",
            "found": True,
            "expected": expected,
            "actual": record,
            "message": None if included else f"SPF record does not include {get_spf_host()}",
        }

    budget = [SPF_MAX_LOOKUPS]
    if spf_authorizes(domain_name, record, targets, budget, set()):
        return {"status": "pass", "found": True, "expected": expected, "actual": record}

    if budget[0] <= 0:
        return {
            "status": "fail",
            "found": True,
            "expected": expected,
            "actual": record,
            "message": (
                f"SPF record needs more than {SPF_MAX_LOOKUPS} DNS lookups to "
                "evaluate; receivers return permerror before reaching this server"
            ),
        }

    addresses = ", ".join(str(ip) for ip in targets)
    return {
        "status": "fail",
        "found": True,
        "expected": expected,
        "actual": record,
        "message": f"SPF record does not authorize this mail server ({addresses})",
    }


# Whitelisted ORDER BY targets for GET /domains (console phase-02 SS2.5).
# Closed mapping for the same reason organizations.py has one: ORDER BY
# takes no bound parameter, so an un-whitelisted sort key would have to be
# interpolated into the SQL.
#
# There is deliberately no "status" here or in the filters below: `domains`
# has no status column. Its only state is the `active` boolean (see
# database/models/core.py and 001_init_schema.sql) -- DNS/DKIM verification
# state that phase-02 SS2.5 also wants is computed live by
# POST /{domain_id}/verify-dns, not stored, so it cannot be sorted on.
_DOMAIN_SORT_KEYS = ("domain", "created_at", "total_storage_used")
_SORT_DIRECTIONS = ("asc", "desc")


def _parse_bool_param(raw: str | None) -> tuple[bool, bool | None]:
    """ "true"/"false" -> (valid, value); unrecognised input is reported
    invalid rather than coerced, so a typo 422s instead of silently
    inverting the filter."""
    if raw is None:
        return True, None
    normalised = raw.strip().lower()
    if normalised in ("true", "1"):
        return True, True
    if normalised in ("false", "0"):
        return True, False
    return False, None


@router.get(
    "/",
    summary="List all domains",
    description="Retrieve a paginated list of mail domains, searchable by name and sortable by "
    "name, creation date or storage used. Platform-scope callers see every organization and may "
    "narrow to one with `organization_id`; tenant credentials always see only their own.",
    response_model=DomainListResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Unknown sort key/direction or bad boolean"},
        500: {"model": ErrorResponse, "description": "Failed to retrieve domains"},
    },
)
@require_api_key("read")
async def list_domains(
    request: Request,
    q: str | None = Query(None, description="Search domain name (LIKE)"),
    organization_id: str | None = Query(
        None,
        description="Platform scope only -- narrow to a single organization. Ignored for tenant "
        "credentials, which always see only their own org.",
    ),
    active: str | None = Query(
        None, description="true|false -- filter on domains.active (there is no status column)"
    ),
    sort_by: str = Query("domain", description="domain | created_at | total_storage_used"),
    sort_dir: str = Query("asc", description="asc | desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
):
    """List all domains for the caller's org (or, for a platform-scope
    credential, every org -- mirrors mailboxes.py's list_email_accounts;
    domains.py previously hard-scoped every route to get_org_context()'s
    single organization_id with no platform-scope bypass, so a
    platform-scope key -- e.g. mailyte-api's single global
    MAIL_SERVER_API_KEY, which every tenant's domain operations proxy
    through -- could only ever see the org it happened to be bound to).

    organization_id follows org_filter()'s principle rather than being
    validated-then-rejected: for tenant scope the caller-supplied value is
    silently dropped, never honoured, so the most likely escalation attempt
    (passing someone else's org id and hoping the server trusts it) cannot
    work regardless of what the client sends.
    """
    per_page = min(per_page, 200)
    ctx = request.state.auth_context

    if sort_by not in _DOMAIN_SORT_KEYS:
        return JSONResponse(
            content=create_api_response(
                "error", f"sort_by must be one of {', '.join(_DOMAIN_SORT_KEYS)}"
            ),
            status_code=422,
        )
    if sort_dir not in _SORT_DIRECTIONS:
        return JSONResponse(
            content=create_api_response("error", "sort_dir must be 'asc' or 'desc'"),
            status_code=422,
        )
    active_valid, active_flag = _parse_bool_param(active)
    if not active_valid:
        return JSONResponse(
            content=create_api_response("error", "active must be 'true' or 'false'"),
            status_code=422,
        )

    session = get_db_session()
    try:
        query = session.query(Domain)
        if ctx["scope"] == "organization":
            query = query.filter_by(organization_id=ctx["organization_id"])
        elif organization_id:
            query = query.filter_by(organization_id=organization_id)

        if q:
            query = query.filter(Domain.domain.like(f"%{q}%"))
        if active_flag is not None:
            query = query.filter(Domain.active.is_(active_flag))

        sort_columns = {
            "domain": Domain.domain,
            "created_at": Domain.created_at,
            "total_storage_used": Domain.total_storage_used,
        }
        sort_column = sort_columns[sort_by]
        ordering = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
        # id tiebreaker keeps paging stable across non-unique sort columns.
        query = query.order_by(ordering, Domain.id.asc())

        total = query.count()
        domains = query.offset((page - 1) * per_page).limit(per_page).all()

        # Two grouped lookups for the whole page instead of two queries per
        # row. This loop previously ran a COUNT and an Organization fetch
        # per domain -- 2 + 2N queries, 402 for a full 200-row page, and
        # cross-tenant pages made the org fetch a near-guaranteed miss on
        # any per-session identity map.
        domain_ids = [domain.id for domain in domains]
        org_ids = {domain.organization_id for domain in domains}

        account_counts: dict = {}
        org_names: dict = {}
        if domain_ids:
            account_counts = {
                row.domain_id: int(row.account_count or 0)
                for row in session.query(
                    EmailAccount.domain_id.label("domain_id"),
                    func.count(EmailAccount.id).label("account_count"),
                )
                .filter(EmailAccount.domain_id.in_(domain_ids))
                .group_by(EmailAccount.domain_id)
                .all()
            }
            org_names = {
                org_id: name
                for org_id, name in session.query(Organization.id, Organization.name)
                .filter(Organization.id.in_(org_ids))
                .all()
            }

        result = []
        for domain in domains:
            domain_data = domain.to_dict()
            domain_data["email_account_count"] = account_counts.get(domain.id, 0)
            domain_data["organization_name"] = org_names.get(domain.organization_id, "Unknown")
            result.append(domain_data)

        return create_api_response(
            "success",
            "Domains retrieved successfully",
            {
                "items": result,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": (total + per_page - 1) // per_page,
                },
            },
        )

    except Exception as e:
        logger.error(f"List domains error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve domains"), status_code=500
        )
    finally:
        session.close()


@router.get(
    "/{domain_id}",
    summary="Get domain details",
    description="Retrieve detailed information about a specific domain, including its organization, email accounts, and usage statistics.",
    response_model=DomainDetailResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to retrieve domain"},
    },
)
@require_api_key("read")
async def get_domain(domain_id: str, request: Request):
    """Get specific domain with detailed information"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain or (
            ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        domain_data = domain.to_dict()

        # Add organization information
        org = session.query(Organization).filter_by(id=domain.organization_id).first()
        domain_data["organization"] = org.to_dict() if org else None

        # Add the DKIM record. Only POST /domains used to return this, so any
        # consumer that fetched a domain after creation saw dkim_record: null
        # and reported "no DKIM key generated" for domains that have a working,
        # correctly published key. Sourced from the same dkim_keys row rspamd
        # signs with, so what we advertise is what actually signs.
        dkim_key = session.query(DKIMKey).filter_by(domain_id=domain.id, active=True).first()
        dkim_public_b64 = _dkim_public_key_b64(dkim_key.public_key) if dkim_key else None
        dkim_selector = (
            (dkim_key.selector if dkim_key else None) or domain.dkim_selector or "default"
        )
        domain_data["dkim_record"] = (
            f"v=DKIM1; k=rsa; p={dkim_public_b64}" if dkim_public_b64 else None
        )
        domain_data["dkim_selector"] = dkim_selector
        domain_data["dkim_dns_name"] = f"{dkim_selector}._domainkey.{domain.domain}"

        # Regenerate the DNS records rather than replaying what POST returned
        # when the domain was created.
        #
        # Only POST used to emit these, and the consumer stores that response
        # verbatim -- so a domain created before get_server_hostname() was
        # fixed still hands out "MX c17a958e9f9b." and
        # "include:spf.c17a958e9f9b", the container ID Docker had set as
        # HOSTNAME. Those values are frozen at creation and no amount of
        # re-fetching corrects them, which puts two disagreeing record sets in
        # front of the customer: a live one and a fossil.
        #
        # Generating here means the records always reflect the hostname and
        # DKIM key in force right now, and changing MAIL_HOSTNAME propagates to
        # every domain on the next read instead of only to newly created ones.
        domain_data["dns_records"] = generate_dns_records(
            domain.domain,
            get_server_hostname(),
            dkim_public_b64 if domain.dkim_enabled else None,
            dkim_selector=dkim_selector,
        )

        # Add email accounts
        email_accounts = session.query(EmailAccount).filter_by(domain_id=domain_id).all()
        domain_data["email_accounts"] = [acc.to_dict() for acc in email_accounts]
        domain_data["email_account_count"] = len(email_accounts)

        # Add usage statistics
        total_account_storage = sum(acc.storage_used for acc in email_accounts)
        domain_data["usage_statistics"] = {
            "account_usage_percentage": domain.get_account_usage_percentage(),
            "storage_usage_percentage": domain.get_storage_usage_percentage(),
            "total_account_storage": total_account_storage,
        }

        return create_api_response("success", "Domain retrieved successfully", domain_data)

    except Exception as e:
        logger.error(f"Get domain error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve domain"), status_code=500
        )
    finally:
        session.close()


@router.post(
    "/",
    summary="Create a new domain",
    description="Add a mail domain to the organization. Automatically generates DKIM signing keys and returns the DNS records needed for email delivery.",
    response_model=DomainCreateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No data provided / validation failed"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
        409: {
            "model": ErrorResponse,
            "description": "DOMAIN_ALREADY_CLAIMED (by this org or another) / external_id already exists",
        },
        500: {"model": ErrorResponse, "description": "Failed to create domain"},
    },
)
@require_api_key("write")
async def create_domain(request: Request):
    """Create new domain"""
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    # Validate data
    errors = validate_domain_data(data)
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    # A caller-supplied organization_id was previously honoured VERBATIM, with
    # no check against the caller's own org -- so any tenant credential could
    # create a domain inside any other organization. utils/auth.py's
    # org_filter docstring names exactly this shape as "the most likely
    # escalation vector in the system"; this route was it.
    #
    # Organization scope is now forced to the caller's own org and a supplied
    # value is ignored rather than rejected, matching org_filter's principle
    # that silently dropping it protects every call site automatically.
    # Platform scope still chooses freely -- creating a domain for any tenant
    # is precisely what an operator (and Laravel's provisioning path) does.
    ctx = request.state.auth_context
    if ctx["scope"] == "organization":
        data["organization_id"] = ctx["organization_id"]
    elif not data.get("organization_id"):
        return JSONResponse(
            content=create_api_response(
                "error", "organization_id is required for a platform-scope caller"
            ),
            status_code=400,
        )

    session = get_db_session()
    try:
        # Check if organization exists
        org = session.query(Organization).filter_by(id=data["organization_id"]).first()
        if not org:
            return JSONResponse(
                content=create_api_response("error", "Organization not found"), status_code=404
            )

        # Check if domain already exists (case-insensitive). Domain names are
        # globally unique, not per-org (domains.domain has a UNIQUE index --
        # confirmed live, phase-04 task 4.4), so this can be a collision with
        # either the caller's own org or a different tenant entirely.
        existing = session.query(Domain).filter(Domain.domain.ilike(data["domain"])).first()
        if existing:
            if existing.organization_id == data["organization_id"]:
                # Same org re-claiming its own domain -- safe to hand back
                # the existing resource (phase-04 task 4.3).
                return JSONResponse(
                    content=create_api_response(
                        "error",
                        f"Domain {data['domain']} already exists",
                        {"existing_id": existing.id},
                        error_code="DOMAIN_ALREADY_CLAIMED",
                    ),
                    status_code=409,
                )
            # Claimed by another org -- generic message only, no detail that
            # would confirm which tenant owns it (conventions §8).
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Domain {data['domain']} already exists",
                    error_code="DOMAIN_ALREADY_CLAIMED",
                ),
                status_code=409,
            )

        # Check if external_id already exists (if provided)
        if data.get("external_id"):
            existing_external = (
                session.query(Domain).filter_by(external_id=data["external_id"]).first()
            )
            if existing_external:
                return JSONResponse(
                    content=create_api_response(
                        "error", "Domain with this external_id already exists"
                    ),
                    status_code=409,
                )

        # Create domain
        domain = Domain(
            domain=data["domain"],
            organization_id=data["organization_id"],
            description=sanitize_text(data.get("description")),
            active=data.get("active", True),
            max_quota=data.get("max_quota", 10737418240),  # 10GB default
            max_users=data.get("max_users", 1000),
            dkim_enabled=data.get("dkim_enabled", True),
            dkim_selector=data.get("dkim_selector", "default"),
            rate_limits=data.get("rate_limits", {}),
            storage_quotas=data.get("storage_quotas", {}),
            external_id=data.get("external_id"),
        )

        session.add(domain)
        try:
            session.commit()
        except IntegrityError:
            # Race: another request claimed this domain (or external_id)
            # between our checks above and this commit. The UNIQUE
            # constraint is the real guard; translate its violation to 409
            # instead of a raw DB error reaching the client (task 4.4).
            session.rollback()
            race_existing = (
                session.query(Domain).filter(Domain.domain.ilike(data["domain"])).first()
            )
            if race_existing and race_existing.organization_id == data["organization_id"]:
                return JSONResponse(
                    content=create_api_response(
                        "error",
                        f"Domain {data['domain']} already exists",
                        {"existing_id": race_existing.id},
                        error_code="DOMAIN_ALREADY_CLAIMED",
                    ),
                    status_code=409,
                )
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Domain {data['domain']} already exists",
                    error_code="DOMAIN_ALREADY_CLAIMED",
                ),
                status_code=409,
            )

        # Generate DKIM keys if DKIM is enabled
        dkim_record = None
        if domain.dkim_enabled:
            try:
                private_pem, public_b64 = generate_dkim_keypair()
                selector = domain.dkim_selector or "default"

                # private_key stays NULL -- only the encrypted columns are
                # written (phase-07 C2). private_pem lives in memory only
                # long enough to encrypt it here.
                encrypted = encrypt_private_key(private_pem)
                dkim_key = DKIMKey(
                    domain_id=domain.id,
                    selector=selector,
                    private_key=None,
                    private_key_ciphertext=encrypted.ciphertext,
                    private_key_nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                    public_key=public_b64,
                    active=True,
                )
                session.add(dkim_key)
                session.commit()

                dkim_record = f"v=DKIM1; k=rsa; p={public_b64}"
                logger.info(
                    f"DKIM keys generated for domain {domain.domain} (selector: {selector})"
                )
            except Exception as dkim_err:
                logger.error(f"Failed to generate DKIM keys for domain {domain.domain}: {dkim_err}")
                # Domain was already created successfully; don't fail the whole request

        if dkim_record:
            # Write-through to rspamd's key directory + selector map (the DB
            # row alone signs nothing). Best-effort: on failure the log says
            # what to run, and reconcile heals it -- the API response must
            # not fail for a domain that was created correctly.
            dkim_sync.try_sync_domain(domain.domain)

        dispatch_event(
            Events.DOMAIN_ADDED,
            data={
                "domain_id": domain.id,
                "domain": domain.domain,
                "organization_id": domain.organization_id,
            },
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        response_data = domain.to_dict()
        if dkim_record:
            response_data["dkim_record"] = dkim_record
            response_data["dkim_selector"] = domain.dkim_selector or "default"
            response_data["dkim_dns_name"] = (
                f"{domain.dkim_selector or 'default'}._domainkey.{domain.domain}"
            )

        server_hostname = get_server_hostname()
        dkim_pub = public_b64 if domain.dkim_enabled and dkim_record else None
        response_data["dns_records"] = generate_dns_records(
            domain.domain,
            server_hostname,
            dkim_pub,
            dkim_selector=domain.dkim_selector or "default",
        )

        return JSONResponse(
            content=create_api_response("success", "Domain created successfully", response_data),
            status_code=201,
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Create domain error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to create domain"), status_code=500
        )
    finally:
        session.close()


@router.put(
    "/{domain_id}",
    summary="Update a domain",
    description="Update the settings of an existing domain such as description, active status, quotas, and DKIM configuration.",
    response_model=DomainWriteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No data provided / validation failed"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
        409: {"model": ErrorResponse, "description": "external_id already exists"},
        500: {"model": ErrorResponse, "description": "Failed to update domain"},
    },
)
@require_api_key("write")
async def update_domain(domain_id: str, request: Request):
    """Update domain"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No data provided"), status_code=400
        )

    # Validate data
    errors = validate_domain_data(data, is_update=True)
    if errors:
        return JSONResponse(
            content=create_api_response("error", "Validation failed", {"errors": errors}),
            status_code=400,
        )

    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        # Previously checked only `if not domain` -- any org-scoped write key
        # could update any other org's domain outright, with no isolation at
        # all (distinct from, and worse than, the platform-scope bug this
        # pass otherwise fixes). Closing that gap here too.
        if not domain or (
            ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        # Update fields
        if "description" in data:
            domain.description = data["description"]
        if "active" in data:
            domain.active = data["active"]
        if "max_quota" in data:
            domain.max_quota = data["max_quota"]
        if "max_users" in data:
            domain.max_users = data["max_users"]
        if "dkim_enabled" in data:
            domain.dkim_enabled = data["dkim_enabled"]
        if "dkim_selector" in data:
            domain.dkim_selector = data["dkim_selector"]
        if "rate_limits" in data:
            domain.rate_limits = data["rate_limits"]
        if "storage_quotas" in data:
            domain.storage_quotas = data["storage_quotas"]
        if "external_id" in data:
            # Check if the new external_id already exists for other domains
            existing_external = (
                session.query(Domain)
                .filter(
                    Domain.external_id == data["external_id"],
                    Domain.id != domain_id,  # Exclude the current domain
                )
                .first()
            )

            if existing_external:
                return JSONResponse(
                    content=create_api_response(
                        "error", "Domain with this external_id already exists"
                    ),
                    status_code=409,
                )
            domain.external_id = data["external_id"]

        domain.updated_at = datetime.now()
        session.commit()

        if {"dkim_enabled", "dkim_selector", "active"} & set(data.keys()):
            # dkim_enabled=false / active=false must actually stop rspamd
            # signing: with try_fallback rspamd signs any domain whose
            # default-selector key file exists, so the file has to go, not
            # just the DB flag. Re-enabling re-exports the stored keys, and a
            # selector change refreshes the selector map. Best-effort by
            # design (see utils/dkim_sync.try_sync_domain).
            dkim_sync.try_sync_domain(domain.domain)

        dispatch_event(
            Events.DOMAIN_UPDATED,
            data={
                "domain_id": domain.id,
                "domain": domain.domain,
                "organization_id": domain.organization_id,
                "updated_fields": list(data.keys()),
            },
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        return create_api_response("success", "Domain updated successfully", domain.to_dict())

    except Exception as e:
        session.rollback()
        logger.error(f"Update domain error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update domain"), status_code=500
        )
    finally:
        session.close()


@router.delete(
    "/{domain_id}",
    summary="Delete a domain",
    description="Permanently remove a domain. The domain must have no remaining email accounts; delete those first.",
    response_model=SimpleMessageResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Domain still has email accounts"},
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to delete domain"},
    },
)
@require_api_key("write")
async def delete_domain_by_id(domain_id: str, request: Request):
    """Delete domain and all related email accounts"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain or (
            ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        # Check for existing email accounts
        account_count = session.query(EmailAccount).filter_by(domain_id=domain_id).count()

        if account_count > 0:
            return JSONResponse(
                content=create_api_response(
                    "error",
                    f"Cannot delete domain with {account_count} email accounts. Delete them first.",
                ),
                status_code=400,
            )

        domain_id_val = domain.id
        domain_name_val = domain.domain
        org_id_val = domain.organization_id

        # SMTP credentials must go WITH the domain. smtp_credentials.domain_id
        # is the one FK to domains that does not cascade, so a domain holding
        # any credential could not be deleted at all: the IntegrityError was
        # swallowed by the catch below and returned as an opaque 500 "Failed
        # to delete domain" with no hint of the cause. Every provisioned
        # domain now auto-mints an internal platform send credential, so this
        # made essentially every domain undeletable (found live 2026-09-08).
        #
        # Deleting them is the correct semantic, not merely the convenient
        # one: a credential authenticates sending FOR this domain and is
        # meaningless once it is gone -- leaving it behind would strand an
        # access token pointing at a domain this platform no longer hosts.
        # Mailboxes are different and still refuse the delete above: they
        # hold mail, so removing them has to be a deliberate act.
        credentials = session.query(SmtpCredential).filter_by(domain_id=domain_id_val).all()
        credential_usernames = [c.username for c in credentials]
        for credential in credentials:
            session.delete(credential)

        session.delete(domain)
        session.commit()

        # Dovecot caches successful auth for up to an hour; without this a
        # deleted domain's credential keeps authenticating after its row is
        # gone (the same cache gotcha as revocation).
        for username in credential_usernames:
            try:
                flush_auth_cache(username)
            except Exception as exc:  # noqa: BLE001 - best effort, never block the delete
                logger.warning(f"Auth cache flush failed for {username}: {exc}")

        # Remove the tenant's key files and its selector-map entry -- a
        # leftover file would keep rspamd signing for a domain this platform
        # no longer hosts (dkim_keys rows themselves go via ON DELETE CASCADE).
        dkim_sync.try_sync_domain(domain_name_val)

        dispatch_event(
            Events.DOMAIN_DELETED,
            data={
                "domain_id": domain_id_val,
                "domain": domain_name_val,
                "organization_id": org_id_val,
            },
            org_id=org_id_val,
            domain=domain_name_val,
            source_service="api",
        )

        return create_api_response("success", "Domain deleted successfully")

    except Exception as e:
        session.rollback()
        logger.error(f"Delete domain error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete domain"), status_code=500
        )
    finally:
        session.close()


@router.get(
    "/{domain_id}/verify-dns",
    summary="Verify domain DNS records",
    description="Perform live DNS lookups to check whether MX, SPF, DKIM, and DMARC records are correctly configured for the domain.",
    response_model=DomainVerifyDNSResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to verify DNS records"},
    },
)
@require_api_key("read")
async def verify_domain_dns(domain_id: str, request: Request):
    """Verify DNS records for a domain"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain or (
            ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        domain_name = domain.domain
        server_hostname = get_server_hostname()

        # Check MX record
        mx_result = check_dns_record(domain_name, "MX")
        mx_status = "fail"
        mx_actual = None
        if mx_result:
            # MX output format: "10 mx.mailyte.com." — strip priority and trailing dot
            parts = mx_result.split()
            if len(parts) >= 2:
                mx_actual = parts[-1].rstrip(".")
            else:
                mx_actual = mx_result.rstrip(".")
            if server_hostname in mx_actual:
                mx_status = "pass"
        mx_verification = {"status": mx_status, "expected": server_hostname, "actual": mx_actual}

        # Check SPF record (TXT on the domain)
        spf_verification = check_spf(domain_name, server_hostname)

        # Check DKIM record
        dkim_selector = domain.dkim_selector or "default"
        dkim_query = f"{dkim_selector}._domainkey.{domain_name}"
        dkim_result = check_dns_record(dkim_query, "TXT")
        dkim_status = "fail"
        dkim_verification = {}
        if dkim_result and "v=DKIM1" in dkim_result:
            dkim_status = "pass"
            dkim_verification = {"status": dkim_status, "found": True}
        else:
            dkim_verification = {"status": dkim_status, "message": "DKIM record not found"}

        # Check DMARC record
        dmarc_query = f"_dmarc.{domain_name}"
        dmarc_result = check_dns_record(dmarc_query, "TXT")
        dmarc_status = "fail"
        dmarc_verification = {}
        if dmarc_result and "v=DMARC1" in dmarc_result:
            dmarc_status = "pass"
            # Extract policy
            policy = None
            for part in dmarc_result.replace('"', "").split(";"):
                part = part.strip()
                if part.startswith("p="):
                    policy = part[2:]
                    break
            dmarc_verification = {"status": dmarc_status, "policy": policy}
        else:
            dmarc_verification = {"status": dmarc_status, "message": "DMARC record not found"}

        return create_api_response(
            "success",
            "DNS verification completed",
            {
                "domain": domain_name,
                "verification": {
                    "mx": mx_verification,
                    "spf": spf_verification,
                    "dkim": dkim_verification,
                    "dmarc": dmarc_verification,
                },
            },
        )

    except Exception as e:
        logger.error(f"DNS verification error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to verify DNS records"), status_code=500
        )
    finally:
        session.close()


@router.get(
    "/{domain_id}/quotas",
    summary="Get domain quotas",
    description="Retrieve quota limits and current usage for a domain, including per-account storage breakdowns.",
    response_model=DomainQuotaResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to retrieve quota information"},
    },
)
@require_api_key("read")
async def get_domain_quotas(domain_id: str, request: Request):
    """Get domain quota and usage information"""
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain or (
            ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        email_accounts = session.query(EmailAccount).filter_by(domain_id=domain_id).all()

        quota_info = {
            "domain_id": domain_id,
            "domain": domain.domain,
            "max_quota": domain.max_quota,
            "max_users": domain.max_users,
            "storage_used": domain.total_storage_used,
            "usage_percentage": domain.get_storage_usage_percentage(),
            "account_usage_percentage": domain.get_account_usage_percentage(),
            "rate_limits": domain.rate_limits or {},
            "storage_quotas": domain.storage_quotas or {},
            "email_accounts": [
                {
                    "id": acc.id,
                    "email": acc.email,
                    "storage_quota": acc.storage_quota,
                    "storage_used": acc.storage_used,
                    "usage_percentage": acc.get_storage_usage_percentage(),
                }
                for acc in email_accounts
            ],
        }

        return create_api_response(
            "success", "Domain quota information retrieved successfully", quota_info
        )

    except Exception as e:
        logger.error(f"Get domain quotas error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve quota information"),
            status_code=500,
        )
    finally:
        session.close()


@router.put(
    "/{domain_id}/quotas",
    summary="Update domain quotas",
    description="Adjust the storage quota, maximum mailbox count, rate limits, or per-account storage defaults for a domain.",
    response_model=DomainQuotaUpdateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No quota data provided"},
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to update quotas"},
    },
)
@require_api_key("write")
async def update_domain_quotas(domain_id: str, request: Request):
    """Update domain quota settings"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data:
        return JSONResponse(
            content=create_api_response("error", "No quota data provided"), status_code=400
        )

    session = get_db_session()
    try:
        domain = session.query(Domain).filter_by(id=domain_id).first()
        if not domain or (
            ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
        ):
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        # Update quotas
        if "max_quota" in data:
            domain.max_quota = data["max_quota"]
        if "max_users" in data:
            domain.max_users = data["max_users"]
        if "rate_limits" in data:
            domain.rate_limits = data["rate_limits"]
        if "storage_quotas" in data:
            domain.storage_quotas = data["storage_quotas"]

        domain.updated_at = datetime.now()
        session.commit()

        dispatch_event(
            Events.DOMAIN_UPDATED,
            data={
                "domain_id": domain_id,
                "domain": domain.domain,
                "update_type": "quotas",
                "max_quota": domain.max_quota,
                "max_users": domain.max_users,
            },
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        return create_api_response(
            "success",
            "Domain quotas updated successfully",
            {
                "domain_id": domain_id,
                "max_quota": domain.max_quota,
                "max_users": domain.max_users,
                "rate_limits": domain.rate_limits,
                "storage_quotas": domain.storage_quotas,
            },
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Update domain quotas error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update quotas"), status_code=500
        )
    finally:
        session.close()


@router.post(
    "/edit",
    summary="Edit domain (legacy)",
    description="Mailcow-compatible bulk domain edit endpoint. Accepts a list of domain names or IDs and a set of attributes to update on each.",
    response_model=DomainEditResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request format -- items/attr expected",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to edit domain",
        },
    },
)
@require_api_key("write")
async def edit_domain(request: Request):
    """Edit domain settings"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data or "items" not in data or "attr" not in data:
        return JSONResponse(
            content=create_api_response("error", "Invalid request format"), status_code=400
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        results = []

        for domain in data["items"]:
            # Previously updated by domain/id alone -- any tenant could
            # edit another org's domain. Verify ownership before touching it.
            cursor.execute(
                "SELECT organization_id FROM domains WHERE domain = %s OR id = %s",
                (domain, domain),
            )
            owner = cursor.fetchone()
            # Platform scope may edit any org's domain (ADR-002 SS8);
            # organization scope only its own.
            if not owner or (
                ctx["scope"] == "organization"
                and owner["organization_id"] != ctx["organization_id"]
            ):
                results.append(
                    {"domain": domain, "status": "error", "msg": f"Domain {domain} not found"}
                )
                continue
            # Re-pin the UPDATE to the row's own org rather than the caller's:
            # a platform caller has none, and this keeps the write narrowed to
            # exactly the domain whose ownership was just verified.
            domain_org_id = owner["organization_id"]

            update_fields = []
            update_values = []

            # Build dynamic update query (column names whitelisted to prevent SQL injection)
            for key, value in data["attr"].items():
                if key in ALLOWED_LEGACY_DOMAIN_EDIT_COLUMNS:
                    update_fields.append(f"{key} = %s")
                    update_values.append(value)

            if update_fields:
                update_fields.append("modified = %s")
                update_values.extend([datetime.now(), domain])

                cursor.execute(
                    f"""
                    UPDATE domains
                    SET {", ".join(update_fields)}
                    WHERE (domain = %s OR id = %s) AND organization_id = %s
                """,
                    update_values + [domain, domain_org_id],
                )

                if cursor.rowcount > 0:
                    dispatch_event(
                        Events.DOMAIN_UPDATED,
                        data={"domain": domain, "updated_fields": list(data["attr"].keys())},
                        domain=domain if isinstance(domain, str) else None,
                        source_service="api",
                    )
                    results.append(
                        {
                            "domain": domain,
                            "status": "success",
                            "msg": f"Domain {domain} updated successfully",
                        }
                    )
                else:
                    results.append(
                        {"domain": domain, "status": "error", "msg": f"Domain {domain} not found"}
                    )

        return create_api_response("success", "Domain update completed", results)

    except Exception as e:
        logger.error(f"Edit domain error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to edit domain"), status_code=500
        )
    finally:
        conn.close()


@router.post(
    "/delete/domain",
    summary="Delete domains (legacy)",
    description="Legacy bulk domain deletion endpoint. Accepts an array of domain names and removes each domain along with its mailboxes, aliases, and DKIM keys.",
    response_model=DomainDeleteResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request format -- array of domains expected",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to delete domains",
        },
    },
)
@require_api_key("write")
async def delete_domain(request: Request):
    """Delete domain(s) and associated data"""
    ctx = request.state.auth_context
    data = await request.json()

    if not data or not isinstance(data, list):
        return JSONResponse(
            content=create_api_response(
                "error", "Invalid request format - array of domains expected"
            ),
            status_code=400,
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        results = []

        for domain in data:
            # Previously deleted by domain name alone -- any tenant could
            # delete another org's domain. Verify ownership first.
            cursor.execute(
                "SELECT organization_id FROM domains WHERE domain = %s",
                (domain,),
            )
            owner = cursor.fetchone()
            # Platform scope may delete any org's domain (ADR-002 SS8);
            # organization scope only its own.
            if not owner or (
                ctx["scope"] == "organization"
                and owner["organization_id"] != ctx["organization_id"]
            ):
                results.append(
                    {"domain": domain, "status": "error", "msg": f"Domain {domain} not found"}
                )
                continue
            # Re-pin the DELETE to the row's own org (see edit_domain).
            domain_org_id = owner["organization_id"]

            try:
                # Start transaction for each domain
                cursor.execute("START TRANSACTION")

                # Collect the addresses first: after COMMIT each one needs a
                # Dovecot auth-cache flush, or the deleted mailboxes keep
                # authenticating for up to an hour (auth_cache_ttl).
                cursor.execute(
                    "SELECT ea.email FROM email_accounts ea "
                    "JOIN domains d ON ea.domain_id = d.id WHERE d.domain = %s",
                    (domain,),
                )
                deleted_emails = [row["email"] for row in cursor.fetchall()]

                # email_accounts and aliases both key on domain_id, not a
                # `domain` name column (and aliases has source/destination,
                # not `address`) -- the old name-based DELETEs raised
                # "Unknown column", the per-domain except swallowed it, and
                # every legacy delete rolled back as "not found".
                cursor.execute(
                    "DELETE ea FROM email_accounts ea "
                    "JOIN domains d ON ea.domain_id = d.id WHERE d.domain = %s",
                    (domain,),
                )
                mailboxes_deleted = cursor.rowcount

                cursor.execute(
                    "DELETE a FROM aliases a "
                    "JOIN domains d ON a.domain_id = d.id WHERE d.domain = %s",
                    (domain,),
                )
                aliases_deleted = cursor.rowcount

                # dkim_keys has no `domain` column (0001_baseline: domain_id
                # CHAR(26) FK with ON DELETE CASCADE) -- the old
                # `WHERE domain = %s` raised "Unknown column", the per-domain
                # except swallowed it, and EVERY legacy delete rolled back as
                # "not found". Join on domain_id; the cascade would also cover
                # this, but the explicit delete keeps the intent visible.
                cursor.execute(
                    "DELETE dk FROM dkim_keys dk "
                    "JOIN domains d ON dk.domain_id = d.id WHERE d.domain = %s",
                    (domain,),
                )

                # No domain_admins DELETE: that table exists in no migration
                # or model -- the statement raised "Unknown table" and rolled
                # the whole cascade back (same failure mode as above).

                cursor.execute(
                    "DELETE FROM domains WHERE domain = %s AND organization_id = %s",
                    (domain, domain_org_id),
                )
                domain_deleted = cursor.rowcount

                if domain_deleted > 0:
                    cursor.execute("COMMIT")
                    # Same cleanup as delete_domain_by_id: drop the domain's
                    # key files + selector-map entry so rspamd stops signing.
                    dkim_sync.try_sync_domain(domain)
                    # Best-effort revocation: without the flush each deleted
                    # mailbox keeps authenticating from Dovecot's cache for
                    # up to an hour. Never fails the delete (helper logs).
                    for email in deleted_emails:
                        flush_auth_cache(email)
                    dispatch_event(
                        Events.DOMAIN_DELETED,
                        data={
                            "domain": domain,
                            "mailboxes_deleted": mailboxes_deleted,
                            "aliases_deleted": aliases_deleted,
                        },
                        domain=domain if isinstance(domain, str) else None,
                        source_service="api",
                    )
                    results.append(
                        {
                            "domain": domain,
                            "status": "success",
                            "msg": f"Domain {domain} deleted successfully",
                            "stats": {
                                "mailboxes_deleted": mailboxes_deleted,
                                "aliases_deleted": aliases_deleted,
                            },
                        }
                    )
                else:
                    cursor.execute("ROLLBACK")
                    results.append(
                        {"domain": domain, "status": "error", "msg": f"Domain {domain} not found"}
                    )

            except Exception as e:
                cursor.execute("ROLLBACK")
                results.append(
                    {
                        "domain": domain,
                        "status": "error",
                        "msg": f"Failed to delete domain {domain}: {str(e)}",
                    }
                )

        return create_api_response("success", "Domain deletion completed", results)

    except Exception as e:
        logger.error(f"Delete domain error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to delete domains"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/get/domain/policy/{domain}",
    summary="Get domain policy",
    description="Retrieve the spam and security policy settings for a domain, including greylisting, RBL, and blacklist-only flags.",
    response_model=DomainPolicyResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Domain not found"},
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to retrieve domain policy",
        },
    },
)
@require_api_key("read")
async def get_domain_policy(domain: str, request: Request):
    """Get domain policies and restrictions.

    This route had NO org scoping of any kind: it keyed straight off the
    `domain` path segment, so any authenticated tenant could read any other
    domain's spam and security policy by name. verify_domain_scope is the
    existing helper for exactly this shape -- a route keyed by a raw domain
    value -- and it passes platform scope through unchanged.
    """
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        verify_domain_scope(cursor, domain, request.state.auth_context)

        # Get domain policy information
        cursor.execute(
            """
            -- rate_limits, not rl_value/rl_frame: those two columns exist on
            -- no table in this schema, so this query raised "Unknown column
            -- 'd.rl_value'" and the endpoint 500'd on every call. The domains
            -- table carries rate limits as a JSON column instead.
            SELECT d.domain, d.rate_limits, d.active,
                   dp.policy_bl_only, dp.policy_reject_spam,
                   dp.policy_greylist, dp.policy_rbl
            FROM domains d
            LEFT JOIN domain_policy dp ON d.domain = dp.domain
            WHERE d.domain = %s
        """,
            (domain,),
        )

        policy = cursor.fetchone()
        if not policy:
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        return create_api_response("success", "Domain policy retrieved successfully", policy)

    except Exception as e:
        logger.error(f"Get domain policy error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve domain policy"),
            status_code=500,
        )
    finally:
        conn.close()


@router.post(
    "/edit/domain/policy",
    summary="Edit domain policy",
    description="Create or update the spam and security policy for a domain. Uses upsert semantics so the record is created if it does not exist.",
    response_model=SimpleMessageResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Missing required field: domain"},
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to update domain policy",
        },
    },
)
@require_api_key("write")
async def edit_domain_policy(request: Request):
    """Edit domain policies and restrictions.

    Same unscoped-by-domain-name bug as get_domain_policy, in the writing
    direction: any tenant could rewrite any other domain's greylisting, RBL
    and spam-rejection settings. That is a denial-of-service against another
    tenant's mail at best, and a way to disable their spam filtering at worst.
    """
    data = await request.json()

    if not data or "domain" not in data:
        return JSONResponse(
            content=create_api_response("error", "Missing required field: domain"), status_code=400
        )

    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)
        verify_domain_scope(cursor, data["domain"], request.state.auth_context)
        cursor.close()
        cursor = conn.cursor()

        # Update or insert domain policy
        cursor.execute(
            """
            INSERT INTO domain_policy (
                domain, policy_bl_only, policy_reject_spam,
                policy_greylist, policy_rbl, created, modified
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                policy_bl_only = VALUES(policy_bl_only),
                policy_reject_spam = VALUES(policy_reject_spam),
                policy_greylist = VALUES(policy_greylist),
                policy_rbl = VALUES(policy_rbl),
                modified = VALUES(modified)
        """,
            (
                data["domain"],
                data.get("policy_bl_only", 0),
                data.get("policy_reject_spam", 0),
                data.get("policy_greylist", 1),
                data.get("policy_rbl", 1),
                datetime.now(),
                datetime.now(),
            ),
        )

        dispatch_event(
            Events.DOMAIN_UPDATED,
            data={"domain": data["domain"], "update_type": "policy"},
            domain=data["domain"],
            source_service="api",
        )

        return create_api_response("success", f"Domain policy updated for {data['domain']}")

    except Exception as e:
        logger.error(f"Edit domain policy error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to update domain policy"), status_code=500
        )
    finally:
        conn.close()


@router.get(
    "/stats/{domain}",
    summary="Get domain statistics",
    description="Retrieve aggregate statistics for a domain including mailbox count, alias count, and total quota usage.",
    response_model=DomainStatsResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {
            "model": ErrorResponse,
            "description": "Database connection failed / failed to retrieve domain statistics",
        },
    },
)
@require_api_key("read")
async def get_domain_stats(domain: str, request: Request):
    """Get domain statistics"""
    ctx = request.state.auth_context
    conn = get_db_connection()
    if not conn:
        return JSONResponse(
            content=create_api_response("error", "Database connection failed"), status_code=500
        )

    try:
        cursor = conn.cursor(dictionary=True)

        # Get domain details, scoped to the caller's org -- previously
        # matched by domain name alone, so any tenant could pull another
        # org's domain details, mailbox/alias counts, and storage usage.
        # org_filter() degrades to "1=1" for platform scope, which reads any
        # org's stats by design (ADR-002 SS8).
        org_sql, org_params = org_filter(ctx)
        cursor.execute(
            f"SELECT * FROM domains WHERE domain = %s AND {org_sql}",
            tuple([domain] + org_params),
        )
        domain_details = cursor.fetchone()

        if not domain_details:
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        domain_id = domain_details["id"]

        # Get mailbox count
        cursor.execute(
            """
            SELECT COUNT(*) AS mailbox_count FROM email_accounts
            WHERE domain_id = %s AND status = 'active'
        """,
            (domain_id,),
        )
        mailbox_count = cursor.fetchone()["mailbox_count"]

        # Get alias count
        cursor.execute(
            """
            SELECT COUNT(*) AS alias_count FROM aliases
            WHERE domain_id = %s AND active = 1
        """,
            (domain_id,),
        )
        alias_count = cursor.fetchone()["alias_count"]

        # Get total storage used
        cursor.execute(
            """
            SELECT COALESCE(SUM(storage_used), 0) AS total_storage_used FROM email_accounts
            WHERE domain_id = %s AND status = 'active'
        """,
            (domain_id,),
        )
        total_quota_used = cursor.fetchone()["total_storage_used"]

        # Structure the response
        stats = {
            "domain": domain,
            "mailbox_count": mailbox_count,
            "alias_count": alias_count,
            "total_quota_used": total_quota_used,
            "domain_details": domain_details,  # Include domain details
        }

        return create_api_response("success", "Domain statistics retrieved successfully", stats)

    except Exception as e:
        logger.error(f"Get domain statistics error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve domain statistics"),
            status_code=500,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DKIM key read + two-step rotation (Console PRD SS12 gap #8a, SS5.3)
# ---------------------------------------------------------------------------
#
# The console's DKIM manager needs three things this file did not expose:
# the public key as a copy-pasteable DNS record, a way to mint a new key
# without breaking the currently-signing one, and a way to cut over once DNS
# has propagated. Hence generate-then-activate as two separate calls rather
# than one "rotate" button: a single-step rotation that swaps the signing
# selector at the same moment it mints the key guarantees a window where
# outbound mail is signed with a selector whose TXT record does not exist
# yet -- every message in that window fails DKIM, and therefore DMARC.
#
# NONE of these endpoints ever return the private key -- not the plaintext
# `private_key` column (NULL on every row written since phase-07 C2), not
# the `private_key_ciphertext`/`private_key_nonce` columns, and not a
# decrypted copy. There is no operational reason for a private DKIM key to
# cross the API boundary: possession of it is the entire ability to send
# DKIM-passing mail as the customer's domain (security-model.md C2), which
# is precisely the threat envelope encryption was added to contain. The
# SELECTs below name their columns explicitly so a future `SELECT *` cannot
# quietly start leaking them.

# `dkim_keys` has no algorithm or key_size column (0001_baseline +
# 0003_encrypt_private_keys -- verified, not assumed). Both are derived from
# the stored public key instead of inventing schema for them
# (conventions.md SS1 rule 5).
_DKIM_RSPAMD_NOTE = (
    "Key files and the selector map are exported to rspamd automatically "
    "(utils/dkim_sync.py write-through; `rspamd_synced` in this response reports whether "
    "that export succeeded). If rspamd_synced is false the key is safely stored in MySQL "
    "but rspamd is not signing with it yet -- run `scripts/generate_dkim.py sync` on the "
    "docker host (or `./start.sh dkim`) to export the keys, fix file ownership for the "
    "_rspamd user, and refresh /etc/rspamd/dkim_selectors.map inside the rspamd container."
)


class DKIMRotateRequest(BaseModel):
    """Request body for DKIM rotate/activate."""

    reason: str = Field(
        ...,
        min_length=1,
        description="Why the key is being rotated or cut over. Recorded by the audit "
        "middleware (phase-02 cross-cutting: destructive and corrective actions carry a reason).",
    )


def _dkim_public_key_b64(stored: str | None) -> str | None:
    """Normalise `dkim_keys.public_key` to the bare base64 a DKIM TXT record
    carries in its p= tag.

    Two writers put two different formats in this one column, which is why
    this cannot just be returned verbatim:
      - create_domain() above stores base64-encoded DER (no PEM armour),
      - scripts/generate_dkim.py stores the full PEM including
        '-----BEGIN PUBLIC KEY-----' lines.
    A p= tag containing PEM armour and newlines is not a valid DKIM record,
    so both shapes are collapsed here rather than at the call sites. The one
    implementation lives in utils/dkim_sync.py (which needs it for its own
    DNS output); this is an alias, not a second spelling that can drift.
    """
    return dkim_sync.public_key_b64(stored)


def _dkim_key_details(public_b64: str | None) -> tuple:
    """Return (algorithm, key_size) read back off the public key itself.

    Best-effort: a key we cannot parse still has to appear in the list --
    the operator needs to see the selector exists even if this service's
    cryptography version cannot decode it -- so failures degrade to
    (None, None) rather than failing the request.
    """
    if not public_b64:
        return None, None
    try:
        public_key = serialization.load_der_public_key(base64.b64decode(public_b64))
    except Exception:
        return None, None
    if isinstance(public_key, rsa.RSAPublicKey):
        return "rsa", public_key.key_size
    return type(public_key).__name__, getattr(public_key, "key_size", None)


def _dkim_record(domain: str, selector: str, public_b64: str | None) -> dict:
    """The exact DNS TXT record to publish, split into the name and value a
    registrar's form asks for separately."""
    return {
        "type": "TXT",
        "name": f"{selector}._domainkey.{domain}",
        # Same format string generate_dns_records() uses above -- one
        # spelling of the record in this file, not two that can drift.
        "value": f"v=DKIM1; k=rsa; p={public_b64}" if public_b64 else None,
    }


def _load_domain_scoped(session, domain_id: str, ctx):
    """get_domain()'s ownership idiom, factored out for the three DKIM
    routes below. Platform scope sees every organization; an organization
    credential sees only its own, and a miss is 404 rather than 403 so the
    existence of another tenant's domain is not leaked (conventions.md SS8).

    Today the role floor on all three routes means only an operator session
    reaches them, and operator sessions are always platform scope -- the
    organization branch is defence-in-depth that becomes load-bearing the
    moment the floor is relaxed for tenant self-service.
    """
    domain = session.query(Domain).filter_by(id=domain_id).first()
    if not domain or (
        ctx["scope"] == "organization" and domain.organization_id != ctx["organization_id"]
    ):
        return None
    return domain


@router.get(
    "/{domain_id}/dkim",
    summary="List a domain's DKIM keys",
    description="Every DKIM key on the domain with the exact TXT record to publish for each "
    "(PRD SS5.3). Private key material is never included in this response in any form -- "
    "plaintext, encrypted, or decrypted.",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to retrieve DKIM keys"},
    },
)
@require_api_key("read", role="support")
async def list_dkim_keys(domain_id: str, request: Request):
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = _load_domain_scoped(session, domain_id, ctx)
        if not domain:
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        keys = (
            session.query(DKIMKey)
            .filter_by(domain_id=domain_id)
            .order_by(DKIMKey.active.desc(), DKIMKey.created_at.desc())
            .all()
        )

        items = []
        for key in keys:
            public_b64 = _dkim_public_key_b64(key.public_key)
            algorithm, key_size = _dkim_key_details(public_b64)
            record = _dkim_record(domain.domain, key.selector, public_b64)
            items.append(
                {
                    "selector": key.selector,
                    "algorithm": algorithm,
                    "key_size": key_size,
                    "active": bool(key.active),
                    "created_at": key.created_at.isoformat() if key.created_at else None,
                    "updated_at": key.updated_at.isoformat() if key.updated_at else None,
                    "record_name": record["name"],
                    "record_type": record["type"],
                    "record_value": record["value"],
                }
            )

        return create_api_response(
            "success",
            "DKIM keys retrieved",
            {
                "domain": domain.domain,
                "domain_id": domain.id,
                "dkim_enabled": bool(domain.dkim_enabled),
                # The selector the domain row claims is signing. Reported
                # alongside the per-key `active` flags because the two can
                # disagree (nothing enforces that they match), and an
                # operator debugging a DKIM failure needs to see the
                # disagreement rather than one of the two values alone.
                "signing_selector": domain.dkim_selector,
                "items": items,
                "total": len(items),
            },
        )

    except Exception as e:
        logger.error(f"List DKIM keys error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to retrieve DKIM keys"), status_code=500
        )
    finally:
        session.close()


@router.post(
    "/{domain_id}/dkim/rotate",
    summary="Generate a new DKIM key under a new selector",
    description="Step one of a safe rotation: mints a fresh 2048-bit RSA key under a NEW "
    "selector and leaves the existing key active and signing. Returns the DNS record to "
    "publish. Nothing starts signing with the new key until POST /{domain_id}/dkim/{selector}"
    "/activate is called -- deliberately, so the record has time to propagate first.",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain not found (or belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to rotate DKIM key"},
    },
)
@require_api_key("write", role="operator")
async def rotate_dkim_key(domain_id: str, body: DKIMRotateRequest, request: Request):
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = _load_domain_scoped(session, domain_id, ctx)
        if not domain:
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        existing = {
            key.selector for key in session.query(DKIMKey).filter_by(domain_id=domain_id).all()
        }

        # Timestamped and alphanumeric: a DNS label, valid at every
        # registrar, sorts chronologically, and says when it was minted.
        # The suffix loop covers two rotations inside the same second --
        # `selector` is not unique-constrained on this table, so a collision
        # would silently produce two rows the activate step could not tell
        # apart rather than erroring.
        base_selector = f"mailyte{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        selector = base_selector
        suffix = 1
        while selector in existing:
            selector = f"{base_selector}x{suffix}"
            suffix += 1

        private_pem, public_b64 = generate_dkim_keypair()

        # Same write path as create_domain() above: private_key stays NULL,
        # only the envelope-encrypted columns are written (phase-07 C2), and
        # private_pem is never logged, returned, or held past this block.
        encrypted = encrypt_private_key(private_pem)
        new_key = DKIMKey(
            domain_id=domain.id,
            selector=selector,
            private_key=None,
            private_key_ciphertext=encrypted.ciphertext,
            private_key_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            public_key=public_b64,
            # Inactive on purpose -- this is the half of the rotation that
            # must NOT change what is signing. The old key keeps signing
            # until activate is called.
            active=False,
        )
        session.add(new_key)
        session.commit()

        # Export the new (still inactive) key's file now, while its DNS
        # record propagates -- the activate cutover must find the file
        # already on disk, or there is a window where rspamd signs nothing.
        # The selector map is untouched here because the active key did not
        # change; that is exactly the point of the two-step flow.
        rspamd_synced = dkim_sync.try_sync_domain(domain.domain)

        algorithm, key_size = _dkim_key_details(public_b64)
        record = _dkim_record(domain.domain, selector, public_b64)
        previous = [
            key.selector
            for key in session.query(DKIMKey).filter_by(domain_id=domain_id, active=True).all()
        ]

        logger.warning(
            f"DKIM key rotated for domain={domain.domain} new_selector={selector} "
            f"by={ctx['operator_id']} reason={body.reason!r}"
        )

        dispatch_event(
            Events.ENCRYPTION_KEY_GENERATED,
            data={
                "domain_id": domain.id,
                "domain": domain.domain,
                "selector": selector,
                "kind": "dkim",
            },
            org_id=domain.organization_id,
            domain=domain.domain,
            source_service="api",
        )

        return JSONResponse(
            content=create_api_response(
                "success",
                "New DKIM key generated -- publish the record, then activate the selector",
                {
                    "domain": domain.domain,
                    "selector": selector,
                    "algorithm": algorithm,
                    "key_size": key_size,
                    "active": False,
                    "rspamd_synced": rspamd_synced,
                    "record_name": record["name"],
                    "record_type": record["type"],
                    "record_value": record["value"],
                    "previous_active_selectors": previous,
                    "activate_url": f"/api/v1/domains/{domain.id}/dkim/{selector}/activate",
                    "warning": (
                        f"Do NOT remove the existing DKIM record(s) "
                        f"({', '.join(previous) if previous else 'none currently active'}). "
                        "Mail already in flight is signed with the old selector and verifiers "
                        "resolve the selector named in each message's signature, so pulling the "
                        "old record early makes those messages fail DKIM and therefore DMARC. "
                        f"Publish {record['name']}, wait for it to resolve everywhere (allow at "
                        "least the old record's TTL), then call the activate endpoint. Signing "
                        "does not move until you do -- this call changed nothing about what is "
                        f"signing today. {_DKIM_RSPAMD_NOTE}"
                    ),
                },
            ),
            status_code=201,
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Rotate DKIM key error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to rotate DKIM key"), status_code=500
        )
    finally:
        session.close()


@router.post(
    "/{domain_id}/dkim/{selector}/activate",
    summary="Cut DKIM signing over to a selector",
    description="Step two of a safe rotation: makes the named selector the signing key and "
    "deactivates every other selector on the domain. Call only once the selector's TXT record "
    "resolves publicly -- activating before propagation is what breaks DKIM.",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Domain or selector not found (or domain belongs to another org)",
        },
        500: {"model": ErrorResponse, "description": "Failed to activate DKIM selector"},
    },
)
@require_api_key("write", role="operator")
async def activate_dkim_selector(
    domain_id: str, selector: str, body: DKIMRotateRequest, request: Request
):
    ctx = request.state.auth_context
    session = get_db_session()
    try:
        domain = _load_domain_scoped(session, domain_id, ctx)
        if not domain:
            return JSONResponse(
                content=create_api_response("error", "Domain not found"), status_code=404
            )

        keys = session.query(DKIMKey).filter_by(domain_id=domain_id).all()
        target = [key for key in keys if key.selector == selector]
        if not target:
            return JSONResponse(
                content=create_api_response(
                    "error", f"No DKIM key with selector '{selector}' on this domain"
                ),
                status_code=404,
            )

        deactivated = []
        for key in keys:
            if key.selector == selector:
                key.active = True
            elif key.active:
                key.active = False
                deactivated.append(key.selector)

        # domains.dkim_selector is a second, independent record of "which
        # selector signs" -- it is what verify-dns above queries and what
        # create_domain seeds. Leaving it stale would make the DKIM check
        # look up the OLD selector's record after a successful cutover and
        # report a pass/fail about the wrong key entirely.
        domain.dkim_selector = selector
        session.commit()

        # The cutover is only real once the selector map on disk names this
        # selector -- until then rspamd keeps signing with the fallback
        # `default` path. Write-through now; rspamd_synced below reports it.
        rspamd_synced = dkim_sync.try_sync_domain(domain.domain)

        public_b64 = _dkim_public_key_b64(target[0].public_key)
        record = _dkim_record(domain.domain, selector, public_b64)

        logger.warning(
            f"DKIM selector activated for domain={domain.domain} selector={selector} "
            f"deactivated={deactivated} by={ctx['operator_id']} reason={body.reason!r}"
        )

        return create_api_response(
            "success",
            f"DKIM signing selector set to '{selector}'",
            {
                "domain": domain.domain,
                "active_selector": selector,
                "deactivated_selectors": deactivated,
                "rspamd_synced": rspamd_synced,
                "record_name": record["name"],
                "record_value": record["value"],
                "warning": (
                    "Keep the deactivated selectors' TXT records published until mail signed "
                    "with them has aged out of every retry queue -- a few days is the usual "
                    f"advice. {_DKIM_RSPAMD_NOTE}"
                ),
            },
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Activate DKIM selector error: {e}")
        return JSONResponse(
            content=create_api_response("error", "Failed to activate DKIM selector"),
            status_code=500,
        )
    finally:
        session.close()
