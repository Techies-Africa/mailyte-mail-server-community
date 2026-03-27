---
title: Setting Up DKIM
description: Generate DKIM keys, publish DNS records, configure Rspamd to sign outgoing mail, and verify it all works.
---

# Setting Up DKIM

DKIM (DomainKeys Identified Mail) signs your outgoing emails with a cryptographic key so receiving servers can verify the message really came from you. Without it, your email is much more likely to land in spam.

## How DKIM Works (30-Second Version)

1. Mailyte generates a key pair (private + public) for your domain
2. The private key stays on the server — Rspamd uses it to sign every outgoing email
3. The public key goes into a DNS TXT record
4. When Gmail/Outlook receives your email, they check the signature against the public key in DNS

If the signature matches, the email passes DKIM. If not, it fails — and that hurts your reputation.

## Step 1: Generate Keys via the API

The easiest way:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/dkim \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domains": "example.com",
    "dkim_selector": "default",
    "key_size": "2048"
  }'
```

This generates a 2048-bit RSA key pair, stores the private key in the database and on disk at `/var/lib/rspamd/dkim/`, and returns the public key.

### Or Generate Keys Manually

If you prefer the command line:

```bash
# Generate with the built-in script
docker exec -it postfix python3 /scripts/generate_dkim.py \
  --domain example.com \
  --selector default \
  --dns
```

Or with OpenSSL directly:

```bash
# Generate private key
openssl genrsa -out example.com.key 2048

# Extract public key
openssl rsa -in example.com.key -pubout -out example.com.pub

# Get the public key in DNS format (base64, single line)
grep -v "PUBLIC KEY" example.com.pub | tr -d '\n'
```

!!! info "Key size"
    Use **2048 bits** minimum. Some providers accept 1024-bit keys, but they're considered weak. 4096-bit keys are more secure but may not fit in a single DNS TXT record without splitting.

## Step 2: Get the Public Key

Retrieve it via the API:

```bash
curl http://mail.yourdomain.com:8083/api/v1/get/dkim/example.com \
  -H "X-API-Key: YOUR_API_KEY"
```

The response includes the public key in DNS-ready format.

## Step 3: Add the DNS Record

Create a TXT record at your DNS provider:

| Field | Value |
|-------|-------|
| **Name** | `default._domainkey.example.com` |
| **Type** | TXT |
| **Value** | `v=DKIM1; k=rsa; p=MIIBIjANBgkqh...` |
| **TTL** | 3600 |

The `default` part is the selector — it matches what you passed to `dkim_selector` during generation.

!!! warning "Long TXT records"
    DKIM public keys are long. DNS limits TXT records to 255 characters per string. Most DNS providers handle splitting automatically, but if yours doesn't, break the value into 255-character chunks:
    ```
    "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA..."
    "...rest-of-the-key..."
    ```

## Step 4: Configure Rspamd Signing

Mailyte's Rspamd container handles DKIM signing automatically when keys are present. The configuration lives at:

```
config/mailer/rspamd/local.d/dkim_signing.conf
```

The default config:

```lua
# Enable DKIM signing
enabled = true;

# Path to DKIM keys
path = "/var/lib/rspamd/dkim/$domain.$selector.key";

# Default selector
selector = "default";

# Sign for all domains that have keys
allow_username_mismatch = true;

# Use the From header domain for signing
use_domain = "header";
```

If you generated keys via the API, Rspamd picks them up automatically. No restart needed — Rspamd watches the key directory.

### Custom Selector per Domain

If different domains need different selectors:

```lua
# In dkim_signing.conf
domain {
    example.com {
        selector = "default";
        path = "/var/lib/rspamd/dkim/example.com.default.key";
    }
    other.com {
        selector = "mail2025";
        path = "/var/lib/rspamd/dkim/other.com.mail2025.key";
    }
}
```

## Step 5: Verify DKIM is Working

### Check DNS Propagation

```bash
dig TXT default._domainkey.example.com +short
```

You should see your public key in the response. If it's empty, DNS hasn't propagated yet — wait a few minutes.

### Send a Test Email

Send an email to an external address (Gmail works well) and check the headers:

```
Authentication-Results: mx.google.com;
    dkim=pass header.i=@example.com header.s=default
```

The `dkim=pass` is what you're looking for.

### Use Online Tools

- **[mail-tester.com](https://www.mail-tester.com/)** — send a test email and get a full report
- **[MXToolbox DKIM Lookup](https://mxtoolbox.com/dkim.aspx)** — check the DNS record directly
- **[Google Admin Toolbox](https://toolbox.googleapps.com/apps/checkmx/)** — comprehensive MX check

### Check via Rspamd

```bash
# Check Rspamd DKIM signing status
docker exec -it rspamd rspamc stat | grep dkim
```

## Troubleshooting

### "DKIM signature not found"

The email wasn't signed at all. Check:

1. Does the key file exist in the container?
   ```bash
   docker exec -it rspamd ls -la /var/lib/rspamd/dkim/
   ```

2. Is Rspamd running and processing mail?
   ```bash
   docker exec -it rspamd rspamc stat
   ```

3. Check Rspamd logs for signing errors:
   ```bash
   docker logs rspamd 2>&1 | grep -i dkim
   ```

### "DKIM signature verification failed"

The email was signed, but the receiving server couldn't verify it. Common causes:

- **DNS record mismatch** — the public key in DNS doesn't match the private key on the server
- **DNS not propagated** — you just added the record, give it time
- **Message was modified in transit** — a mailing list or forwarding service altered the message body

### "Key too long for DNS"

If your DNS provider can't handle the key length:

1. Try a different DNS provider that supports long TXT records
2. Use a 2048-bit key instead of 4096-bit
3. Manually split the record into 255-character strings

## Rotating DKIM Keys

Good practice: rotate your DKIM keys once a year.

1. Generate a new key with a new selector (e.g., `mail2026`)
2. Add the new public key to DNS
3. Update Rspamd to use the new selector
4. Wait 48 hours for DNS propagation
5. Remove the old DNS record

```bash
# Generate new key with new selector
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/dkim \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domains": "example.com",
    "dkim_selector": "mail2026",
    "key_size": "2048"
  }'
```

Keep both selectors in DNS during the transition. Old emails in recipients' inboxes will still verify against the old key.
