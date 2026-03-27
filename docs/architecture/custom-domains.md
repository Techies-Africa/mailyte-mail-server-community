# Custom Domain Architecture — How 500K+ Domains Work

## Overview

Mailyte supports **unlimited custom domains**. Each customer can use their own domain (`user@theircompany.com`) for both sending and receiving email. There is no architectural limit on the number of domains — the system uses database-driven lookups, not configuration files.

This document explains how custom domains work end-to-end, how DNS records connect everything, and what the scaling limits are.

---

## How It Works — The Big Picture

```
                    Customer's Domain DNS
                    ┌─────────────────────────────┐
                    │ theircompany.com             │
                    │                              │
                    │ MX  → mx.mailyte.com         │  ← All mail routes here
                    │ TXT → v=spf1 include:...     │  ← Authorizes Mailyte to send
                    │ TXT → DKIM public key         │  ← Proves emails are authentic
                    │ TXT → DMARC policy            │  ← Tells receivers what to do
                    └─────────────────────────────┘
                                  │
                                  ▼
              ┌───────────────────────────────────────┐
              │         Mailyte Email Server           │
              │         (mx.mailyte.com)               │
              │                                        │
              │  ┌──────────┐     ┌──────────────┐    │
              │  │ Postfix   │────→│ MySQL        │    │
              │  │ SMTP      │     │              │    │
              │  │           │     │ domains      │    │  500K+ rows
              │  │ Receives  │     │ email_accounts│   │  Millions of rows
              │  │ mail for  │     │ aliases       │   │
              │  │ ANY domain│     │ dkim_keys     │   │
              │  │ in the DB │     └──────────────┘    │
              │  └──────────┘              │           │
              │       │                    │           │
              │       ▼                    ▼           │
              │  ┌──────────┐     ┌──────────────┐    │
              │  │ Rspamd   │     │ Dovecot      │    │
              │  │          │     │              │    │
              │  │ DKIM sign│     │ IMAP/POP3    │    │
              │  │ per-domain│    │ auth from DB  │    │
              │  │ from DB   │    │              │    │
              │  └──────────┘     └──────────────┘    │
              └───────────────────────────────────────┘
```

**Key insight:** Postfix doesn't have a config file listing every domain. It queries MySQL in real-time:

```sql
-- "Should I accept mail for theircompany.com?"
SELECT 1 FROM domains WHERE domain = 'theircompany.com' AND active = 1;
-- If yes → accept. If no → reject (not our domain).
```

This query runs in microseconds even with 500K+ domains (indexed by `domain` column).

---

## DNS Records Explained

When a customer adds their domain to Mailyte, they need to configure 5 DNS records. Here's what each one does and how it connects to the server.

### 1. MX Record — "Where should my email go?"

```dns
theircompany.com.   IN  MX  10  mx.mailyte.com.
```

**What it does:** Tells every email server in the world that mail for `@theircompany.com` should be delivered to `mx.mailyte.com`.

**How it connects:**
```
Someone sends to user@theircompany.com
    → Sender's server does: dig MX theircompany.com
    → Gets: mx.mailyte.com
    → Connects to mx.mailyte.com:25
    → Mailyte's Postfix accepts the mail
    → Postfix checks MySQL: "Is theircompany.com in our domains table?"
    → Yes → delivers to the user's mailbox
```

### 2. SPF Record — "Who is allowed to send email as my domain?"

```dns
theircompany.com.   IN  TXT  "v=spf1 include:spf.mailyte.com ~all"
```

**What it does:** Tells receiving servers that only Mailyte's mail servers are authorized to send email from `@theircompany.com`. Without this, emails from the customer's domain would be flagged as potential spoofing.

**How it connects:**
```
Mailyte sends email from user@theircompany.com
    → Gmail receives it
    → Gmail checks: dig TXT theircompany.com
    → Gets: "v=spf1 include:spf.mailyte.com ~all"
    → Gmail checks: dig TXT spf.mailyte.com
    → Gets: "v=spf1 ip4:203.0.113.10 ~all"  (Mailyte's server IP)
    → Gmail verifies the sending IP matches → SPF PASS
    → Email is trusted
```

**Mailyte's SPF record (`spf.mailyte.com`):**
```dns
spf.mailyte.com.  IN  TXT  "v=spf1 ip4:<SERVER_IP> ip6:<SERVER_IPv6> ~all"
```

Customers include Mailyte's SPF record so they don't need to know the server IPs directly. If Mailyte's IPs change, only `spf.mailyte.com` needs updating — all 500K customer domains automatically pick up the change.

### 3. DKIM Record — "Prove this email wasn't tampered with"

```dns
default._domainkey.theircompany.com.  IN  TXT  "v=DKIM1; k=rsa; p=MIIBIjANBg..."
```

**What it does:** Publishes the public half of a cryptographic key pair. The private half is stored in Mailyte's database. When Mailyte sends an email from `@theircompany.com`, Rspamd signs the email headers with the private key. The receiving server uses the public key from DNS to verify the signature.

**How it connects:**
```
Mailyte sends email from user@theircompany.com
    → Rspamd queries MySQL: "What's the DKIM key for theircompany.com?"
    → Gets private key from dkim_keys table
    → Signs the email: DKIM-Signature: d=theircompany.com; s=default; ...
    → Email sent to Gmail
    → Gmail checks: dig TXT default._domainkey.theircompany.com
    → Gets the public key
    → Gmail verifies the signature → DKIM PASS
    → Email is authenticated
```

**Per-domain key isolation:** Each of the 500K domains has its OWN key pair. If one domain's key is compromised, no other domain is affected. Keys are generated automatically when a domain is added via the API.

### 4. DMARC Record — "What should receivers do with unauthenticated email?"

```dns
_dmarc.theircompany.com.  IN  TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc@theircompany.com"
```

**What it does:** Tells receiving servers the policy for handling email that fails SPF or DKIM checks:

- `p=none` — do nothing (monitoring only)
- `p=quarantine` — send to spam folder
- `p=reject` — refuse delivery entirely

**How it connects:**
```
Someone spoofs an email as user@theircompany.com (not from Mailyte)
    → Gmail receives it
    → SPF check: FAIL (sender IP not in SPF record)
    → DKIM check: FAIL (no valid signature)
    → Gmail checks: dig TXT _dmarc.theircompany.com
    → Gets: "v=DMARC1; p=quarantine"
    → Gmail moves the spoofed email to spam
    → Sends a report to dmarc@theircompany.com
```

### 5. Reverse DNS (PTR) — "Does this server's IP match its hostname?"

```dns
10.113.0.203.in-addr.arpa.  IN  PTR  mx.mailyte.com.
```

**What it does:** Maps Mailyte's IP address back to its hostname. This is set on the **server's IP**, not on customer domains. Gmail and other providers check that the sending IP resolves back to a valid mail hostname.

**This is configured ONCE for the Mailyte server, not per customer domain.**

---

## Complete DNS Setup Per Customer Domain

```dns
# 1. MX — Route all email to Mailyte
theircompany.com.                          MX    10  mx.mailyte.com.

# 2. SPF — Authorize Mailyte to send as this domain
theircompany.com.                          TXT   "v=spf1 include:spf.mailyte.com ~all"

# 3. DKIM — Publish the signing key (generated by Mailyte)
default._domainkey.theircompany.com.       TXT   "v=DKIM1; k=rsa; p=<PUBLIC_KEY>"

# 4. DMARC — Set policy for failed authentication
_dmarc.theircompany.com.                   TXT   "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@theircompany.com"

# 5. Autodiscover (optional) — Helps email clients auto-configure
autoconfig.theircompany.com.               CNAME autoconfig.mailyte.com.
autodiscover.theircompany.com.             CNAME autodiscover.mailyte.com.
```

---

## How Postfix Handles 500K Domains

Postfix uses **virtual domain lookup** via MySQL:

```
┌────────────────────────────────────────────────────────┐
│ Incoming email to: user@theircompany.com               │
│                                                        │
│ Step 1: Is this domain ours?                           │
│   SELECT 1 FROM domains                                │
│   WHERE domain = 'theircompany.com' AND active = 1     │
│   → Yes (0.1ms with index)                             │
│                                                        │
│ Step 2: Does this mailbox exist?                       │
│   SELECT 1 FROM email_accounts                         │
│   WHERE email = 'user@theircompany.com'                │
│     AND status = 'active'                              │
│   → Yes (0.1ms with index)                             │
│                                                        │
│ Step 3: Or is it an alias?                             │
│   SELECT destination FROM aliases                      │
│   WHERE source = 'user@theircompany.com'               │
│     AND active = 1                                     │
│   → Forward to real mailbox                            │
│                                                        │
│ Step 4: Deliver to Dovecot via LMTP                    │
│   → Stored at /var/mail/vhosts/theircompany.com/user/  │
└────────────────────────────────────────────────────────┘
```

**Performance:** Each lookup is a single indexed query. MySQL handles millions of rows with sub-millisecond response times. Postfix caches results via `proxymap` to reduce DB load.

---

## How Dovecot Authenticates 500K Domains

When `user@theircompany.com` logs into IMAP:

```sql
-- Password check (runs on every login)
SELECT email as user, password
FROM email_accounts
WHERE email = 'user@theircompany.com' AND status = 'active'

-- Mailbox location (runs once per session)
SELECT CONCAT('maildir:/var/mail/vhosts/', d.domain, '/', ea.local_part) as mail
FROM email_accounts ea
JOIN domains d ON ea.domain_id = d.id
WHERE ea.email = 'user@theircompany.com'
```

Dovecot uses auth caching (10MB cache, 1-hour TTL) to avoid hitting MySQL on every IMAP command. With 500K domains and millions of users, the cache handles the hot path.

---

## How DKIM Signing Works at Scale

Rspamd signs outbound email with per-domain DKIM keys:

```
┌─────────────────────────────────────────────┐
│ Outbound email from: user@theircompany.com  │
│                                             │
│ Rspamd checks dkim_signing.conf:            │
│   → selector_map: query MySQL for domain    │
│   → key: /var/lib/rspamd/dkim/{domain}.key  │
│                                             │
│ Signs with theircompany.com's private key   │
│ Adds: DKIM-Signature: d=theircompany.com    │
│                                             │
│ Receiving server verifies against DNS:      │
│   default._domainkey.theircompany.com TXT   │
└─────────────────────────────────────────────┘
```

**Key storage:** Keys are stored in the `dkim_keys` MySQL table and optionally cached on disk. At 500K domains, that's ~500K RSA key pairs (~2KB each = ~1GB total). This fits comfortably in memory.

---

## SSL/TLS at Scale — The Real Challenge

This is the **hardest part** of supporting 500K custom domains. Each domain needs its own TLS certificate for IMAP/SMTP.

### Option A: Shared Hostname (Simplest)

All customers use Mailyte's hostname for mail client connections:

```
IMAP: imap.mailyte.com:993
SMTP: smtp.mailyte.com:587
```

Only ONE certificate needed (wildcard `*.mailyte.com`). The customer's domain is only used in email addresses, not in server hostnames.

**Pros:** No per-domain cert management. Works at any scale.
**Cons:** Users see `mailyte.com` in their email client settings instead of their own domain.

### Option B: SNI Per-Domain (Enterprise)

Each customer gets their own hostname:

```
IMAP: mail.theircompany.com:993  (CNAME → mx.mailyte.com)
SMTP: mail.theircompany.com:587  (CNAME → mx.mailyte.com)
```

Postfix and Dovecot use **SNI (Server Name Indication)** to serve the correct certificate per domain.

**Mailyte's cert_manager handles this:**
1. Customer adds CNAME: `mail.theircompany.com → mx.mailyte.com`
2. Mailyte requests a Let's Encrypt cert for `mail.theircompany.com`
3. Cert stored in `ssl_certificates` table
4. Postfix/Dovecot SNI config updated dynamically
5. TLS handshake serves the correct cert based on the hostname the client connects to

**Let's Encrypt limits:**
- 50 certificates per registered domain per week
- But each CUSTOMER domain is a different registered domain
- So the limit is 50 certs per week per customer (not per Mailyte)
- For 500K domains: issue ~70K certs/day (well within limits)
- Renewal: certs last 90 days, renewed at 60 days = ~8,300 renewals/day

### Option C: Hybrid (Recommended for Production)

- **Default:** All customers use `imap.mailyte.com` / `smtp.mailyte.com` (shared cert)
- **Premium:** Enterprise customers get SNI certs for `mail.theirdomain.com`
- This lets you start with zero cert management complexity and add SNI per-domain as customers upgrade

---

## Scaling Limits

### What Scales Linearly (No Problem)

| Component | 1K Domains | 500K Domains | Limit |
|-----------|-----------|-------------|-------|
| MySQL domain lookups | <1ms | <1ms | Indexed, O(log n) |
| Postfix virtual domains | instant | instant | DB-driven, no config reload |
| Dovecot auth | cached | cached | 10MB auth cache |
| DKIM keys in DB | 2MB | 1GB | Fits in memory |
| API provisioning | instant | instant | Per-request DB ops |

### What Needs Horizontal Scaling (At Very High Volume)

| Component | When to Scale | How |
|-----------|--------------|-----|
| MySQL | >10K queries/sec | Read replicas, connection pooling |
| Postfix | >50K msgs/hour | Multiple MX servers behind DNS round-robin |
| Dovecot | >10K concurrent IMAP | Multiple IMAP servers with shared storage |
| Rspamd | >20K msgs/hour | Multiple workers, Redis clustering |
| SSL certs | >50K SNI domains | Cert sharding, distributed cert_manager |
| Storage | >10TB maildir | NFS/distributed storage, cloud sync |

### Hard Limits

| Limit | Value | Source |
|-------|-------|--------|
| Max domains per MySQL table | ~4 billion | INT column |
| Max mailboxes | ~4 billion | INT column |
| Max message size | 50MB | Postfix config (adjustable) |
| Max IMAP connections per user | 20 | Dovecot config (adjustable) |
| Let's Encrypt certs per domain/week | 50 | LE rate limit |
| DKIM key size | 2048-bit RSA | Industry standard |

---

## Architecture for 500K Domains in Production

```
                                Internet
                                   │
                              ┌────┴────┐
                              │  DNS    │
                              │ Routing │
                              └────┬────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
              │ MX Server │ │ MX Server │ │ MX Server │
              │ (Postfix) │ │ (Postfix) │ │ (Postfix) │
              │ + Rspamd  │ │ + Rspamd  │ │ + Rspamd  │
              └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
              │   IMAP    │ │   IMAP    │ │   IMAP    │
              │ (Dovecot) │ │ (Dovecot) │ │ (Dovecot) │
              └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                         ┌─────────┴─────────┐
                         │   Shared Storage   │
                         │  (NFS / S3 / EFS)  │
                         └─────────┬─────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
              │  MySQL    │ │  MySQL    │ │  Redis    │
              │  Primary  │ │  Replica  │ │  Cluster  │
              └───────────┘ └───────────┘ └───────────┘
```

**How DNS round-robin works for multiple MX servers:**
```dns
; Multiple MX records with same priority = round-robin
theircompany.com.  MX  10  mx1.mailyte.com.
theircompany.com.  MX  10  mx2.mailyte.com.
theircompany.com.  MX  10  mx3.mailyte.com.
```

Each MX server runs identical Postfix/Rspamd stacks, all reading from the same MySQL primary. Sending servers automatically distribute load and failover between them.

---

## Customer Onboarding Flow

```
1. Customer signs up on Mailyte
   └→ API creates Organization

2. Customer adds their domain: theircompany.com
   └→ API creates Domain record
   └→ API generates DKIM key pair
   └→ API returns DNS records to configure

3. Customer configures DNS:
   ├→ MX  → mx.mailyte.com
   ├→ TXT → SPF record
   ├→ TXT → DKIM public key
   └→ TXT → DMARC policy

4. Customer clicks "Verify DNS" in dashboard
   └→ API checks all 4 records via DNS lookup
   └→ Domain status → "verified" or "pending"

5. Customer creates mailboxes:
   └→ user@theircompany.com immediately works
   └→ IMAP login works instantly
   └→ Sending works (with DKIM signing)

Total time: 5 minutes (DNS propagation may take up to 48 hours)
```

---

## Summary

| Question | Answer |
|----------|--------|
| Can Mailyte handle 500K custom domains? | **Yes** — database-driven, no config file limits |
| Does each domain get its own DKIM key? | **Yes** — per-domain RSA 2048-bit keys |
| Does each domain need DNS changes? | **Yes** — MX, SPF, DKIM, DMARC (4 records) |
| Do customers need their own server? | **No** — all domains share the same Mailyte infrastructure |
| Can customers use their own hostname for IMAP/SMTP? | **Yes** — via SNI + per-domain certs (optional) |
| What's the main scaling bottleneck? | SSL certificate management for per-domain SNI |
| What's the recommended approach? | Shared hostname (imap.mailyte.com) + optional SNI for enterprise |
