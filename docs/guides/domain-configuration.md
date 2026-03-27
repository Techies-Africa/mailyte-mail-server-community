---
title: Domain Configuration
description: Add domains to Mailyte, configure DNS verification, set up DKIM, and create catch-all aliases.
---

# Domain Configuration

Every email address in Mailyte belongs to a domain, and every domain belongs to an organization. This guide covers adding, configuring, and verifying domains.

## Adding a Domain

### Via the API

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/domain \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "organization_id": "my-org",
    "description": "Primary company domain",
    "mailboxes": 100,
    "aliases": 400,
    "maxquota": 10240,
    "quota": 10240,
    "defquota": 3072,
    "active": 1
  }'
```

**Fields explained:**

| Field | Description |
|-------|-------------|
| `domain` | The domain name (no subdomains needed for email) |
| `organization_id` | Which org owns this domain |
| `mailboxes` | Maximum number of mailboxes allowed |
| `aliases` | Maximum number of aliases allowed |
| `maxquota` | Maximum quota per mailbox in MB |
| `quota` | Total domain quota in MB |
| `defquota` | Default quota for new mailboxes in MB |

## DNS Verification

After adding a domain, you need to set up DNS records so the world knows your Mailyte server handles email for this domain.

### Required DNS Records

```
; MX record — routes incoming email to your server
example.com.    IN  MX  10  mail.yourdomain.com.

; SPF — authorizes your server to send
example.com.    IN  TXT  "v=spf1 mx a:mail.yourdomain.com ip4:YOUR_IP -all"

; DKIM — see next section
default._domainkey.example.com.  IN  TXT  "v=DKIM1; k=rsa; p=..."

; DMARC — authentication policy
_dmarc.example.com.  IN  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@example.com"
```

### Verify DNS

Use the API to check if DNS is configured correctly:

```bash
curl http://mail.yourdomain.com:8083/api/v1/get/domain/example.com \
  -H "X-API-Key: YOUR_API_KEY"
```

Or manually:

```bash
# Check all records at once
DOMAIN="example.com"
echo "MX:" && dig MX $DOMAIN +short
echo "SPF:" && dig TXT $DOMAIN +short | grep spf
echo "DKIM:" && dig TXT default._domainkey.$DOMAIN +short
echo "DMARC:" && dig TXT _dmarc.$DOMAIN +short
```

## DKIM Setup

Generate DKIM keys for the domain:

```bash
# Generate
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/dkim \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domains": "example.com",
    "dkim_selector": "default",
    "key_size": "2048"
  }'

# Get the public key for DNS
curl http://mail.yourdomain.com:8083/api/v1/get/dkim/example.com \
  -H "X-API-Key: YOUR_API_KEY"
```

Add the returned public key as a DNS TXT record at `default._domainkey.example.com`.

For the complete DKIM setup process, see [Setting Up DKIM](setting-up-dkim.md).

## Creating Mailboxes

Once the domain is added:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/mailbox \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "john",
    "domain": "example.com",
    "password": "strong-password",
    "name": "John Smith",
    "quota": 5120
  }'
```

This creates `john@example.com` with a 5 GB quota.

## Catch-All Aliases

A catch-all alias delivers email sent to any address at the domain that doesn't have its own mailbox. Useful for small teams that don't want to miss anything.

### Set Up a Catch-All

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/alias \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "@example.com",
    "goto": "admin@example.com",
    "active": 1
  }'
```

The `@example.com` address (with nothing before the `@`) acts as the catch-all. Now `anything@example.com` goes to `admin@example.com`.

!!! warning "Catch-all and spam"
    Catch-all aliases receive *all* mail to the domain, including spam sent to made-up addresses. This can increase spam volume significantly. Consider using it only on low-traffic domains.

### Multiple Catch-All Destinations

Route to multiple recipients:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/alias \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "@example.com",
    "goto": "admin@example.com,support@example.com",
    "active": 1
  }'
```

## Aliases

### Simple Alias

Forward `info@example.com` to a specific mailbox:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/alias \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "info@example.com",
    "goto": "john@example.com",
    "active": 1
  }'
```

### Group Alias

Send to multiple people:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/alias \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "team@example.com",
    "goto": "john@example.com,jane@example.com,bob@example.com",
    "active": 1
  }'
```

### External Forwarding

Forward to an address outside Mailyte:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/alias \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "billing@example.com",
    "goto": "accounting@external-service.com",
    "active": 1
  }'
```

## Domain Settings

### Update Domain Configuration

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/edit/domain \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": ["example.com"],
    "attr": {
      "maxquota": 20480,
      "max_users": 200,
      "description": "Updated domain settings"
    }
  }'
```

### Disable a Domain

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/edit/domain \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": ["example.com"],
    "attr": {
      "active": false
    }
  }'
```

When a domain is disabled, email to and from that domain stops flowing. Existing mailbox data is preserved.

### Delete a Domain

!!! danger "This deletes all mailboxes and aliases under the domain"
    Back up any data you need before deleting.

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/delete/domain \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '["example.com"]'
```

## Multi-Tenant Domain Setup

For white-label setups where customers bring their own domains:

1. Customer adds their domain via your app
2. Your app calls the Mailyte API to create the domain
3. Customer updates their DNS to point to your mail server
4. Mailyte verifies DNS and activates the domain
5. DKIM keys are generated automatically

See the [DNS Setup guide](../configuration/dns-setup.md) for the full DNS record list, including tenant-specific records.
