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

All API endpoints are versioned under `/api/v1/` to ensure backward compatibility:

### Domain Management (`/api/v1/domains/`)
- `POST /add/domain` - Add new email domain
- `GET /get/domain/{domain_id}` - Retrieve domain information
- `POST /edit/domain` - Update domain settings
- `POST /delete/domain` - Remove domain
- `GET /get/domain/stats/{domain}` - Domain statistics

### Mailbox Management (`/api/v1/mailboxes/`)
- `POST /add/mailbox` - Create user mailbox
- `GET /get/mailbox/{mailbox_id}` - Get mailbox details
- `POST /edit/mailbox` - Update mailbox settings
- `POST /delete/mailbox` - Remove mailbox
- `GET /get/mailbox/stats/{domain}` - Mailbox statistics

### Alias Management (`/api/v1/aliases/`)
- `POST /add/alias` - Create email alias
- `GET /get/alias/{alias_id}` - Retrieve alias information
- `POST /edit/alias` - Update alias configuration
- `POST /delete/alias` - Remove alias
- `POST /add/alias/bulk` - Bulk alias creation

### Service Proxying

The API Gateway proxies requests to internal services (without versioning the internal calls):

- **Tracking System** (`/api/v1/tracking/`) → Internal port 8081
- **Webhook Management** (`/api/v1/webhooks/`) → Internal port 8083
- **Rate Limiting** (`/api/v1/rate-limiter/`) → Internal port 8082
- **Storage Management** (`/api/v1/storage/`) → Internal port 8084
- **RAG System** (`/api/v1/rag/`) → Internal port 8090
- **Queue Management** (`/api/v1/queue/`) → Internal port 5001
- **Analytics** (`/api/v1/analytics/`) → Internal port 8087

## Authentication

### API Key Authentication
```bash
curl -H "X-API-Key: your-api-key" \
     -H "Content-Type: application/json" \
     http://your-server:5000/api/v1/domains/all
```

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
# Health check
curl http://0.0.0.0:5000/health

# API test with key
curl -H "X-API-Key: test-key" http://0.0.0.0:5000/api/v1/health
```

## Dependencies

- **MySQL Database**: Core data storage
- **Internal Services**: All worker microservices
- **Authentication Service**: User and key validation