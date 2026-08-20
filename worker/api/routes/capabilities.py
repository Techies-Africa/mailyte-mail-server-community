"""
Capability Manifest API

GET /api/v1/capabilities -- the whole CE/EE gating seam (ADR-001 "How gating
is implemented", console PRD §11). The Mailyte Console fetches this once at
session start; an absent capability means the nav item is not rendered at
all, and the route redirects. Never a disabled-looking button, never a screen
that renders and then errors.

Unauthenticated by design: the console reads it before anyone has logged in,
so it cannot require a credential -- which is also why it must never return
tenant data, a hostname, a version of anything but this application, or any
other deployment detail.

THIS IS THE COMMUNITY EDITION MANIFEST. Unlike mailyte-email-server's copy,
`edition` is a constant, not read from MAILYTE_EDITION. This repo IS the
Community Edition; letting an env var flip it to "enterprise" would let a
misconfigured CE box advertise capabilities its own code does not implement,
and ADR-001 is explicit that a user seeing a screen that errors is worse than
not seeing it. Fail safe, not fail open.

Capability strings are a public contract consumed by the console and by
mailyte-web. Once shipped, renaming one is a breaking change.
"""

from fastapi import APIRouter

router = APIRouter()

APP_VERSION = "1.0.0"
EDITION = "community"

# Capability names shared with mailyte-email-server's own manifest, byte for
# byte -- the console and mailyte-web must not have to remember which edition
# spells a shared capability differently. Derived from ADR-001's CE/Pro split
# table ("CE" column) plus substrate-level basics; organizations are
# substrate, not a gated feature.
_SHARED_CAPABILITIES = [
    "domains",
    "mailboxes",
    "aliases",
    "dkim",
    "webmail",
    "queue",
    "logs",
    "health",
]

# Console module names (PRD §11's table, "CE" column). These gate the
# console's nav sections; the shared names above gate mailyte-web's. Both
# live in one flat list because the console flattens the manifest into a set
# of names and asks "is this one present" -- it never inspects structure.
#
# Everything CE ships:
#   overview        the landing screen and every nav badge
#   directory       domains / DKIM / mailboxes / aliases / SMTP credentials
#   mail_flow       queue, trace, webhooks, tracking, suppressions, filters
#   infrastructure  services, metrics, logs, storage, certificates
#   security        quarantine + basic auth security (NOT DLP, NOT reputation)
#   analytics       volume and deliverability basics (NOT engagement/reports)
#   governance      operator audit, operators, sessions
#   docs            API reference, architecture, capability explainer
#   settings        instance settings
_CONSOLE_MODULES = [
    "overview",
    "directory",
    "mail_flow",
    "infrastructure",
    "security",
    "analytics",
    "governance",
    "docs",
    "settings",
]

# Named here purely as documentation of what is deliberately ABSENT, and
# never added to the response. ADR-001's gate is on scale and commercial
# capability, never on basic operability -- CE gets a usable Mailcow-class
# panel, not a crippled demo.
#
#   reseller, whitelabel     commercial: sub-organizations and branding
#   compliance               GDPR request tooling and legal holds
#   rag, migration           Data & AI: semantic search, IMAP migration
#   dlp, reputation          the enterprise security suite
#   analytics_engagement     open/click engagement analytics
#   analytics_reports        scheduled reports and digests
ENTERPRISE_ONLY_CAPABILITIES = [
    "reseller",
    "whitelabel",
    "compliance",
    "rag",
    "migration",
    "dlp",
    "reputation",
    "analytics_engagement",
    "analytics_reports",
]

COMMUNITY_CAPABILITIES = _SHARED_CAPABILITIES + _CONSOLE_MODULES


@router.get(
    "/",
    summary="Get edition capability manifest",
    description="Returns the platform edition and the list of capabilities it ships. "
    "Unauthenticated by design -- the console and mailyte-web read this before login to "
    "decide which navigation entries and routes to render. Never exposes tenant data.",
)
async def get_capabilities():
    """Return this deployment's capability manifest.

    A constant, not a query: nothing here depends on the database, so the
    manifest still answers correctly while the rest of the stack is starting
    up -- which is exactly when a console is trying to decide what to render.
    """
    return {
        "edition": EDITION,
        "version": APP_VERSION,
        "capabilities": list(COMMUNITY_CAPABILITIES),
    }
