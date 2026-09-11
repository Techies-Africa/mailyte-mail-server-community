---
title: Send Marketing & Bulk Email
description: What Mailyte SMTP Send is, what you must have in place before your first campaign, the bounce and complaint limits that keep you sending, and how warm-up works.
---

# Send Marketing & Bulk Email through Mailyte SMTP

Mailyte SMTP Send lets you relay **marketing and bulk email** — newsletters, product announcements, drip campaigns — through Mailyte using a standard SMTP credential. You keep the campaign tool you already know (Sendy, MailWizz, Mautic, Listmonk, a WordPress plugin, or your own script); Mailyte is the pipe underneath it, plus the compliance and deliverability surface around it.

!!! info "Bring your own campaign tool"
    This is a **relay**, not a campaign builder. Your tool owns contacts, lists, segmentation, merge fields, and scheduling. Mailyte owns authenticated delivery, suppression, unsubscribe compliance, reputation, and billing. There is no campaign UI in Mailyte today — point your existing tool at Mailyte over SMTP and send.

Many relays refuse marketing outright and only permit transactional mail. Mailyte SMTP Send permits marketing, under the sending rules below — the rules are what keep everyone's mail landing in the inbox.

## What you need before your first send

Every marketing sender must clear these gates before a live sending credential is issued. The onboarding wizard enforces them in order; skipping one is not possible.

| Requirement | What it means |
|---|---|
| **Verified domain** | Your sending domain must pass SPF, DKIM, **and** DMARC — all three green in the [Deliverability Center](improving-deliverability.md). Marketing mail without full authentication is treated as suspicious by Gmail, Microsoft, and Yahoo. See [Domain Configuration](domain-configuration.md) and [Setting Up DKIM](setting-up-dkim.md). |
| **Accepted AUP** | You accept the Acceptable Use Policy — opt-in only, no purchased or scraped lists, honour unsubscribes. Acceptance is recorded with your user, timestamp, and IP. |
| **Opt-in lists only** | You may only mail recipients who explicitly asked to hear from you. Purchased, rented, scraped, or "we found your address" lists are prohibited and are the fastest route to a blocklist. |
| **One-click unsubscribe** | Mailyte injects a `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058 one-click) header automatically **unless your tool already sends its own** — in which case yours is passed through untouched. Every message still needs a visible unsubscribe link in the body; that part is your tool's job. |
| **Prepaid credits** | SMTP Send is prepaid. You buy send credits up front and they are drawn down per recipient accepted. Prepaid means a surprise bill is impossible — when the balance hits zero, sending stops with a clear SMTP error rather than running up an invoice. |

!!! tip "Authenticate on your own domain, not just ours"
    Gmail and Yahoo hang reputation primarily on the **authenticated (DKIM-aligned) domain**. Sending marketing from a subdomain such as `news.acme.com` with strict DKIM alignment keeps reputation attached to you, and is the single cheapest thing you can do to protect deliverability.

## The limits that keep you sending

Marketing traffic generates far more bounces and complaints than transactional mail, and both are watched continuously per credential and per organization.

| Metric | Threshold | What happens if you cross it |
|---|---|---|
| **Bounce rate** | below **5%** | A sending window above this **auto-pauses** the credential and notifies you. |
| **Complaint rate** (spam reports) | below **0.3%** | Gmail's published ceiling. Crossing it **auto-pauses** the credential and notifies you. |

An auto-pause is a documented product behaviour, not an outage — you are notified, the cause is reviewed, and sending resumes once it is fixed (usually a bad list segment). To stay well clear:

- **Mail only engaged, opted-in recipients.** Bounces and complaints come from stale or unwilling addresses.
- **Import your old ESP's bounce and unsubscribe list** into Mailyte's suppressions *before* your first send, so you never re-mail an address that already bounced or unsubscribed elsewhere.
- **Watch the numbers** in the [Deliverability Center](improving-deliverability.md) and per-credential usage as you ramp.

### Suppression is enforced server-side

Mailyte keeps a unified, organization-scoped suppression list and enforces it **at the server**, not just in your tool:

- A hard bounce or spam complaint **auto-suppresses** that recipient.
- Any later attempt to send to a suppressed address is **rejected with a permanent `5.7.1`** at the SMTP layer — the message never leaves, whether your tool remembered to exclude it or not.

This is a safety net, not a substitute for good list hygiene: repeated attempts to mail suppressed addresses still count against your reputation with your tool and show up in your logs.

## Warm-up

A new sending credential (and, for a brand-new domain, a new sending reputation) starts on a deliberately low limit and grows over time — sending your full list on day one is the classic way to get blocked.

- **Start low.** New marketing senders begin around a few hundred sends per day.
- **Grow on healthy stats.** The limit rises over the following weeks **while bounces stay under ~2% and complaints under ~0.1%** — comfortably inside the hard thresholds above.
- **Start with your most engaged recipients** — people who reliably open your mail — and expand outward as the numbers hold.

Your credential's `hourly_limit` / `daily_limit` are the enforcement mechanism for the ramp; they are raised as your reputation proves out. The warm-up schedule for your account is shown on screen during onboarding and in the Sending area of the dashboard. For the full mechanics, see [Improving Deliverability](improving-deliverability.md).

## Connect your tool

Once your domain is verified and your credential is issued, point your campaign tool at Mailyte:

- [Connect Sendy to Mailyte](connect-sendy.md)
- [Connect MailWizz to Mailyte](connect-mailwizz.md)
- [Connect WordPress (MailPoet / WP Mail SMTP) to Mailyte](connect-wordpress.md)

Any tool that speaks SMTP works the same way — host, port, and an SMTP credential. See [Custom Integrations](custom-integrations.md) for scripting against the API directly.
