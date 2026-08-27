import os
import sys

# Fail closed on missing/weak secrets (phase-07 C3) -- deliberately the very
# first thing this process does, before any other import that might touch the
# database or a secret-derived value. Only the subset this container actually
# receives is checked; see startup_checks.API_SECRETS for which and why.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from startup_checks import API_SECRETS, verify_secrets

verify_secrets(required=API_SECRETS)

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.logging_config import setup_logging

# Setup logging
logger = setup_logging()

app = FastAPI(
    title="Mailyte Email Server API",
    description="""
# Mailyte Email Server API — Community Edition

The Mailyte Community Edition API provides programmatic control over a self-hosted email server built on Postfix, Dovecot, and Rspamd.

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

Admin endpoints additionally accept `X-Admin-Token` for system-level operations.

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
        "name": "AGPL-3.0",
        "url": "https://www.gnu.org/licenses/agpl-3.0.en.html",
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
        {
            "name": "Capabilities",
            "description": "The capability manifest this deployment ships. The Mailyte Console "
            "and mailyte-web read it before login to decide which navigation entries and routes "
            "to render. Unauthenticated by design, and it never exposes tenant data.",
        },
        {
            "name": "Platform Auth",
            "description": "Operator login for the Mailyte Console. Real, individually-revocable "
            "identities with mandatory MFA, replacing the static admin token. Two steps: "
            "password, then TOTP.",
        },
        {
            "name": "Platform",
            "description": "The console's platform-scope surface: the overview aggregate that "
            "paints the landing screen and every nav badge, operator lifecycle management "
            "(owner-only), and read-only views over the append-only operator audit trail.",
        },
        {
            "name": "Monitoring",
            "description": "Service health, per-service status, system metrics, restart, and "
            "auto-heal. A thin authenticated proxy over a monitoring service; returns 503 with "
            "an explanation when this deployment has none configured.",
        },
        {
            "name": "Queue",
            "description": "Postfix mail queue status, deferred listing, and flush. A thin "
            "authenticated proxy over a queue service; returns 503 with an explanation when "
            "this deployment has none configured.",
        },
    ],
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def operator_audit_middleware(request: Request, call_next):
    """Every privileged action is audited -- no exceptions (ADR-002 §6).

    Middleware, not per-handler calls: a handler that forgets to log is a
    security hole, and middleware cannot forget. It also catches denials,
    which are the signal of compromise and which a handler never reaches at
    all because the auth guard raised before it ran.

    Reads the raw request body before call_next -- Starlette caches it on the
    Request object after first read, so route handlers downstream still see
    it normally.
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


OPERATOR_BOOTSTRAP_TOKEN_PATH = "/app/data/operator-bootstrap-token"


@app.on_event("startup")
async def generate_operator_bootstrap_token_if_none_exist():
    """Write a single-use bootstrap token when no platform operator exists.

    Resolves the console's bootstrap paradox (ADR-004): the console needs an
    operator to log in as, and only an existing `owner` can mint one --
    except on a fresh install, where there is no owner yet. This token is the
    only way in. `POST /api/v1/platform/auth/bootstrap` consumes it, creates
    the first `owner`, and deletes it.

    Deliberately gated on its own condition (no platform_operators row) and
    kept separate from any organization bootstrap: an install can already
    have organizations with zero operators, or the reverse, so sharing one
    single-use token between them would let whichever bootstrap ran first
    consume it and permanently lock out the other.

    Non-fatal by design -- a failure here must never stop the API serving.
    The token is never logged; the 0600 file is the only place it lives.
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
                "Operator bootstrap token generated (no operators exist yet), written to "
                f"{OPERATOR_BOOTSTRAP_TOKEN_PATH}"
            )
        elif os.path.isfile(OPERATOR_BOOTSTRAP_TOKEN_PATH):
            # Already provisioned -- remove any stale token so there is
            # nothing left to leak or reuse.
            os.remove(OPERATOR_BOOTSTRAP_TOKEN_PATH)
    except Exception as exc:
        logger.warning(f"Operator bootstrap token check failed (non-fatal): {exc}")


# Serve static files and templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="static",
)


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
    ("rate_limiter", "/api/v1/rate-limiter", "Rate Limiting"),
    ("filters", "/api/v1/filters", "Filters"),
    ("tracking", "/api/v1/tracking", "Tracking"),
    ("webhooks", "/api/v1/webhooks", "Webhooks"),
    ("ssl", "/api/v1/ssl", "SSL"),
    ("smtp_credentials", "/api/v1/smtp-credentials", "SMTP Credentials"),
    # Read-side split of the same resource (K6 parity of EE K1/K2,
    # 00-PRD-smtp-api-keys): events/usage live in their own module purely
    # for file-size reasons. Same prefix is fine -- the paths ({id}/events,
    # {id}/usage) don't collide with the lifecycle router's.
    ("smtp_credential_reports", "/api/v1/smtp-credentials", "SMTP Credential Reports"),
    ("monitoring", "/api/v1/monitoring", "Monitoring"),
    ("queue", "/api/v1/queue", "Queue"),
    ("capabilities", "/api/v1/capabilities", "Capabilities"),
    ("platform_auth", "/api/v1/platform/auth", "Platform Auth"),
    # Registered AFTER platform_auth deliberately: routers are matched in
    # include order, so the more specific /api/v1/platform/auth/* prefix must
    # claim its paths before the broader /api/v1/platform mount sees them.
    ("platform", "/api/v1/platform", "Platform"),
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
        <p>Mailyte Email Server v1.0.0 &mdash; Community Edition</p>
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
