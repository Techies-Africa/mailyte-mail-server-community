---
title: Connect WordPress to Mailyte
description: Route WordPress email through Mailyte with WP Mail SMTP or MailPoet — create a credential, use the Other SMTP mailer, manage tracking per plugin, and let Mailyte handle bounces and complaints.
---

# Connect WordPress (MailPoet / WP Mail SMTP) to Mailyte

WordPress sends email two ways, and both relay cleanly through Mailyte SMTP Send:

- **[WP Mail SMTP](https://wpmailsmtp.com/)** routes *all* of WordPress's `wp_mail()` traffic — WooCommerce receipts, contact-form notifications, password resets — over SMTP.
- **[MailPoet](https://www.mailpoet.com/)** is the newsletter/campaign plugin; you point its "own sending method" at Mailyte for bulk sends.

Both connect the same way: an **Other SMTP** mailer pointed at Mailyte with an SMTP credential.

!!! prerequisite "Read this first"
    Your domain must be verified (SPF + DKIM + DMARC green), the AUP accepted, and prepaid credits on the account before a live credential is issued. See [Send Marketing & Bulk Email](sending-marketing-email.md) for the sending requirements, bounce/complaint limits, and warm-up. MailPoet campaigns are marketing traffic and are subject to those limits.

!!! tip "Use one credential per plugin"
    Create a **separate** SMTP credential for WP Mail SMTP and for MailPoet. That lets you set the tracking toggle and the send limits independently — MailPoet needs tracking off (Step 3), WP Mail SMTP usually doesn't.

## Step 1 — Create an SMTP credential in Mailyte

In the Mailyte dashboard, open **Sending → SMTP Credentials** and create a credential scoped to your sending domain (for example `acme.com`, or a marketing subdomain such as `news.acme.com`). Name it clearly — `wordpress-wpmailsmtp` or `wordpress-mailpoet`.

Mailyte shows the **secret exactly once** — copy it now; it's stored only as a hash and can't be retrieved later (rotate it any time). Note the generated **username**, e.g. `acme-com-smtp-x7k2m9`.

The connection settings you'll enter in either plugin:

| Setting | Value |
|---|---|
| SMTP Host | `smtp.acme.com` (if you've pointed that record at Mailyte) or your Mailyte mail hostname, e.g. `mail.yourdomain.com` |
| Port | `587` (STARTTLS) or `465` (SSL/TLS) |
| Encryption | TLS on 587, SSL on 465 |
| Authentication | On |
| Username | the credential username, e.g. `acme-com-smtp-x7k2m9` |
| Password | the secret from above |
| From email | an address **at the credential's domain**, e.g. `news@acme.com` |

!!! warning "The From address must match the credential's domain"
    A credential scoped to `acme.com` may only send `From:` addresses at `acme.com` (or its subdomains). Sender-login mismatch is enforced on ports 587 and 465. In WP Mail SMTP, set **From Email** and enable **Force From Email** so plugins can't override it with a mismatched address.

## Step 2 — Configure the plugin

=== "WP Mail SMTP"

    1. Install and activate **WP Mail SMTP**, then go to **WP Mail SMTP → Settings**.
    2. Under **Mailer**, choose **Other SMTP**.
    3. Enter the **From Email** and **From Name**; enable **Force From Email** (and, optionally, Force From Name).
    4. Set **SMTP Host**, **Encryption** (TLS/SSL), **SMTP Port**, and leave **Auto TLS** on.
    5. Turn **Authentication** on and enter the **SMTP Username** and **SMTP Password** from Step 1.
    6. Save, then use the **Email Test** tab to send yourself a test message.

=== "MailPoet"

    1. In WordPress, go to **MailPoet → Settings → Send With...**.
    2. Choose **Your own sending method → SMTP**.
    3. Enter the **Host**, **Port**, **Login** (the credential username), **Password** (the secret), and the **encryption** (SSL/TLS) matching your port.
    4. Set the **From** address to an address at the credential's domain.
    5. Set MailPoet's **sending frequency** at or below your credential's `hourly_limit` so MailPoet paces itself.
    6. Use **Send a test email** to confirm the connection.

## Step 3 — Manage tracking per plugin

Mailyte's content filter can inject an open pixel and rewrite links for click tracking. Whether you want that on depends on the plugin:

- **WP Mail SMTP** does **no** link tracking of its own — it just delivers `wp_mail()`. Leave the credential's **tracking toggle on** if you want Mailyte's open/click stats for these messages, or off if you don't. There's nothing to double-wrap.
- **MailPoet** does its **own** open and click tracking (Settings → Advanced). Running Mailyte's tracking on top **double-wraps** every link and skews both sets of stats — so turn the **per-credential tracking toggle to off** for the MailPoet credential (this is why a separate credential per plugin is worth it).

## Step 4 — Send a test

Send a test from the plugin and confirm:

- The message arrives with `dkim=pass` in its headers.
- Links resolve correctly (proof you're not double-wrapped for MailPoet).
- Newsletters carry an unsubscribe option — Mailyte injects a `List-Unsubscribe` one-click header automatically unless MailPoet already sends its own; MailPoet also adds its own unsubscribe link in the body, which you should keep.

## Where bounce and complaint handling comes from

WordPress plugins have limited native bounce handling over plain SMTP — **Mailyte handles bounces and complaints server-side:**

- A hard bounce or spam complaint **auto-suppresses** that recipient on your organization's unified suppression list.
- Any later send to a suppressed address is **rejected with a permanent `5.7.1`** at the SMTP layer, whether or not the plugin excluded it.
- Sustained bounce (>5%) or complaint (>0.3%) rates **auto-pause** the credential and notify you — see [Send Marketing & Bulk Email](sending-marketing-email.md).

Watch delivery, bounce, and complaint numbers in the Mailyte **Deliverability Center** and the credential's **usage** view. Before your first MailPoet campaign, import your previous ESP's bounce/unsubscribe list into Mailyte's suppressions so you never re-mail an address that already went bad.
