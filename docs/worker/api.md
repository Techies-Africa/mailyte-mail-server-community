# API Gateway

The API gateway is the single entry point for managing the entire Mailyte mail server. It is a FastAPI application that exposes REST endpoints for organizations, domains, mailboxes, aliases, analytics, webhooks, tracking, RAG search, storage, compliance, migration, SMTP credentials, platform (console) operations, and the webmail's mailbox surface.

If you are building an integration, the console, or the webmail, this is the service you talk to.

## What It Does

- Centralized REST API -- container port **8080**, published as **8083** in dev; in production reached only through Traefik at `api.${DOMAIN}` (and the bare domain serves the same landing page)
- 32 route modules, loaded dynamically at startup (`route_modules` in `app.py`)
- MySQL and Redis backends; S3 for object storage
- Auth: tenant API keys, tenant browser sessions, and operator (console) sessions -- one permission model, three credential types
- Proxies to sibling workers (monitoring, queue, storage, rag, analytics, tracking, rate limiter, delivery optimizer) with org scoping enforced at the gateway
- Global exception handling (no stack traces leak to clients)

## Route Modules

All mounted under `/api/v1/` (from `route_modules` in `worker/api/app.py`):

| Prefix | Purpose |
|--------|---------|
| `/api/v1/organizations` | Org CRUD and settings |
| `/api/v1/domains` | Domain add/verify, DNS checks, DKIM setup |
| `/api/v1/mailboxes` | Mailbox management (org-admin resource) |
| `/api/v1/aliases` | Alias management |
| `/api/v1/analytics` | Proxies the analytics worker |
| `/api/v1/monitoring` | Proxies the monitoring worker |
| `/api/v1/queue` | Proxies the queue manager |
| `/api/v1/webhooks` | Webhook endpoint management |
| `/api/v1/rate-limiter` | Proxies the rate limiter |
| `/api/v1/storage` | Proxies storage usage |
| `/api/v1/tracking` | Tracking pixel/click/suppression/stats (public pixel URLs live here) |
| `/api/v1/rag` | Semantic search and indexing |
| `/api/v1/filters` | Sieve filter management (via ManageSieve, `dovecot:4190`) |
| `/api/v1/shared-mailboxes` | Shared mailboxes and ACLs |
| `/api/v1/message-trace` | Delivery path tracing (reads `mail_logs`) |
| `/api/v1/transport-rules` | Transport/routing rules |
| `/api/v1/whitelabel` | White-label branding |
| `/api/v1/reseller` | Reseller accounts |
| `/api/v1/compliance` | GDPR exports, retention |
| `/api/v1/migration` | Migration job management (fronts the migration worker) |
| `/api/v1/ssl` | SSL certificate management |
| `/api/v1/smtp-credentials` | SMTP API key lifecycle (two modules share this prefix; the second serves `{id}/events` and `{id}/usage`) |
| `/api/v1/capabilities` | Edition/capability manifest (`MAILYTE_EDITION`) |
| `/api/v1/bootstrap` | First-boot operator bootstrap |
| `/api/v1/auth` | Tenant auth (sessions) |
| `/api/v1/platform/auth` | Operator auth (registered before `/api/v1/platform` so the more specific prefix wins) |
| `/api/v1/platform` | Console/platform operations |
| `/api/v1/security` | Security module (console) |
| `/api/v1/reputation` | Reputation module (console) |
| `/api/v1/mailbox-auth` | Webmail login (registered before `/api/v1/mailbox`) |
| `/api/v1/mailbox` | Webmail mailbox surface (distinct from `/api/v1/mailboxes`) |

### Top-Level Endpoints

```
GET /               -- Landing page (HTML)
GET /features       -- Features page (HTML)
GET /health         -- Health check with database status
GET /metrics        -- Prometheus metrics
GET /api-reference  -- ReDoc API reference
GET /api-docs       -- Swagger UI (docs_url is customized; /docs is NOT served)
```

## Authentication

Three credential types resolve to one permission model (`worker/api/utils/auth.py`):

- **Tenant API keys** -- `X-API-Key` header; route handlers guard with `Depends(require_api_key('read'|'write'))`
- **Tenant browser sessions** -- `mailyte_session` cookie (used by web UIs so no long-lived key sits in the browser); a thin layer over the same permission checks
- **Operator sessions** -- `mailyte_operator_session` cookie, separate table and shorter lifetime (ADR-002); operator MFA is verified through the TOTP security service (`TOTP_SERVICE_URL`)

The webmail authenticates mailbox holders via `/api/v1/mailbox-auth` -- credentials are verified against Dovecot over IMAP (not a stored copy), and mail is sent through Postfix's internal submission listener.

## Mail Submission

Messages sent through the API/webmail are submitted to Postfix on the **internal submission listener `postfix:10587`** (`MAIL_SUBMIT_HOST`/`MAIL_SUBMIT_PORT`), not port 25. That listener carries the tracking content filter; port 25 deliberately does not, because it also receives all inbound mail. Sending on 25 is why no webmail message was ever tracked before 2026-08.

## Configuration

The compose file is the reference; the load-bearing variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Bind port |
| `HOSTNAME` | `mail.example.com` | The public mail hostname -- baked into generated MX/SPF records; without it Docker's container-ID hostname breaks DNS verification |
| `MAIL_HOSTNAME` / `MAIL_SPF_HOST` | (unset) | Public MX name and SPF include host -- deliberately separate settings |
| `MAIL_SUBMIT_HOST` / `MAIL_SUBMIT_PORT` | `postfix` / `10587` | Internal tracked submission |
| `SIEVE_HOST` / `SIEVE_PORT` | `dovecot` / `4190` | ManageSieve for the filters module |
| `DOVEADM_URL` / `DOVEADM_API_KEY` | `http://dovecot:24180` / -- | Flushes Dovecot's auth cache when SMTP credentials change -- without it, revocation takes up to 1h |
| `IMAP_MASTER_USER` / `IMAP_MASTER_PASSWORD` | -- | IMAP impersonation |
| `DB_*` / `REDIS_*` | `mysql` / `redis` | Backends |
| `AWS_*` | -- | S3 access |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `MAILYTE_EDITION` | `enterprise` (compose) | Capability manifest; code defaults to `community` when unset |
| `TOTP_SERVICE_URL` | `http://totp:8103` | Operator MFA |
| `SMTP_HOST` / `SMTP_PORT` / `ALERT_FROM_ADDRESS` / `ALERT_EVAL_INTERVAL_SECONDS` | `postfix` / `25` / `ADMIN_EMAIL` / `60` | Alert rule evaluation and delivery (one evaluator runs across replicas via a MySQL advisory lock) |
| `MONITORING_SERVICE_URL` / `QUEUE_SERVICE_URL` / `STORAGE_SERVICE_URL` / `RAG_SERVICE_URL` | sibling service defaults | Gateway proxy targets |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Backs the console's system-metrics range queries |
| `MAILYTE_COOKIE_SECURE` | `true` | Secure flag on session cookies |

## Docker Configuration

```yaml
api:
  build: ./worker/api
  container_name: api
  ports:
    - "8083:8080"
  volumes:
    - ./worker/api:/app
    - ./shared:/app/shared
    - ./database:/app/database
    - ./storage/api_data:/app/data          # writable data dir (uid 10001)
    - ./secrets/encryption_kek:/run/secrets/encryption_kek:ro
```

In production (`docker-compose.prod.yml`) the service runs with **`replicas: 2`**: `container_name` and the host port are reset (fixed names/ports cannot be shared between replicas), and Traefik load-balances `api.${DOMAIN}` across the replicas. The bare `${DOMAIN}` router serves the same landing page.

## Gotchas

!!! warning "Route loading"
    Routes are imported dynamically at startup. A route module that fails to import is skipped with a warning -- the API starts but that prefix 404s. Check startup logs when a module is missing.

!!! warning "Blocking work in `async def`"
    Many endpoints are declared `async def` while doing blocking DB/HTTP work, which stalls the event loop for every request in the process. When "the API is slow", check the endpoint's decorator before blaming a dependency.

!!! tip "API documentation"
    Swagger UI at `/api-docs` (not `/docs`), ReDoc at `/api-reference`, raw schema at `/openapi.json`.
