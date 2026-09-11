---
title: Connect MailWizz to Mailyte
description: Add Mailyte as an SMTP delivery server in MailWizz — create a credential, set host and port, avoid double tracking and double DKIM signing, and let Mailyte handle bounces and complaints.
---

# Connect MailWizz to Mailyte

[MailWizz](https://www.mailwizz.com/) is a self-hosted email marketing application that sends through **delivery servers** you configure. This guide adds Mailyte SMTP Send as an SMTP delivery server so your campaigns relay through your verified domain.

!!! prerequisite "Read this first"
    Your domain must be verified (SPF + DKIM + DMARC green), the AUP accepted, and prepaid credits on the account before a live credential is issued. See [Send Marketing & Bulk Email](sending-marketing-email.md) for the sending requirements, bounce/complaint limits, and warm-up.

## Before you start

- A running MailWizz install.
- A verified sending domain in Mailyte — for example `acme.com`, ideally a marketing subdomain such as `news.acme.com`.
- A Mailyte login with access to the **Sending** area of the dashboard.

## Step 1 — Create an SMTP credential in Mailyte

In the Mailyte dashboard, open **Sending → SMTP Credentials** and create a credential scoped to your sending domain. Name it `mailwizz`, and for marketing start with conservative `hourly_limit` / `daily_limit` values that match your warm-up schedule.

Mailyte shows the **secret exactly once** — copy it now; it's stored only as a hash and can't be retrieved later (you can rotate it any time).

!!! tip "Lock the credential to MailWizz's IP"
    Add your MailWizz server's IP to the credential's **IP allowlist** so a leaked secret can't be used from anywhere else.

## Step 2 — Add Mailyte as a delivery server

In MailWizz, go to **Servers → Delivery servers → Create new server** and choose type **SMTP**. Fill in:

| MailWizz field | Value |
|---|---|
| Hostname | `smtp.acme.com` (if you've pointed that record at Mailyte) or your Mailyte mail hostname, e.g. `mail.yourdomain.com` |
| Username | the credential username, e.g. `acme-com-smtp-x7k2m9` |
| Password | the secret from Step 1 |
| Port | `587` (STARTTLS) or `465` (SSL/TLS) |
| Protocol | `tls` for port 587, `ssl` for port 465 |
| From email | an address **at the credential's domain**, e.g. `news@acme.com` |
| From name | your sender name |
| Hourly/Daily quota | set at or below your Mailyte credential's `hourly_limit` / `daily_limit` |

!!! warning "The From address must match the credential's domain"
    A credential scoped to `acme.com` may only send `From:` addresses at `acme.com` (or its subdomains). Sender-login mismatch is enforced on ports 587 and 465, so a mismatched From address is rejected at submission. Set the delivery server's **Force from** to your domain to keep every campaign compliant.

Send MailWizz's built-in **validation email** for the delivery server to confirm the connection before assigning it to a campaign.

## Step 3 — Avoid double tracking and double signing

Two settings will collide with Mailyte if you leave them on:

- **Link/open tracking.** MailWizz rewrites links and injects an open pixel for its own campaign stats. Mailyte's content filter can do the same, which **double-wraps** every link. Choose one owner: keep MailWizz's tracking (recommended for campaigns) and turn the **per-credential tracking toggle to off** in Mailyte, or leave Mailyte's tracking on and disable MailWizz's.
- **DKIM signing.** MailWizz delivery servers can sign with DKIM ("Signing enabled"). Leave this **off** — Mailyte signs outbound mail with your domain's DKIM key via Rspamd, and a second signature is unnecessary and can conflict.

## Step 4 — Send a test campaign

Create a small test list of addresses you control and send a campaign. Confirm:

- The message arrives with `dkim=pass` in its headers (one signature, from Mailyte).
- Links resolve correctly (proof you're not double-wrapped).
- An unsubscribe option is present — Mailyte injects a `List-Unsubscribe` one-click header automatically unless MailWizz already sends its own; you must still include a visible unsubscribe link in the body ([UNSUBSCRIBE_URL]).

## Where bounce and complaint handling comes from

MailWizz can poll a **bounce server** and **feedback-loop server** if you host mailboxes for them, but you do not have to wire that up for suppression to work — **Mailyte handles bounces and complaints server-side:**

- A hard bounce or spam complaint **auto-suppresses** that recipient on your organization's unified suppression list.
- Any later send to a suppressed address is **rejected with a permanent `5.7.1`** at the SMTP layer, whether or not MailWizz excluded it.
- Sustained bounce (>5%) or complaint (>0.3%) rates **auto-pause** the credential and notify you — see [Send Marketing & Bulk Email](sending-marketing-email.md).

Watch delivery, bounce, and complaint numbers in the Mailyte **Deliverability Center** and the credential's **usage** view. Before your first MailWizz campaign, import your previous ESP's bounce/unsubscribe list into Mailyte's suppressions so you never re-mail an address that already went bad.
