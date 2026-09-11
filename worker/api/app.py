import os
import sys

# Fail closed on missing/weak secrets (phase-07 C3) -- deliberately the
# very first thing this process does, before any other import that might
# touch the database or a secret-derived value. `secrets-check` (compose)
# already gates every service on this same check running first, but `api`
# checks again for itself in case it's ever started standalone
# (`docker compose run api ...`), bypassing that gate.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from startup_checks import API_SECRETS, verify_secrets

verify_secrets(required=API_SECRETS)

import json
import logging
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.logging_config import setup_logging
from shared.metrics import get_metrics

# Setup logging
logger = setup_logging()

from utils.client_versions import client_version_middleware
from utils.correlation import CorrelationIdLogFilter, generate_correlation_id, set_correlation_id

# Every log line for a given request carries its correlation id (phase-04
# task 4.7) -- attached to the root logger's handlers so every module's
# `logging.getLogger(__name__)` call picks it up without being touched.
_correlation_filter = CorrelationIdLogFilter()
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_correlation_filter)
    _handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s"
        )
    )

app = FastAPI(
    title="Mailyte Email Server API",
    description="""
# Mailyte Email Server API — Enterprise Edition

The Mailyte Enterprise Edition API provides programmatic control over a self-hosted email server built on Postfix, Dovecot, and Rspamd.

## What you can do

- **Provision email infrastructure** — Add domains and spin up mailboxes that are immediately usable via IMAP/POP3/SMTP.
- **Manage email routing** — Configure aliases, forwarding rules, and Sieve mail filters.
- **Enforce rate limits** — Set sending rate limits to prevent abuse.
- **Manage certificates** — SSL/TLS certificate management with Let's Encrypt support.

## Authentication

All endpoints require an API key passed in the `X-API-Key` header.

```
X-API-Key: your-api-key-here
```

Platform-only endpoints (service restarts, cross-tenant compliance actions,
organization lifecycle, and similar) additionally require a platform-scoped
credential -- either an operator session (`POST /api/v1/platform/auth/login`
+ MFA) or an API key issued with `scope='platform'`. A tenant credential can
never reach these, regardless of its own permission flags.

## Base URL

All API endpoints are prefixed with `/api/v1/`.

- **Local development:** `http://localhost:8083/api/v1/`
- **Production:** `https://api.yourdomain.com/api/v1/`

## Rate Limits

API requests are rate-limited per API key. Default limits are 100 requests/minute for read operations and 30 requests/minute for write operations.

## Want more?

Mailyte Enterprise Edition adds email tracking, webhooks, analytics, AI-powered search, GDPR compliance, migration tools, shared mailboxes, and more. Visit [mailyte.com](https://mailyte.com) for details.

## Need help?

- **Interactive docs (Swagger):** [/api-docs](/api-docs)
- **API Reference (Redoc):** [/api-reference](/api-reference)
""",
    version="1.0.0",
    docs_url="/api-docs",
    redoc_url=None,  # Custom Redoc below
    openapi_url="/openapi.json",
    contact={
        "name": "Mailyte Community",
        "url": "https://github.com/Techies-Africa/mailyte-mail-server-community",
    },
    license_info={
        "name": "Enterprise License",
    },
    openapi_tags=[
        {
            "name": "Organizations",
            "description": "Organizations are the top-level entity in Mailyte. Each organization owns domains, mailboxes, and API keys. Use these endpoints to manage your organization.",
        },
        {
            "name": "Domains",
            "description": "Domains represent the email domains managed by the server (e.g., `example.com`). When you add a domain, Postfix immediately begins accepting mail for it. You must configure DNS records (MX, SPF, DKIM, DMARC) for external email delivery to work.",
        },
        {
            "name": "Mailboxes",
            "description": "Mailboxes (email accounts) are the individual `user@domain.com` addresses. Creating a mailbox provisions a Dovecot maildir, sets up authentication credentials (bcrypt-hashed), and applies storage quotas. Users can immediately log in via IMAP (port 993) or POP3 (port 995) after creation.",
        },
        {
            "name": "Aliases",
            "description": "Aliases forward email from one address to another without creating a full mailbox. For example, `sales@example.com` can forward to `alice@example.com`. Aliases are resolved by Postfix at delivery time. You can create one-to-one, one-to-many, and catch-all aliases.",
        },
        {
            "name": "Filters",
            "description": "Mail filters use the Sieve language to automatically process incoming email. Filters can move messages to folders, forward them, auto-reply, or discard them based on sender, subject, headers, or other criteria.",
        },
        {
            "name": "Rate Limiting",
            "description": "Rate limiting prevents abuse by enforcing sending quotas. Limits are configurable for messages per hour, recipients per message, and concurrent connections.",
        },
        {
            "name": "Tracking",
            "description": "Email tracking monitors recipient engagement through invisible tracking pixels (open tracking) and link URL rewriting (click tracking). When a recipient opens an email or clicks a link, the event is recorded with timestamp, IP address, and user agent.",
        },
        {
            "name": "Webhooks",
            "description": "Webhooks deliver real-time HTTP notifications when mail events occur. Over 50 event types are supported including `email.delivered`, `email.bounced`, `tracking.open`, `tracking.click`, and more. Each webhook delivery is signed with HMAC-SHA256 for authenticity verification.",
        },
        {
            "name": "SSL",
            "description": "SSL certificate management handles TLS certificates for mail services. Certificates can be auto-provisioned via Let's Encrypt or manually uploaded. The system monitors certificate expiry and auto-renews before certificates expire.",
        },
    ],
)

# Initialize metrics
metrics = get_metrics("api")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    metrics.record_request(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration=duration,
    )
    return response


# Client version negotiation for native/web clients (mobile requirements
# SS5): stamps X-Min-Client-Version / X-Latest-Client-Version /
# X-Update-Required / X-Update-Url from MIN_CLIENT_VERSION_<PLATFORM> and
# friends. Advisory only -- never blocks a request. See utils/client_versions.
app.middleware("http")(client_version_middleware)


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint"""
    metrics_data = metrics.get_prometheus_metrics()
    return PlainTextResponse(metrics_data)


# Configure CORS (phase-07 H8, security-model.md H8).
#
# allow_credentials=True below is load-bearing for the session cookie
# (routes/auth.py) -- and Starlette's CORSMiddleware special-cases that
# combination: with allow_credentials=True, a wildcard origin isn't sent
# to the browser as the literal string "*" (browsers reject that pairing
# outright), it's silently replaced with the caller's own Origin header,
# reflected back on every request. That makes `CORS_ALLOWED_ORIGINS=*`
# functionally "any origin, with credentials" -- not a no-op fallback, an
# open door -- so it's rejected at startup rather than allowed to degrade
# into that quietly.
_cors_allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
if "*" in _cors_allowed_origins:
    sys.exit(
        "FATAL: CORS_ALLOWED_ORIGINS may not include '*' -- combined with "
        "allow_credentials=True (required for session-cookie auth), Starlette "
        "reflects any request's Origin back as allowed, which defeats the "
        "restriction entirely. Set it to the real, specific origin(s) that "
        "serve the dashboard instead."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=[
        "Content-Type",
        "X-API-Key",
        "X-CSRF-Token",
        "X-Bootstrap-Token",
        # Pass-through to worker/monitoring/app.py's own separate,
        # pre-existing token gate (restart/auto-heal/test-webhooks) -- not
        # read via os.getenv() in this service; phase-06 replaced
        # utils/auth.py's own require_admin() (which DID read
        # ADMIN_TOKEN_SECRET/ADMIN_PASSWORD here) with real operator
        # identities instead, so X-Admin-Password has no remaining consumer
        # and is deliberately not listed below.
        "X-Admin-Token",
        "Idempotency-Key",
        # Native/web clients declare themselves (utils/client_versions.py).
        "X-Client-Platform",
        "X-Client-Version",
        "X-Client-Build",
    ],
    # Browsers only let scripts read non-simple response headers that are
    # listed here -- without this the web client cannot see the update
    # headers or the correlation id it logs alongside errors.
    expose_headers=[
        "X-Correlation-Id",
        "X-Min-Client-Version",
        "X-Latest-Client-Version",
        "X-Update-Required",
        "X-Update-Url",
    ],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Generate a correlation id per request (phase-04 task 4.7).

    Set on request.state and the logging contextvar for every request;
    stamped onto every response as X-Correlation-Id; also folded into the
    JSON body of error responses (>=400) so Laravel can log it alongside its
    own job id without re-deriving it from headers.
    """
    correlation_id = generate_correlation_id()
    request.state.correlation_id = correlation_id
    set_correlation_id(correlation_id)

    response = await call_next(request)

    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400 and content_type.startswith("application/json"):
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                payload.setdefault("correlation_id", correlation_id)
                body = json.dumps(payload).encode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
        headers = dict(response.headers)
        headers.pop("content-length", None)
        response = Response(
            content=body, status_code=response.status_code, headers=headers, media_type=content_type
        )

    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.middleware("http")
async def operator_audit_middleware(request: Request, call_next):
    """Every privileged action is audited -- no exceptions (task 6.8,
    ADR-002 SS6). Registered after correlation_id_middleware so it wraps it
    (Starlette: last-registered middleware runs outermost), meaning the
    response this sees already carries X-Correlation-Id.

    Reads the raw request body before call_next -- Starlette caches it on
    the Request object after first read, so route handlers downstream (and
    utils/idempotency.py's own body read) still see it normally.
    """
    raw_body = await request.body()
    response = await call_next(request)

    from utils.operator_audit import maybe_audit

    try:
        await maybe_audit(request, response, raw_body)
    except Exception as exc:
        # Audit logging must never break the response it's describing.
        logger.error(f"operator_audit_middleware failed: {exc}")

    return response


# Serve static files and templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="static",
)

BOOTSTRAP_TOKEN_PATH = "/app/data/bootstrap-token"
OPERATOR_BOOTSTRAP_TOKEN_PATH = "/app/data/operator-bootstrap-token"


@app.on_event("startup")
async def generate_operator_bootstrap_token_if_none_exist():
    """Same paradox as generate_bootstrap_token_if_fresh_install below, for
    operators instead of organizations (task 6.9) -- and deliberately a
    SEPARATE token gated on its own condition (no platform_operators row
    exists), not reused from the org bootstrap token. The two are
    independent resources: a stack can already have organizations (e.g.
    this phase built on top of an existing install) with zero operators, or
    vice versa, so sharing one single-use token between them would let
    whichever bootstrap ran first consume the only token and permanently
    lock out the other.
    """
    try:
        import secrets as _secrets

        import mysql.connector

        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "mysql"),
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.getenv("DB_NAME", "mailserver"),
            user=os.getenv("DB_USER", "mailuser"),
            password=os.getenv("DB_PASSWORD", ""),
            connect_timeout=5,
        )
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM platform_operators")
        (count,) = cursor.fetchone()
        cursor.close()
        conn.close()

        if count == 0:
            os.makedirs(os.path.dirname(OPERATOR_BOOTSTRAP_TOKEN_PATH), exist_ok=True)
            token = _secrets.token_urlsafe(32)
            with open(OPERATOR_BOOTSTRAP_TOKEN_PATH, "w") as f:
                f.write(token)
            os.chmod(OPERATOR_BOOTSTRAP_TOKEN_PATH, 0o600)
            logger.info(
                f"Operator bootstrap token generated (no operators exist yet), written to {OPERATOR_BOOTSTRAP_TOKEN_PATH}"
            )
        elif os.path.isfile(OPERATOR_BOOTSTRAP_TOKEN_PATH):
            os.remove(OPERATOR_BOOTSTRAP_TOKEN_PATH)
    except Exception as exc:
        logger.warning(f"Operator bootstrap token check failed (non-fatal): {exc}")


@app.on_event("startup")
async def generate_bootstrap_token_if_fresh_install():
    """Write a single-use bootstrap token when no API keys exist yet.

    Resolves the bootstrap paradox (phase-02 task 2.4): scripts/setup-first-user.sh
    needs an API key to call the API, but a fresh install has none. This token
    is the only way in; POST /api/v1/bootstrap consumes it and creates the
    first organization, domain, mailbox, and API key.

    Non-fatal by design -- a failure here must never crash the API service.
    Bootstrap is not on the critical path for the rest of the API.
    """
    try:
        import secrets as _secrets

        import mysql.connector

        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "mysql"),
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.getenv("DB_NAME", "mailserver"),
            user=os.getenv("DB_USER", "mailuser"),
            password=os.getenv("DB_PASSWORD", ""),
            connect_timeout=5,
        )
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM api_keys")
        (count,) = cursor.fetchone()
        cursor.close()
        conn.close()

        if count == 0:
            os.makedirs(os.path.dirname(BOOTSTRAP_TOKEN_PATH), exist_ok=True)
            token = _secrets.token_urlsafe(32)
            with open(BOOTSTRAP_TOKEN_PATH, "w") as f:
                f.write(token)
            os.chmod(BOOTSTRAP_TOKEN_PATH, 0o600)
            # Never log the token itself (H2, security-model.md) -- the file at
            # BOOTSTRAP_TOKEN_PATH (0600) is the only place it's meant to live;
            # scripts/setup-first-user.sh reads it from there, not from logs.
            logger.info(
                f"Bootstrap token generated (no organizations exist yet), written to {BOOTSTRAP_TOKEN_PATH}"
            )
        elif os.path.isfile(BOOTSTRAP_TOKEN_PATH):
            # Already provisioned -- remove any stale token so there is
            # nothing left to leak or reuse.
            os.remove(BOOTSTRAP_TOKEN_PATH)
    except Exception as exc:
        logger.warning(f"Bootstrap token check failed (non-fatal): {exc}")


@app.on_event("startup")
async def start_session_cleanup_worker():
    """Start the web_sessions purge thread (phase-03 task 3.6). Non-fatal --
    an expired-session backlog is a nuisance, not an outage."""
    try:
        from utils.session_cleanup import start_session_cleanup

        start_session_cleanup()
    except Exception as exc:
        logger.warning(f"Session cleanup worker failed to start (non-fatal): {exc}")


@app.on_event("startup")
async def start_idempotency_cleanup_worker():
    """Purge expired idempotency_keys rows on a background thread (phase-04 task 4.2)."""
    from utils.idempotency_cleanup import start_idempotency_cleanup

    start_idempotency_cleanup()


@app.get("/api-reference", include_in_schema=False)
async def api_reference():
    return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
    <title>Mailyte API Reference</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
    <redoc spec-url="/openapi.json"></redoc>
    <script src="/static/redoc.standalone.js"></script>
</body>
</html>""")


# Dynamically import and include routers with error handling
# Format: (module_name, url_prefix, display_tag)
route_modules = [
    ("organizations", "/api/v1/organizations", "Organizations"),
    ("domains", "/api/v1/domains", "Domains"),
    ("mailboxes", "/api/v1/mailboxes", "Mailboxes"),
    ("aliases", "/api/v1/aliases", "Aliases"),
    ("analytics", "/api/v1/analytics", "Analytics"),
    ("monitoring", "/api/v1/monitoring", "Monitoring"),
    ("queue", "/api/v1/queue", "Queue"),
    ("webhooks", "/api/v1/webhooks", "Webhooks"),
    ("rate_limiter", "/api/v1/rate-limiter", "Rate Limiting"),
    ("tracking", "/api/v1/tracking", "Tracking"),
    ("filters", "/api/v1/filters", "Filters"),
    ("ssl", "/api/v1/ssl", "SSL"),
    ("smtp_credentials", "/api/v1/smtp-credentials", "SMTP Credentials"),
    # Read-side split of the same resource (K1, 00-PRD-smtp-api-keys):
    # events/usage live in their own module purely for file-size reasons.
    # Same prefix is fine -- the paths ({id}/events, {id}/usage) don't
    # collide with the lifecycle router's.
    ("smtp_credential_reports", "/api/v1/smtp-credentials", "SMTP Credential Reports"),
    ("capabilities", "/api/v1/capabilities", "Capabilities"),
    ("bootstrap", "/api/v1/bootstrap", "Bootstrap"),
    ("platform_auth", "/api/v1/platform/auth", "Platform Auth"),
    # Registered AFTER platform_auth deliberately: routers are matched in
    # include order, so the more specific /api/v1/platform/auth/* prefix must
    # claim its paths before the broader /api/v1/platform mount sees them.
    ("platform", "/api/v1/platform", "Platform"),
    # Console phase-04 (PRD SS12 gaps #4 and #5). Appended at the end rather
    # than filed alphabetically: include order only matters where one prefix
    # is a prefix of another (see the platform_auth note above), and neither
    # of these overlaps an existing mount -- so the cheapest position is the
    # one that does not touch a single existing line.
    ("security", "/api/v1/security", "Security"),
    # Webmail phase-01 (04-mailyte-web/02-PRD-webmail-standalone). The
    # mailbox-holder surface, replacing mailyte-api's /api/v1/mailbox/*.
    #
    # mailbox_auth is registered BEFORE mailbox for the same reason
    # platform_auth precedes platform above: "/api/v1/mailbox-auth" has
    # "/api/v1/mailbox" as a string prefix, so the more specific mount claims
    # its paths first. These two are also deliberately distinct from
    # "mailboxes" (/api/v1/mailboxes) near the top of this list -- that is the
    # org-admin resource for managing accounts, a different audience with a
    # different credential. One letter apart, nothing else in common.
    ("mailbox_auth", "/api/v1/mailbox-auth", "Mailbox Auth"),
    ("mailbox", "/api/v1/mailbox", "Mailbox"),
    # Mobile-backend pass (2026-08-31): the mailbox-holder surface grew four
    # sibling modules sharing mailbox's prefix (same pattern as the
    # smtp_credentials/smtp_credential_reports split above -- their paths
    # don't collide with mailbox.py's). Registered AFTER mailbox so its
    # existing routes keep first claim on any overlapping shape.
    ("mailbox_password", "/api/v1/mailbox", "Mailbox Password"),
    ("mailbox_blocked_senders", "/api/v1/mailbox", "Mailbox Blocked Senders"),
    ("mailbox_insights", "/api/v1/mailbox", "Mailbox Insights"),
    ("mailbox_ai", "/api/v1/mailbox", "Mailbox AI"),
]

for module_name, prefix, tag in route_modules:
    try:
        module = __import__(f"routes.{module_name}", fromlist=["router"])
        if hasattr(module, "router"):
            app.include_router(module.router, prefix=prefix, tags=[tag])
            logger.info(f"Successfully loaded {module_name} router")
        else:
            logger.warning(f"No router found in {module_name} module")
    except ImportError as e:
        logger.warning(f"Could not import {module_name} router: {e}")
    except Exception as e:
        logger.error(f"Error loading {module_name} router: {e}")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Consistent error format for HTTP exceptions (removes FastAPI's detail wrapper)."""
    content = exc.detail
    if isinstance(content, dict) and ("type" in content or "msg" in content):
        return JSONResponse(status_code=exc.status_code, content=content)
    return JSONResponse(
        status_code=exc.status_code,
        content={"type": "error", "msg": str(content) if content else "Request failed"},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler — prevents stack traces leaking to clients."""
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"type": "error", "msg": "Internal server error"})


@app.get("/", include_in_schema=False)
async def root():
    with open(os.path.join(TEMPLATES_DIR, "landing.html")) as f:
        return HTMLResponse(f.read())


@app.get("/features", include_in_schema=False)
async def features():
    with open(os.path.join(TEMPLATES_DIR, "features.html")) as f:
        return HTMLResponse(f.read())


# Legacy — kept for backward compat, now served from template
_UNUSED_INLINE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mailyte Email Server</title>
    <link rel="icon" href="/static/favicon.ico" type="image/x-icon">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #09090B;
            color: #F9F9F9;
            min-height: 100vh;
        }
        .topbar {
            border-bottom: 1px solid rgba(235,174,19,0.15);
            padding: 14px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            max-width: 1100px;
            margin: 0 auto;
        }
        .topbar .logo {
            font-size: 20px;
            font-weight: 700;
            background: linear-gradient(135deg, #EBAE13, #FF9900);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .topbar nav a {
            color: #A1A1A9;
            text-decoration: none;
            font-size: 13px;
            margin-left: 24px;
            transition: color 0.2s;
        }
        .topbar nav a:hover { color: #EBAE13; }
        .hero {
            max-width: 1100px;
            margin: 0 auto;
            padding: 80px 32px 40px;
            text-align: center;
        }
        .badge {
            display: inline-block;
            background: rgba(235,174,19,0.12);
            color: #EBAE13;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.5px;
            padding: 6px 16px;
            border-radius: 50px;
            border: 1px solid rgba(235,174,19,0.25);
            margin-bottom: 28px;
        }
        h1 {
            font-size: 52px;
            font-weight: 800;
            line-height: 1.08;
            margin-bottom: 20px;
            background: linear-gradient(135deg, #FFFFFF 0%, #EBAE13 70%, #FF9900 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle {
            font-size: 17px;
            color: #A1A1A9;
            line-height: 1.7;
            max-width: 620px;
            margin: 0 auto 40px;
        }
        .links {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            justify-content: center;
            margin-bottom: 72px;
        }
        .links a {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 11px 22px;
            border-radius: 50px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.25s;
        }
        .btn-primary {
            background: #EBAE13;
            color: #09090B;
        }
        .btn-primary:hover {
            background: #DBA500;
            transform: translateY(-1px);
            box-shadow: 0 4px 20px rgba(235,174,19,0.3);
        }
        .btn-secondary {
            background: rgba(255,255,255,0.06);
            color: #d1d5db;
            border: 1px solid rgba(255,255,255,0.08);
        }
        .btn-secondary:hover {
            background: rgba(235,174,19,0.08);
            border-color: rgba(235,174,19,0.2);
            color: #EBAE13;
        }
        .section-label {
            text-align: center;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 2px;
            color: #EBAE13;
            margin-bottom: 12px;
            text-transform: uppercase;
        }
        .section-title {
            text-align: center;
            font-size: 28px;
            font-weight: 700;
            color: #F9F9F9;
            margin-bottom: 40px;
        }
        .content {
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 32px 60px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin-bottom: 80px;
        }
        @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
        .card {
            background: #111114;
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            padding: 28px 24px;
            position: relative;
            overflow: hidden;
            transition: border-color 0.3s, transform 0.3s;
        }
        .card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            background: linear-gradient(90deg, #EBAE13, #FF9900);
            opacity: 0;
            transition: opacity 0.3s;
        }
        .card:hover {
            border-color: rgba(235,174,19,0.2);
            transform: translateY(-2px);
        }
        .card:hover::before { opacity: 1; }
        .card-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: rgba(235,174,19,0.1);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            margin-bottom: 16px;
        }
        .card h3 {
            font-size: 15px;
            font-weight: 600;
            color: #F9F9F9;
            margin-bottom: 8px;
        }
        .card p {
            font-size: 13px;
            color: #71717A;
            line-height: 1.6;
        }
        .endpoints {
            margin-bottom: 60px;
        }
        .endpoint-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
        }
        @media (max-width: 800px) { .endpoint-grid { grid-template-columns: 1fr; } }
        .endpoint-grid a {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            background: #111114;
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 8px;
            color: #d1d5db;
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .endpoint-grid a:hover {
            background: rgba(235,174,19,0.06);
            border-color: rgba(235,174,19,0.2);
            color: #EBAE13;
        }
        .endpoint-grid a span {
            color: #52525B;
            font-size: 12px;
            font-weight: 400;
        }
        .upgrade-banner {
            background: linear-gradient(135deg, rgba(235,174,19,0.08), rgba(25,63,255,0.06));
            border: 1px solid rgba(235,174,19,0.15);
            border-radius: 12px;
            padding: 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 24px;
            margin-bottom: 60px;
        }
        @media (max-width: 600px) { .upgrade-banner { flex-direction: column; text-align: center; } }
        .upgrade-banner h3 {
            font-size: 18px;
            font-weight: 700;
            color: #F9F9F9;
            margin-bottom: 6px;
        }
        .upgrade-banner p {
            font-size: 13px;
            color: #71717A;
            line-height: 1.5;
        }
        .upgrade-banner a {
            flex-shrink: 0;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 10px 24px;
            border-radius: 50px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            background: #EBAE13;
            color: #09090B;
            transition: all 0.25s;
        }
        .upgrade-banner a:hover {
            background: #DBA500;
            box-shadow: 0 4px 16px rgba(235,174,19,0.25);
        }
        footer {
            max-width: 1100px;
            margin: 0 auto;
            padding: 24px 32px 40px;
            border-top: 1px solid rgba(255,255,255,0.06);
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }
        footer p {
            color: #52525B;
            font-size: 12px;
        }
        footer a { color: #71717A; text-decoration: none; font-size: 12px; }
        footer a:hover { color: #EBAE13; }
        footer .footer-links { display: flex; gap: 20px; }
    </style>
</head>
<body>
    <div class="topbar">
        <a href="/" class="logo" style="display:flex;align-items:center;gap:8px;text-decoration:none;">
            <img src="/static/logo-40.png" alt="Mailyte" width="28" height="28">
            <span>Mailyte</span>
        </a>
        <nav>
            <a href="/api-docs">Swagger</a>
            <a href="/api-reference">API Docs</a>
            <a href="/health">Health</a>
            <a href="https://github.com/Techies-Africa/mailyte-mail-server-community">GitHub</a>
        </nav>
    </div>

    <div class="hero">
        <img src="/static/logo-transparent.png" alt="Mailyte" width="80" height="80" style="margin-bottom: 20px; filter: drop-shadow(0 0 20px rgba(235,174,19,0.3));">
        <div class="badge">COMMUNITY EDITION &mdash; FREE &amp; OPEN SOURCE</div>
        <h1>Mailyte Email Server</h1>
        <p class="subtitle">
            Self-hosted email infrastructure built on Postfix, Dovecot, and Rspamd.
            SMTP, IMAP, POP3 with email tracking, webhooks, and a full REST API.
        </p>
        <div class="links">
            <a href="/api-reference" class="btn-primary">API Reference &#8594;</a>
            <a href="/api-docs" class="btn-secondary">Swagger UI</a>
            <a href="/openapi.json" class="btn-secondary">OpenAPI Spec</a>
            <a href="/health" class="btn-secondary">Health Check</a>
        </div>
    </div>

    <div class="content">
        <div class="section-label">Features</div>
        <div class="section-title">Everything you need to run email</div>
        <div class="grid">
            <div class="card">
                <div class="card-icon">&#9993;</div>
                <h3>Complete Mail Stack</h3>
                <p>Postfix for SMTP, Dovecot for IMAP/POP3, Rspamd for anti-spam. DKIM, SPF, DMARC built in. TLS with Let's Encrypt.</p>
            </div>
            <div class="card">
                <div class="card-icon">&#128272;</div>
                <h3>Domain &amp; Mailbox Management</h3>
                <p>Manage domains, mailboxes, and aliases through the REST API. Sieve mail filters for automatic routing.</p>
            </div>
            <div class="card">
                <div class="card-icon">&#128200;</div>
                <h3>Email Tracking</h3>
                <p>Open tracking via invisible pixel, click tracking via URL rewriting. Know when recipients engage with your emails.</p>
            </div>
            <div class="card">
                <div class="card-icon">&#128268;</div>
                <h3>Webhook Notifications</h3>
                <p>50+ event types: sent, delivered, bounced, opened, clicked. HMAC-signed payloads with automatic retry.</p>
            </div>
            <div class="card">
                <div class="card-icon">&#128737;</div>
                <h3>Security &amp; Rate Limiting</h3>
                <p>Per-org, per-domain, per-mailbox rate limits. Rspamd spam filtering. TLS encryption everywhere.</p>
            </div>
            <div class="card">
                <div class="card-icon">&#9881;</div>
                <h3>REST API</h3>
                <p>Full CRUD for domains, mailboxes, aliases, filters, and certificates. OpenAPI 3.0 with Swagger UI.</p>
            </div>
        </div>

        <div class="endpoints">
            <div class="section-label">API</div>
            <div class="section-title">Quick Links</div>
            <div class="endpoint-grid">
                <a href="/api-reference#tag/Organizations">Organizations <span>&#8594;</span></a>
                <a href="/api-reference#tag/Domains">Domains <span>&#8594;</span></a>
                <a href="/api-reference#tag/Mailboxes">Mailboxes <span>&#8594;</span></a>
                <a href="/api-reference#tag/Aliases">Aliases <span>&#8594;</span></a>
                <a href="/api-reference#tag/Filters">Filters <span>&#8594;</span></a>
                <a href="/api-reference#tag/Rate-Limiting">Rate Limiting <span>&#8594;</span></a>
                <a href="/api-reference#tag/Tracking">Tracking <span>&#8594;</span></a>
                <a href="/api-reference#tag/Webhooks">Webhooks <span>&#8594;</span></a>
                <a href="/api-reference#tag/SSL">SSL Certificates <span>&#8594;</span></a>
            </div>
        </div>

        <div class="upgrade-banner">
            <div>
                <h3>Need more? Try Mailyte Enterprise</h3>
                <p>Analytics, AI-powered search, GDPR compliance, IMAP migration, shared mailboxes, multi-tenancy, and more.</p>
            </div>
            <a href="https://mailyte.com">Learn More &#8594;</a>
        </div>
    </div>

    <footer>
        <p>Mailyte Email Server v1.0.0 &mdash; Enterprise Edition</p>
        <div class="footer-links">
            <a href="https://github.com/Techies-Africa/mailyte-mail-server-community">GitHub</a>
            <a href="https://mailyte.com">mailyte.com</a>
            <a href="https://techies.africa">Techies Africa</a>
        </div>
    </footer>
</body>
</html>"""


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Try to import database utility
        try:
            import mysql.connector

            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST", "mysql"),
                port=int(os.getenv("DB_PORT", "3306")),
                database=os.getenv("DB_NAME", "mailserver"),
                user=os.getenv("DB_USER", "mailuser"),
                password=os.getenv("DB_PASSWORD", ""),
                connect_timeout=5,
            )
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            conn.close()
            db_status = "connected"
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            db_status = "failed"

        return {"status": "healthy", "database": db_status, "version": "1.0.0"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "error": str(e), "version": "1.0.0"}
        )


if __name__ == "__main__":
    # Use 0.0.0.0 for Replit compatibility and port 5000
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    uvicorn.run("app:app", host=host, port=port, reload=True)


@app.on_event("startup")
async def start_alert_evaluator():
    """Start the alert evaluation loop.

    Until this ran, alert rules were stored and never evaluated: nothing read
    `alert_rules` except the API that wrote it, so no rule fired on its own and
    no channel was ever notified. A monitoring feature is trusted precisely
    when nobody is watching, which is the one situation it did not work.

    Non-fatal, like the session cleanup worker above. A mail server that cannot
    evaluate alerts should still carry mail -- and failing to boot the API over
    it would take out the console used to diagnose the problem.

    Safe under replicas: the loop takes a MySQL advisory lock each tick, so
    only one of the `api` replicas evaluates and notifications are not doubled.
    """
    try:
        import asyncio

        from routes.platform import _alert_evaluation_loop

        asyncio.create_task(_alert_evaluation_loop())
        logger.info("Alert evaluator started")
    except Exception as exc:
        logger.warning(f"Alert evaluator failed to start (non-fatal): {exc}")
