---
title: Setting Up DKIM
description: Understand how Mailyte generates DKIM keys, get the key file onto disk for Rspamd, publish the DNS record, and rotate keys safely.
---

# Setting Up DKIM

DKIM (DomainKeys Identified Mail) signs your outgoing emails with a cryptographic key so receiving servers can verify the message really came from you. Without it, your email is much more likely to land in spam.

## How DKIM Works (30-Second Version)

1. Mailyte generates a 2048-bit RSA key pair (private + public) for your domain
2. The private key stays on the server — Rspamd uses it to sign every outgoing email
3. The public key goes into a DNS TXT record
4. When Gmail/Outlook receives your email, they check the signature against the public key in DNS

If the signature matches, the email passes DKIM. If not, it fails — and that hurts your reputation.

## Where DKIM Lives in Mailyte

Two places, and both must agree:

| Location | Written by | Read by |
|----------|-----------|---------|
| `dkim_keys` table in MySQL (envelope-encrypted) | The API (`POST /api/v1/domains/`, rotate endpoints) and the backfill in `utils/dkim_sync.py` | The API — powers `GET /{domain_id}/dkim` and DNS verification. **Source of truth.** |
| Key files at `/var/lib/rspamd/dkim/{domain}.{selector}.key` (bind-mounted from `storage/dkim_keys/` on the host) plus the selector map | `worker/api/utils/dkim_sync.py` — write-through from the API routes, and via `scripts/generate_dkim.py sync` on the host | Rspamd — this is what actually signs mail |

Every API action that changes DKIM state (domain create, rotate, activate, `dkim_enabled`/`dkim_selector` updates, domain delete) writes through to the key directory and selector map via `worker/api/utils/dkim_sync.py`. The write-through is best-effort: if it cannot run (the api container has no key directory mounted, or the directory is not writable by the api user), the key is still safe in MySQL, the API logs exactly what to run, and the rotate/activate responses carry `rspamd_synced: false`. The export below heals any drift.

## Step 1: Export Keys to Rspamd with generate_dkim.py

`scripts/generate_dkim.py` runs **on the docker host** and reconciles disk with the database. It asks the `api` container for the decrypted key material (only that container has both MySQL access and the encryption KEK; keys travel over the `docker exec` pipe, never the host disk) and writes each key file and the selector map **inside the rspamd container**, owned by `_rspamd` with mode `0600`:

```bash
python3 scripts/generate_dkim.py sync            # export all keys + selector map
python3 scripts/generate_dkim.py sync --prune    # also delete files for removed/disabled domains
python3 scripts/generate_dkim.py backfill        # mint keys for active domains with NONE, then sync
python3 scripts/generate_dkim.py dns example.com # reprint the DNS record
./start.sh dkim                                  # same as `sync`, via mailyte-ctl
```

Inside the api container the same logic is available directly as `python -m utils.dkim_sync` (`reconcile`, `sync-domain <domain>`, `generate-missing`, `dns <domain>`, `map`). Its paths are configured by `DKIM_KEY_DIR` (default `/var/lib/rspamd/dkim`) and `DKIM_SELECTOR_MAP` (default: `dkim_selectors.map` inside the key directory).

!!! note "Per-domain generation and rotation are API operations"
    The script deliberately refuses `--rotate` and bare-domain generation: minting a key whose DNS record does not exist yet (or re-keying under the same selector) creates a window where mail fails DKIM. Use the two-step rotate/activate API flow below; `backfill` only creates keys for domains that have none at all.

## Step 2: Get the Public Key

The API returns copy-pasteable records for every key on a domain:

```bash
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/dkim \
  -H "X-API-Key: YOUR_API_KEY"
```

Each item includes `record_name` (e.g. `default._domainkey.example.com`), `record_type` (`TXT`), and `record_value` (`v=DKIM1; k=rsa; p=...`), along with the key's `selector`, `active` flag, and the domain's current `signing_selector`. Private keys are never returned.

If the key was minted by `generate_dkim.py backfill`, the script already printed the record; `generate_dkim.py dns example.com` reprints it. The `record_name` always carries the domain's **actual** selector — `POST /api/v1/domains/` and `GET /{domain_id}` return the same (the `dns_records` array used to hardcode `default._domainkey` regardless of selector; it now follows the active selector).

## Step 3: Add the DNS Record

Create a TXT record at your DNS provider:

| Field | Value |
|-------|-------|
| **Name** | `default._domainkey.example.com` |
| **Type** | TXT |
| **Value** | `v=DKIM1; k=rsa; p=MIIBIjANBgkqh...` |
| **TTL** | 3600 |

The `default` part is the selector — it matches the selector the key was generated under.

!!! warning "Long TXT records"
    DKIM public keys are long. DNS limits TXT records to 255 characters per string. Most DNS providers handle splitting automatically; split the value into 255-character quoted chunks yourself for providers that don't.

## Step 4: How Rspamd Signing is Configured

The signing configuration is baked into the Rspamd image from `mailer/rspamd/config/local.d/dkim_signing.conf` (changing it requires rebuilding the `rspamd` image):

```
enabled = true;

# Path template — Rspamd substitutes $domain and $selector
path = "/var/lib/rspamd/dkim/$domain.$selector.key";

# Default selector (overridden by selector_map for per-domain selectors)
selector = "default";

sign_authenticated = true;
sign_local = true;
use_esld = true;

# Sign any domain whose key file exists at the path template —
# new domains are signed automatically as soon as their key file appears
try_fallback = true;

# Per-domain selector map (domain → selector), maintained by generate_dkim.py
selector_map = "/etc/rspamd/dkim_selectors.map";

sign_algorithm = "rsa";
```

Because `try_fallback = true`, any domain with a key file at `/var/lib/rspamd/dkim/<domain>.default.key` is signed with the `default` selector — no map entry or restart needed. The selector map only matters for domains signing under a **non-default** selector (anything that has been through rotate/activate); `generate_dkim.py sync` rebuilds it from the database.

!!! warning "The live selector map resets on container recreate"
    `/etc/rspamd/dkim_selectors.map` is baked into the rspamd image layer, not bind-mounted — a `docker compose up` that recreates the rspamd container resets it to the empty packaged example. Run `generate_dkim.py sync` (or `./start.sh dkim`) after any deploy that recreates rspamd. The sync also keeps a durable copy at `/var/lib/rspamd/dkim/dkim_selectors.map` (i.e. `storage/dkim_keys/` on the host); pointing `selector_map` there in `dkim_signing.conf` and rebuilding the rspamd image removes this reset entirely.

## Step 5: Verify DKIM is Working

### Check DNS Propagation

```bash
dig TXT default._domainkey.example.com +short
```

You should see your public key in the response. If it's empty, DNS hasn't propagated yet — wait a few minutes. You can also run the server-side check:

```bash
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/verify-dns \
  -H "X-API-Key: YOUR_API_KEY"
```

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

## Troubleshooting

### "DKIM signature not found" / `R_DKIM_NA`

The email wasn't signed at all. Check:

1. Does the key file exist in the container?
   ```bash
   docker exec rspamd ls -la /var/lib/rspamd/dkim/
   ```
   No file for the domain means the write-through could not run when the key was created — run `generate_dkim.py sync` (Step 1) and check the api container's logs for the `DKIM sync` error explaining why.

2. Is the key file readable by Rspamd? It must be owned by `_rspamd` (mode 640 owned by root is unreadable):
   ```bash
   docker exec rspamd stat -c '%U %a %n' /var/lib/rspamd/dkim/example.com.default.key
   ```

3. Check Rspamd logs for signing errors (host path `logs/mailer/rspamd/rspamd.log`):
   ```bash
   docker logs rspamd 2>&1 | grep -i dkim | tail -20
   ```

### "DKIM signature verification failed"

The email was signed, but the receiving server couldn't verify it. Common causes:

- **DNS record mismatch** — the published public key doesn't match the private key on disk. Compare against `GET /{domain_id}/dkim` (or `generate_dkim.py dns <domain>`) and republish the record for the **active** selector.
- **DNS not propagated** — you just added the record, give it time
- **Message was modified in transit** — a mailing list or forwarding service altered the message body

### "Key too long for DNS"

1. Use a 2048-bit key instead of 4096-bit
2. Manually split the record into 255-character quoted strings

## Rotating DKIM Keys

Good practice: rotate your DKIM keys once a year. The API implements rotation as a **two-step** operation so there is never a window where mail is signed with a selector whose DNS record doesn't exist yet:

```bash
# Step 1: mint a new key under a new timestamped selector.
# The old key keeps signing. The response contains the new DNS record.
curl -X POST https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/dkim/rotate \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "annual rotation"}'

# Publish the returned TXT record, wait for it to resolve publicly, then:

# Step 2: cut signing over to the new selector.
curl -X POST https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/dkim/SELECTOR/activate \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "record propagated, cutting over"}'
```

Both endpoints require an operator-role credential and record the `reason` in the audit log.

!!! note "Disk cutover"
    Both endpoints write through to disk: rotate exports the new selector's key file immediately (so it is already in place while DNS propagates), and activate rewrites the selector map. Check `rspamd_synced` in each response — `false` means the DB is updated but the export could not run; run `generate_dkim.py sync` before relying on the cutover. Rspamd re-reads the selector map on its map watch interval (about a minute), so the cutover is not instantaneous.

Keep both selectors in DNS during the transition — old emails in recipients' inboxes still verify against the old key. Remove the old record after 48 hours.
