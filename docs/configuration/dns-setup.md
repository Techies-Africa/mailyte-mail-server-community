# DNS Setup

MX, SPF, DKIM, DMARC, PTR, MTA-STS, autoconfig — what each record does, the exact values Mailyte expects, and how verification works.

---

DNS records tell the world how to reach your mail server and how to verify that mail from your domain is legitimate. Missing or broken DNS records are the number one cause of delivery problems.

Mailyte generates the records for you: `POST /api/v1/domains` (and `GET /api/v1/domains/{id}`) returns the exact MX/SPF/DKIM/DMARC records for a domain, and `GET /dns-records/{domain}` on the autoconfig service returns an extended set including MTA-STS, TLSRPT, SRV, and the autoconfig/autodiscover CNAMEs.

## The Records Mailyte Generates

With the server hostname `mail.yourdomain-provider.com` (from `MAIL_HOSTNAME`, falling back to `HOSTNAME`) and SPF host `spf.mail.yourdomain-provider.com` (from `MAIL_SPF_HOST`, default `spf.<server hostname>`):

| Record | Name | Value |
|--------|------|-------|
| MX | `customer.com` | `10 mail.yourdomain-provider.com.` |
| SPF | `customer.com` TXT | `v=spf1 include:spf.mail.yourdomain-provider.com ~all` |
| DKIM | `default._domainkey.customer.com` TXT | `v=DKIM1; k=rsa; p=<base64 public key>` |
| DMARC | `_dmarc.customer.com` TXT | `v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@customer.com` |
| Autoconfig | `autoconfig.customer.com` CNAME | `mail.yourdomain-provider.com.` |
| Autodiscover | `autodiscover.customer.com` CNAME | `mail.yourdomain-provider.com.` |

The SPF `include:` host publishes the server's IPs once, so customer records never need updating when IPs change. `MAIL_SPF_HOST` must stay in step with that published record.

## MX Record

```
customer.com.    IN    MX    10    mail.yourdomain-provider.com.
```

- **Priority 10** — lower numbers mean higher priority.
- MX must point to a hostname (never an IP), and that hostname must have an A record to this server.

> [!WARNING]
> A domain provisioned in Mailyte whose MX still points at the old mail system needs an entry in Postfix's `transport_cutover` map until cutover, or its mail either loop-bounces or lands in an unread local copy. See [Postfix Configuration](postfix-configuration.md#transport-cutover-map).

## SPF Record

```
customer.com.    IN    TXT    "v=spf1 include:spf.mail.yourdomain-provider.com ~all"
```

- **One SPF record only.** RFC 7208 allows exactly one `v=spf1` TXT per name; receivers treat more as a permanent error. Mailyte's verifier fails a domain with duplicates.
- If the domain also sends through another service, merge into one record: `"v=spf1 include:spf.mail.yourdomain-provider.com include:_spf.google.com ~all"`.
- Use `~all` (softfail) until you're certain every sending source is listed, then optionally tighten to `-all`.

## DKIM Record

Every domain gets an RSA-2048 key pair, generated when the domain is added (or with `scripts/generate_dkim.py`). The selector is `default` unless you change `dkim_selector` on the domain:

```
default._domainkey.customer.com.    IN    TXT    "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQ..."
```

The domain-detail API response includes `dkim_record`, `dkim_selector`, and `dkim_dns_name` ready to paste. To verify:

```bash
dig TXT default._domainkey.customer.com +short
```

> [!TIP]
> A 2048-bit key is ~392 base64 characters (~412 with the prefix). Many DNS providers split TXT values over 255 bytes into multiple quoted strings — that's fine, resolvers rejoin them. If you see a much shorter value (<300 chars), it's probably a legacy 1024-bit key worth rotating (`python3 scripts/generate_dkim.py --rotate customer.com`).

## DMARC Record

```
_dmarc.customer.com.    IN    TXT    "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@customer.com"
```

- **`p=quarantine`** — failing mail goes to spam. `none` = monitor only; `reject` = block outright.
- **`rua=`** — aggregate reports destination.

### Recommended Rollout

1. Start with `p=none` and read the reports for a couple of weeks.
2. Move to `p=quarantine` (optionally with `pct=25` → `pct=100`).
3. Finish at `p=reject` once reports are clean.

## PTR Record (Reverse DNS)

Your server's IP must resolve back to `HOSTNAME`. Set this with your hosting provider — it is not in your domain's zone.

```bash
dig -x YOUR_SERVER_IP +short
# must return the value of HOSTNAME, e.g. mail.yourdomain-provider.com.
```

> [!WARNING]
> A missing or mismatched PTR is one of the most common reasons Gmail/Outlook reject or spam-folder mail.

## MTA-STS and TLSRPT

The autoconfig service serves the MTA-STS policy automatically at `https://mta-sts.<domain>/.well-known/mta-sts.txt` for every hosted domain — you only add DNS:

```
mta-sts.customer.com.      IN    CNAME    mail.yourdomain-provider.com.
_mta-sts.customer.com.     IN    TXT      "v=STSv1; id=20260827000000"
_smtp._tls.customer.com.   IN    TXT      "v=TLSRPTv1; rua=mailto:tls-reports@customer.com"
```

The served policy mode comes from `MTA_STS_MODE` — `testing` in development, `enforce` in the production compose file. Change the `id` whenever the policy changes.

## Client Auto-Setup (Autoconfig / Autodiscover / SRV)

The `autoconfig` worker answers Thunderbird's `config-v1.1.xml`, Microsoft's `autodiscover.xml` (POX and V2 JSON), for **every hosted domain** — Traefik routes `autoconfig.*`, `autodiscover.*`, and `mta-sts.*` hostnames to it, and cert_manager includes `autoconfig.`/`autodiscover.` names in each domain's certificate. It hands clients: IMAP 993 (SSL), POP3 995 (SSL), SMTP 587 (STARTTLS), username = full email address.

DNS needed per domain — two CNAMEs (in the generated set above) and optionally SRV records for clients that use them:

```
_autodiscover._tcp.customer.com.  IN  SRV  0 1 443 mail.yourdomain-provider.com.
_imaps._tcp.customer.com.         IN  SRV  0 1 993 mail.yourdomain-provider.com.
_submission._tcp.customer.com.    IN  SRV  0 1 587 mail.yourdomain-provider.com.
_pop3s._tcp.customer.com.         IN  SRV  0 1 995 mail.yourdomain-provider.com.
```

## Verifying a Domain

Mailyte's DNS verification is one engine (in the API's domains routes, consolidated 2026-08-21) using in-process DNS resolution:

```bash
curl -X POST https://api.yourdomain-provider.com/api/v1/domains/{domain_id}/verify-dns \
  -H "X-API-Key: $API_KEY"
```

It checks exactly four records:

| Check | Pass condition |
|-------|----------------|
| MX | The server hostname appears among the domain's MX targets |
| SPF | Real mechanism evaluation — the record must actually authorize this server's IPs (`ip4:`/`ip6:`/`a`/`mx`/`include:`/`redirect=` are followed, within RFC 7208's 10-lookup budget); more than one SPF record fails |
| DKIM | `{selector}._domainkey.{domain}` TXT contains `v=DKIM1` |
| DMARC | `_dmarc.{domain}` TXT contains `v=DMARC1` (policy reported back) |

Manual spot checks:

```bash
dig MX customer.com +short
dig TXT customer.com +short
dig TXT default._domainkey.customer.com +short
dig TXT _dmarc.customer.com +short
dig -x YOUR_SERVER_IP +short
```

Online tools like [MXToolbox](https://mxtoolbox.com/) and [Mail-Tester](https://www.mail-tester.com/) give a good second opinion.

## Bulk Tooling

For estates managed in Cloudflare, `scripts/dns/cloudflare_apply.sh` applies MX/SPF/DKIM/DMARC across all configured zones — dry-run by default, `--apply` to write, `--prune-mx` to remove stale MX records. It merges SPF instead of replacing it and never rewrites an existing DMARC record. `scripts/dns/export_dkim_records.sh` exports every domain's DKIM key in TSV or zone-file form.

> [!NOTE]
> DNS changes can take up to 48 hours to propagate, though most are visible within minutes at TTL 300.
