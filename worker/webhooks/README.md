# Webhooks Module

Real-time event notification system that delivers HTTP callbacks for email events, system alerts, and service notifications with production-grade reliability and monitoring.

## Overview

The Webhooks module provides:
- **Event-Driven Architecture**: Real-time notifications for email and system events
- **Reliable Delivery**: Automatic retries with exponential backoff
- **Health Monitoring**: Comprehensive endpoint health tracking
- **Flexible Payloads**: Customizable event data formatting
- **Security**: Signature validation and authentication

## Supported Events

### Email Events
- `email.sent` - Email successfully sent
- `email.delivered` - Email delivered to recipient
- `email.bounced` - Email bounced back
- `email.complaint` - Spam complaint received
- `email.opened` - Email opened (tracking enabled)
- `email.clicked` - Link clicked in email

### System Events
- `quota.warning` - Storage quota warning
- `quota.exceeded` - Storage quota exceeded
- `service.health` - Service health changes
- `security.intrusion` - Security intrusion detected
- `certificate.renewal` - SSL certificate renewed
- `rate_limit.exceeded` - Rate limit violations

### Administrative Events
- `domain.added` - New domain configured
- `mailbox.created` - New mailbox created
- `alias.created` - New alias configured
- `user.login` - User authentication events

## API Endpoints

All endpoints are accessed through the API Gateway at `/api/v1/webhooks/`:

### Webhook Management
```
POST /api/v1/webhooks                 # Create webhook
GET  /api/v1/webhooks                 # List webhooks
GET  /api/v1/webhooks/{id}           # Get webhook details
PUT  /api/v1/webhooks/{id}           # Update webhook
DELETE /api/v1/webhooks/{id}         # Delete webhook
```

### Health & Monitoring
```
GET  /api/v1/webhooks/{id}/health    # Webhook health status
GET  /api/v1/webhooks/{id}/stats     # Delivery statistics
POST /api/v1/webhooks/{id}/test      # Test webhook delivery
```

## Configuration

### Webhook Creation
```json
{
  "url": "https://your-app.com/webhooks/mail",
  "events": ["email.sent", "email.delivered", "email.bounced"],
  "active": true,
  "secret": "your-webhook-secret",
  "headers": {
    "Authorization": "Bearer your-token"
  },
  "retry_policy": {
    "max_attempts": 5,
    "initial_delay": 1,
    "exponential_base": 2
  }
}
```

## Event Payloads

### Email Event Example
```json
{
  "event_id": "evt_123456789",
  "event_type": "email.delivered",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "message_id": "msg_abc123",
    "recipient": "user@example.com",
    "sender": "noreply@yourdomain.com",
    "subject": "Welcome to our service",
    "delivery_time": "2024-01-15T10:30:00Z"
  }
}
```

## Security

### Signature Validation
All webhook deliveries include a signature header for verification:
```python
import hmac
import hashlib

def verify_webhook_signature(payload, signature, secret):
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected_signature}")
```

## Retry Logic

### Automatic Retries
- **Initial Delay**: 1 second
- **Exponential Backoff**: 2x multiplier
- **Max Attempts**: 5 (configurable)
- **Retry Conditions**: Network errors, HTTP 5xx, HTTP 429

### No Retry Conditions
- **HTTP 4xx**: Client errors (except 408, 429)
- **Invalid URL**: Malformed webhook URLs
- **SSL Errors**: Certificate validation failures

## Health Monitoring

### Health Status
- **Healthy**: >95% success rate, <5s response time
- **Warning**: 90-95% success rate, 5-10s response time
- **Critical**: <90% success rate, >10s response time

## Development

### Local Setup
```bash
cd worker/webhooks
python app.py
```

### Testing
```bash
# Health check
curl http://0.0.0.0:8083/health

# Test webhook delivery
curl -X POST http://0.0.0.0:5000/api/v1/webhooks/test \
  -H "Content-Type: application/json" \
  -d '{"webhook_id": "webhook_123", "event_type": "email.sent"}'
```

## Dependencies

- **MySQL Database**: Webhook configuration and logs
- **Health Monitor**: Service status monitoring
- **API Gateway**: External API access