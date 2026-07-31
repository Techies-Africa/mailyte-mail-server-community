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

    Create forwarding aliases, catch-all addresses, and distribution lists.

    [:octicons-arrow-right-24: Aliases](aliases.md)

-   :material-webhook:{ .lg .middle } **Webhooks**

    ---

    Subscribe to delivery, bounce, open, and click events with HMAC-signed payloads.

    [:octicons-arrow-right-24: Webhooks](webhooks.md)

-   :material-chart-bar:{ .lg .middle } **Statistics**

    ---

    Query delivery stats, engagement metrics, geo data, and time-series analytics.

    [:octicons-arrow-right-24: Statistics](statistics.md)

-   :material-speedometer:{ .lg .middle } **Rate Limiting**

    ---

    View and update rate limits at the org, domain, and mailbox level.

    [:octicons-arrow-right-24: Rate limiting](rate-limiting.md)

-   :material-harddisk:{ .lg .middle } **Storage**

    ---

    Check storage usage, set quotas, and get alerts before mailboxes fill up.

    [:octicons-arrow-right-24: Storage management](storage-management.md)

-   :material-brain:{ .lg .middle } **RAG System**

    ---

    AI-powered semantic search over email content using vector embeddings.

    [:octicons-arrow-right-24: RAG system](rag-system.md)

-   :material-heart-pulse:{ .lg .middle } **Monitoring**

    ---

    Health checks, service status, Prometheus metrics, and uptime tracking.

    [:octicons-arrow-right-24: Monitoring](monitoring.md)

</div>

---

## Base URL

All API endpoints are served under:

```
http://your-server:5000/api/v1/
```

Replace `your-server` with your actual hostname or IP address.

---

## Authentication

Every request must include one of two headers:

| Header | Purpose | Example |
|---|---|---|
| `X-API-Key` | Standard authentication for all API operations | `X-API-Key: mk_live_abc123...` |
| `X-Admin-Password` | Admin-level operations (service restarts, auto-healing) | `X-Admin-Password: your-admin-pass` |

!!! warning "Keep your API keys safe"
    API keys grant full access to the organization they're scoped to. Never commit them to version control or expose them in client-side code. Use environment variables or a secrets manager.

See the [Authentication](authentication.md) page for details on creating keys and permission levels.

---

## Request & Response Format

=== "Request"

    - All request bodies use JSON (`Content-Type: application/json`).
    - Use UTF-8 encoding for all text.
    - Query parameters handle filtering and pagination.

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
      "msg": "Organization not found"
    }
    ```

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

API keys are rate-limited to **1,000 requests per hour** by default. When you exceed the limit, the API returns `429 Too Many Requests`.

| Detail | Value |
|--------|-------|
| Default limit | 1,000 requests/hour |
| Reset | Start of each clock hour |
| Header | `X-RateLimit-Remaining` in responses |
| Configurable | Yes, per org via [Rate Limiting API](rate-limiting.md) |

!!! tip "Need higher limits?"
    You can adjust rate limits per organization through the Rate Limiting API. See [Rate Limiting](rate-limiting.md) for details.

---

## HTTP Status Codes

| Code | Meaning | When you'll see it |
|---|---|---|
| `200` | Request succeeded | Successful GET, PUT, PATCH, DELETE |
| `201` | Resource created | Successful POST that creates something |
| `400` | Bad request | Validation error, missing fields, malformed JSON |
| `401` | Unauthorized | Missing or invalid `X-API-Key` header |
| `403` | Forbidden | Valid key but insufficient permissions |
| `404` | Not found | Resource doesn't exist or belongs to another org |
| `409` | Conflict | Duplicate resource (e.g., domain already registered) |
| `429` | Rate limit exceeded | Too many requests in the current window |
| `500` | Internal server error | Something broke on the server side |
| `503` | Service unavailable | A downstream service (MySQL, Redis) is down |

See the [Errors](errors.md) page for the full error reference.

---

## Available Endpoints

| Module | Prefix | Methods | Description |
|---|---|---|---|
| [Organizations](organizations.md) | `/api/v1/organizations` | GET, POST, PUT, DELETE | Manage organizations |
| [Domains](domains.md) | `/api/v1/domains` | GET, POST, PUT, DELETE | Manage domains |
| [Email Accounts](email-accounts.md) | `/api/v1/mailboxes` | GET, POST, PUT, DELETE | Manage mailboxes |
| [Aliases](aliases.md) | `/api/v1/aliases` | GET, POST, PUT, DELETE | Manage email aliases |
| [Webhooks](webhooks.md) | `/api/v1/webhooks` | GET, POST, PUT, DELETE | Configure webhook endpoints |
| [Statistics](statistics.md) | `/api/v1/analytics`, `/api/v1/tracking` | GET | Delivery and engagement stats |
| [Rate Limiting](rate-limiting.md) | `/api/v1/rate-limiter` | GET, PUT | View and update rate limits |
| [Storage](storage-management.md) | `/api/v1/storage` | GET, PUT | Storage usage and quotas |
| [RAG System](rag-system.md) | `/api/v1/rag` | GET, POST | AI-powered email search |
| [Monitoring](monitoring.md) | `/api/v1/monitoring` | GET | Health checks and metrics |

---

## Quick Start

The fastest way to verify your API is working and start exploring:

=== "curl"

    ```bash
    # 1. Check the server is running
    curl http://your-server:5000/health

    # 2. List your organizations
    curl -H "X-API-Key: YOUR_KEY" \
      http://your-server:5000/api/v1/organizations

    # 3. Create a domain
    curl -X POST -H "X-API-Key: YOUR_KEY" \
      -H "Content-Type: application/json" \
      -d '{"domain": "example.com", "organization_id": "acme"}' \
      http://your-server:5000/api/v1/domains

    # 4. Create a mailbox
    curl -X POST -H "X-API-Key: YOUR_KEY" \
      -H "Content-Type: application/json" \
      -d '{"username": "alice", "domain": "example.com", "password": "secure-pass-123"}' \
      http://your-server:5000/api/v1/mailboxes
    ```

=== "Python"

    ```python
    import requests

    BASE = "http://your-server:5000/api/v1"
    HEADERS = {"X-API-Key": "YOUR_KEY"}

    # List organizations
    orgs = requests.get(f"{BASE}/organizations", headers=HEADERS)
    print(orgs.json())

    # Create a domain
    domain = requests.post(
        f"{BASE}/domains", headers=HEADERS, json={"domain": "example.com", "organization_id": "acme"}
    )
    print(domain.json())

    # Create a mailbox
    mailbox = requests.post(
        f"{BASE}/mailboxes",
        headers=HEADERS,
        json={"username": "alice", "domain": "example.com", "password": "secure-pass-123"},
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
| Get notified on delivery events | [Webhooks](webhooks.md) |
| Track email opens and clicks | [Statistics](statistics.md) |
| Search emails by meaning | [RAG System](rag-system.md) |
| Check if the server is healthy | [Monitoring](monitoring.md) |

---

## Related sections

- [Getting Started](../getting-started/index.md) -- install the server and send your first email
- [Features](../features/index.md) -- understand what each feature does beyond the API
- [Architecture](../architecture/index.md) -- how the API fits into the broader system
- [Security](../security/index.md) -- authentication, authorization, and API key management
