# Disaster Recovery Runbook

**Owner:** platform operations · **Last drill:** 2026-08-23 · **Last reviewed:** 2026-08-25

This is an operational document. It assumes something has already gone wrong and
you need commands, not concepts. Background and rationale live in
`plans/06-operations/00-PRD-disaster-recovery.md` (in the workspace planning repo, outside this docs site).

---

## 0. Before you do anything

**Do not start restoring until you know what actually broke.** A restore over
healthy data is worse than the outage. Two minutes of diagnosis:

```bash
# Is the mail server reachable at all?
ssh devops@66.29.133.223 'uptime; df -h /; docker ps --format "{{.Names}} {{.Status}}" | grep -v healthy'

# Is mail flowing? An empty queue and a recent log line means mail is fine and
# your problem is somewhere else.
ssh devops@66.29.133.223 'docker exec postfix postqueue -p | tail -3; docker logs --since 10m postfix 2>&1 | tail -5'

# Is anything actually lost, or just unavailable?
```

Senders retry for 48-72 hours on a dead MX. A mail server that is down is not
losing mail; a mail server that is restored badly is.

---

## 1. What exists, and where

| Thing | Location | Recovered by |
|---|---|---|
| `mail_crypt` key (decrypts **all** mail), `encryption_kek`, `.env`s, DKIM keys | `s3://mailyte-ent/escrow/mail/server1/latest.tar.age` | §3 |
| Mail server backups (MySQL, Maildir, Redis, SSL, config, secrets) | `s3://mailyte-ent/mailyte/backups/mail/server1/<backup_id>/` | §4 |
| Web server backups (Laravel DB, app files) | `s3://mailyte-ent/mailyte/backups/web/server1/<backup_id>/` | §5 |
| Per-message mail archive (raw EML) | `s3://mailyte-ent/mail-archive/{org}/{yyyy}/{mm}/` | §6 |
| Maildir folder/flag state, ≤15 min old | `s3://mailyte-ent/mail-state/server1/mail_data/` | §4.3 |

**Two things are not in S3 and never will be, by design:**

- the **age identity** that decrypts all of the above — it is in the owner's
  offline escrow (password manager / safe). Without it, every object listed
  above is unreadable ciphertext.
- the **`dr-restore` credentials** — read-only S3 access, also escrow-only.

If you have neither, you cannot recover. That is the intended property: a
fully compromised production server cannot read or destroy the offsite copies.

### 1.1 Escrow status of the age identity

Public recipient: `age1sfwss465khchhw5vfuugv2v0803lwmrueljsmedsm5u9chugyppqyxdekk`
Verification digest of the secret key line (sha256):
`03006b855ed6d6776c5fef7ae545980f0a80b8a831f05c44dd6da7a1ab55d8d9`

Use the digest to confirm any copy is intact without revealing it:

```bash
grep -o '^AGE-SECRET-KEY[A-Z0-9-]*' <copy> | tr -d '\n' | shasum -a 256
```

| Location | Status | Date | Notes |
|---|---|---|---|
| Bitwarden secure note (cloud) | **done** | 2026-08-25 | Vault is Bitwarden cloud, so it does not depend on the infrastructure it protects. |
| Printed copy, physical safe | **OUTSTANDING** | — | The only path that needs no email, device, network or login. |
| Working copy on the build Mac | present | 2026-08-22 | `~/.mailyte-dr/dr-age-identity.txt`, mode 600, FileVault on. Not a durable location. |

**Two locations is not belt-and-braces, it is the requirement.** Bitwarden
itself needs either a logged-in device, the account email, or the recovery
code to open. In a total-loss scenario where the build Mac is gone, the
printed copy is what breaks the deadlock. Until the safe copy exists, the
recovery path still has a single point of failure.

Related artefacts that belong in the same safe:

- the Bitwarden **recovery code** (*Settings → My Account → View Recovery Code*)
- the Bitwarden account email, changed 2026-08-25 to an address **not** hosted
  on this platform, because `techies.africa` (35 mailboxes), `mailyte.com` and
  `devpilot.io` all live on the mail server this runbook restores. A password
  manager whose recovery mail lands on the dead server cannot be opened to
  retrieve the key that revives it.

---

## 2. Scenario table

Each row links to its procedure. "Duration" is measured where a drill has run
and estimated (marked ~) where it has not.

| Scenario | Who | Procedure | Duration | Verify with |
|---|---|---|---|---|
| MySQL corruption (mail server) | on-call | [§4.1](#41-mysql-only) | ~15 min | mailbox count matches, mail flows |
| Mail storage loss | on-call | [§4.2](#42-mail-storage) | ~45 min | per-mailbox message counts |
| Laravel DB loss (web server) | on-call | [§5](#5-web-server-restore) | ~10 min | login works, org list intact |
| Total host loss | owner + on-call | [§7](#7-total-rebuild) | **30 s restore**, ~2-4 h end to end | [§8](#8-verification) |
| Ransomware | owner | [§7](#7-total-rebuild) + rotate everything | ~4 h | [§8](#8-verification) |
| Accidental tenant deletion | on-call | [§9](#9-single-tenant-restore) | ~10 min | that org's mailboxes only |
| "Customer says they sent it in March" | support | [§6](#6-single-message-retrieval) | ~1 min | EML matches |
| DKIM key loss | on-call | [§10](#10-dkim-key-loss) | ~30 min + DNS TTL | `dig` + a signed test send |
| Backups stopped silently | on-call | [§11](#11-backups-stopped) | ~15 min | `backup_age_seconds` drops |

---

## 3. Recover the keys (always first)

Nothing else works until this does. Run it on a machine that is **not** either
production server.

```bash
export AWS_ACCESS_KEY_ID=...        # dr-restore, from escrow
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=eu-west-2

aws s3 cp s3://mailyte-ent/escrow/mail/server1/latest.tar.age  ./bundle.tar.age
aws s3 cp s3://mailyte-ent/escrow/mail/server1/latest.manifest.json ./bundle.json

# The manifest is plaintext on purpose -- readable before you have the identity.
cat ./bundle.json
sha256sum ./bundle.tar.age          # must match "sha256" in the manifest

age --decrypt --identity /path/to/escrowed-identity.txt ./bundle.tar.age | tar xz
```

You now have `secrets/mail_crypt/`, `secrets/encryption_kek`,
`secrets/db_root_password`, `.env`, and `storage/dkim_keys/`.

> **If the checksum does not match**, do not use the bundle. Fetch a previous
> version — the bucket has versioning and Object Lock, so older copies exist:
> `aws s3api list-object-versions --bucket mailyte-ent --prefix escrow/mail/server1/`

---

## 4. Mail server restore

`restore.sh` handles download, decryption and restore. It **requires** the
escrowed identity — the key is deliberately not on the server.

```bash
cd /var/www/mailyte-email-server/current

# What is available offsite?
./scripts/restore.sh --list

# Full restore from a specific backup
./scripts/restore.sh --from-s3 20260822_180729 \
                     --identity /path/to/escrowed-identity.txt \
                     --host server1
```

It stops the affected services, restores, restarts, and verifies. The decrypted
working copy is shredded afterwards — it contains plaintext DKIM keys and a
full `.env`.

### 4.1 MySQL only

```bash
./scripts/restore.sh --from-s3 <backup_id> --identity <key> --mysql-only
```

Verify: `docker exec mysql mysql -uroot -p"$(cat secrets/db_root_password)" \
  -e "SELECT COUNT(*) FROM mailserver.email_accounts;"` — expect ~107.

### 4.2 Mail storage

```bash
./scripts/restore.sh --from-s3 <backup_id> --identity <key> --mail-only
```

Then rebuild Dovecot's indexes, or restored mail stays invisible to IMAP:

```bash
docker exec dovecot doveadm index -A '*'
```

### 4.3 Recovering the last 15 minutes of folder state

Nightly backups carry Maildir as of the backup. `mailyte-mail-sync.timer`
mirrors it every 15 minutes, which is what recovers "which folder was this in,
was it read" between backups:

```bash
aws s3 sync s3://mailyte-ent/mail-state/server1/mail_data/ \
            /var/www/mailyte-email-server/storage/mail_data/
docker exec dovecot doveadm index -A '*'
```

---

## 5. Web server restore

```bash
# On the web server (or a rebuild of it)
cd /var/www/html/mailyte/production/api
aws s3 ls s3://mailyte-ent/mailyte/backups/web/server1/

aws s3 cp s3://mailyte-ent/mailyte/backups/web/server1/<id>/mailyte_full_<id>.sql.gz.age .
age --decrypt --identity <key> mailyte_full_<id>.sql.gz.age | gzip -dc | mysql -u root -p mailyte

# App files (avatars, exports, Passport keys)
aws s3 cp s3://mailyte-ent/mailyte/backups/web/server1/<id>/appfiles_<id>.tar.gz.age .
age --decrypt --identity <key> appfiles_<id>.tar.gz.age | tar xz -C .
```

> Restore `storage/oauth-private.key` or **every issued API token stops
> working**. It is in both the app-file archive and the web escrow bundle.

---

## 6. Single message retrieval

The archive holds every accepted message as raw EML, encrypted, keyed by
Message-ID. Far faster than opening a backup:

```bash
curl -s "http://archiver:8083/archive/$(python3 -c \
  'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=""))' \
  '<message-id@example.com>')" | python3 -m json.tool
```

Or by sender/date without knowing the Message-ID:

```bash
curl -s "http://archiver:8083/archive/search?organization_id=<org>&sender=a@b.com" | python3 -m json.tool
```

If the archiver is gone, objects are still directly retrievable and decryptable
with the escrowed identity:

```bash
aws s3 cp s3://mailyte-ent/mail-archive/<org>/2026/03/<msgid>.eml.age .
age --decrypt --identity <key> <msgid>.eml.age | gzip -dc
```

---

## 7. Total rebuild

Rebuild from S3 with both production disks gone.

1. **Provision a host.** Ubuntu 24.04, ≥4 vCPU, ≥8 GB RAM, ≥150 GB disk.
2. **Install prerequisites**: Docker, Docker Compose, `age`, `aws` CLI
   (`deployment/systemd/../../scripts/` has no installer; use the same steps as
   `deployment/deploy.sh` pre-flight).
3. **Clone the repo** at the tag that was in production.
4. **Recover the keys** — [§3](#3-recover-the-keys-always-first). Place them at
   `secrets/`, mode 600. Restore `.env` from the bundle.
5. **Restore data** — [§4](#4-mail-server-restore).
6. **Staged startup**, not `docker compose up -d`:
   ```bash
   ./scripts/staged-startup.sh
   ```
7. **Repoint DNS.** Records are 300 s TTL. MX, A, SPF, DKIM, DMARC, autoconfig.
8. **Verify** — [§8](#8-verification).

**Measured RTO: 30 s** for the restore itself (2026-08-22 drill: key recovery,
database restore, Maildir restore, and mail read back through mail_crypt using
only the escrowed key). That number excludes host provisioning, Docker image
pulls and DNS propagation, which dominate: budget **2-4 hours** end to end and
treat 30 s as proof the *data* comes back fast, not as service-restoration time.

> **IP reputation.** A rebuilt server on a new IP starts cold and will see
> deferrals from the large providers for days. If the old IP can be reclaimed
> from the provider, that is worth more than any warm-up strategy.

---

## 8. Verification

Run all of these. A restore is not done because the commands exited 0.

```bash
# Schema is at the expected revision
docker exec mysql mysql -uroot -p"$(cat secrets/db_root_password)" \
  -N -e "SELECT version_num FROM mailserver.alembic_version;"

# Tenant data is really there
docker exec mysql mysql -uroot -p"$(cat secrets/db_root_password)" -N \
  -e "SELECT COUNT(*) FROM mailserver.email_accounts;
      SELECT COUNT(*) FROM mailserver.domains;"

# Mail is readable THROUGH mail_crypt -- the check that actually matters
docker exec dovecot doveadm fetch -u <known-mailbox> text mailbox INBOX | head -20

# Mail flows, both ways
swaks --to canary@<yourdomain> --server localhost:25
docker exec dovecot doveadm search -u canary@<yourdomain> mailbox INBOX SINCE today

# DKIM signs
dig +short default._domainkey.<yourdomain> TXT
```

The full unattended version of this is `./scripts/dr-drill.sh`, which restores
from S3 only and prints a measured RTO.

---

## 9. Single-tenant restore

The one you will actually use. Restores one organization without stopping
services or touching other tenants.

```bash
./scripts/restore.sh --organization <org_id> \
                     --from-s3 <backup_id> \
                     --identity <key>
```

It loads the dump into a staging schema, copies only rows matching that
`organization_id` into the live schema, restores only that org's Maildirs, and
refreshes Dovecot's indexes for those domains. `--dry-run` first.

Deliberately **not** restored: `api_keys`, sessions, and usage counters —
resurrecting rotated credentials or corrupting billing is not what the customer
asked for.

### 9.1 Credential changes and the Dovecot auth cache

Any operation here (or anywhere) that suspends a mailbox, deletes it, or
changes its password is **not enforced for up to 1 hour** unless you flush
Dovecot's auth cache: `auth_cache_ttl = 1 hour`, and a cached success keeps
authenticating for the full TTL. Verified live on 2.3.16 — a `nocache` passdb
field is not honoured.

```bash
# Per user (preferred -- surgical)
docker exec dovecot doveadm auth cache flush user@domain.com

# Everything (after a bulk restore or mass password rotation)
docker exec dovecot doveadm auth cache flush
```

SMTP API-key mutations through `/api/v1/smtp-credentials` flush automatically
via the doveadm HTTP API (port 24180, internal only, `DOVEADM_API_KEY`), and
since 2026-08-30 so do the API's mailbox mutations (password change,
suspend/deactivate, delete — the `/email-accounts` CRUD routes and the legacy
`/mailboxes/edit` / `/mailboxes/delete` paths). The manual flush above is
**still required** for anything done outside those routes: direct SQL edits,
bulk restores, migration scripts that rewrite Dovecot hashes, and the legacy
domain cascade delete. This matters most in the ransomware /
rotate-everything scenario (§7): passwords rotated by direct SQL or restore
tooling are not actually rotated, from the attacker's point of view, until
the cache is flushed or an hour passes — after any bulk operation, run the
flush-everything form.

---

## 10. DKIM key loss

Keys are in the escrow bundle ([§3](#3-recover-the-keys-always-first)) and in
every backup. Restore them rather than regenerating — regenerating means a
signing gap and a DNS change per domain.

```bash
tar xzf bundle.tar -C /var/www/mailyte-email-server/ storage/dkim_keys
docker exec rspamd rspamadm configtest && docker compose restart rspamd
```

If the keys are genuinely gone: generate new ones (`scripts/generate_dkim.py`),
publish the new DNS records, and accept that mail signed with the old selector
fails validation until caches expire.

---

## 11. Backups stopped

Triggered by `NoRecentFullBackup` or `BackupMonitoringGone`.

```bash
ssh devops@66.29.133.223
systemctl status mailyte-backup-full.timer mailyte-backup-incremental.timer
journalctl -u mailyte-backup-full --since '2 days ago' | tail -40
sudo ls -l /var/www/mailyte-email-server/secrets/dr.env   # must exist, mode 600
```

Most common causes, in order:

1. `secrets/dr.env` missing after a host rebuild — backups run but never upload,
   and the run exits non-zero.
2. S3 credentials expired or rotated — same symptom.
3. Disk full — the backup writes ~3 GB before uploading.
4. The timer was never installed on a new host:
   `sudo ./deployment/systemd/install-timers.sh`

Force a run and watch it:

```bash
sudo systemctl start mailyte-backup-full.service
journalctl -u mailyte-backup-full -f
```

---

## 12. Schedules and alerts

| Unit | Schedule | Produces |
|---|---|---|
| `mailyte-backup-full.timer` (mail) | 02:30 daily | full backup → S3 |
| `mailyte-backup-incremental.timer` (mail) | hourly, :15 | binlog + new mail → S3 |
| `mailyte-mail-sync.timer` (mail) | every 15 min | Maildir state → S3 |
| `mailyte-backup-web-full.timer` (web) | 02:00 daily | Laravel DB + app files → S3 |
| `mailyte-backup-web-incremental.timer` (web) | hourly, :45 | Laravel DB → S3 |
| pre-deploy | every deploy | full backup, blocks the deploy on failure |

Alerts (`monitoring/prometheus/rules/backup_alerts.yml`) fire on **absence**:
no full in 26 h, no incremental in 2 h, metrics gone for 15 min, unencrypted
backups present, archive spool not draining.

---

## 13. Quarterly drill

```bash
DR_AGE_IDENTITY_FILE=<escrowed key> DR_CRED_DIR=<escrow creds> ./scripts/dr-drill.sh
```

It uses **only** the escrowed identity, the read-only credentials and S3 — never
a production host. Record the printed RTO in the table below and update
[§7](#7-total-rebuild) if it moves.

| Date | Result | Measured RTO | Notes |
|---|---|---|---|
| 2026-08-22 | **pass (22/22)** | 30 s | First drill. Escrow bundle, database (82 tables, 107 mailboxes, 27 domains), and a single-domain Maildir read through mail_crypt with the escrowed key. |
| 2026-08-23 | **pass (22/22)** | 1 m 30 s | Same, against the **complete** 2.74 GB backup: the whole Maildir, all 27 domains, ~31k messages. Restored from S3 alone and read back through mail_crypt with the escrowed key. |

---

## 14. Known gaps

Honest list. Each is a real limitation of what has been verified, not a
theoretical one.

1. ~~"S3" is a local MinIO reached over an SSH reverse tunnel.~~
   **Closed 2026-08-25.** Both hosts now write directly to **`s3://mailyte-ent`
   in `eu-west-2`** on real AWS. The tunnel and the workstation MinIO have been
   torn down, and an incremental backup was run with the tunnel dead to prove
   the dependency is gone. Verified from S3 after cutover: escrow checksum
   matches its manifest, the escrowed `mail_crypt` key still hashes identical
   to the live one, the `mailserver` dump decrypts to 81 tables, and an
   archived EML decrypts byte-exact.
2. **The IAM policy on the supplied key is weaker than PRD D4 asks for**, and
   this is the most important open risk here:
   - It grants `s3:DeleteObject`, and the key lives **on the production mail
     server**. Anyone who gets root there can delete every backup. D4 exists
     specifically so a compromised host can pollute the offsite copy but not
     destroy it.
   - It has no bucket-level rights, so Object Lock, versioning and lifecycle
     can be neither set nor read from here. **We cannot currently confirm
     whether versioning is enabled**, which means point-in-time recovery from
     a malicious overwrite is unproven.
   - One key serves backup-writer, archiver and restore, so the escrow-only
     restore credential of D4 no longer exists as a separate principal.
   - `s3:AbortMultipartUpload` is absent: uploads succeed, but an interrupted
     multi-GB upload strands parts that cannot be cleaned up and keep billing.

   Fix is policy-only, no code change — see §16.
3. ~~The 2.8 GB full Maildir archive has not completed an upload.~~
   **Closed 2026-08-23**, and re-confirmed against real AWS on 2026-08-25
   (2.7 GiB object, uploaded in one pass in 5m29s with no retry loop — the
   retries were a property of the tunnel, not the backup path).
4. **The archive producers are built but not enabled in production.** Enabling
   them needs a Postfix and Dovecot image rebuild; the archiver itself is
   deployed and verified. See §15.
5. **The drill has not run on a fresh VPS.** It ran in local Docker, so it does
   not exercise host provisioning, package installation or DNS.
6. **Binlog shipping is not used on the web server** — the app's MySQL user has
   no `REPLICATION CLIENT` grant. Hourly full dumps (16 MB, 3 s) meet the
   stated ≤1 h RPO instead.

---

## 15. Enabling the archive producers

The archiver is live. The two producers ship in the images but need a rebuild:

```bash
cd /var/www/mailyte-email-server/current

# Outbound (tracking_injector.py). Postfix has a persistent spool volume, so a
# recreate does not lose queued mail -- but check the queue is drained first.
docker exec postfix postqueue -p | tail -2
docker compose build postfix && docker compose up -d --no-deps postfix

# Inbound (Dovecot global sieve pipe).
docker compose build dovecot && docker compose up -d --no-deps dovecot

# Verify immediately, both directions, before walking away:
swaks --to canary@<yourdomain> --server <mx> --from external@<other-domain>
curl -s http://127.0.0.1:8089/archive/spool
docker exec -e MYSQL_PWD="$(cat ../secrets/db_root_password)" mysql \
  mysql -uroot -N -e "SELECT COUNT(*) FROM mailserver.email_archive;"
```

If mail stops flowing, the fastest rollback is
`ARCHIVE_OUTBOUND_ENABLED=false` in `.env` plus recreating Postfix; the sieve
pipe cannot defer mail by design (`archive-message` always exits 0) but the
same rebuild-and-recreate reverts it.

---

## 16. Tightening the S3 IAM policy

The key in use today can delete the backups it writes (§14.2). Everything here
is an AWS-side change: no Mailyte code changes, nothing to restart.

### 16.1 Which prefixes may be deleted from, and why

Not "no delete anywhere" — two prefixes genuinely need it, and denying them
breaks working features:

| Prefix | Delete? | Why |
|---|---|---|
| `mailyte/backups/*` | **never** | The recovery path. A host that can delete this can destroy its own history — the exact capability D4 exists to deny. |
| `escrow/*` | **never** | Same, and it is the key material without which every other object is unreadable. |
| `mail-state/*` | yes | `mail-sync.sh` runs `aws s3 sync --delete` deliberately, so a restore does not resurrect mail the user expunged. Denying delete here breaks the 15-minute mirror. |
| `mail-archive/*` | yes | The archiver's `DELETE /archive/{id}` implements retention and legal-hold expiry. |

With versioning on, a delete against the two allowed prefixes writes a delete
marker rather than destroying bytes, so even those stay recoverable.

### 16.2 Key A — `mailyte-backup` (lives on both production hosts)

The `Deny` block is the load-bearing part: an explicit Deny cannot be
overridden by any Allow, now or by a future policy edit.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListAndMultipart",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::mailyte-ent"
    },
    {
      "Sid": "ReadWriteObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::mailyte-ent/*"
    },
    {
      "Sid": "DeleteOnlyWhereTheCodeNeedsIt",
      "Effect": "Allow",
      "Action": "s3:DeleteObject",
      "Resource": [
        "arn:aws:s3:::mailyte-ent/mail-state/*",
        "arn:aws:s3:::mailyte-ent/mail-archive/*"
      ]
    },
    {
      "Sid": "NeverDestroyTheRecoveryPath",
      "Effect": "Deny",
      "Action": [
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:PutObjectRetention",
        "s3:BypassGovernanceRetention"
      ],
      "Resource": [
        "arn:aws:s3:::mailyte-ent/mailyte/backups/*",
        "arn:aws:s3:::mailyte-ent/escrow/*"
      ]
    },
    {
      "Sid": "NoBucketReconfigurationFromAHost",
      "Effect": "Deny",
      "Action": [
        "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration",
        "s3:PutBucketObjectLockConfiguration",
        "s3:PutBucketPolicy",
        "s3:DeleteBucketPolicy",
        "s3:PutBucketPublicAccessBlock"
      ],
      "Resource": "arn:aws:s3:::mailyte-ent"
    }
  ]
}
```

### 16.3 Key B — `mailyte-dr-admin` (never on a production host)

Lives in the password manager beside the age identity. Used for bucket
configuration, lifecycle work, and any deliberate pruning.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BucketAdmin",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketLocation",
        "s3:GetBucketVersioning", "s3:PutBucketVersioning",
        "s3:GetLifecycleConfiguration", "s3:PutLifecycleConfiguration",
        "s3:GetBucketObjectLockConfiguration", "s3:PutBucketObjectLockConfiguration",
        "s3:GetEncryptionConfiguration", "s3:PutEncryptionConfiguration",
        "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock"
      ],
      "Resource": "arn:aws:s3:::mailyte-ent"
    },
    {
      "Sid": "ObjectAdmin",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject",
        "s3:DeleteObject", "s3:DeleteObjectVersion",
        "s3:GetObjectRetention", "s3:PutObjectRetention",
        "s3:BypassGovernanceRetention", "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::mailyte-ent/*"
    }
  ]
}
```

### 16.4 Key C — `mailyte-dr-restore` (optional, escrow-only, read-only)

What PRD D4 actually asked for: the credential you hand someone doing a restore,
which cannot alter anything. Key B can also restore; this one just cannot cause
damage while doing it.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadOnlyForRestore",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:GetObjectVersion"],
      "Resource": "arn:aws:s3:::mailyte-ent/*"
    },
    {
      "Sid": "ListForRestore",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::mailyte-ent"
    }
  ]
}
```

### 16.5 Bucket configuration (run with Key B)

```bash
export AWS_DEFAULT_REGION=eu-west-2

# Versioning: makes every delete a delete marker, so the two delete-allowed
# prefixes stay recoverable and an overwrite never destroys the prior copy.
aws s3api put-bucket-versioning --bucket mailyte-ent \
  --versioning-configuration Status=Enabled

aws s3api get-bucket-versioning --bucket mailyte-ent    # expect Status: Enabled
```

**Object Lock cannot be enabled on an existing bucket.** If `mailyte-ent` was
not created with it, the choices are honest ones: accept versioning alone
(defeats overwrite and accidental delete, not a determined attacker with the
admin key), or create `mailyte-ent-locked` with
`--object-lock-enabled-for-bucket`, copy the current contents across, and change
`S3_BUCKET` in `secrets/dr.env` on both hosts. Nothing else changes.

Lifecycle, matching PRD D6 — save as `lifecycle.json`:

```json
{
  "Rules": [
    { "ID": "abort-stranded-multipart",
      "Filter": {"Prefix": ""}, "Status": "Enabled",
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7} },
    { "ID": "backups-tier-then-expire",
      "Filter": {"Prefix": "mailyte/backups/"}, "Status": "Enabled",
      "Transitions": [
        {"Days": 30, "StorageClass": "STANDARD_IA"},
        {"Days": 120, "StorageClass": "GLACIER_IR"}
      ],
      "Expiration": {"Days": 400} },
    { "ID": "archive-standard-then-ia",
      "Filter": {"Prefix": "mail-archive/"}, "Status": "Enabled",
      "Transitions": [{"Days": 90, "StorageClass": "STANDARD_IA"}] },
    { "ID": "mail-state-is-a-mirror-not-history",
      "Filter": {"Prefix": "mail-state/"}, "Status": "Enabled",
      "NoncurrentVersionExpiration": {"NoncurrentDays": 14} },
    { "ID": "escrow-never-expires",
      "Filter": {"Prefix": "escrow/"}, "Status": "Enabled",
      "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}] }
  ]
}
```

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket mailyte-ent --lifecycle-configuration file://lifecycle.json
```

> `mail-state/` gets `NoncurrentVersionExpiration` because it is a mirror of a
> 3 GB Maildir that changes constantly. Without it, versioning retains every
> superseded message file forever and the bucket grows without bound.

### 16.6 Rolling the key on the hosts

```bash
# On each host, edit the two values -- nothing else in the file changes.
sudo -u devops vi /var/www/mailyte-email-server/secrets/dr.env          # mail
sudo -u devops vi /var/www/html/mailyte/production/api/secrets/dr.env   # web

# The archiver reads its copy from the compose env, so it also needs:
#   ARCHIVE_AWS_ACCESS_KEY_ID / ARCHIVE_AWS_SECRET_ACCESS_KEY in .env
cd /var/www/mailyte-email-server/current && docker compose up -d --no-deps archiver
```

Then prove it, rather than assuming:

```bash
# 1. A backup still completes and reaches S3
/var/www/mailyte-email-server/current/scripts/backup.sh --incremental

# 2. The mirror still prunes (this is what a no-delete policy breaks)
/var/www/mailyte-email-server/current/scripts/mail-sync.sh

# 3. The recovery path is genuinely protected -- MUST fail with AccessDenied
set -a; . /var/www/mailyte-email-server/secrets/dr.env; set +a
aws s3 rm s3://mailyte-ent/escrow/mail/server1/latest.manifest.json
```

If step 3 succeeds, the policy is not applied and the mail server can still
destroy its own escrow.
