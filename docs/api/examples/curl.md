# curl Examples

Complete curl examples for common Mailyte API workflows.

## Setup

Set these variables in your terminal to avoid repeating them in every command:

```bash
export MAILYTE_URL="http://your-server:5000"
export MAILYTE_KEY="mk_live_your_api_key_here"
```

## Organization Workflow

### Create an Organization

```bash
curl -X POST "$MAILYTE_URL/api/v1/organizations" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme",
    "name": "Acme Corp",
    "external_id": "cust_12345",
    "admin_email": "admin@acme.com",
    "admin_name": "Jane Smith",
    "rate_limits": {
      "inbound_hourly": 5000,
      "outbound_hourly": 2000
    },
    "storage_quotas": {
      "storage_quota_mb": 51200
    }
  }'
```

### List Organizations

```bash
curl "$MAILYTE_URL/api/v1/organizations?page=1&per_page=25" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Get a Specific Organization

```bash
curl "$MAILYTE_URL/api/v1/organizations/acme" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Look Up by External ID

```bash
curl "$MAILYTE_URL/api/v1/organizations/by-external-id/cust_12345" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Update an Organization

```bash
curl -X PUT "$MAILYTE_URL/api/v1/organizations/acme" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Corporation",
    "description": "Updated description"
  }'
```

### Delete an Organization

```bash
curl -X DELETE "$MAILYTE_URL/api/v1/organizations/acme" \
  -H "X-API-Key: $MAILYTE_KEY"
```

## Domain Workflow

### Add a Domain

```bash
curl -X POST "$MAILYTE_URL/api/v1/domains" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "acme.com",
    "organization_id": "acme",
    "description": "Primary domain",
    "max_quota": 10737418240,
    "max_users": 500,
    "dkim_enabled": true,
    "dkim_selector": "mailyte"
  }'
```

### List Domains for an Organization

```bash
curl "$MAILYTE_URL/api/v1/domains?organization_id=acme" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Get Domain Details

```bash
curl "$MAILYTE_URL/api/v1/domains/1" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Check Domain Quotas

```bash
curl "$MAILYTE_URL/api/v1/domains/1/quotas" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Update Domain Quotas

```bash
curl -X PUT "$MAILYTE_URL/api/v1/domains/1/quotas" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "max_quota": 21474836480,
    "max_users": 1000
  }'
```

## Email Account Workflow

### Create an Email Account

```bash
curl -X POST "$MAILYTE_URL/api/v1/email-accounts" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "john@acme.com",
    "password": "SecurePass123",
    "name": "John Doe",
    "storage_quota": 2147483648
  }'
```

### List Accounts for a Domain

```bash
curl "$MAILYTE_URL/api/v1/email-accounts?domain_id=1&status=ACTIVE" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Update an Account

```bash
curl -X PUT "$MAILYTE_URL/api/v1/email-accounts/1" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John A. Doe",
    "forward_enabled": true,
    "forward_destination": "john@gmail.com"
  }'
```

### Change Password

```bash
curl -X PUT "$MAILYTE_URL/api/v1/email-accounts/1" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"password": "NewSecure456"}'
```

### Delete an Account

```bash
curl -X DELETE "$MAILYTE_URL/api/v1/email-accounts/1" \
  -H "X-API-Key: $MAILYTE_KEY"
```

## Alias Workflow

### Create an Alias

```bash
curl -X POST "$MAILYTE_URL/api/v1/add/alias" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "info@acme.com",
    "goto": "john@acme.com, jane@acme.com"
  }'
```

### Create a Catch-All

```bash
curl -X POST "$MAILYTE_URL/api/v1/add/alias" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "@acme.com",
    "goto": "catchall@acme.com"
  }'
```

### Bulk Create Aliases

```bash
curl -X POST "$MAILYTE_URL/api/v1/add/alias/bulk" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "aliases": [
      {"address": "sales@acme.com", "goto": "john@acme.com"},
      {"address": "support@acme.com", "goto": "jane@acme.com"},
      {"address": "billing@acme.com", "goto": "finance@acme.com"}
    ]
  }'
```

### List All Aliases

```bash
curl "$MAILYTE_URL/api/v1/get/alias/all?page=1&per_page=50" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Delete Aliases

```bash
curl -X POST "$MAILYTE_URL/api/v1/delete/alias" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '["info@acme.com", "sales@acme.com"]'
```

## Webhook Workflow

### Register a Webhook

```bash
curl -X POST "$MAILYTE_URL/api/v1/webhooks/endpoints" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.acme.com/mailyte",
    "description": "Production webhook",
    "event_types": ["email.sent", "email.delivered", "email.bounced"]
  }'
```

### Test a Webhook

```bash
curl -X POST "$MAILYTE_URL/api/v1/webhooks/endpoints/1/test" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Check Delivery Failures

```bash
curl "$MAILYTE_URL/api/v1/webhooks/deliveries?status=FAILED" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Check Dead Letter Queue

```bash
curl "$MAILYTE_URL/api/v1/webhooks/dead-letters" \
  -H "X-API-Key: $MAILYTE_KEY"
```

## Analytics & Monitoring

### Get Engagement Stats

```bash
curl "$MAILYTE_URL/api/v1/analytics/engagement/acme.com?start_date=2025-03-01&end_date=2025-03-25" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Check Storage Usage

```bash
curl "$MAILYTE_URL/api/v1/storage/usage/domain/acme.com" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Check Rate Limit Usage

```bash
curl "$MAILYTE_URL/api/v1/rate-limits/usage/acme.com?period=daily" \
  -H "X-API-Key: $MAILYTE_KEY"
```

### Health Check

```bash
curl "$MAILYTE_URL/health"
```

### Semantic Email Search

```bash
curl -X POST "$MAILYTE_URL/api/v1/rag/search" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "invoices from last quarter",
    "organization_id": "acme",
    "limit": 5
  }'
```

## Admin Operations

### Restart a Service

```bash
curl -X POST "$MAILYTE_URL/api/v1/monitoring/services/postfix/restart" \
  -H "X-Admin-Password: your-admin-password"
```

### Auto-Heal All Services

```bash
curl -X POST "$MAILYTE_URL/api/v1/monitoring/auto-heal" \
  -H "X-Admin-Password: your-admin-password"
```

## Full Setup Script

Set up an organization with a domain, email accounts, and aliases from scratch:

```bash
#!/bin/bash
set -e

MAILYTE_URL="http://your-server:5000"
MAILYTE_KEY="mk_live_your_api_key_here"

echo "=== Creating organization ==="
curl -s -X POST "$MAILYTE_URL/api/v1/organizations" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme",
    "name": "Acme Corp",
    "admin_email": "admin@acme.com"
  }' | python3 -m json.tool

echo "=== Adding domain ==="
curl -s -X POST "$MAILYTE_URL/api/v1/domains" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "acme.com",
    "organization_id": "acme"
  }' | python3 -m json.tool

echo "=== Creating email accounts ==="
for user in john jane admin; do
  curl -s -X POST "$MAILYTE_URL/api/v1/email-accounts" \
    -H "X-API-Key: $MAILYTE_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"email\": \"${user}@acme.com\",
      \"password\": \"SecurePass123\",
      \"name\": \"${user^}\"
    }" | python3 -m json.tool
done

echo "=== Creating aliases ==="
curl -s -X POST "$MAILYTE_URL/api/v1/add/alias/bulk" \
  -H "X-API-Key: $MAILYTE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "aliases": [
      {"address": "info@acme.com", "goto": "admin@acme.com"},
      {"address": "support@acme.com", "goto": "jane@acme.com"}
    ]
  }' | python3 -m json.tool

echo "=== Done! ==="
```
