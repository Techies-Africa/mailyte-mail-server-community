# Rspamd Configuration

Spam filtering, DKIM signing, ClamAV virus scanning, greylisting, and how to train the Bayesian filter.

---

Rspamd is the brain behind Mailyte's spam detection. It uses machine learning, blacklists, content analysis, and a bunch of other signals to score every message. It also handles DKIM signing for outgoing mail and integrates with ClamAV for virus scanning.

All Rspamd config files live under `/etc/rspamd/`. The main config structure:

```
/etc/rspamd/
  rspamd.conf            # Main config (usually don't touch this)
  local.d/               # Your overrides go here
  override.d/            # Hard overrides (rarely needed)
```

> [!TIP]
> Always put your changes in `/etc/rspamd/local.d/`. Files in this directory merge with the defaults. Files in `override.d/` replace entire config blocks, which can break things when Rspamd updates.

## Spam Scoring

Rspamd assigns a score to every incoming message. The score determines what happens to it.

`/etc/rspamd/local.d/actions.conf`:

```ini
reject = 15;
add_header = 6;
greylist = 4;
```

- **Score below 4** — Message is delivered normally.
- **Score 4 to 6** — Message gets greylisted (delayed, then retried).
- **Score 6 to 15** — Message is delivered but gets a spam header (`X-Spam: Yes`). The user's client or a Sieve rule can move it to Junk.
- **Score 15 or above** — Message is rejected outright at the SMTP level.

> [!NOTE]
> These thresholds are a good starting point. If you're getting too many false positives, raise the `add_header` threshold. If spam is getting through, lower it. Check your logs for a week before adjusting.

## DKIM Signing

Rspamd signs all outgoing mail with DKIM keys. This helps receiving servers verify that mail really came from your domain.

`/etc/rspamd/local.d/dkim_signing.conf`:

```ini
allow_envfrom_empty = true;
allow_hdrfrom_mismatch = false;
allow_hdrfrom_multiple = false;
allow_username_mismatch = false;
use_domain = "header";
use_esld = true;
sign_authenticated = true;
sign_local = true;

domain {
  yourdomain.com {
    path = "/var/lib/rspamd/dkim/yourdomain.com.key";
    selector = "mail";
  }
}
```

Each domain needs its own DKIM key. To generate one:

```bash
# Generate a 2048-bit DKIM key
docker exec mailyte-rspamd rspamadm dkim_keygen \
  -s mail -d yourdomain.com -b 2048 \
  -k /var/lib/rspamd/dkim/yourdomain.com.key

# This prints the DNS TXT record you need to add
```

The command outputs a DNS record. Add it to your domain's DNS — see [DNS Setup](dns-setup.md) for details.

> [!WARNING]
> Keep DKIM private keys secure. They should be readable only by the Rspamd process. If a key is compromised, anyone can send email that appears to come from your domain.

## ClamAV Integration

Rspamd sends attachments to ClamAV for virus scanning.

`/etc/rspamd/local.d/antivirus.conf`:

```ini
clamav {
  action = "reject";
  type = "clamav";
  servers = "clamav:3310";
  scan_mime_parts = true;
  scan_text_mime = false;
  scan_image_mime = false;
  symbol = "CLAM_VIRUS";
  patterns {
    JUST_EICAR = "/^Eicar-Test-Signature$/i";
  }
}
```

Key settings:

- **`action = "reject"`** — Messages with viruses are rejected immediately. No quarantine, no "maybe." Viruses get bounced.
- **`scan_mime_parts = true`** — Each MIME attachment is scanned individually.
- **`scan_text_mime = false`** — Plain text parts aren't scanned (they can't contain executable viruses).
- **`scan_image_mime = false`** — Images aren't scanned either, which saves CPU.

> [!NOTE]
> ClamAV needs to download its virus signature database on first start. This can take a few minutes. The `freshclam` process handles updates automatically after that.

## Greylisting

Greylisting temporarily rejects mail from unknown senders. Legitimate servers retry after a delay; most spam bots don't.

`/etc/rspamd/local.d/greylist.conf`:

```ini
servers = "redis:6379";
expire = 86400;
timeout = 300;
key_prefix = "rg";
max_data_len = 10240;
message = "Try again later";
whitelisted_ip = "/etc/rspamd/local.d/greylist-whitelist-ip.inc";
```

- **`timeout = 300`** — Senders must wait 5 minutes before retrying. Most legitimate mail servers retry within 5-15 minutes.
- **`expire = 86400`** — Once a sender passes greylisting, they're remembered for 24 hours.

To whitelist known good senders (like Google, Microsoft, etc.), add their IP ranges to the whitelist file:

```
# /etc/rspamd/local.d/greylist-whitelist-ip.inc
209.85.128.0/17   # Google
40.92.0.0/15      # Microsoft
```

## Bayesian Filtering

Rspamd's Bayesian classifier learns from the mail it sees. It stores statistics in Redis.

`/etc/rspamd/local.d/classifier-bayes.conf`:

```ini
servers = "redis:6379";
backend = "redis";
autolearn = true;

autolearn {
  spam_threshold = 12.0;
  ham_threshold = -0.5;
}

min_learns = 200;
```

- **`autolearn = true`** — Messages with very high or very low scores automatically train the filter. High-scoring messages teach it what spam looks like; low-scoring messages teach it what good mail looks like.
- **`min_learns = 200`** — The Bayesian filter doesn't activate until it has seen at least 200 messages total (ham + spam). Until then, it doesn't have enough data to be useful.

### Manual Training

You can manually train the filter to improve accuracy:

```bash
# Train a message as spam
docker exec mailyte-rspamd rspamc learn_spam < /path/to/spam-message.eml

# Train a message as ham (not spam)
docker exec mailyte-rspamd rspamc learn_ham < /path/to/good-message.eml

# Check training statistics
docker exec mailyte-rspamd rspamc stat
```

> [!TIP]
> The more you train, the better Bayesian filtering gets. If users report spam that got through, feed those messages to `learn_spam`. If good mail ended up in Junk, feed it to `learn_ham`. Over time, the filter becomes very accurate for your specific mail patterns.

## Other Useful Modules

### Rate Limiting

`/etc/rspamd/local.d/ratelimit.conf`:

```ini
rates {
  to = {
    symbol = "RATELIMIT_CHECK";
    bucket {
      burst = 100;
      rate = "1 / 1m";
    }
  }
  bounce_to = {
    symbol = "RATELIMIT_CHECK";
    bucket {
      burst = 10;
      rate = "1 / 5m";
    }
  }
}
```

### Phishing Detection

`/etc/rspamd/local.d/phishing.conf`:

```ini
openphish_enabled = true;
phishtank_enabled = true;
```

These modules check URLs in messages against known phishing databases.

### Multimap Rules

You can create custom rules that match specific patterns:

`/etc/rspamd/local.d/multimap.conf`:

```ini
BLOCK_SENDER_DOMAIN {
  type = "from";
  filter = "email:domain";
  map = "/etc/rspamd/local.d/blocked-domains.map";
  score = 15.0;
  description = "Sender domain is blocked";
}
```

## Rspamd Web UI

Rspamd includes a web interface for monitoring and managing the filter. It runs on port 11334.

`/etc/rspamd/local.d/worker-controller.inc`:

```ini
password = "$2$hash-of-your-password";
enable_password = "$2$hash-of-your-admin-password";
```

Generate password hashes with:

```bash
docker exec mailyte-rspamd rspamadm pw
```

> [!WARNING]
> Don't expose port 11334 to the public internet. Access the web UI through a reverse proxy with authentication, or over an SSH tunnel.

## Checking a Message

To manually check how Rspamd would score a message:

```bash
docker exec mailyte-rspamd rspamc < /path/to/message.eml
```

This prints the score, matched rules, and what action would be taken. Useful for debugging why a specific message was flagged or missed.
