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

### This service's own endpoints (FastAPI, port 8081)

```
POST /webhook/email/inbound    # Ingest a raw inbound email event
POST /webhook/email/outbound   # Ingest a raw outbound email event
POST /webhook/email/imap       # Ingest an IMAP activity event
POST /webhook/email/pop3       # Ingest a POP3 activity event
GET  /webhook/status           # Queue/worker stats
POST /webhook/test             # Fire a test event
GET  /cleanup/stats            # Delivery-log cleanup stats
POST /cleanup/now              # Run cleanup immediately
GET/PUT /cleanup/config        # Cleanup configuration
GET  /health                   # Health check
GET  /metrics                  # Prometheus metrics
```

`health_monitor.py` in this directory is a legacy Flask-style app that `app.py` does not start.

### Endpoint management (API Gateway)

Tenant webhook endpoint CRUD lives in the gateway (`worker/api/routes/webhooks.py`) under `/api/v1/webhooks/endpoints*` (create/list/get/update/delete/test), backed by the gateway's own database.

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
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
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
curl http://localhost:8081/health

# Fire a test event through the dispatcher
curl -X POST http://localhost:8081/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"event_type": "email.outbound"}'
```

Note: `shared/webhook_dispatcher.py` reads `WEBHOOK_URL` (singular). Compose maps `WEBHOOK_URLS` into it; without that mapping every `dispatch_event()` call silently no-ops. The canonical event catalog is the `Events` class in that module.

## Dependencies

- **MySQL Database**: Webhook configuration and logs
- **Health Monitor**: Service status monitoring
- **API Gateway**: External API access