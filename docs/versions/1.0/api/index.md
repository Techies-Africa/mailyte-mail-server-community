# API Documentation

The Enterprise Mail Server provides a comprehensive REST API for managing all aspects of the email infrastructure. The API is designed to be RESTful, well-documented, and follows OpenAPI 3.0 specifications.

## API Overview

The API is organized around the following core resources:

- **Organizations**: Multi-tenant organization management
- **Domains**: Email domain configuration and management  
- **Mailboxes**: Individual email account management
- **Aliases**: Email forwarding and distribution lists
- **Tracking**: Email tracking and analytics
- **Webhooks**: Real-time event notifications
- **Rate Limiting**: Usage limits and quota management
- **Storage**: Storage monitoring and quota management
- **Analytics**: Performance metrics and reporting
- **RAG**: AI-powered email intelligence

## Base URL

All API requests should be made to:
```
https://api.yourdomain.com/api/v1
```

For development:
```
http://localhost:5000/api/v1
```

## Authentication

The API supports multiple authentication methods:

### API Key Authentication
```bash
curl -H "X-API-Key: your-api-key" https://api.yourdomain.com/api/v1/organizations
```

### JWT Token Authentication
```bash
curl -H "Authorization: Bearer your-jwt-token" https://api.yourdomain.com/api/v1/organizations
```

## Rate Limiting

API requests are rate limited based on your plan:

- **Basic**: 1,000 requests/hour
- **Professional**: 10,000 requests/hour  
- **Enterprise**: 100,000 requests/hour

Rate limit headers are included in all responses:
```http
X-RateLimit-Limit: 10000
X-RateLimit-Remaining: 9999
X-RateLimit-Reset: 1640995200
```

## Error Handling

The API uses conventional HTTP response codes and returns JSON error objects:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid email address format",
    "details": {
      "field": "email",
      "provided": "invalid-email"
    }
  }
}
```

### Common Error Codes

| Code | Status | Description |
|------|--------|-------------|
| `VALIDATION_ERROR` | 400 | Request validation failed |
| `UNAUTHORIZED` | 401 | Authentication required |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `CONFLICT` | 409 | Resource already exists |
| `RATE_LIMITED` | 429 | Rate limit exceeded |
| `INTERNAL_ERROR` | 500 | Server error |

## Resource Documentation

### Organizations API
Manage multi-tenant organizations with external identifiers.

**Endpoints:**
- `GET /organizations` - List organizations
- `POST /organizations` - Create organization
- `GET /organizations/{id}` - Get organization details
- `PUT /organizations/{id}` - Update organization
- `DELETE /organizations/{id}` - Delete organization

### Domains API
Manage email domains with DNS configuration and SSL certificates.

**Endpoints:**
- `GET /domains` - List domains
- `POST /domains` - Add domain
- `GET /domains/{id}` - Get domain details
- `PUT /domains/{id}` - Update domain settings
- `DELETE /domains/{id}` - Remove domain

### Mailboxes API
Manage individual email accounts with quotas and settings.

**Endpoints:**
- `GET /mailboxes` - List mailboxes
- `POST /mailboxes` - Create mailbox
- `GET /mailboxes/{id}` - Get mailbox details
- `PUT /mailboxes/{id}` - Update mailbox
- `DELETE /mailboxes/{id}` - Delete mailbox

### Tracking API
Access email tracking data and analytics.

**Endpoints:**
- `GET /tracking/events` - Get tracking events
- `GET /tracking/stats` - Get tracking statistics
- `POST /tracking/suppress` - Add suppression entry

### Webhooks API
Configure real-time event notifications.

**Endpoints:**
- `GET /webhooks` - List webhook configurations
- `POST /webhooks` - Create webhook
- `PUT /webhooks/{id}` - Update webhook
- `DELETE /webhooks/{id}` - Delete webhook

## SDKs and Libraries

Official SDKs are available for:

- **Python**: `pip install enterprise-mail-sdk`
- **Node.js**: `npm install enterprise-mail-sdk`
- **PHP**: `composer require enterprise/mail-sdk`
- **Ruby**: `gem install enterprise-mail-sdk`

## OpenAPI Specification

The complete API specification is available at:
```
https://api.yourdomain.com/api/v1/openapi.json
```

## Examples

### Create Organization
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Corporation",
    "external_id": "acme-corp-123",
    "limits": {
      "inbound_hourly": 10000,
      "outbound_hourly": 5000
    }
  }' \
  https://api.yourdomain.com/api/v1/organizations
```

### Add Domain
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "organization_id": 1,
    "external_id": "domain-example-com"
  }' \
  https://api.yourdomain.com/api/v1/domains
```

### Create Mailbox
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "secure-password",
    "domain_id": 1,
    "external_id": "user-123",
    "quota_mb": 1000
  }' \
  https://api.yourdomain.com/api/v1/mailboxes
```

## Webhook Events

The API sends webhooks for various events:

### Email Events
- `email.smtp.inbound` - Inbound email received
- `email.smtp.outbound` - Outbound email sent
- `email.bounced` - Email bounced
- `email.delivered` - Email delivered

### Tracking Events
- `email.opened` - Email opened
- `email.clicked` - Link clicked
- `email.unsubscribed` - Unsubscribe request

### System Events
- `quota.warning` - Storage quota warning
- `quota.exceeded` - Storage quota exceeded
- `rate_limit.exceeded` - Rate limit exceeded

## Best Practices

1. **Use External IDs**: Always provide external_id when creating resources for easy mapping
2. **Handle Rate Limits**: Implement exponential backoff for rate limited requests
3. **Validate Webhooks**: Always verify webhook signatures using HMAC
4. **Use HTTPS**: Always use HTTPS in production for API requests
5. **Monitor Usage**: Track your API usage and quotas regularly

## Support

- **Documentation**: [https://docs.mailserver.example.com](https://docs.mailserver.example.com)
- **API Status**: [https://status.mailserver.example.com](https://status.mailserver.example.com)
- **Support**: [support@mailserver.example.com](mailto:support@mailserver.example.com)