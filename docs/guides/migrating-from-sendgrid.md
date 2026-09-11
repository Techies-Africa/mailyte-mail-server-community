---
title: Migrating from SendGrid
description: Move your sending domains, application sending, suppression lists, and event webhooks from SendGrid to Mailyte step by step.
---

# Migrating from SendGrid

This guide covers migrating from SendGrid to Mailyte. SendGrid is primarily a transactional/marketing email service, so the migration focuses on sending domains, replacing the sending API with SMTP credentials, and webhook reconfiguration.

## Before You Start

You'll need:

- A running Mailyte server with API access
- Admin access to your SendGrid account
- Access to your DNS provider
- Your Mailyte API key (see [Authentication](../api/authentication.md))

!!! info "API base URL"
    Examples use `https://api.yourdomain.com` — in production Traefik publishes the API at `api.<your-domain>`; a dev checkout exposes it at `http://localhost:8083`.

!!! note "SendGrid vs Mailyte"
    SendGrid is a cloud email API. Mailyte is a full email server — it handles both sending *and* receiving, plus IMAP/POP3 access. You're gaining mailbox hosting, spam filtering, and full control over your infrastructure. The one structural difference for your application: Mailyte has no HTTP "send" endpoint — apps send over standard SMTP with a domain-scoped credential.

## Step 1: Audit Your SendGrid Setup

Log into SendGrid and note:

- **Authenticated domains** — Settings > Sender Authentication
- **API keys** and which apps use them
- **Webhook endpoints** — Settings > Mail Settings > Event Webhook
- **IP addresses** — if you have dedicated IPs, note your reputation score
- **Suppression lists** — bounces, spam reports, unsubscribes
- **Templates** — if you use SendGrid's template engine, export the HTML; you'll render templates in your own application when sending via SMTP

### Export Suppression Lists

From the SendGrid dashboard, go to Suppressions and export each list as CSV, or use the API:

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
# Create org (admin-scoped platform key required)
curl -X POST https://api.yourdomain.com/api/v1/organizations/ \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-company",
    "name": "My Company",
    "admin_email": "admin@mycompany.com"
  }'

# Add domain — DKIM keys are generated automatically and the response
# carries domain_id plus every DNS record to publish
curl -X POST https://api.yourdomain.com/api/v1/domains/ \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "mycompany.com",
    "organization_id": "my-company",
    "description": "Migrated from SendGrid"
  }'
```

Save the response — `domain_id`, `dns_records`, and `dkim_record` are all needed below.

### Create Sending Mailboxes

Unlike SendGrid, Mailyte hosts real mailboxes. Create one for each sending address so replies and bounces have somewhere to land:

```bash
curl -X POST https://api.yourdomain.com/api/v1/mailboxes/email-accounts \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "noreply@mycompany.com",
    "password": "Str0ng-Passw0rd-2026",
    "name": "No Reply",
    "domain_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV"
  }'
```

### Mint an SMTP Credential for Your Application

This replaces the SendGrid API key (SMTP credentials shipped 2026-08-27):

```bash
curl -X POST https://api.yourdomain.com/api/v1/smtp-credentials/ \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "name": "production app",
    "daily_limit": 100000
  }'
```

The response carries the generated `username` and the plaintext `secret` — **returned exactly once**, so store it in your secrets manager immediately. Credentials support IP allowlists, expiry, hourly/daily limits, rotation (`POST /{id}/rotate`), and instant revocation (`POST /{id}/revoke`).

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

Publish the records from the `dns_records` array in the domain-creation response — the shape is:

```
; MX record — point incoming mail to Mailyte
mycompany.com.    IN  MX  10  mail.yourdomain.com.

; SPF — authorize Mailyte (copy the exact include from dns_records)
mycompany.com.    IN  TXT  "v=spf1 include:spf.mail.yourdomain.com ~all"

; DKIM — the dkim_record value, at the dkim_dns_name name
default._domainkey.mycompany.com.  IN  TXT  "v=DKIM1; k=rsa; p=YOUR_PUBLIC_KEY"

; DMARC — start with monitoring
_dmarc.mycompany.com.  IN  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@mycompany.com"
```

!!! warning "Turn DKIM signing on"
    The DKIM key created via the API enables the DNS record, but Rspamd signs from key files on disk — follow [Setting Up DKIM](setting-up-dkim.md) or outbound mail goes out unsigned.

!!! tip "Lower TTL first"
    Drop your DNS TTL to 300 seconds the day before migration. This lets you switch back quickly if needed.

## Step 4: Update Your Application Code

The biggest change: SendGrid API calls become plain SMTP with your new credential.

### SendGrid SDK (Before)

```python
import sendgrid
from sendgrid.helpers.mail import Mail

sg = sendgrid.SendGridAPIClient(api_key="SG.xxx")

message = Mail(
    from_email="noreply@mycompany.com",
    to_emails="user@example.com",
    subject="Hello",
    html_content="<p>Hi there</p>",
)
sg.send(message)
```

### Mailyte SMTP (After)

```python
import smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg["From"] = "noreply@mycompany.com"
msg["To"] = "user@example.com"
msg["Subject"] = "Hello"
msg.set_content("Hi there")  # text/plain part
msg.add_alternative("<p>Hi there</p>", subtype="html")

with smtplib.SMTP("mail.yourdomain.com", 587) as smtp:
    smtp.starttls()
    smtp.login("mycompany-com-smtp-a1b2c3d4", "YOUR_SMTP_SECRET")
    smtp.send_message(msg)
```

Any framework mailer (Laravel, Rails ActionMailer, Django, Nodemailer) works unchanged — just point it at host `mail.yourdomain.com`, port 587 (STARTTLS) or 465 (TLS), with the credential's username/password.

!!! warning "Sender must match the credential's domain"
    Sender-login mismatch is enforced on ports 587 and 465 — a credential scoped to `mycompany.com` can only send `From:` addresses at that domain.

### Quick Reference: API Mapping

| SendGrid | Mailyte | Notes |
|----------|---------|-------|
| `POST /v3/mail/send` | SMTP submission on 587/465 with an SMTP credential | No HTTP send endpoint |
| API key management | `GET/POST /api/v1/smtp-credentials/`, `/{id}/rotate`, `/{id}/revoke` | Per-domain scoping, limits, IP allowlists |
| `GET /v3/stats` | `GET /api/v1/analytics/email-volume/{domain}`, `GET /api/v1/analytics/deliverability/{domain}` | Per-domain scoping |
| `GET /v3/suppression/bounces` | `GET /api/v1/tracking/suppressions?suppression_type=BOUNCE` | Filterable, paginated |
| Event Webhook | Global `WEBHOOK_URL` dispatcher + registered endpoints | Different event names — see below |
| Message search | `GET /api/v1/message-trace/trace` | Full delivery lifecycle per message |

## Step 5: Import Suppression Lists

```python
import json
import requests

headers = {"X-API-Key": "YOUR_MAILYTE_API_KEY", "Content-Type": "application/json"}

for filename, stype in [
    ("sg_bounces.json", "BOUNCE"),
    ("sg_spam_reports.json", "COMPLAINT"),
    ("sg_blocks.json", "BOUNCE"),
]:
    with open(filename) as f:
        emails = [item["email"] for item in json.load(f)]
    for chunk in (emails[i : i + 1000] for i in range(0, len(emails), 1000)):
        resp = requests.post(
            "https://api.yourdomain.com/api/v1/tracking/suppressions/bulk",
            headers=headers,
            json={"emails": chunk, "suppression_type": stype, "reason": "imported from SendGrid"},
        )
        print(filename, resp.status_code)
```

The bulk endpoint takes up to 1000 addresses per call and requires an operator-role credential; per-address failures are reported individually rather than silently dropped.

## Step 6: Migrate Webhook Handling

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

Mail-flow events are delivered to the platform-wide `WEBHOOK_URL` with a consistent envelope:

```json
{
  "id": "0d4f6c1e-6a0e-4f3f-9d3c-0b8b1a2c3d4e",
  "event": "email.delivered",
  "timestamp": "2026-08-30T10:30:00+00:00",
  "source": "log_ingestor",
  "org_id": "my-company",
  "domain": "mycompany.com",
  "data": {
    "message_id": "<abc@mycompany.com>",
    "recipient": "user@example.com"
  },
  "signature": {"timestamp": 1756550000, "token": "…", "signature": "…"}
}
```

Key differences:

- SendGrid sends an array of events; Mailyte sends one event per request
- Mailyte signs the raw body with an `X-Webhook-Signature: sha256=<hex>` header (HMAC-SHA256) and includes an inline replay-protection signature block
- Event names differ — `delivered` → `email.delivered`, `bounce` → `email.bounced` / `delivery.bounce.hard`, `open` → `tracking.open`, `click` → `tracking.click`

Set `WEBHOOK_URL` and `WEBHOOK_SECRET` on the Mailyte deployment, and see [Custom Integrations](custom-integrations.md) for the full delivery model, verification code, and retry schedule.

## Step 7: Warm Up Your IP

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

The SMTP credential's `hourly_limit`/`daily_limit` fields are a convenient enforcement mechanism for the ramp — raise them week by week. See [Improving Deliverability](improving-deliverability.md) for the full warm-up guide.

## Step 8: Verify Everything

### DNS Check

```bash
# Server-side, all four records at once
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/verify-dns \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY"

# Or manually
DOMAIN="mycompany.com"
echo "=== MX ===" && dig MX $DOMAIN +short
echo "=== SPF ===" && dig TXT $DOMAIN +short | grep spf
echo "=== DKIM ===" && dig TXT default._domainkey.$DOMAIN +short
echo "=== DMARC ===" && dig TXT _dmarc.$DOMAIN +short
```

### Send Test Email

Send to [mail-tester.com](https://www.mail-tester.com/) using the SMTP credential and aim for a score of 9/10 or higher.

### Checklist

- [x] MX records point to Mailyte
- [x] SPF includes the Mailyte SPF host
- [x] DKIM key is published and signing works (check `dkim=pass` in received headers)
- [x] DMARC record is present
- [x] Test emails arrive in inbox (not spam)
- [x] Application sends via SMTP with the new credential
- [x] Webhooks are arriving at your receiver
- [x] Suppression lists have been imported
- [x] Bounce handling is working

## Step 9: Decommission SendGrid

After 48-72 hours of successful operation:

1. Remove the authenticated domain from SendGrid
2. Revoke your SendGrid API keys
3. Update any remaining references in your code
4. Cancel your SendGrid plan

Keep your suppression list exports for at least 90 days — you may need to cross-reference bounce data.
