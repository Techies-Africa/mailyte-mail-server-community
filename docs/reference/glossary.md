---
title: Glossary
description: Plain English definitions for every email and server term you'll encounter in these docs.
---

# Glossary

Email has a lot of jargon. This page explains all of it in plain English.

## Protocols

**SMTP (Simple Mail Transfer Protocol)**
:   The protocol used to *send* email between servers. When you hit "Send," your email client talks SMTP to your mail server, and your mail server talks SMTP to the recipient's server. Runs on ports 25 (server-to-server), 587 (client submission with STARTTLS), and 465 (client submission with implicit TLS).

**IMAP (Internet Message Access Protocol)**
:   The protocol used to *read* email. Your mail client (Thunderbird, Outlook, Apple Mail) connects via IMAP to browse your inbox, read messages, and manage folders. The key thing: email stays on the server. Runs on ports 143 (STARTTLS) and 993 (implicit TLS).

**POP3 (Post Office Protocol v3)**
:   An older protocol for reading email. Unlike IMAP, POP3 typically downloads messages to your device and deletes them from the server. Most people use IMAP these days. Runs on ports 110 (STARTTLS) and 995 (implicit TLS).

**LMTP (Local Mail Transfer Protocol)**
:   A simplified version of SMTP used for delivering mail locally — like when Postfix hands off a message to Dovecot. You rarely interact with LMTP directly.

**JMAP (JSON Meta Application Protocol)**
:   A modern replacement for IMAP that uses JSON over HTTP. Faster and more efficient than IMAP for mobile clients. Still gaining adoption.

## Server Roles

**MTA (Mail Transfer Agent)**
:   The software that sends and receives email between servers. In Mailyte, this is **Postfix**. It accepts incoming mail on port 25 and delivers outgoing mail to other servers.

**MDA (Mail Delivery Agent)**
:   The software that puts incoming email into the right mailbox on disk. In Mailyte, **Dovecot** handles this via LMTP.

**MUA (Mail User Agent)**
:   The email client — Thunderbird, Outlook, Apple Mail, Gmail's web interface. The thing humans use to read and write email.

**MSA (Mail Submission Agent)**
:   The part of the MTA that accepts email from authenticated users (on port 587/465). It's how your email client submits outgoing mail.

## Authentication & Security

**SPF (Sender Policy Framework)**
:   A DNS TXT record that lists which servers are allowed to send email for your domain. When Gmail receives an email from `@yourdomain.com`, it checks the SPF record to see if the sending server is authorized.

**DKIM (DomainKeys Identified Mail)**
:   A system that signs outgoing email with a cryptographic key. The private key stays on your server; the public key goes in DNS. Receiving servers check the signature to verify the email wasn't tampered with in transit.

**DMARC (Domain-based Message Authentication, Reporting & Conformance)**
:   A DNS record that tells receiving servers what to do when SPF and DKIM fail. Options: `none` (just report), `quarantine` (put in spam), or `reject` (block entirely). Also tells receivers where to send aggregate reports about your domain's email.

**TLS (Transport Layer Security)**
:   Encryption for data in transit. When you see "STARTTLS" or port 993 for IMAP, that's TLS. It prevents anyone between your client and the server from reading the email content.

**STARTTLS**
:   A way to upgrade a plain-text connection to an encrypted one. The client connects on the regular port, then says "STARTTLS" to switch to encrypted mode. Contrast with implicit TLS, where the connection is encrypted from the start.

**SASL (Simple Authentication and Security Layer)**
:   A framework for authentication. In email, SASL is how your mail client proves your identity to the server when sending email. Dovecot handles SASL authentication for Postfix in Mailyte.

**MTA-STS (Mail Transfer Agent Strict Transport Security)**
:   A policy that tells other mail servers they *must* use TLS when sending to you. Prevents TLS downgrade attacks.

**TLSRPT (TLS Reporting)**
:   A DNS record that tells other servers where to send reports about TLS connection failures when sending to your domain.

## DNS Records

**MX Record (Mail Exchange)**
:   A DNS record that tells other servers where to deliver email for your domain. `example.com MX 10 mail.example.com` means "send email for example.com to mail.example.com." The number (10) is priority — lower is preferred.

**PTR Record (Pointer / Reverse DNS)**
:   Maps an IP address back to a hostname. Critical for email: if your server's IP is `203.0.113.1`, the PTR record should resolve to `mail.yourdomain.com`. Without it, most providers will reject your email.

**A Record**
:   Maps a hostname to an IP address. `mail.example.com A 203.0.113.1`.

**CNAME Record**
:   An alias that points one hostname to another. `autoconfig.example.com CNAME mail.example.com`.

**TXT Record**
:   A free-form text record. Used for SPF, DKIM public keys, DMARC policies, and various verification purposes.

**SRV Record**
:   A record that specifies the host and port for a service. Used for auto-configuration of email clients.

## Email Concepts

**Bounce**
:   When an email can't be delivered. A **hard bounce** (5xx) means the address doesn't exist — you should never send to it again. A **soft bounce** (4xx) means a temporary problem — full mailbox, server down. Postfix will retry soft bounces.

**Greylisting**
:   A spam-fighting technique where the server temporarily rejects email from unknown senders with a "try again later" response. Legitimate servers retry; most spambots don't. It delays first-time delivery by a few minutes.

**DNSBL (DNS-based Blackhole List)**
:   A list of IP addresses known to send spam. Rspamd checks incoming mail against several DNSBLs. If the sender's IP is listed, the spam score goes up. Also called RBL (Realtime Blackhole List).

**Bayesian Filter**
:   A statistical spam detection method. The filter learns from examples of spam and legitimate email, then calculates the probability that new messages are spam based on their content. Rspamd includes a Bayesian classifier backed by Redis.

**MIME (Multipurpose Internet Mail Extensions)**
:   The standard that allows email to contain things beyond plain text — HTML formatting, attachments, images, different character encodings. When you attach a PDF to an email, MIME is what makes that work.

**DSN (Delivery Status Notification)**
:   A standardized message that reports whether an email was delivered, delayed, or failed. The format uses 3-part codes like `2.0.0` (success) or `5.1.1` (user unknown).

**Envelope vs Header**
:   Every email has two sets of sender/recipient information. The **envelope** is used by SMTP for routing (like the outside of a physical envelope). The **headers** (From:, To:, Subject:) are what the recipient sees. They can differ — which is how BCC works, and also how spoofing works.

**Relay**
:   When a mail server passes email to another server for delivery. An "open relay" accepts email from anyone and sends it anywhere — this is bad and will get your server blacklisted immediately.

**Queue**
:   Where emails wait between being received and being delivered. Postfix queues messages when the destination server is temporarily unreachable and retries them later.

**Message-ID**
:   A unique identifier for each email, set by the sending server. Looks like `<random-string@yourdomain.com>`. Used for threading conversations and tracking delivery.

**Return-Path**
:   The email address that receives bounces. Set in the envelope (not the From: header). Also called the "envelope sender."

## Mailyte-Specific Terms

**Organization**
:   The top-level tenant in Mailyte's multi-tenant model. Each organization owns domains, which contain mailboxes. Organizations have separate quotas, rate limits, and webhook endpoints.

**Worker**
:   A background service that handles a specific job — tracking events, sending webhooks, calculating analytics, managing the queue, etc. Workers run as separate Docker containers.

**SMTP Credential**
:   A domain-scoped API key for sending mail over SMTP (ports 587/465) without a mailbox. Looks like `acme-com-smtp-a1b2c3d4` plus a secret shown once at creation. Authenticated by Dovecot directly against the `smtp_credentials` table, with optional per-key IP allowlists, expiry, and rate limits, and a full audit trail. Live since 2026-08-27.

**Log Ingestor**
:   The service that tails Postfix's mail log and turns each delivery attempt into a `mail_logs` row and an `email.delivered`/`bounced`/`deferred`/`rejected` webhook. It is where "email logs" in the UIs come from (since 2026-08-22).

**Transport Cutover Map**
:   A Postfix `transport_maps` file (`config/mailer/postfix/transport_cutover`) listing domains provisioned here whose MX still points elsewhere, so their mail routes out by public MX instead of delivering into an unread local mailbox. Entries must be removed at MX cutover or inbound mail loop-bounces.

**RAG (Retrieval-Augmented Generation)**
:   Mailyte's AI-powered email search. Emails are converted to vector embeddings (locally by default, via sentence-transformers) and stored in Qdrant. When you search, the query is compared against these vectors to find semantically similar emails — even if the exact words don't match.

**Qdrant**
:   The vector database used for RAG search. It stores mathematical representations of email content, enabling "meaning-based" search rather than keyword matching.

**cert_manager**
:   Mailyte's built-in service that handles Let's Encrypt certificate issuance and renewal (certbot webroot HTTP-01). It also generates the SNI (Server Name Indication) maps that let Postfix, Dovecot, and Traefik serve each domain its own certificate.
