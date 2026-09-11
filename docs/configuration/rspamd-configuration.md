# Rspamd Configuration

Spam filtering, DKIM/ARC signing, greylisting, Bayesian training, per-organization policies, and the smart-folder classifier.

---

Rspamd is the brain behind Mailyte's spam detection. It scores every message, signs outgoing mail with DKIM and ARC, greylists unknown senders, and tags messages with an `X-Email-Category` header for smart-folder routing. It connects to Postfix as a milter on port 11332.

The configuration is **baked into the image** from `mailer/rspamd/config/` (`COPY config/ /etc/rspamd/`), so `local.d/` overrides live in the repo, not on the host:

```
mailer/rspamd/config/
  rspamd.local.lua          # loads the custom Lua modules
  dkim_selectors.map        # baked placeholder; rewritten at runtime by the DKIM sync (see below)
  local.d/                  # module configs (baked)
  local.d/lua/              # email_classifier.lua, transport_rules.lua
```

## Workers

| Worker | Bind | Purpose |
|--------|------|---------|
| proxy | `*:11332` (milter mode, `self_scan = yes`) | What Postfix connects to |
| normal | `*:11333` | Scanning |
| controller | `*:11334` | Web UI, `/metrics`, learn/stat API |

> [!WARNING]
> The controller (`worker-controller.inc`) has **no password and no `secure_ip` configured**, and port 11334 is published (bound to 127.0.0.1 in production). Anyone who can reach it can reconfigure and train the filter. Don't expose it beyond localhost/reverse-proxy with auth.

## Spam Scoring

`local.d/actions.conf` — the global defaults:

```ini
reject = 15;
rewrite_subject = 10;
add_header = 6;
greylist = 4;
subject = "[SPAM] %s";
```

- **Score below 4** — delivered normally.
- **Score 4–6** — greylisted (deferred, retried).
- **Score 6–10** — delivered with a spam header; the global sieve script files it into Junk.
- **Score 10–15** — subject rewritten to `[SPAM] ...`.
- **Score 15+** — rejected at SMTP time.

### Per-Organization Overrides

Thresholds can differ per organization. The `settings` module reads dynamic rules from Redis (`settings.conf`: `redis { servers = "redis:6379"; } id_prefix = "rspamd_settings:";`), and `scripts/sync_rspamd_settings.py` populates them from each organization's `settings.spam_policy` JSON in MySQL:

```bash
python3 scripts/sync_rspamd_settings.py --watch   # daemon mode, default 60s interval
python3 scripts/sync_rspamd_settings.py --org <org_id>
```

It writes one `rspamd_settings:org_{org}_{domain}` entry per domain (actions: reject / add header / greylist / rewrite subject, optional quarantine) and maintains the per-domain sender/domain white/blacklist sets consumed by the multimap rules below. Two Redis databases are involved, matching what each rspamd module reads: the `rspamd_settings:*` keys go to **db 0** (the `settings` module's default), while the `org_sender_whitelist_*` / `org_sender_blacklist_*` / `org_domain_whitelist_*` sets go to **db 3** — the db `multimap.conf`'s `redis://redis:6379/3/...` maps read. (Before 2026-08-30 the script wrote everything to db 0, so per-org sender lists never matched at scan time.) No compose service runs it — schedule it via cron or run it by hand after changing an organization's spam policy.

## DKIM and ARC Signing

`local.d/dkim_signing.conf`:

```ini
enabled = true;
path = "/var/lib/rspamd/dkim/$domain.$selector.key";
selector = "default";
selector_map = "/etc/rspamd/dkim_selectors.map";
sign_authenticated = true;
sign_local = true;
use_esld = true;
allow_hdrfrom_mismatch = false;
allow_username_mismatch = false;
try_fallback = true;
sign_algorithm = "rsa";
```

`local.d/arc.conf` mirrors the same settings for ARC sealing.

Keys live at `/var/lib/rspamd/dkim/{domain}.{selector}.key`, bind-mounted from the host's `./storage/dkim_keys`, owned by `_rspamd`. MySQL (`dkim_keys`, envelope-encrypted) is the source of truth; `worker/api/utils/dkim_sync.py` exports it to these files and the selector map — automatically on every API DKIM change (create/rotate/activate/update/delete), and on demand from the docker host:

```bash
python3 scripts/generate_dkim.py sync             # reconcile disk with MySQL (keys + selector map)
python3 scripts/generate_dkim.py sync --prune     # also remove files for deleted/disabled domains
python3 scripts/generate_dkim.py backfill         # mint keys for active domains that have none, then sync
python3 scripts/generate_dkim.py dns yourdomain.com   # print the DNS record
```

The host tool works by `docker exec`: the `api` container decrypts and streams the desired state, and the files are written inside the `rspamd` container (its image has no Python and MySQL is not published on the host). Per-domain rotation is an API flow (`POST /{domain_id}/dkim/rotate` + `.../activate`), not a script flag.

The default selector is `default` (DNS name `default._domainkey.<domain>`); rotated domains use timestamped selectors via the selector map. See [DNS Setup](dns-setup.md) for the record format.

> [!NOTE]
> `try_fallback = true` matters: with it set to `false`, a missing selector-map entry made Rspamd sign **nothing** (`R_DKIM_NA` on all outbound mail).

> [!WARNING]
> `/etc/rspamd/dkim_selectors.map` is baked into the image layer, so recreating the rspamd container resets it to the packaged placeholder — non-default selectors then stop matching until `generate_dkim.py sync` runs again (the sync also maintains a durable copy at `/var/lib/rspamd/dkim/dkim_selectors.map`; repointing `selector_map` there and rebuilding the image makes the map survive recreates). New key files need no reload — the `path` template is resolved per message — and map edits are picked up on Rspamd's map watch interval.

## Antivirus (ClamAV) — Not Deployed by Default

`local.d/antivirus.conf` ships **disabled**:

```ini
clamav {
  enabled = false;
  servers = "clamav:3310";
  scan_mime_parts = true;
  symbol = "CLAM_VIRUS";
  action = "reject";
}
```

There is no `clamav` service in either compose file. To enable virus scanning: add a `clamav/clamav` service to `docker-compose.yml`, flip `enabled = true`, and rebuild the rspamd image. Leaving it disabled while ClamAV isn't running is deliberate — it prevents connection errors on every scan.

## Greylisting

`local.d/greylisting.conf`:

```ini
enabled = true;
servers = "redis:6379";
timeout = 300;
expire = 86400;
expire_whitelist = 604800;
threshold = 1.0;
skip_authenticated = true;
skip_local = true;
whitelist_symbols = ["DKIM_VALID", "DKIM_VALID_AU", "DMARC_POLICY_ALLOW", "SPF_ALLOW"];
per_domain_whitelist = true;
message = "Greylisted for %d seconds";
```

- **`timeout = 300`** — unknown senders must wait 5 minutes before retrying.
- **`expire = 86400`** — a sender that passes is remembered for 24 hours (whitelisted entries for 7 days).
- **Authenticated and local mail is never greylisted**, and mail that already passes DKIM/DMARC/SPF skips it too.
- `local.d/whitelist_domains.map` pre-whitelists 34 major providers (Gmail, Outlook/Microsoft, Yahoo, iCloud, Proton, Zoho, the big ESPs, …).

## Bayesian Filtering

`local.d/classifier-bayes.conf`:

```ini
backend = "redis";
servers = "redis:6379";
new_schema = true;
expire = 8640000;
per_user = true;
per_language = true;
min_learns = 200;
```

The classifier doesn't act until it has seen at least 200 learned messages. Training is per-user and per-language.

### Manual Training

```bash
docker exec -i rspamd rspamc learn_spam < /path/to/spam-message.eml
docker exec -i rspamd rspamc learn_ham < /path/to/good-message.eml
docker exec rspamd rspamc stat
```

## Other Active Modules

| Module | Config | Notes |
|--------|--------|-------|
| DMARC | `dmarc.conf` | `quarantine → add_header`, `reject → reject`, `softfail → add_header`; reporting off |
| SPF | `spf.conf` | 2k cache, 1d expiry, `whitelist_on_authenticated = true` |
| Fuzzy | `fuzzy_check.conf` | Local read/write fuzzy store in Redis + read-only `rspamd.com` public feed |
| Neural | `neural.conf` | `NEURAL_SPAM` network, spam_score 8 / ham_score −2, retrains continuously |
| Phishing | `phishing.conf` | OpenPhish + PhishTank feeds enabled |
| URL reputation | `url_reputation.conf` | Redis-backed, 30-day expiry |
| Milter headers | `milter_headers.conf` | Adds `Authentication-Results`, `X-Spam-Status`; strips upstream spam flags; adds `X-Email-Category` for recipients |
| Multimap | `multimap.conf` | Per-org lists from Redis: `ORG_SENDER_WHITELIST` (−5.0), `ORG_SENDER_BLACKLIST` (+10.0), `ORG_DOMAIN_WHITELIST` (−3.0); plus `DISPOSABLE_EMAIL` (+3.0) and `FREEMAIL_FROM` (0.0) from rspamd.com maps |

## Custom Lua Modules

`rspamd.local.lua` loads two repo-local modules:

- **`local.d/lua/email_classifier.lua`** — assigns `X-Email-Category` ∈ {`primary`, `notifications`, `social`, `promotions`, `updates`} from sender domain/local-part heuristics. Dovecot's global sieve script routes Social/Promotions/Updates into the matching smart folders (Notifications is deliberately not auto-filed).
- **`local.d/lua/transport_rules.lua`** — evaluates console-authored transport rules stored in Redis (`mailyte:transport_rules`). Gated by `TRANSPORT_RULES_ENABLED` (compose default `false`). `redirect`/`bcc` actions are evaluated but skipped — a milter cannot reroute a message.

## Rspamd Web UI

The controller Web UI runs on port 11334 (production binds it to `127.0.0.1`). As shipped it has **no password** — access it through an SSH tunnel:

```bash
ssh -L 11334:127.0.0.1:11334 your-server
# then open http://localhost:11334
```

## Checking a Message

```bash
docker exec -i rspamd rspamc < /path/to/message.eml
```

This prints the score, matched symbols, and the action that would be taken.

## Testing and Reloading

```bash
docker exec rspamd rspamadm configtest    # validate config
docker exec rspamd rspamadm configdump    # dump effective config
docker compose restart rspamd             # apply changes (config is baked — rebuild for repo edits)
```
