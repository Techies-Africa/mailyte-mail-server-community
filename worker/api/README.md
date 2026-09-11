# API Gateway Module

The API Gateway serves as the central entry point for all external API requests, providing a unified interface to manage and secure access to internal microservices.

## Overview

This module acts as an abstraction layer that:
- **Centralizes Access Control**: Single point for authentication and authorization
- **Service Orchestration**: Routes requests to appropriate internal services
- **Security Enhancement**: Implements rate limiting, API key validation, and request sanitization
- **Version Management**: Provides versioned API endpoints for backward compatibility

## Architecture

```
External Clients → API Gateway → Internal Services
                     ↓
               [Authentication]
               [Rate Limiting]
               [Request Routing]
               [Response Formatting]
```

## Versioned API Endpoints

All API endpoints are versioned under `/api/v1/`. 32 route modules are mounted dynamically from the `route_modules` list in `app.py` -- organizations, domains, mailboxes, aliases, analytics, monitoring, queue, webhooks, rate_limiter, storage, tracking, rag, filters, shared_mailboxes, message_trace, transport_rules, whitelabel, reseller, compliance, migration, ssl, smtp_credentials (+ reports), capabilities, bootstrap, auth, platform_auth, platform, security, reputation, mailbox_auth, mailbox.

Resource modules use RESTful paths (e.g. `GET/POST /api/v1/domains/`, `GET/PUT/DELETE /api/v1/domains/{domain_id}`, `POST /api/v1/domains/{domain_id}/verify-dns`, DKIM management under `/{domain_id}/dkim*`), with some legacy `/add` / `/edit` / `/delete` compatibility routes still present in domains/mailboxes/aliases. Consult `/api-docs` (Swagger UI -- `docs_url` is customized, `/docs` is not served) or `/api-reference` (ReDoc) on a running instance for the authoritative list.

### Service Proxying

The API Gateway proxies to internal services over the compose network (target = service name : container port):

- **Tracking** (`/api/v1/tracking/`) → `tracking:8086`
- **Rate Limiting** (`/api/v1/rate-limiter/`) → `rate_limiter:8082`
- **Analytics** (`/api/v1/analytics/`) → `analytics:8085`
- **Monitoring** (`/api/v1/monitoring/`) → `monitoring:8085`
- **Queue Management** (`/api/v1/queue/`) → `queue_manager:8090`
- **Storage** (`/api/v1/storage/`) → `storage_usage:8092`
- **RAG** (`/api/v1/rag/`) → `rag:8090`

Webhook endpoint management (`/api/v1/webhooks/endpoints*`) is served from the gateway's own database, not proxied.

## Authentication

### API Key Authentication
```bash
curl -H "X-API-Key: your-api-key" \
     -H "Content-Type: application/json" \
     http://your-server:8083/api/v1/domains/
```

Besides tenant API keys, the gateway supports tenant browser sessions (`mailyte_session` cookie) and operator sessions (`mailyte_operator_session` cookie, separate table and lifetime) -- see `utils/auth.py`.

### Key Management
- Keys support read/write permissions
- Automatic expiration and rotation
- Usage tracking and monitoring
- IP-based restrictions (optional)

## Security Features

- **Rate Limiting**: Per-key and per-IP rate limiting
- **Input Validation**: Comprehensive request sanitization
- **CORS Protection**: Configurable cross-origin policies
- **Request Logging**: Full audit trail for compliance
- **Error Masking**: Secure error responses

## Response Format

All API responses follow a consistent format:
```json
{
  "type": "success|error",
  "msg": "Human-readable message",
  "data": {...},
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Error Handling

- **400 Bad Request**: Invalid request format or parameters
- **401 Unauthorized**: Missing or invalid API key
- **403 Forbidden**: Insufficient permissions
- **404 Not Found**: Resource not found
- **429 Too Many Requests**: Rate limit exceeded
- **500 Internal Server Error**: Server-side errors
- **503 Service Unavailable**: Downstream service unavailable

## Development

### Local Setup
```bash
cd worker/api
python app.py
```

### Testing
```bash
# Health check (container binds 8080; published as 8083 in dev)
curl http://localhost:8083/health

# API test with key
curl -H "X-API-Key: test-key" http://localhost:8083/api/v1/organizations
```

## Dependencies

- **MySQL Database**: Core data storage
- **Internal Services**: All worker microservices
- **Authentication Service**: User and key validation