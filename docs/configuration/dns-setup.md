# DNS Setup

MX, SPF, DKIM, DMARC, MTA-STS, and SRV records — what each one does and the exact records to create.

---

DNS records tell the world how to reach your mail server and how to verify that mail from your domain is legitimate. Getting these right is critical. Missing or broken DNS records are the number one cause of delivery problems.

## The Records You Need

Here's a quick summary. Detailed explanations follow.

| Record | Type | Purpose |
|--------|------|---------|
| MX | MX | Points to your mail server |
| SPF | TXT | Lists servers allowed to send for your domain |
| DKIM | TXT | Public key for email signature verification |
| DMARC | TXT | Policy for handling failed SPF/DKIM checks |
| PTR | PTR | Reverse DNS for your server IP |
| MTA-STS | TXT + HTTPS | Enforces TLS for incoming mail |
| SRV | SRV | Helps email clients auto-discover server settings |

## MX Record

The MX record tells other mail servers where to deliver email for your domain.

```
yourdomain.com.    IN    MX    10    mail.yourdomain.com.
```

- **Priority 10** — Lower numbers mean higher priority. If you have a backup server, give it a higher number (e.g., 20).
- The target (`mail.yourdomain.com`) must have an A record pointing to your server's IP.

You also need an A record for the mail server itself:

```
mail.yourdomain.com.    IN    A    203.0.113.1
```

Replace `203.0.113.1` with your actual server IP.

> [!NOTE]
> MX records must point to a hostname, not an IP address. The hostname then resolves to an IP through the A record. This is a hard requirement in the email standards.

## SPF Record

SPF (Sender Policy Framework) tells receiving servers which IP addresses are allowed to send email on behalf of your domain.

```
yourdomain.com.    IN    TXT    "v=spf1 mx a:mail.yourdomain.com -all"
```

Breaking this down:
- **`v=spf1`** — This is an SPF record (version 1).
- **`mx`** — Any server listed in MX records is allowed to send.
- **`a:mail.yourdomain.com`** — The IP that `mail.yourdomain.com` resolves to is also allowed.
- **`-all`** — Reject mail from any other source.

If you also send through a third-party service (like a transactional email provider), include their SPF:

```
yourdomain.com.    IN    TXT    "v=spf1 mx a:mail.yourdomain.com include:_spf.google.com -all"
```

> [!WARNING]
> Use `-all` (hard fail) once you're confident your SPF record is complete. During initial testing, use `~all` (soft fail) so legitimate mail doesn't get rejected if you missed a sending source.

## DKIM Record

DKIM (DomainKeys Identified Mail) lets receiving servers verify that an email was actually sent by your server and hasn't been tampered with.

When you generate a DKIM key in Rspamd (see [Rspamd Configuration](rspamd-configuration.md)), it outputs a DNS record. It looks like this:

```
mail._domainkey.yourdomain.com.    IN    TXT    "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA..."
```

- **`mail`** is the selector — it matches the selector in your Rspamd DKIM config.
- **`_domainkey`** is the standard DKIM namespace.
- **`p=`** is your public key (the long base64 string).

To verify the record is set up correctly:

```bash
dig TXT mail._domainkey.yourdomain.com +short
```

> [!TIP]
> Some DNS providers have a character limit on TXT records. If your DKIM key is too long for a single string, split it into multiple quoted strings:
> ```
> "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQE..."
> "...FAAOCAQ8AMIIBCgKCAQEA..."
> ```
> Most DNS providers handle this automatically.

## DMARC Record

DMARC (Domain-based Message Authentication, Reporting, and Conformance) ties SPF and DKIM together. It tells receiving servers what to do when a message fails authentication.

```
_dmarc.yourdomain.com.    IN    TXT    "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@yourdomain.com; ruf=mailto:dmarc-forensics@yourdomain.com; sp=quarantine; adkim=s; aspf=s"
```

The key parts:
- **`p=quarantine`** — Messages that fail DMARC should be sent to spam. Other options: `none` (monitor only) or `reject` (hard reject).
- **`rua=`** — Aggregate reports get sent here (daily summaries of authentication results).
- **`ruf=`** — Forensic reports get sent here (details of individual failures).
- **`adkim=s`** — Strict DKIM alignment (the signing domain must exactly match the From domain).
- **`aspf=s`** — Strict SPF alignment.

### Recommended Rollout

Start cautious and tighten over time:

1. **Week 1-2:** `p=none` — Just collect reports, don't take action.
2. **Week 3-4:** `p=quarantine; pct=25` — Quarantine 25% of failing mail.
3. **Week 5-6:** `p=quarantine; pct=100` — Quarantine all failing mail.
4. **Week 7+:** `p=reject` — Reject all failing mail.

## PTR Record (Reverse DNS)

A PTR record maps your server's IP address back to its hostname. Many mail servers check this and reject mail if it's missing or wrong.

You don't set this in your domain's DNS. You set it through your hosting provider or ISP — they control the reverse DNS zone for your IP address.

The PTR record should resolve to your `HOSTNAME`:

```
1.113.0.203.in-addr.arpa.    IN    PTR    mail.yourdomain.com.
```

To verify:

```bash
dig -x 203.0.113.1 +short
# Should return: mail.yourdomain.com.
```

> [!WARNING]
> A missing or mismatched PTR record is one of the most common reasons email gets rejected. Many large providers (Gmail, Outlook) will reject or spam-folder your mail without it.

## MTA-STS

MTA-STS (Mail Transfer Agent Strict Transport Security) tells sending servers that they must use TLS when delivering mail to your domain. It prevents downgrade attacks.

### Step 1: Create the Policy File

Host this file at `https://mta-sts.yourdomain.com/.well-known/mta-sts.txt`:

```
version: STSv1
mode: enforce
mx: mail.yourdomain.com
max_age: 604800
```

- **`mode: enforce`** — Require TLS. Use `testing` first to avoid breaking mail delivery while you validate.
- **`mx:`** — List each MX hostname, one per line.
- **`max_age: 604800`** — Policy is valid for 7 days (in seconds).

### Step 2: Add the DNS Record

```
_mta-sts.yourdomain.com.    IN    TXT    "v=STSv1; id=20260325001"
```

The `id` is a version identifier. Change it every time you update the policy so caching servers pick up the new version.

### Step 3: Add the Reporting Record (Optional)

```
_smtp._tls.yourdomain.com.    IN    TXT    "v=TLSRPTv1; rua=mailto:tls-reports@yourdomain.com"
```

This tells sending servers where to report TLS delivery failures.

## SRV Records

SRV records help email clients auto-discover your server settings. When a user types their email address into Thunderbird, Outlook, or a mobile client, the app looks up these records.

```
_submission._tcp.yourdomain.com.    IN    SRV    0 1 587 mail.yourdomain.com.
_imaps._tcp.yourdomain.com.         IN    SRV    0 1 993 mail.yourdomain.com.
_pop3s._tcp.yourdomain.com.         IN    SRV    0 1 995 mail.yourdomain.com.
```

The format is: `priority weight port target`.

- **`_submission`** — SMTP submission (sending mail).
- **`_imaps`** — IMAP over TLS (reading mail).
- **`_pop3s`** — POP3 over TLS (reading mail).

> [!TIP]
> Not all email clients support SRV records, but the ones that do give users a much smoother setup experience. It takes two minutes to add them and saves your users from manually entering server settings.

## Autoconfig / Autodiscover

In addition to SRV records, you can set up autoconfig for Mozilla clients and autodiscover for Outlook.

### Mozilla Autoconfig

Create a CNAME record:

```
autoconfig.yourdomain.com.    IN    CNAME    mail.yourdomain.com.
```

Then serve an XML config at `https://autoconfig.yourdomain.com/mail/config-v1.1.xml`. The Mailyte API handles this automatically.

### Outlook Autodiscover

```
_autodiscover._tcp.yourdomain.com.    IN    SRV    0 1 443 mail.yourdomain.com.
```

## Verifying Your DNS Setup

After adding all records, verify them:

```bash
# Check MX
dig MX yourdomain.com +short

# Check SPF
dig TXT yourdomain.com +short

# Check DKIM
dig TXT mail._domainkey.yourdomain.com +short

# Check DMARC
dig TXT _dmarc.yourdomain.com +short

# Check PTR
dig -x YOUR_SERVER_IP +short

# Check MTA-STS
dig TXT _mta-sts.yourdomain.com +short

# Check SRV
dig SRV _submission._tcp.yourdomain.com +short
dig SRV _imaps._tcp.yourdomain.com +short
```

You can also use online tools like [MXToolbox](https://mxtoolbox.com/) or [Mail-Tester](https://www.mail-tester.com/) to get a comprehensive report.

> [!NOTE]
> DNS changes can take up to 48 hours to propagate, though most updates are visible within minutes. If you just made changes and they're not showing up, wait a bit and try again.
