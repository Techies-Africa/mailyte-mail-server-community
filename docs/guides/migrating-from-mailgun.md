---
title: Migrating from Mailgun
description: Move your domains, sending credentials, suppression lists, and DNS from Mailgun to Mailyte without downtime.
---

# Migrating from Mailgun

This guide walks you through a zero-downtime migration from Mailgun to Mailyte. The whole process takes about an hour per domain, mostly waiting for DNS propagation.

## Before You Start

Make sure you have:

- A running Mailyte server with the API accessible
- Admin access to your Mailgun account
- Access to your DNS provider
- Your Mailyte API key (see [Authentication](../api/authentication.md))

!!! info "API base URL"
    Examples use `https://api.yourdomain.com` — in production Traefik publishes the API at `api.<your-domain>`; a dev checkout exposes it at `http://localhost:8083`.

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

Requires an admin-scoped platform API key:

```bash
curl -X POST https://api.yourdomain.com/api/v1/organizations/ \
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
curl -X POST https://api.yourdomain.com/api/v1/domains/ \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "mycompany.com",
    "organization_id": "my-company",
    "description": "Migrated from Mailgun"
  }'
```

The response is important — save it. It contains:

- `domain_id` — the ULID you'll need for every later call
- `dns_records` — the exact MX, SPF, and DMARC records to publish in Step 6
- `dkim_record` / `dkim_dns_name` — the DKIM TXT record (generated automatically)

### Bulk Import Script

If you have many domains, script it:

```python
import requests

MAILYTE_API = "https://api.yourdomain.com/api/v1"
API_KEY = "YOUR_MAILYTE_API_KEY"
ORG_ID = "my-company"

headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

# Domains exported from Mailgun
domains = ["domain1.com", "domain2.com", "domain3.com"]

dns_plans = {}
for domain in domains:
    resp = requests.post(
        f"{MAILYTE_API}/domains/",
        headers=headers,
        json={
            "domain": domain,
            "organization_id": ORG_ID,
            "description": "Migrated from Mailgun",
        },
    )
    result = resp.json()
    print(f"{domain}: {result.get('type', 'unknown')} - {result.get('msg', '')}")
    if result.get("type") == "success":
        dns_plans[domain] = result["data"]["dns_records"]
```

## Step 4: Create Mailboxes and Aliases

For each mailbox that needs to exist on Mailyte (use the `domain_id` from Step 3):

```bash
curl -X POST https://api.yourdomain.com/api/v1/mailboxes/email-accounts \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@mycompany.com",
    "password": "Temp0rary-Passw0rd-2026",
    "name": "User Name",
    "domain_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV"
  }'
```

Recreate your Mailgun routes as Mailyte aliases:

```bash
curl -X POST https://api.yourdomain.com/api/v1/aliases/add \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "info@mycompany.com",
    "destination": "user@mycompany.com"
  }'
```

`destination` accepts a comma-separated list for routes that forwarded to several recipients. Note that Mailyte's alias API requires a full source address — Mailgun catch-all routes have no direct equivalent.

## Step 5: Replace the Mailgun Sending API with SMTP Credentials

Mailyte has no HTTP "send message" endpoint — applications send over standard SMTP submission (port 587 STARTTLS or 465 TLS) using a domain-scoped **SMTP credential** (available since 2026-08-27):

```bash
curl -X POST https://api.yourdomain.com/api/v1/smtp-credentials/ \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "name": "app sending",
    "daily_limit": 50000
  }'
```

The response contains a generated `username` (shaped like `mycompany-com-smtp-a1b2c3d4`) and the plaintext `secret` — **shown exactly once**. Then swap your Mailgun HTTP calls for SMTP:

```python
import smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg["From"] = "noreply@mycompany.com"
msg["To"] = "user@example.com"
msg["Subject"] = "Hello from Mailyte"
msg.set_content("Plain-text body")

with smtplib.SMTP("mail.yourdomain.com", 587) as smtp:
    smtp.starttls()
    smtp.login("mycompany-com-smtp-a1b2c3d4", "THE_SECRET")
    smtp.send_message(msg)
```

!!! warning "Sender must match the credential's domain"
    Sender-login mismatch is enforced on ports 587 and 465: a credential scoped to `mycompany.com` can only send `From:` addresses at that domain.

## Step 6: Update DNS Records

This is the critical step. Publish the records returned in Step 3 — don't compose them by hand.

### MX Record

```
mycompany.com.    IN  MX  10  mail.yourdomain.com.
```

Remove the Mailgun MX records (`mxa.mailgun.org`, `mxb.mailgun.org`).

### SPF Record

Replace the Mailgun include with the SPF record from the domain-creation response (it uses an `include:` for the Mailyte SPF host):

```
; Before (Mailgun)
mycompany.com.  IN  TXT  "v=spf1 include:mailgun.org -all"

; After (Mailyte — copy the exact value from dns_records)
mycompany.com.  IN  TXT  "v=spf1 include:spf.mail.yourdomain.com ~all"
```

### DKIM Record

Replace Mailgun's DKIM with the `dkim_record` from Step 3, published at `dkim_dns_name`:

```
default._domainkey.mycompany.com.  IN  TXT  "v=DKIM1; k=rsa; p=YOUR_PUBLIC_KEY"
```

!!! warning "Make sure DKIM signing is actually on"
    The API-generated key enables verification records, but Rspamd signs from key files on disk — see [Setting Up DKIM](setting-up-dkim.md) for the step that writes the key file. Skipping it means mail goes out unsigned.

### DMARC Record

Keep your existing DMARC record, but start with `p=none` during the transition:

```
_dmarc.mycompany.com.  IN  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@mycompany.com"
```

!!! info "DNS propagation"
    Lower your TTL to 300 seconds (5 minutes) a day before the migration. This makes the switchover much faster. After confirming everything works, raise it back to 3600.

## Step 7: Import Suppression Lists

Feed the exports from Step 1 into Mailyte's suppression list so previously bounced or unsubscribed addresses are never mailed again:

```python
import json
import requests

headers = {"X-API-Key": "YOUR_MAILYTE_API_KEY", "Content-Type": "application/json"}

for filename, stype in [
    ("bounces.json", "BOUNCE"),
    ("complaints.json", "COMPLAINT"),
    ("unsubscribes.json", "UNSUBSCRIBE"),
]:
    with open(filename) as f:
        items = json.load(f).get("items", [])
    emails = [i["address"] for i in items]
    # At most 1000 addresses per call
    for chunk in (emails[i : i + 1000] for i in range(0, len(emails), 1000)):
        resp = requests.post(
            "https://api.yourdomain.com/api/v1/tracking/suppressions/bulk",
            headers=headers,
            json={
                "emails": chunk,
                "suppression_type": stype,
                "reason": "imported from Mailgun",
            },
        )
        print(filename, resp.status_code, resp.json().get("msg"))
```

The bulk endpoint requires an operator-role credential; single additions go through `POST /api/v1/tracking/suppress` with `{"email": ..., "reason": ...}`.

## Step 8: Verify the Migration

### Check DNS via the API

```bash
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/verify-dns \
  -H "X-API-Key: YOUR_MAILYTE_API_KEY"
```

All four checks — `mx`, `spf`, `dkim`, `dmarc` — should pass.

### Check DNS Manually

```bash
dig MX mycompany.com +short
dig TXT mycompany.com +short | grep spf
dig TXT default._domainkey.mycompany.com +short
dig TXT _dmarc.mycompany.com +short
```

### Send a Test Email

Use the SMTP credential from Step 5 to send to an external address (Gmail works well), then check the received headers for `spf=pass`, `dkim=pass`, and `dmarc=pass`.

### Verify with External Tools

- [MXToolbox](https://mxtoolbox.com/) — check MX, SPF, DKIM, DMARC
- [mail-tester.com](https://www.mail-tester.com/) — send a test and get a deliverability score
- [Google Postmaster Tools](https://postmaster.google.com/) — monitor reputation over time

## Step 9: Update Webhook Handling

Mailgun's event webhooks map onto Mailyte's global event dispatcher: set `WEBHOOK_URL` and `WEBHOOK_SECRET` on the Mailyte deployment and point them at your receiver. Key differences from Mailgun:

| Mailgun | Mailyte |
|---------|---------|
| `event-data.event` | top-level `event` (e.g. `email.delivered`, `email.bounced`) |
| `event-data.recipient` | inside the `data` object |
| Per-domain webhook config in the dashboard | One global `WEBHOOK_URL`; tracking/rate-limit/storage events can additionally target endpoints registered via `POST /api/v1/webhooks/endpoints` |
| Signature: `timestamp`+`token` HMAC | Same idea — inline `signature` block, plus `X-Webhook-Signature` over the raw body |

See [Custom Integrations](custom-integrations.md) for the full envelope format, verification code, and the retry schedule (which is deliberately Mailgun-compatible: 7 retries over ~8 hours, HTTP 406 to stop).

## Step 10: Decommission Mailgun

Once you've confirmed:

- [x] Mail is flowing in and out through Mailyte
- [x] DKIM signatures are passing
- [x] SPF checks are passing
- [x] Webhooks are arriving at your receiver
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
