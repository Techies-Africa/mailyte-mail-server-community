# Rate Limiter Module

Advanced rate limiting service that prevents abuse and ensures fair resource usage across all email operations with real-time monitoring and intelligent throttling.

## Overview

The Rate Limiter provides:
- **Multi-Level Protection**: Organization, domain, and mailbox-based rate limiting
- **Real-Time Monitoring**: Live usage tracking and analytics
- **Intelligent Throttling**: Dynamic rate adjustment based on behavior
- **Abuse Prevention**: Automatic blocking of suspicious activity
- **Webhook Integration**: Real-time alerts for limit violations

## Rate Limiting Levels

### 1. Organization-Level Limits
- **Daily/Hourly Limits**: Maximum emails per organization
- **Monthly Quotas**: Long-term usage tracking
- **Burst Limits**: Short-term sending restrictions

### 2. Domain-Level Limits
- **Per-Domain Quotas**: Individual domain restrictions
- **Cross-Domain Tracking**: Aggregate organization usage
- **Custom Limits**: Domain-specific configurations

### 3. Mailbox-Level Limits
- **Per-User Sending**: Individual user sending quotas
- **Authentication Attempts**: Login failure protection
- **Connection Limits**: Concurrent connection restrictions

## API Endpoints

All endpoints are accessed through the API Gateway at `/api/v1/rate-limiter/`:

### Rate Limit Management
```
GET  /api/v1/rate-limiter/limits                    # List all rate limits
POST /api/v1/rate-limiter/limits                    # Create rate limit rule
GET  /api/v1/rate-limiter/limits/{id}              # Get specific rule
PUT  /api/v1/rate-limiter/limits/{id}              # Update rule
DELETE /api/v1/rate-limiter/limits/{id}            # Delete rule
```

### Usage Monitoring
```
GET  /api/v1/rate-limiter/usage/organization/{org_id}    # Organization usage
GET  /api/v1/rate-limiter/usage/domain/{domain}         # Domain usage stats
GET  /api/v1/rate-limiter/usage/mailbox/{email}         # Mailbox usage stats
```

### Real-Time Checking
```
POST /api/v1/rate-limiter/check                         # Check rate limit status
GET  /api/v1/rate-limiter/status                        # Live usage dashboard
```

## Configuration

### Default Limits (from .env.example)
```bash
# Organization Level
ORG_INBOUND_HOURLY_DEFAULT=5000
ORG_OUTBOUND_HOURLY_DEFAULT=10000
ORG_INBOUND_BURST_DEFAULT=1000

# Domain Level
DOMAIN_INBOUND_HOURLY_DEFAULT=1000
DOMAIN_OUTBOUND_HOURLY_DEFAULT=2000
DOMAIN_INBOUND_BURST_DEFAULT=200

# Mailbox Level
MAILBOX_INBOUND_HOURLY_DEFAULT=100
MAILBOX_OUTBOUND_HOURLY_DEFAULT=200
MAILBOX_INBOUND_BURST_DEFAULT=50
```

### Rate Limit Rules
```json
{
  "id": "rule_123",
  "name": "Organization Daily Limit",
  "type": "organization",
  "target": "org_456",
  "limits": {
    "hourly": 10000,
    "daily": 100000,
    "burst": 500
  },
  "active": true
}
```

## Alert System

### Alert Configuration
- **Warning Threshold**: 80% of limit reached
- **Critical Threshold**: 95% of limit reached
- **Exceeded Threshold**: 100% of limit reached

### Webhook Alerts
```json
{
  "alert_id": "alert_123",
  "type": "limit_exceeded",
  "timestamp": "2024-01-15T10:30:00Z",
  "organization_id": "org_456",
  "limit_type": "hourly",
  "current_usage": 10500,
  "limit": 10000,
  "percentage": 105
}
```

## Integration

### API Gateway Integration
The rate limiter is integrated into the API Gateway for automatic checking of all requests.

### Postfix Integration
Rate limits are enforced at the SMTP level through policy service integration.

## Performance Features

### Caching Strategy
- **Redis Caching**: Fast in-memory rate limit checks
- **Local Cache**: Process-level caching for hot paths
- **Cache TTL**: Configurable cache expiration (300 seconds default)

### Scalability
- **Horizontal Scaling**: Multiple rate limiter instances
- **Shared State**: Redis for distributed rate limiting
- **Database Optimization**: Efficient usage tracking

## Development

### Local Setup
```bash
cd worker/rate_limiter
python app.py
```

### Testing
```bash
# Health check
curl http://0.0.0.0:8082/health

# Test rate limit check via API Gateway
curl -X POST http://0.0.0.0:5000/api/v1/rate-limiter/check \
  -H "Content-Type: application/json" \
  -d '{"organization_id": "org_123", "action": "send_email", "count": 1}'
```

## Dependencies

- **MySQL Database**: Rate limit rules and usage tracking
- **Redis**: High-performance caching
- **API Gateway**: External API access
- **Health Monitor**: Service status monitoring