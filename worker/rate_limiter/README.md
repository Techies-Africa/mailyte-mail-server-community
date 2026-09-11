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

### This service's own endpoints (FastAPI, port 8082)

```
POST /check_rate_limit                      # Check (and optionally record) usage
POST /increment_usage                       # Record usage without a check
GET  /get_usage/{entity_type}/{identifier}  # Current usage and limits
POST /set_limits                            # Per-entity limit overrides
POST /reset_counters                        # Reset counters
POST /policy                                # Raw Postfix policy protocol over HTTP
GET  /stats                                 # Service statistics
GET  /health                                # Health check
GET  /metrics                               # Prometheus metrics
```

### Through the API Gateway (`worker/api/routes/rate_limiter.py`)

```
GET/PUT /api/v1/rate-limiter/rate-limits/domain/{domain}
GET/PUT /api/v1/rate-limiter/rate-limits/mailbox/{email}
GET     /api/v1/rate-limiter/rate-limits/usage/{domain}
POST    /api/v1/rate-limiter/rate-limits/reset
GET/PUT /api/v1/rate-limiter/quotas/domain/{domain}
```

The gateway proxies to this service and enforces organization ownership of the domain/email path params before forwarding.

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

### Postfix Integration
`mailer/postfix/scripts/rate_limit_policy.py` (the `policy-rate-limit` spawn service) speaks the Postfix policy protocol and delegates every decision to this service's `/check_rate_limit` over HTTP with `record: true`. It is referenced from exactly one restriction list -- `smtpd_data_restrictions` -- so each message is counted once; adding it to other lists re-creates a documented production outage (see `main.cf`'s comments). Over quota, Postfix answers `DEFER_IF_PERMIT 4.7.1`; if this service is unreachable, the policy script fails open.

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

# Read-only rate limit check (no quota consumed without "record": true)
curl -X POST http://localhost:8082/check_rate_limit \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "direction": "outbound"}'
```

## Dependencies

- **MySQL Database**: Rate limit rules and usage tracking
- **Redis**: High-performance caching
- **API Gateway**: External API access
- **Health Monitor**: Service status monitoring