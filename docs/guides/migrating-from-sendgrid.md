---
title: Migrating from SendGrid
description: Move your sending domains, templates, and email flow from SendGrid to Mailyte step by step.
---

# Migrating from SendGrid

This guide covers migrating from SendGrid to Mailyte. SendGrid is primarily a transactional/marketing email service, so the migration focuses on sending domains, API integration, and webhook reconfiguration.

## Before You Start

You'll need:

- A running Mailyte server with API access
- Admin access to your SendGrid account
- Access to your DNS provider
- Your Mailyte API key

!!! note "SendGrid vs Mailyte"
    SendGrid is a cloud email API. Mailyte is a full email server — it handles both sending *and* receiving, plus IMAP/POP3 access. You're gaining mailbox hosting, spam filtering, and full control over your infrastructure.

## Step 1: Audit Your SendGrid Setup

Log into SendGrid and note:

- **Authenticated domains** — Settings > Sender Authentication
- **API keys** and which apps use them
- **Webhook endpoints** — Settings > Mail Settings > Event Webhook
- **IP addresses** — if you have dedicated IPs, note your reputation score
- **Suppression lists** — bounces, spam reports, unsubscribes
- **Templates** — if you use SendGrid's template engine

### Export Suppression Lists

From the SendGrid dashboard, go to Suppressions and export each list as CSV:

- Bounces
- Spam Reports
- Invalid Emails
- Unsubscribes

Or use the API:

```bash
# Bounces
curl -X GET https://api.sendgrid.com/v3/suppression/bounces \
  -H "Authorization: Bearer YOUR_SENDGRID_API_KEY" > sg_bounces.json

# Spam reports
curl -X GET https://api.sendgrid.com/v3/suppression/spam_reports \
  -H "Authorization: Bearer YOUR_SENDGRID_API_KEY" > sg_spam_reports.json

# Blocks
curl -X GET https://api.sendgrid.com/v3/suppression/blocks \
  -H "Authorization: Bearer YOUR_SENDGRID_API_KEY" > sg_blocks.json
```

## Step 2: Set Up Mailyte

### Create Organization and Domain

```bash
# Create org
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/organization \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-company",
    "name": "My Company",
    "admin_email": "admin@mycompany.com"
  }'

# Add domain
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/domain \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "mycompany.com",
    "organization_id": "my-company",
    "description": "Migrated from SendGrid"
  }'
```

### Create Sending Mailboxes

Unlike SendGrid, Mailyte needs actual mailboxes for sending addresses:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/mailbox \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "noreply",
    "domain": "mycompany.com",
    "password": "strong-password-here",
    "name": "No Reply"
  }'
```

### Generate DKIM

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

## Step 3: Update DNS Records

### Remove SendGrid Records

SendGrid typically adds these CNAME records:

```
# Remove these
em1234.mycompany.com     CNAME  u1234567.wl123.sendgrid.net
s1._domainkey.mycompany.com  CNAME  s1.domainkey.u1234567.wl123.sendgrid.net
s2._domainkey.mycompany.com  CNAME  s2.domainkey.u1234567.wl123.sendgrid.net
```

### Add Mailyte Records

```
; MX record — point incoming mail to Mailyte
mycompany.com.    IN  MX  10  mail.yourdomain.com.

; SPF — authorize Mailyte
mycompany.com.    IN  TXT  "v=spf1 mx a:mail.yourdomain.com ip4:YOUR_SERVER_IP -all"

; DKIM — use key from Mailyte
default._domainkey.mycompany.com.  IN  TXT  "v=DKIM1; k=rsa; p=YOUR_PUBLIC_KEY"

; DMARC — start with monitoring
_dmarc.mycompany.com.  IN  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@mycompany.com"
```

!!! tip "Lower TTL first"
    Drop your DNS TTL to 300 seconds the day before migration. This lets you switch back quickly if needed.

## Step 4: Update Your Application Code

The biggest change is swapping the SendGrid API calls for Mailyte API calls.

### SendGrid SDK (Before)

```python
import sendgrid
from sendgrid.helpers.mail import Mail

sg = sendgrid.SendGridAPIClient(api_key="SG.xxx")

message = Mail(
    from_email="noreply@mycompany.com",
    to_emails="user@example.com",
    subject="Hello",
    html_content="<p>Hi there</p>"
)
sg.send(message)
```

### Mailyte API (After)

```python
import requests

MAILYTE_API = "http://mail.yourdomain.com:8083/api/v1"
API_KEY = "YOUR_MAILYTE_API_KEY"

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

resp = requests.post(f"{MAILYTE_API}/send/email", headers=headers, json={
    "from": "noreply@mycompany.com",
    "to": "user@example.com",
    "subject": "Hello",
    "html": "<p>Hi there</p>"
})
print(resp.json())
```

### Quick Reference: API Mapping

| SendGrid | Mailyte | Notes |
|----------|---------|-------|
| `POST /v3/mail/send` | `POST /api/v1/send/email` | Different payload format |
| `GET /v3/stats` | `GET /api/v1/get/status/stats` | Similar data, different structure |
| `GET /v3/suppression/bounces` | `GET /api/v1/get/bounces` | Per-organization scoping |
| Event Webhook | Webhook service on `:8081` | Different event names |

## Step 5: Migrate Webhook Handling

SendGrid and Mailyte use different webhook formats.

### SendGrid Event Format

```json
[
  {
    "email": "user@example.com",
    "event": "delivered",
    "sg_message_id": "abc123",
    "timestamp": 1234567890
  }
]
```

### Mailyte Event Format

```json
{
  "event": "email.smtp.outbound",
  "timestamp": "2025-01-15T10:30:00Z",
  "payload": {
    "direction": "outbound",
    "metadata": {
      "message_id": "<abc@mycompany.com>",
      "from": "noreply@mycompany.com",
      "to": "user@example.com"
    },
    "delivery_info": {
      "delivery_status": "sent",
      "dsn_status": "2.0.0"
    }
  }
}
```

Key differences:

- SendGrid sends an array of events; Mailyte sends one event per request
- Mailyte signs payloads with `X-Webhook-Signature` header (HMAC-SHA256)
- Event names differ — see [Webhook Events](../reference/webhook-events.md) for the full list

## Step 6: Warm Up Your IP

If you were on SendGrid's shared IPs, your Mailyte server IP has no sending reputation. You need to warm it up gradually.

```mermaid
graph LR
    A[Week 1<br/>50 emails/day] --> B[Week 2<br/>200 emails/day]
    B --> C[Week 3<br/>1,000 emails/day]
    C --> D[Week 4<br/>5,000 emails/day]
    D --> E[Week 5+<br/>Full volume]
```

During warm-up:

- Start with your most engaged recipients (people who open your emails)
- Monitor bounce rates — keep them under 2%
- Watch for deferrals from Gmail and Microsoft
- Check [Google Postmaster Tools](https://postmaster.google.com/) daily

See [Improving Deliverability](improving-deliverability.md) for the full warm-up guide.

## Step 7: Verify Everything

### DNS Check

```bash
DOMAIN="mycompany.com"
echo "=== MX ===" && dig MX $DOMAIN +short
echo "=== SPF ===" && dig TXT $DOMAIN +short | grep spf
echo "=== DKIM ===" && dig TXT default._domainkey.$DOMAIN +short
echo "=== DMARC ===" && dig TXT _dmarc.$DOMAIN +short
```

### Send Test Email

Send to [mail-tester.com](https://www.mail-tester.com/) and aim for a score of 9/10 or higher.

### Checklist

- [x] MX records point to Mailyte
- [x] SPF includes Mailyte server IP
- [x] DKIM key is published and signing works
- [x] DMARC record is present
- [x] Test emails arrive in inbox (not spam)
- [x] Application code uses Mailyte API
- [x] Webhooks are firing to your endpoints
- [x] Suppression lists have been imported
- [x] Bounce handling is working

## Step 8: Decommission SendGrid

After 48-72 hours of successful operation:

1. Remove the authenticated domain from SendGrid
2. Revoke your SendGrid API keys
3. Update any remaining references in your code
4. Cancel your SendGrid plan

Keep your suppression list exports for at least 90 days — you may need to cross-reference bounce data.
