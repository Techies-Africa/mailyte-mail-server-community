---
title: API Reference
description: Complete reference for the Mailyte REST API — endpoints, authentication, pagination, errors, and quick-start examples.
---

# API Reference

The Mailyte REST API is your control plane for managing organizations, domains, email accounts, aliases, webhooks, and everything else — all from code. If you can `curl` it, you can automate it.

---

## In this section

<div class="grid cards" markdown>

-   :material-office-building:{ .lg .middle } **Organizations**

    ---

    Create, list, update, and delete organizations — the top-level tenant boundary.

    [:octicons-arrow-right-24: Organizations](organizations.md)

-   :material-web:{ .lg .middle } **Domains**

    ---

    Add domains, verify DNS records, configure DKIM/SPF/DMARC, and manage domain settings.

    [:octicons-arrow-right-24: Domains](domains.md)

-   :material-email:{ .lg .middle } **Email Accounts**

    ---

    Provision mailboxes, set quotas, update passwords, and manage mailbox lifecycle.

    [:octicons-arrow-right-24: Email accounts](email-accounts.md)

-   :material-email-multiple:{ .lg .middle } **Aliases**

    ---

    Create forwarding aliases and manage them in bulk.

    [:octicons-arrow-right-24: Aliases](aliases.md)

-   :material-webhook:{ .lg .middle } **Webhooks**

    ---

    HMAC-signed event delivery, delivery logs, and the dead-letter queue.

    [:octicons-arrow-right-24: Webhooks](webhooks.md)

-   :material-chart-bar:{ .lg .middle } **Statistics**

    ---

    Query delivery stats, engagement metrics, reports, and the suppression list.

    [:octicons-arrow-right-24: Statistics](statistics.md)

-   :material-speedometer:{ .lg .middle } **Rate Limiting**

    ---

    View and update sending rate limits at the org, domain, and mailbox level.

    [:octicons-arrow-right-24: Rate limiting](rate-limiting.md)

-   :material-harddisk:{ .lg .middle } **Storage**

    ---

    Check storage usage and quota configuration per domain and mailbox.

    [:octicons-arrow-right-24: Storage management](storage-management.md)

-   :material-brain:{ .lg .middle } **RAG System**

    ---

    AI-powered semantic search over email content using vector embeddings.

    [:octicons-arrow-right-24: RAG system](rag-system.md)

-   :material-heart-pulse:{ .lg .middle } **Monitoring**

    ---

    Health checks, service status, system metrics, and admin operations.

    [:octicons-arrow-right-24: Monitoring](monitoring.md)

</div>

---

## Base URL

All API endpoints are served under:

```
http://your-server:8083/api/v1/
```

Replace `your-server` with your actual hostname or IP address. Port `8083` is the
default host port mapping in `docker-compose.yml` (the container listens on 8080);
production deployments typically front this with a reverse proxy on 443.

!!! tip "Trailing slashes"
    Collection roots are registered **with** a trailing slash
    (`/api/v1/domains/`, `/api/v1/organizations/`, `/api/v1/smtp-credentials/`,
    `/api/v1/bootstrap/`, `/api/v1/capabilities/`). Requests without the slash are
    answered with a `307` redirect — most clients follow it transparently, but a
    `POST` body survives the redirect only if your client re-sends it, so use the
    canonical form.

---

## Authentication

Most requests authenticate with an API key; browser dashboards use session cookies
instead:

| Credential | How | Notes |
|---|---|---|
| API key | `X-API-Key: mk_live_abc123...` header | Bound to one organization (or platform-scoped) |
| Dashboard session | `mailyte_session` cookie + `X-CSRF-Token` on writes | From `POST /api/v1/auth/login` |
| Operator session | `mailyte_operator_session` cookie + `X-CSRF-Token` on writes | Platform staff; MFA-gated, role-tiered |
| Mailbox session | Cookie or `Authorization: Bearer` token | Webmail users, from `POST /api/v1/mailbox-auth/login` |

Platform-only endpoints (service restarts, cross-tenant listings, and similar)
require a platform-scoped credential; many additionally require an operator role and
so can only be reached by an operator session.

!!! warning "Keep your API keys safe"
    API keys grant full access to the organization they're scoped to. Never commit them to version control or expose them in client-side code. Use environment variables or a secrets manager.

See the [Authentication](authentication.md) page for all four credential types, CSRF rules, and the bootstrap flow that issues your first key.

---

## Request & Response Format

=== "Request"

    - All request bodies use JSON (`Content-Type: application/json`).
    - Use UTF-8 encoding for all text.
    - Query parameters handle filtering and pagination.
    - Any mutating request (`POST`/`PUT`/`PATCH`/`DELETE`) accepts an optional
      `Idempotency-Key` header — retrying the same key + body within 24 hours
      replays the original response instead of repeating the side effect.

=== "Success Response"

    ```json
    {
      "type": "success",
      "msg": "Organizations retrieved successfully",
      "data": { ... }
    }
    ```

=== "Error Response"

    ```json
    {
      "type": "error",
      "msg": "Organization not found",
      "correlation_id": "a1b2c3d4e5f6"
    }
    ```

    Every JSON error response (status ≥ 400) has a `correlation_id` folded in, and
    every response carries it in the `X-Correlation-Id` header — quote it when
    reporting problems. A stable, machine-readable `error_code` field is present on
    the errors that need one (e.g. `DOMAIN_ALREADY_CLAIMED`).

=== "Validation Error"

    ```json
    {
      "type": "error",
      "msg": "Validation failed",
      "data": {
        "errors": [
          "Organization name is required",
          "Invalid admin email format"
        ]
      }
    }
    ```

    Endpoints that validate request bodies with Pydantic models instead return
    FastAPI's standard `422` shape: `{"detail": [{"loc": [...], "msg": ...}]}`.

---

## Pagination

List endpoints support pagination with these query parameters:

| Parameter | Type | Default | Max | Description |
|---|---|---|---|---|
| `page` | integer | `1` | -- | Page number (1-based) |
| `per_page` | integer | `50` | `200` | Items per page |

Paginated responses include a `pagination` object:

```json
{
  "type": "success",
  "msg": "Organizations retrieved successfully",
  "data": {
    "items": [ ... ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 127,
      "total_pages": 3
    }
  }
}
```

---

## Rate Limiting

The HTTP API does not enforce a blanket requests-per-hour quota on valid
credentials. What is enforced:

| Throttle | Limit | Scope |
|---|---|---|
| Invalid API keys | 20 failures / 5 min, backoff from the 5th | per client IP |
| Dashboard login failures | 5 / 15 min, backoff from the 3rd | per IP **and** per email |
| Operator login failures | 3 / 15 min | per IP and per email |
| Mailbox (webmail) login failures | rate-limited by IP and address | per IP and per address |

All of these return `429 Too Many Requests` when tripped.

**Email sending** rate limits (messages per hour/day/month for organizations,
domains, mailboxes, and individual SMTP credentials) are a separate system — see
[Rate Limiting](rate-limiting.md).

---

## HTTP Status Codes

| Code | Meaning | When you'll see it |
|---|---|---|
| `200` | Request succeeded | Successful GET, PUT, PATCH, DELETE |
| `201` | Resource created | Successful POST that creates something |
| `204` | No content | e.g. deleting an email account that is already absent |
| `307` | Redirect | Collection root requested without its trailing slash |
| `400` | Bad request | Validation error, missing fields, malformed JSON |
| `401` | Unauthorized | Missing or invalid credential |
| `403` | Forbidden | Valid credential but insufficient permission, scope, role, or CSRF |
| `404` | Not found | Resource doesn't exist or belongs to another org |
| `409` | Conflict | Duplicate resource, or resource state forbids the change |
| `422` | Unprocessable | Bad query parameter value (unknown sort key, bad enum) or Pydantic body validation |
| `429` | Rate limit exceeded | An authentication throttle tripped |
| `500` | Internal server error | Something broke on the server side |
| `503` | Service unavailable | A downstream microservice (analytics, tracking, RAG, storage, rate limiter) is unreachable |

See the [Errors](errors.md) page for the full error reference.

---

## Available Endpoints

| Module | Prefix | Methods | Description |
|---|---|---|---|
| [Organizations](organizations.md) | `/api/v1/organizations` | GET, POST, PUT, DELETE | Manage organizations |
| [Domains](domains.md) | `/api/v1/domains` | GET, POST, PUT, DELETE | Manage domains, DNS verification, DKIM |
| [Email Accounts](email-accounts.md) | `/api/v1/mailboxes` | GET, POST, PUT, DELETE | Manage mailboxes (`/mailboxes/email-accounts...`) |
| [Aliases](aliases.md) | `/api/v1/aliases` | GET, POST | Manage email aliases |
| [Webhooks](webhooks.md) | `/api/v1/webhooks` | GET, POST, PUT, DELETE | Webhook endpoints, delivery log, dead letters |
| [Statistics](statistics.md) | `/api/v1/analytics`, `/api/v1/tracking` | GET, POST, DELETE | Delivery/engagement stats, reports, suppressions |
| [Rate Limiting](rate-limiting.md) | `/api/v1/rate-limiter` | GET, POST | View and update sending limits |
| [Storage](storage-management.md) | `/api/v1/storage` | GET, POST | Storage usage and quota configuration |
| [RAG System](rag-system.md) | `/api/v1/rag` | GET, POST, PUT | AI-powered email search |
| [Monitoring](monitoring.md) | `/api/v1/monitoring` | GET, POST | Health checks, metrics, admin operations |
| [SMTP Credentials](smtp-credentials.md) | `/api/v1/smtp-credentials` | GET, POST, DELETE | Domain-scoped SMTP API keys: create, rotate, revoke, per-key rate limits, delivery reports (live since 2026-08-27) |
| [Transport Rules](transport-rules.md) | `/api/v1/transport-rules` | GET, POST, PUT, DELETE | Priority-ordered mail-flow rules with conditions and actions |
| [Message Trace](message-trace.md) | `/api/v1/message-trace` | GET, POST | Delivery-log search, message lifecycle, quarantine (console) |
| [Shared Mailboxes](shared-mailboxes.md) | `/api/v1/shared-mailboxes` | GET, POST, PUT, DELETE | Team addresses with per-member permission levels |
| [Filters](filters.md) | `/api/v1/filters` | GET, POST, PUT, DELETE | Per-user Dovecot Sieve scripts, templates, vacation responder |
| [Tracking](tracking.md) | `/api/v1/tracking` | GET, POST, DELETE | Open/click/unsubscribe tracking and suppression lists |
| [Analytics](analytics.md) | `/api/v1/analytics` | GET | Domain analytics gateway (mostly unimplemented upstream — see page) |
| [Queue](queue.md) | `/api/v1/queue` | GET, POST | Postfix queue status, deferred listing, flush (console) |
| [Migration](migration.md) | `/api/v1/migration` | GET, POST, DELETE | IMAP import/export jobs with progress, retry, delta sync |
| [SSL Certificates](ssl.md) | `/api/v1/ssl` | GET, POST | Certificate expiry, ACME accounts (console) |
| [Security](security.md) | `/api/v1/security` | GET, POST, DELETE | Failed-auth blocking, IP rules, geo policies, DLP (console) |
| [Reputation](reputation.md) | `/api/v1/reputation` | GET | Domain/IP reputation and FBL complaints (console) |
| [Compliance](compliance.md) | `/api/v1/compliance` | GET, POST, DELETE | GDPR export/erasure, consent, legal holds, retention |
| [White Label](whitelabel.md) | `/api/v1/whitelabel` | GET, PUT | Per-organization branding |
| [Reseller](reseller.md) | `/api/v1/reseller` | GET, POST | Sub-organizations and billing rollups |
| [Platform](platform.md) | `/api/v1/platform` | GET, POST, PUT, DELETE | Console: operators, audit, alerts, backups, platform analytics |
| [Capabilities](capabilities.md) | `/api/v1/capabilities` | GET | Unauthenticated edition manifest |
| [Bootstrap](bootstrap.md) | `/api/v1/bootstrap` | POST | One-time first-install setup via `X-Bootstrap-Token` |

The [full endpoint index](../reference/api-endpoints.md) lists all 307 endpoints across
the 32 route modules. You can also browse the live
[Swagger UI](http://your-server:8083/api-docs) or
[Redoc reference](http://your-server:8083/api-reference) on your own deployment.

---

## Quick Start

The fastest way to verify your API is working and start exploring:

=== "curl"

    ```bash
    # 1. Check the server is running (no auth required)
    curl http://your-server:8083/health

    # 2. List your domains
    curl -H "X-API-Key: YOUR_KEY" \
      http://your-server:8083/api/v1/domains/

    # 3. Create a domain (tenant keys create it in their own org automatically)
    curl -X POST -H "X-API-Key: YOUR_KEY" \
      -H "Content-Type: application/json" \
      -d '{"domain": "example.com"}' \
      http://your-server:8083/api/v1/domains/

    # 4. Create a mailbox (passwords: 12+ chars with letters and numbers)
    curl -X POST -H "X-API-Key: YOUR_KEY" \
      -H "Content-Type: application/json" \
      -d '{"email": "alice@example.com", "password": "secure-pass-1234", "name": "Alice"}' \
      http://your-server:8083/api/v1/mailboxes/email-accounts
    ```

=== "Python"

    ```python
    import requests

    BASE = "http://your-server:8083/api/v1"
    HEADERS = {"X-API-Key": "YOUR_KEY"}

    # List domains
    domains = requests.get(f"{BASE}/domains/", headers=HEADERS)
    print(domains.json())

    # Create a domain
    domain = requests.post(f"{BASE}/domains/", headers=HEADERS, json={"domain": "example.com"})
    print(domain.json())

    # Create a mailbox
    mailbox = requests.post(
        f"{BASE}/mailboxes/email-accounts",
        headers=HEADERS,
        json={"email": "alice@example.com", "password": "secure-pass-1234", "name": "Alice"},
    )
    print(mailbox.json())
    ```

---

## Most common workflows

| I want to... | Start here |
|---|---|
| Set up a new customer | [Organizations](organizations.md) |
| Add a sending domain | [Domains](domains.md) |
| Provision mailboxes in bulk | [Email Accounts](email-accounts.md) |
| Get notified on mail events | [Webhooks](webhooks.md) |
| Track email opens and clicks | [Statistics](statistics.md) |
| Search emails by meaning | [RAG System](rag-system.md) |
| Check if the server is healthy | [Monitoring](monitoring.md) |

---

## Related sections

- [Getting Started](../getting-started/index.md) -- install the server and send your first email
- [Features](../features/index.md) -- understand what each feature does beyond the API
- [Architecture](../architecture/index.md) -- how the API fits into the broader system
- [Security](../security/index.md) -- authentication, authorization, and API key management
