---
title: Migrating from Mailgun
description: Move your domains, mailboxes, and sending configuration from Mailgun to Mailyte without downtime.
---

# Migrating from Mailgun

This guide walks you through a zero-downtime migration from Mailgun to Mailyte. The whole process takes about an hour per domain, mostly waiting for DNS propagation.

## Before You Start

Make sure you have:

- A running Mailyte server with the API accessible
- Admin access to your Mailgun account
- Access to your DNS provider
- Your Mailyte API key (see [Authentication](../api/authentication.md))

!!! warning "Plan for overlap"
    Keep Mailgun active until you've confirmed Mailyte is receiving and sending mail correctly. Don't delete anything from Mailgun until you're fully migrated.

## Step 1: Export Your Mailgun Data

### Export Domains

Mailgun doesn't have a bulk export button, but you can grab everything via their API.

```bash
# List all domains
curl -s -u "api:YOUR_MAILGUN_API_KEY" \
  https://api.mailgun.net/v3/domains | python3 -m json.tool
```

Save this output. You'll need the domain names, DKIM selectors, and any custom settings.

### Export Routes and Forwarding Rules

```bash
# List all routes
curl -s -u "api:YOUR_MAILGUN_API_KEY" \
  https://api.mailgun.net/v3/routes | python3 -m json.tool
```

### Export Suppression Lists

```bash
# Bounces
curl -s -u "api:YOUR_MAILGUN_API_KEY" \
  https://api.mailgun.net/v3/YOUR_DOMAIN/bounces | python3 -m json.tool > bounces.json

# Complaints
curl -s -u "api:YOUR_MAILGUN_API_KEY" \
  https://api.mailgun.net/v3/YOUR_DOMAIN/complaints | python3 -m json.tool > complaints.json

# Unsubscribes
curl -s -u "api:YOUR_MAILGUN_API_KEY" \
  https://api.mailgun.net/v3/YOUR_DOMAIN/unsubscribes | python3 -m json.tool > unsubscribes.json
```

## Step 2: Create the Organization in Mailyte

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/organization \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-company",
    "name": "My Company",
    "admin_email": "admin@mycompany.com"
  }'
```

## Step 3: Add Domains

For each domain you exported from Mailgun:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/domain \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "mycompany.com",
    "description": "Migrated from Mailgun",
    "organization_id": "my-company",
    "mailboxes": 100,
    "aliases": 400,
    "maxquota": 10240
  }'
```

### Bulk Import Script

If you have many domains, script it:

```python
import requests
import json

MAILYTE_API = "http://mail.yourdomain.com:8083/api/v1"
API_KEY = "YOUR_MAILYTE_API_KEY"
ORG_ID = "my-company"

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

# Domains exported from Mailgun
domains = ["domain1.com", "domain2.com", "domain3.com"]

for domain in domains:
    resp = requests.post(f"{MAILYTE_API}/add/domain", headers=headers, json={
        "domain": domain,
        "organization_id": ORG_ID,
        "description": "Migrated from Mailgun",
        "mailboxes": 100,
        "aliases": 400,
    })
    result = resp.json()
    print(f"{domain}: {result.get('type', 'unknown')} - {result.get('msg', '')}")
```

## Step 4: Create Mailboxes

For each mailbox that needs to exist on Mailyte:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/mailbox \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "user",
    "domain": "mycompany.com",
    "password": "a-strong-password",
    "name": "User Name",
    "quota": 5120
  }'
```

!!! tip "Password handling"
    If you're migrating real user accounts, generate temporary passwords and force a password reset on first login by setting `"force_pw_update": 1`.

## Step 5: Set Up Aliases and Forwarding

Recreate your Mailgun routes as Mailyte aliases:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/alias \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "info@mycompany.com",
    "goto": "user@mycompany.com",
    "active": 1
  }'
```

## Step 6: Generate DKIM Keys

Generate DKIM keys for each domain on Mailyte:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/dkim \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domains": "mycompany.com",
    "dkim_selector": "default",
    "key_size": "2048"
  }'
```

Retrieve the public key for DNS:

```bash
curl http://mail.yourdomain.com:8083/api/v1/get/dkim/mycompany.com \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY"
```

## Step 7: Update DNS Records

This is the critical step. Update your DNS records to point to Mailyte instead of Mailgun.

### MX Record

```
mycompany.com.    IN  MX  10  mail.yourdomain.com.
```

Remove the Mailgun MX records (`mxa.mailgun.org`, `mxb.mailgun.org`).

### SPF Record

Replace the Mailgun include with your Mailyte server:

```
; Before (Mailgun)
mycompany.com.  IN  TXT  "v=spf1 include:mailgun.org -all"

; After (Mailyte)
mycompany.com.  IN  TXT  "v=spf1 mx a:mail.yourdomain.com ip4:YOUR_SERVER_IP -all"
```

### DKIM Record

Replace Mailgun's DKIM with the key from Step 6:

```
default._domainkey.mycompany.com.  IN  TXT  "v=DKIM1; k=rsa; p=YOUR_PUBLIC_KEY"
```

### DMARC Record

Keep your existing DMARC record, but start with `p=none` during the transition:

```
_dmarc.mycompany.com.  IN  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@mycompany.com"
```

!!! info "DNS propagation"
    Lower your TTL to 300 seconds (5 minutes) a day before the migration. This makes the switchover much faster. After confirming everything works, raise it back to 3600.

## Step 8: Verify the Migration

### Check DNS Propagation

```bash
# MX record
dig MX mycompany.com +short

# SPF
dig TXT mycompany.com +short | grep spf

# DKIM
dig TXT default._domainkey.mycompany.com +short

# DMARC
dig TXT _dmarc.mycompany.com +short
```

### Send a Test Email

```bash
# Send from Mailyte
curl -X POST http://mail.yourdomain.com:8083/api/v1/send/email \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "from": "test@mycompany.com",
    "to": "your-personal-email@gmail.com",
    "subject": "Mailyte migration test",
    "text": "If you see this, the migration is working."
  }'
```

### Verify with External Tools

- [MXToolbox](https://mxtoolbox.com/) — check MX, SPF, DKIM, DMARC
- [mail-tester.com](https://www.mail-tester.com/) — send a test and get a deliverability score
- [Google Postmaster Tools](https://postmaster.google.com/) — monitor reputation over time

## Step 9: Update Webhook Endpoints

If you were using Mailgun webhooks, update your application to accept Mailyte's webhook format. See [Webhook Events](../reference/webhook-events.md) for the full payload structure.

Key differences from Mailgun:

| Mailgun | Mailyte |
|---------|---------|
| `event-data.event` | `event` |
| `event-data.message.headers` | `payload.metadata` |
| `event-data.recipient` | `payload.delivery_info.recipient` |
| Signature in body | Signature in `X-Webhook-Signature` header |

## Step 10: Decommission Mailgun

Once you've confirmed:

- [x] Mail is flowing in and out through Mailyte
- [x] DKIM signatures are passing
- [x] SPF checks are passing
- [x] Webhooks are firing correctly
- [x] Users can log in to their mailboxes
- [x] No mail is stuck in Mailgun's queue

Then you can safely remove your domains from Mailgun and cancel your account.

!!! warning "Wait at least 48 hours"
    DNS caches can hold onto old records for up to 48 hours depending on TTL. Don't rush the decommission.

## Rollback Plan

If something goes wrong, switch the MX records back to Mailgun:

```
mycompany.com.    IN  MX  10  mxa.mailgun.org.
mycompany.com.    IN  MX  10  mxb.mailgun.org.
```

Since you kept Mailgun running during the migration, everything will resume as before within minutes (assuming you lowered the TTL).
