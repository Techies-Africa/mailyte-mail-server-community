# Mailyte DNS Setup

Every record a hosted domain needs, what generates it, and how verification works. Verified against the code on 2026-08-30 (`worker/api/routes/domains.py`, `worker/autoconfig/app.py`).

## Where to get the records

Don't hand-write records — the platform generates them with the domain's real key material:

- `GET /api/v1/domains/{id}` (platform API, `X-API-Key` auth) — the core MX/SPF/DKIM/DMARC set for a domain.
- `GET /dns-records/{domain}` (autoconfig service, port 8100) — the full set including MTA-STS, TLS-RPT, SRV, and client auto-setup records, as structured JSON with copy-paste-ready values.

The hostnames placed into records come from environment configuration:

| Variable | Used for | Notes |
|----------|----------|-------|
| `MAIL_HOSTNAME` | MX target, client auto-setup, MTA-STS `mx:` | The customer-facing FQDN the TLS certificate is issued for. Falls back to `HOSTNAME`; a value with no dot is treated as a container ID and replaced with the built-in default — set `MAIL_HOSTNAME` explicitly |
| `MAIL_SPF_HOST` | The `include:` host in generated SPF records | Deliberately independent of the MX hostname so renaming the mail host can't silently break every customer's SPF. Defaults to `spf.<mail hostname>` — that name must actually exist in DNS |

## Core records (required for mail flow)

For a hosted domain `customer.com` with mail host `mail.example-platform.com`:

```
; Mail routing
customer.com.                    MX   10 mail.example-platform.com.

; SPF — authorizes the platform to send for the domain
customer.com.                    TXT  "v=spf1 include:spf.example-platform.com ~all"
;   (the autoconfig generator emits the equivalent "v=spf1 mx a:mail.example-platform.com ~all")

; DKIM — public key generated when the domain is created
default._domainkey.customer.com. TXT  "v=DKIM1; k=rsa; p=<public key from the API>"

; DMARC
_dmarc.customer.com.             TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@customer.com"
```

The DKIM selector is `default` unless the domain was created with another or has been rotated — always take the selector and key from the API, not from this example.

## Client auto-setup records (recommended)

These make Thunderbird/Outlook/mobile clients configure themselves via the [autoconfig service](features/autoconfig.md) (publicly routed since 2026-08-27):

```
autoconfig.customer.com.              CNAME  mail.example-platform.com.
autodiscover.customer.com.            CNAME  mail.example-platform.com.
_autodiscover._tcp.customer.com.      SRV    0 1 443 mail.example-platform.com.
_imaps._tcp.customer.com.             SRV    0 1 993 mail.example-platform.com.
_submission._tcp.customer.com.        SRV    0 1 587 mail.example-platform.com.
_pop3s._tcp.customer.com.             SRV    0 1 995 mail.example-platform.com.
```

Traefik matches `autoconfig.*` / `autodiscover.*` / `mta-sts.*` for **any** domain, so no per-domain server configuration is needed — but each discovery hostname a client will contact over HTTPS needs certificate coverage.

## Transport security records (recommended)

```
_mta-sts.customer.com.   TXT    "v=STSv1; id=<yyyymmddhhmmss>"
mta-sts.customer.com.    CNAME  mail.example-platform.com.
_smtp._tls.customer.com. TXT    "v=TLSRPTv1; rua=mailto:tls-reports@customer.com"
```

The MTA-STS policy itself is served by the autoconfig service at `https://mta-sts.customer.com/.well-known/mta-sts.txt`; its mode comes from `MTA_STS_MODE` (default `testing` — switch to `enforce` deliberately, and bump the TXT record's `id` whenever the policy changes).

## Platform-side records (once, for the mail host itself)

- **A/AAAA** for `mail.example-platform.com` (and `api.`, `docs.`, etc. per the Traefik routers).
- **PTR (reverse DNS)** for the sending IP must resolve to the server's HELO name — note that the PTR/HELO identity (`HOSTNAME`) and the customer-facing name (`MAIL_HOSTNAME`) can legitimately differ; both must resolve to the server.
- **SPF host**: whatever `MAIL_SPF_HOST` names must exist as a TXT record listing the sending IPs, e.g. `spf.example-platform.com. TXT "v=spf1 ip4:<server-ip> -all"`.

## Verification

```
GET /api/v1/domains/{domain_id}/verify-dns
```

Performs live lookups and returns per-record status:

- **MX** — passes when the resolved MX contains the configured mail hostname.
- **SPF** — passes when the domain's `v=spf1` record either contains the expected `include:` **or** genuinely authorizes the server's IPs via recursive evaluation (`include:`/`redirect=`/`ip4:`/`mx`, standard 10-lookup budget). Multiple SPF records are flagged (that's a permerror per RFC 7208).
- **DKIM** — queries `{active selector}._domainkey.{domain}` for a `v=DKIM1` record.
- **DMARC** — queries `_dmarc.{domain}` for `v=DMARC1` and reports the policy.

A passing run dispatches a `domain.verified` webhook event. This endpoint is the **single** verification engine — the historical situation of two disagreeing verifiers was eliminated on 2026-08-21.

## Gotchas

- **Propagation** — DNS changes take minutes to hours depending on TTLs. Verification reads live DNS; a freshly published record can legitimately still fail.
- **Quoting long DKIM keys** — a 2048-bit key exceeds the 255-byte string limit; most DNS providers split it into multiple quoted strings automatically, but check if verification keeps failing with the key visibly published.
- **One SPF record only** — merge mechanisms into a single `v=spf1` string; two records is a permanent error at receivers.
- **Old DKIM selectors** — after rotation, keep the previous selector's TXT published for a few days until mail signed with it has cleared every retry queue, then remove it.
- **Migrating a domain in?** If the domain exists in the platform but its MX still points elsewhere, the transport cutover map (`config/mailer/postfix/transport_cutover`) must route it externally until DNS cutover — otherwise mail from platform users to that domain is delivered to the local (unread) copy. Remove the entry at cutover.
