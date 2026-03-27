---
title: API Endpoints
description: Quick reference table of all Mailyte API endpoints with methods, paths, and descriptions.
---

# API Endpoints

Base URL: `http://mail.yourdomain.com:8083/api/v1`

All endpoints require the `X-API-Key` header unless noted otherwise. Admin endpoints require the `X-Admin-Password` header instead.

## Organizations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/add/organization` | Create a new organization |
| GET | `/get/organization/{id}` | Get organization details (`all` for all) |
| POST | `/edit/organization` | Update organization settings |
| POST | `/delete/organization` | Delete organizations (array of IDs) |

## Domains

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/add/domain` | Add a new domain |
| GET | `/get/domain/{id}` | Get domain details (`all` for all) |
| POST | `/edit/domain` | Update domain settings |
| POST | `/delete/domain` | Delete domains (array of names) |

## Mailboxes

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/add/mailbox` | Create a new mailbox |
| GET | `/get/mailbox/{id}` | Get mailbox details (`all` for all) |
| POST | `/edit/mailbox` | Update mailbox settings |
| POST | `/delete/mailbox` | Delete mailboxes (array of emails) |

## Aliases

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/add/alias` | Create a new alias |
| GET | `/get/alias/{id}` | Get alias details (`all` for all) |
| POST | `/edit/alias` | Update alias settings |
| POST | `/delete/alias` | Delete aliases (array of IDs) |

## DKIM

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/add/dkim` | Generate DKIM keys for a domain |
| GET | `/get/dkim/{domain}` | Get DKIM public key for DNS |

## Mail Queue

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/get/mailq/all` | Get mail queue status |
| POST | `/edit/mailq` | Queue actions (flush, delete) |

## Email Sending

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/send/email` | Send an email via the API |

## Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/add/webhook` | Register a webhook endpoint |
| GET | `/get/webhook/{id}` | Get webhook details (`all` for all) |
| POST | `/edit/webhook` | Update webhook settings |
| POST | `/delete/webhook` | Delete webhook endpoints |

## Statistics & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/get/status/version` | Get server version |
| GET | `/get/status/stats` | Get system-wide statistics |

## Tracking

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/get/tracking/{email_id}` | Get tracking events for an email |
| GET | `/get/tracking/stats/{domain}` | Get tracking stats for a domain |

## Suppressions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/get/suppressions/{domain}` | Get suppression list |
| POST | `/add/suppression` | Add address to suppression list |
| POST | `/delete/suppression` | Remove from suppression list |

## Storage

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/get/storage/{domain}` | Get storage usage for a domain |
| GET | `/get/storage/account/{email}` | Get storage for a mailbox |

## RAG / AI Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/rag/search` | Search emails with natural language |
| POST | `/rag/index` | Trigger re-indexing |

## Health & Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | API health check |
| GET | `/metrics` | Prometheus metrics |

## Admin (requires X-Admin-Password)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/admin/api-keys` | Create a new API key |
| GET | `/admin/api-keys` | List all API keys |
| DELETE | `/admin/api-keys/{key_id}` | Revoke an API key |
| POST | `/admin/cleanup` | Trigger data cleanup |

## Request/Response Examples

### Headers

```bash
# Standard API request
curl -X GET http://mail.yourdomain.com:8083/api/v1/get/domain/all \
  -H "X-API-Key: YOUR_API_KEY"

# Admin request
curl -X POST http://mail.yourdomain.com:8083/api/v1/admin/api-keys \
  -H "X-Admin-Password: YOUR_ADMIN_PASSWORD" \
  -H "Content-Type: application/json" \
  -d '{"description": "My Key", "read_only": false}'
```

### Success Response

```json
{
  "type": "success",
  "msg": ["domain_added", "example.com"]
}
```

### Error Response

```json
{
  "type": "error",
  "msg": "domain_already_exists"
}
```

## Rate Limits

- 1,000 requests per hour per API key
- 10 requests per second per API key

When rate limited, you'll receive a `429 Too Many Requests` response with a `Retry-After` header.
