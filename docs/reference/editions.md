---
title: Editions
description: What the Community Edition includes, what Enterprise adds, and how to tell which one a page applies to.
---

# Editions

This handbook documents both editions. Most of it applies to either one.

Pages describing something only the Enterprise Edition has carry a badge above
the title:

<p class="mailyte-edition mailyte-edition--enterprise">
  <span class="mailyte-edition__icon">&#9733;</span>
  Enterprise Edition &mdash; not included in the Community Edition.
</p>

No badge means the page applies to both. Community pages are deliberately left
unmarked: badging every page would put a label on almost all of them, and a
label that is always there is a label nobody reads.

---

## Community Edition

Free, AGPL-3.0, self-hosted, no seat limits and no licence key.

A complete mail server: Postfix for SMTP, Dovecot for IMAP and POP3, Rspamd for
filtering, a REST API over all of it, and webmail. Everything you need to run
mail for a domain, or for many.

**Included:** domains, mailboxes, aliases, DKIM, Sieve filters, SMTP
credentials, open and click tracking, unsubscribe handling, webhooks, rate
limiting, Let's Encrypt, autoconfiguration, the mailbox API, basic analytics,
the security policy editor, and the Mailyte Console.

[:octicons-arrow-right-24: Install it](../getting-started/server-requirements.md)

## Enterprise Edition

Everything above, plus the features that matter at scale or commercially.

| | |
|---|---|
| **Scale** | Queue management, delivery optimisation, storage usage accounting, Prometheus and Grafana monitoring |
| **Protocols** | ActiveSync, JMAP, CalDAV, OAuth |
| **Collaboration** | Shared mailboxes, distribution groups |
| **Data** | IMAP-to-IMAP migration, email archiving, PGP and S/MIME, semantic search (RAG) |
| **Governance** | GDPR export and erasure, legal holds, audit logging, DLP and geo-blocking *enforcement* |
| **Commercial** | Reseller accounts, white labelling, engagement analytics, scheduled reports |

[:octicons-arrow-right-24: mailyte.com](https://mailyte.com)

---

## Two things worth knowing

**The security screens differ.** Both editions ship the DLP and geo-blocking
policy editor, so a Community operator can author policies and read violations.
The workers that *act* on them are Enterprise. Community stores the rules;
Enterprise enforces them.

**The queue endpoints answer 503 in Community.** `/api/v1/queue/*` proxies a
queue-manager service the Community Edition does not ship. Inspect the Postfix
queue on the host instead:

```bash
docker exec postfix postqueue -p
```

---

## How the Console adapts

The [Mailyte Console](../api/platform.md) is one build for both editions. At
startup it reads `GET /api/v1/capabilities` from your server and renders only
the sections that server reports.

Against a Community install, the Enterprise sections are not disabled or
greyed out — they are not rendered at all. There is no screen that loads and
then fails, which is deliberate: an empty section is worse than an absent one.

```bash
curl https://your-server/api/v1/capabilities/
```

The `edition` field in that response is the authoritative answer to which
edition you are running.
