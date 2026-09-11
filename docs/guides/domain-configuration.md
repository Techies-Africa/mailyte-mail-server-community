---
title: Domain Configuration
description: Add domains to Mailyte, publish the generated DNS records, verify them via the API, and manage mailboxes and aliases.
---

# Domain Configuration

Every email address in Mailyte belongs to a domain, and every domain belongs to an organization. This guide covers adding, configuring, and verifying domains.

!!! info "API base URL"
    In production the API is published by Traefik at `https://api.<your-domain>`. On a development checkout it is exposed at `http://localhost:8083`. The examples below use `https://api.yourdomain.com` — substitute whichever applies. All requests authenticate with the `X-API-Key` header (see [Authentication](../api/authentication.md)).

## Adding a Domain

### Via the API

```bash
curl -X POST https://api.yourdomain.com/api/v1/domains/ \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "organization_id": "my-org",
    "description": "Primary company domain",
    "max_quota": 10240,
    "max_users": 100
  }'
```

**Fields explained:**

| Field | Description |
|-------|-------------|
| `domain` | The domain name. Globally unique — a domain already claimed by any organization returns `409 DOMAIN_ALREADY_CLAIMED` |
| `organization_id` | Required for platform-scope keys. Organization-scope keys have this forced to their own org — a supplied value is ignored |
| `description` | Free-text label |
| `max_quota` | Maximum quota (must be a non-negative number) |
| `max_users` | Maximum number of mailboxes (non-negative) |
| `external_id` | Optional mapping to an external system; must be unique |

The response includes everything you need for DNS setup:

- `dns_records` — the exact MX, SPF, and DMARC records to publish
- `dkim_record`, `dkim_selector`, `dkim_dns_name` — a DKIM key pair is generated automatically at creation, and the TXT record to publish is returned here

!!! warning "DKIM signing needs one more step"
    Creating a domain stores the DKIM key in the database, but Rspamd signs from key files on disk. See [Setting Up DKIM](setting-up-dkim.md) for the step that actually turns signing on.

## DNS Records

The records the API returns follow this shape (`mx.mailyte.com` stands in for your server's `MAIL_HOSTNAME`):

```
; MX record — routes incoming email to your server
example.com.    IN  MX  10  mx.mailyte.com.

; SPF — authorizes the Mailyte server to send for the domain
example.com.    IN  TXT  "v=spf1 include:spf.mx.mailyte.com ~all"

; DKIM — returned as dkim_record / dkim_dns_name at creation
default._domainkey.example.com.  IN  TXT  "v=DKIM1; k=rsa; p=..."

; DMARC — authentication policy
_dmarc.example.com.  IN  TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@example.com"
```

The MX/SPF hostnames come from the server's `MAIL_HOSTNAME` and `MAIL_SPF_HOST` environment variables. Publish the records exactly as returned by the API rather than composing them by hand.

### Verify DNS via the API

DNS verification performs live lookups of all four record types:

```bash
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/verify-dns \
  -H "X-API-Key: YOUR_API_KEY"
```

The response reports a per-record status for `mx`, `spf`, `dkim`, and `dmarc`. `DOMAIN_ID` is the ULID returned when the domain was created (also available from `GET /api/v1/domains/`).

### Verify DNS manually

```bash
# Check all records at once
DOMAIN="example.com"
echo "MX:" && dig MX $DOMAIN +short
echo "SPF:" && dig TXT $DOMAIN +short | grep spf
echo "DKIM:" && dig TXT default._domainkey.$DOMAIN +short
echo "DMARC:" && dig TXT _dmarc.$DOMAIN +short
```

## DKIM

A DKIM key is generated automatically when the domain is created. To list a domain's keys with the exact TXT record for each:

```bash
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/dkim \
  -H "X-API-Key: YOUR_API_KEY"
```

Each item carries `record_name`, `record_type`, and `record_value` ready to paste into a registrar's form. Private key material is never returned by the API.

For key-file placement, rotation, and troubleshooting, see [Setting Up DKIM](setting-up-dkim.md).

## Creating Mailboxes

Once the domain is added, create accounts with the domain's ULID:

```bash
curl -X POST https://api.yourdomain.com/api/v1/mailboxes/email-accounts \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "john@example.com",
    "password": "Str0ng-Passw0rd-2026",
    "name": "John Smith",
    "domain_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "storage_quota": 5368709120
  }'
```

`storage_quota` is in **bytes** (the example is 5 GB; the default is also 5 GB). Passwords must satisfy the platform policy — minimum length, at least one letter and one number, and not on the common-password blocklist.

## Aliases

### Simple Alias

Forward `info@example.com` to a specific mailbox:

```bash
curl -X POST https://api.yourdomain.com/api/v1/aliases/add \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "info@example.com",
    "destination": "john@example.com"
  }'
```

### Group Alias

`destination` accepts a comma-separated list to fan out to multiple people:

```bash
curl -X POST https://api.yourdomain.com/api/v1/aliases/add \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "team@example.com",
    "destination": "john@example.com,jane@example.com,bob@example.com"
  }'
```

### External Forwarding

Destinations outside Mailyte work the same way:

```bash
curl -X POST https://api.yourdomain.com/api/v1/aliases/add \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "billing@example.com",
    "destination": "accounting@external-service.com"
  }'
```

### Editing and Deleting Aliases

```bash
# Update destination or active flag for one or more aliases
curl -X POST https://api.yourdomain.com/api/v1/aliases/edit \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": ["info@example.com"],
    "attr": {"destination": "jane@example.com"}
  }'

# Delete alias(es) by ID or source address
curl -X POST https://api.yourdomain.com/api/v1/aliases/delete \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '["info@example.com"]'
```

There is also a bulk creation endpoint, `POST /api/v1/aliases/add/bulk`, which takes `{"aliases": [{"source": ..., "destination": ...}, ...]}`.

!!! note "No catch-all aliases"
    The alias API requires a full address as `source` — a bare `@example.com` catch-all is rejected by validation. As of 2026-08-30 catch-all delivery is not a supported feature; create explicit aliases for the addresses you need to receive.

## Domain Settings

### Update Domain Configuration

```bash
curl -X PUT https://api.yourdomain.com/api/v1/domains/DOMAIN_ID \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated domain settings",
    "max_quota": 20480,
    "max_users": 200
  }'
```

### Domain Quotas

```bash
# Current limits and per-account usage breakdown
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/quotas \
  -H "X-API-Key: YOUR_API_KEY"

# Adjust them
curl -X PUT https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/quotas \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"max_quota": 20480, "max_users": 200}'
```

### Disable a Domain

```bash
curl -X PUT https://api.yourdomain.com/api/v1/domains/DOMAIN_ID \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"active": false}'
```

Existing mailbox data is preserved while the domain is inactive.

### Delete a Domain

!!! danger "Delete the mailboxes first"
    `DELETE /api/v1/domains/{domain_id}` refuses to remove a domain that still has email accounts — delete those first. The legacy bulk endpoint `POST /api/v1/domains/delete/domain` (an array of domain names) removes a domain **along with** its mailboxes, aliases, and DKIM keys in one call, so treat it with care.

```bash
curl -X DELETE https://api.yourdomain.com/api/v1/domains/DOMAIN_ID \
  -H "X-API-Key: YOUR_API_KEY"
```

### Spam Policy

Per-domain spam/security policy (greylisting, RBL checks, blacklist-only mode) is managed via:

```bash
curl https://api.yourdomain.com/api/v1/domains/get/domain/policy/example.com \
  -H "X-API-Key: YOUR_API_KEY"

curl -X POST https://api.yourdomain.com/api/v1/domains/edit/domain/policy \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "policy_greylist": 1,
    "policy_rbl": 1,
    "policy_reject_spam": 0,
    "policy_bl_only": 0
  }'
```

## Multi-Tenant Domain Setup

For white-label setups where customers bring their own domains:

1. Customer adds their domain via your app
2. Your app calls `POST /api/v1/domains/` — DKIM keys are generated automatically and the response contains all DNS records
3. Customer publishes the returned records at their DNS provider
4. Your app polls `GET /api/v1/domains/{domain_id}/verify-dns` until all four checks pass

Once DNS is in place, mail clients can be configured automatically: since 2026-08-27 the autoconfig service answers Thunderbird autoconfig (`autoconfig.<domain>`), Outlook autodiscover (`autodiscover.<domain>`), and MTA-STS policy requests for every hosted domain — publish CNAMEs for those two subdomains pointing at the mail server.

See the [DNS Setup guide](../configuration/dns-setup.md) for the full DNS record list, including tenant-specific records.
