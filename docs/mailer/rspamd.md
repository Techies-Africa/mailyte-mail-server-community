# Rspamd -- Anti-Spam, DKIM, and Content Filtering

Rspamd is the brain that decides whether an email is legitimate or garbage. It runs as a milter (mail filter) that Postfix consults for every email. Beyond spam detection, it also handles DKIM signing, SPF/DMARC verification, virus scanning, and phishing detection.

Think of Rspamd as a panel of judges. Each judge (module) scores the email on different criteria. The scores add up, and if the total crosses a threshold, the email gets flagged or rejected.

## What It Does

- **Spam filtering** with Bayesian classification, neural networks, fuzzy hashing
- **DKIM signing** for outbound email and **DKIM verification** for inbound
- **SPF and DMARC** checks for sender authentication
- **Smart folder classification** -- a custom Lua plugin (`local.d/lua/email_classifier.lua`) adds an `X-Email-Category` header (primary / notifications / social / promotions / updates) that Dovecot's global Sieve routes on
- **Transport rule enforcement** -- a custom Lua plugin (`local.d/lua/transport_rules.lua`) applies tenant transport rules from Redis, gated behind `TRANSPORT_RULES_ENABLED` (default **false**: rules written before enforcement existed were drafts, and turning this on puts all of them into force at once)
- **Greylisting** to defer suspicious first-time senders
- **Phishing detection** using URL analysis and domain reputation
- **ClamAV antivirus** hooks exist but are **disabled** -- no ClamAV container is deployed (see `local.d/antivirus.conf` for how to enable one)
- **Web UI** on port 11334 for monitoring and training

## Architecture

```mermaid
flowchart TB
    Postfix -->|"milter protocol\n:11332"| Proxy["rspamd-proxy\nworker"]

    subgraph Rspamd["Rspamd Container"]
        Proxy --> Normal["Normal Worker\n(main scanning)"]

        Normal --> Bayes["Bayesian\nClassifier"]
        Normal --> Neural["Neural\nNetwork"]
        Normal --> DKIM["DKIM\nSigning/Verify"]
        Normal --> SPF["SPF Check"]
        Normal --> DMARC["DMARC Check"]
        Normal --> AV["ClamAV\nAntivirus"]
        Normal --> Grey["Greylisting"]
        Normal --> Phish["Phishing\nDetection"]
        Normal --> Fuzzy["Fuzzy\nHashing"]
        Normal --> URL["URL\nReputation"]

        WebUI["Web UI\n:11334"]
    end

    Bayes --> Redis[(Redis)]
    Neural --> Redis
    Grey --> Redis
    Fuzzy --> Redis
```

## Configuration Files

All customizations live in `mailer/rspamd/config/local.d/`. Rspamd has a layered config system -- files in `local.d/` override the defaults without replacing them.

Here is what each config file does:

| File | Purpose |
|------|---------|
| `classifier-bayes.conf` | Bayesian spam classifier settings |
| `neural.conf` | Neural network spam detection |
| `dkim_signing.conf` | DKIM signing keys and selectors |
| `spf.conf` | SPF validation settings |
| `dmarc.conf` | DMARC policy enforcement |
| `antivirus.conf` | ClamAV integration |
| `greylisting.conf` | Greylisting policy |
| `phishing.conf` | Phishing URL detection |
| `fuzzy_check.conf` | Fuzzy hash matching for known spam |
| `url_reputation.conf` | URL reputation checking |
| `redis.conf` | Redis connection settings |
| `actions.conf` | Score thresholds and actions |
| `milter_headers.conf` | Which headers to add/modify |
| `arc.conf` | ARC (Authenticated Received Chain) settings |
| `multimap.conf` | Custom multi-map rules |
| `settings.conf` | Per-user/per-domain settings |
| `whitelist_domains.map` | Domains that skip spam checks |
| `worker-proxy.inc` | Proxy worker configuration |

### Spam Scoring and Actions

Rspamd assigns a numeric score to each email. The `actions.conf` file defines what happens at each threshold:

| Score | Action | What Happens |
|-------|--------|-------------|
| < 4 | No action | Email delivered normally |
| >= 4 | Greylist | Temporary rejection for unknown senders |
| >= 6 | Add header | `X-Spam: Yes` header added; Dovecot Sieve routes to Junk |
| >= 10 | Rewrite subject | Subject gets `[SPAM]` prefix |
| >= 15 | Reject | Email rejected at SMTP level |

These are the global defaults from `local.d/actions.conf`; per-organization overrides are applied via the settings module. Start conservative (higher thresholds) and tighten as you collect training data.

### Bayesian Classifier

The Bayesian filter learns what spam and ham (legitimate email) look like for your specific mail server. It stores token statistics in Redis.

```lua
-- classifier-bayes.conf
classifier "bayes" {
  backend = "redis";
  min_tokens = 11;
  min_learns = 200;    -- Need 200 samples before classifier activates
  autolearn = true;    -- Auto-learn from very high/low scoring emails
}
```

The classifier needs training data to be effective. It auto-learns from emails that score very high (definitely spam) or very low (definitely ham), but manual training produces better results.

**Training via the Web UI** is the easiest approach -- mark emails as spam or ham in the Rspamd web interface on port 11334.

**Training via command line:**

```bash
# Train as spam
docker exec rspamd rspamc learn_spam < /path/to/spam-email.eml

# Train as ham
docker exec rspamd rspamc learn_ham < /path/to/ham-email.eml

# Check training statistics
docker exec rspamd rspamc stat
```

### Neural Network Detection

Rspamd includes a neural network module that learns complex spam patterns that traditional rules miss:

```lua
-- neural.conf
neural {
  enabled = true;
  train {
    max_trains = 1000;
    max_usages = 20;
    learning_rate = 0.01;
  }
}
```

The neural network trains automatically from the results of other modules. It kicks in after enough training data has been collected and provides an additional scoring signal.

### DKIM Signing

Outbound emails are signed with DKIM to prove they came from your server:

```lua
-- local.d/dkim_signing.conf
enabled = true;
path = "/var/lib/rspamd/dkim/$domain.$selector.key";
selector = "default";
sign_authenticated = true;
sign_local = true;
use_esld = true;
allow_hdrfrom_mismatch = false;
allow_username_mismatch = false;
```

DKIM keys are stored per-domain at `/var/lib/rspamd/dkim/{domain}.{selector}.key` (the bind-mounted `storage/dkim_keys/` directory), with per-domain selectors resolved through `/etc/rspamd/dkim_selectors.map`. The public key goes in a DNS TXT record at `{selector}._domainkey.{domain}`.

!!! warning "Generate keys inside the rspamd container"
    `scripts/generate_dkim.py` is the tool -- and it must run **in the rspamd container**, which is the only one that mounts both the key directory *and* has the image-baked `dkim_selectors.map` on its own filesystem (the map is not a bind mount, so writing it from another container never reaches rspamd). The script also envelope-encrypts the private key via `shared.envelope_encryption`, which is why the container mounts `secrets/encryption_kek`.

### ClamAV Antivirus (not deployed)

`local.d/antivirus.conf` carries a full ClamAV integration, but it ships with `enabled = false` and **no ClamAV container exists in any compose file** -- leaving it disabled prevents connection errors. To enable scanning:

1. Add a `clamav` service to `docker-compose.yml` (image: `clamav/clamav`)
2. Set `enabled = true` in `local.d/antivirus.conf`
3. Confirm `servers = "clamav:3310"` matches the container

Once enabled, a detected virus rejects the email at the SMTP level (`action = "reject"`, symbol `CLAM_VIRUS`).

### Greylisting

Greylisting temporarily rejects the first email from an unknown sender. Legitimate mail servers retry; spam bots usually do not.

```lua
-- greylisting.conf
greylist {
  expire = 86400;        -- Remember senders for 24 hours
  timeout = 300;         -- Defer for 5 minutes on first attempt
  key_prefix = "grey";
  whitelist_domains_url = "/etc/rspamd/local.d/whitelist_domains.map";
}
```

Add major email providers (Gmail, Outlook, etc.) to the whitelist so their mail is never greylisted.

### SPF and DMARC

```lua
-- spf.conf
spf {
  enabled = true;
  spf_cache_size = 2k;
  spf_cache_expire = 12h;
}
```

```lua
-- dmarc.conf
dmarc {
  enabled = true;
  actions = {
    quarantine = "add_header";
    reject = "reject";
  }
}
```

Rspamd respects the sender's DMARC policy. If a domain publishes `p=reject` and the email fails both SPF and DKIM, it gets rejected.

## Web UI

The Rspamd web UI is available on port 11334. It provides:

- Real-time email scanning statistics
- Spam/ham ratio graphs
- Per-symbol score breakdowns
- Manual spam/ham training
- Configuration viewer
- Log viewer

Access it at `http://your-server:11334/`. The controller worker (`local.d/worker-controller.inc`) has **no password configured** -- which is exactly why production (`docker-compose.prod.yml`) binds 11334 to `127.0.0.1` only. Reach it over an SSH tunnel, or through the console's proxied link.

## Tuning False Positives

False positives (legitimate email marked as spam) are the worst outcome. Here is how to reduce them:

1. **Whitelist known senders** -- Add trusted domains to `whitelist_domains.map`
2. **Train the Bayesian classifier** -- More training data = better accuracy
3. **Adjust score thresholds** -- Raise the `add header` threshold in `actions.conf`
4. **Check per-symbol scores** -- Use the Web UI to see which rules are triggering. If a specific rule causes too many false positives, reduce its weight in a custom `.conf` file
5. **Use the settings module** -- Create per-domain or per-user overrides in `settings.conf`

```lua
-- Example: relax scoring for a specific domain
settings {
  trusted_partner {
    priority = 10;
    from = "/@trustedpartner\\.com$/";
    apply {
      actions {
        reject = 999;    -- Never reject
        greylist = 999;  -- Never greylist
      }
    }
  }
}
```

## Docker Configuration

```yaml
rspamd:
  build: ./mailer/rspamd
  container_name: rspamd
  ports:
    - "11332:11332"  # Milter protocol (Postfix connects here)
    - "11334:11334"  # Web UI + API (loopback-only in prod)
  volumes:
    - rspamd_data:/var/lib/rspamd
    - ./storage/dkim_keys:/var/lib/rspamd/dkim
    - ./logs/mailer/rspamd:/var/log/rspamd
    - ./config/mailer/rspamd:/etc/rspamd/custom
    - ./secrets/encryption_kek:/run/secrets/encryption_kek:ro
  depends_on:
    - redis
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | Bayesian data, greylisting, neural network, transport rules |
| `TRANSPORT_RULES_ENABLED` | `false` | Turn on tenant transport-rule enforcement -- read what your rules say first |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `mysql` / `3306` / `mailserver` / -- / -- | Used by `generate_dkim.py` when run in this container |

## Gotchas

!!! warning "Bayesian Minimum Learns"
    The Bayesian classifier will not activate until it has at least 200 spam and 200 ham samples (configurable via `min_learns`). Until then, it contributes zero to the score. Train early and often.

!!! warning "DKIM Key Permissions"
    DKIM private keys must be readable by the `_rspamd` user inside the container. If you mount keys from the host, check the file permissions.

!!! tip "Redis Memory"
    Rspamd stores a lot of data in Redis (Bayesian tokens, neural network weights, greylisting records, fuzzy hashes). Monitor Redis memory usage and set a `maxmemory` policy to prevent OOM issues.

!!! tip "Testing Spam Detection"
    Use the GTUBE test string in an email body to trigger a guaranteed spam detection:
    ```
    XJS*C4JDBQADN1.NSBN3*2IDNEN*GTUBE-STANDARD-ANTI-UBE-TEST-EMAIL*C.34X
    ```
