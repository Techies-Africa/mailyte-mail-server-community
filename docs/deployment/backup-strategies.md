# Backup Strategies

What gets backed up, how often, and where it goes — because the question isn't *if* you'll need a backup, it's *when*.

Mailyte ships a complete backup system: `scripts/backup.sh` (the engine), systemd timers (the schedule), age public-key encryption (the confidentiality), S3 upload (the offsite copy), and `scripts/restore.sh` (the way back). Do not build your own mysqldump cron next to it.

## What Gets Backed Up

A `--full` run covers:

| Data | Priority | Notes |
|------|----------|-------|
| MySQL database | Critical | Full dump; `--incremental` ships binlogs between fulls |
| Mail storage (`storage/mail_data/` Maildirs) | Critical | Incremental runs only pick up new mail |
| Redis data | Medium | Rate-limit and cache state |
| `secrets/` | Critical | Always encrypted — `--no-encrypt` refuses to run when secrets are included |
| DKIM keys (`storage/dkim_keys/`) | High | Encrypted under the KEK on disk already |
| SSL certificates (`storage/ssl_certs/`, `storage/ssl_private/`) | High | Reissuable, but restoring beats reissuing mid-incident |
| Configuration (`config/`, compose files) | High | |

Things deliberately *not* backed up: the Postfix queue (transient), Prometheus data (nice to have), Docker images (rebuilt from source).

## The Schedule: systemd Timers, Not Cron

Backups run from **host systemd**, not from a container — a backup container that stops with `docker compose down` is missing exactly when it's needed.

```bash
sudo ./deployment/systemd/install-timers.sh           # installs + enables
sudo ./deployment/systemd/install-timers.sh --status  # verify
```

| Timer | Runs | Does |
|-------|------|------|
| `mailyte-backup-full.timer` | Daily at 02:30 | `backup.sh --full` |
| `mailyte-backup-incremental.timer` | Hourly at :15 | `backup.sh --incremental` (MySQL binlog + new mail) |
| `mailyte-mail-sync.timer` | Every 15 min | Near-real-time Maildir state sync to S3 |

On top of the schedule, `deployment/deploy.sh` takes its own pre-deploy backup on every release — a database dump whenever a migration is pending, a local config backup otherwise.

## Backup Modes

```bash
./scripts/backup.sh --full            # everything (the default)
./scripts/backup.sh --incremental     # MySQL binlog + new mail since last run
./scripts/backup.sh --mysql-only      # one component only; also:
                                      # --redis-only, --mail-only,
                                      # --secrets-only, --config-only
./scripts/backup.sh --pre-deploy      # database + config, local only (used by deploy.sh)
./scripts/backup.sh --verify          # verify existing backups
./scripts/backup.sh --no-upload       # skip S3 even if configured
```

Each run writes a timestamped set under `storage/backups/<YYYYMMDD_HHMMSS>/` with a `MANIFEST.json`, in a strict order: components → verify → manifest → encrypt → upload → prune. Verification happens *before* encryption (you cannot read inside an encrypted tar), and pruning happens last — local copies are only pruned aggressively once the upload succeeded, so a broken offsite path never costs you the local copies too.

Retention: the last `BACKUP_RETENTION_FULLS` (default 3) local full sets are kept once offsite works, with an age-based fallback of `BACKUP_RETENTION_DAYS` (default 30) when it does not.

## Offsite Configuration: `secrets/dr.env`

Everything offsite-related lives in `secrets/dr.env` (mode 600, never committed, and in `secrets/` — not `config/` — because deploys replace `config/` while `secrets/` persists):

```dotenv
# secrets/dr.env
S3_BUCKET=your-dr-bucket
S3_PREFIX=mailyte/backups
S3_ENDPOINT_URL=            # empty for real AWS; set for MinIO/R2/B2
AWS_ACCESS_KEY_ID=...       # a backup-writer principal: Put, no Delete
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-west-1
DR_AGE_RECIPIENT=age1...    # PUBLIC key only -- the identity never lands on this host
```

### Why age public-key encryption

Backups are encrypted client-side to `DR_AGE_RECIPIENT` before upload. Because only the *public* key exists on the server, a fully compromised host can write new backups but **cannot read any backup — including its own**. The matching private identity exists only in escrow, off this machine.

### Escrow the unrecoverable secrets

Some things a backup cannot recreate: the age identity itself, `secrets/encryption_kek` (protects DKIM/PGP/S-MIME keys), Dovecot mail_crypt keys, `.env`. Bundle and store them off-server:

```bash
./scripts/escrow-secrets.sh
```

Verify the escrow bundle exists after any secret changes — a lost `encryption_kek` or mail_crypt key is permanent data loss no matter how many backups you have.

## Verifying Backups

A backup you haven't tested is not a backup.

```bash
# Built-in verification of existing sets
./scripts/backup.sh --verify

# List what exists (local and, with configuration, S3)
./scripts/restore.sh --list

# Rehearse a restore without touching anything
./scripts/restore.sh --latest --dry-run

# Full disaster-recovery drill against a local throwaway environment
./scripts/dr-drill.sh
```

`monitoring/prometheus/rules/backup_alerts.yml` alerts when backups go stale — make sure Alertmanager routing is configured so those alerts reach a human.

> **Tip:** Schedule a monthly restore drill. It takes an hour and saves you days of panic during a real disaster. The [Disaster Recovery](disaster-recovery.md) page covers the restore side in full.
