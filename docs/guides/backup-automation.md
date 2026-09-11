---
title: Backup Automation
description: Run and schedule Mailyte's built-in backup tooling — scripts/backup.sh, systemd timers, age encryption, S3 offsite, key escrow, and restore drills.
---

# Backup Automation

Losing email data is catastrophic. Mailyte ships a complete backup/restore toolchain — you configure and schedule it rather than writing your own scripts. This guide covers the pieces and the commands; the authoritative operational runbook is [Disaster Recovery](../operations/disaster-recovery.md).

## What Gets Backed Up

`scripts/backup.sh` (with its component library in `scripts/lib/backup_components.sh`) backs up seven components into a timestamped directory under `storage/backups/<YYYYMMDD_HHMMSS>/`:

| Component | What | Output |
|-----------|------|--------|
| MySQL full | `mysqldump` of the whole database, run inside the `mysql` container | `mysql/<db>_full_<date>.sql.gz` |
| MySQL incremental | Binlog delta since the last run (falls back to a full dump) | `mysql/binlogs/incremental_<date>.sql.gz` |
| Redis | RDB snapshot (+ AOF when present) | `redis/dump_<date>.rdb` |
| Mail storage | Maildirs from `storage/mail_data/` | `mail/mail_data_<date>.tar.gz` |
| DKIM keys | `storage/dkim_keys/` | `dkim/dkim_keys_<date>.tar.gz` |
| SSL certificates | `storage/ssl_certs/` + `storage/ssl_private/` | `ssl/ssl_certs_<date>.tar.gz` |
| Configuration | `config/`, `.env`, compose files, `deployment/`, migrations | `config/config_<date>.tar.gz` |
| **Secrets** | `secrets/` (mail_crypt keys, encryption KEK, DB root password) | `secrets/secrets_<date>.tar.age` — never written in plaintext |

Qdrant vectors, Kafka, and Radicale data are deliberately **not** in scope (rebuildable / Tier 2).

Every run writes a `MANIFEST.json`, records a row in the `backup_history` table, and logs to `storage/backups/logs/backup_<date>.log`. The pipeline order is load-bearing: **components → verify → manifest → encrypt → upload → prune** — verification reads inside the archives, so it must happen before encryption.

## Running Backups

```bash
# Full backup: everything, encrypted, uploaded to S3
./scripts/backup.sh --full

# Hourly incremental (binlog + changed mail; skips secrets by design)
./scripts/backup.sh --incremental

# Single components
./scripts/backup.sh --mysql-only
./scripts/backup.sh --redis-only
./scripts/backup.sh --mail-only
./scripts/backup.sh --secrets-only
./scripts/backup.sh --config-only     # config + dkim + ssl

# Before a risky change: db + config + dkim + ssl, local only
./scripts/backup.sh --pre-deploy

# Local test run (skip S3), and verification of the newest backup
./scripts/backup.sh --full --no-upload
./scripts/backup.sh --verify
```

The script runs **on the host** (it needs the Docker socket and the `storage/` tree), from the repo root or the deployment's `current` symlink. Credentials come from the environment or are auto-loaded from `.env`. A full run exits non-zero if the MySQL dump or mail archive is missing, or if nothing reached S3 — treat a non-zero exit as a failed backup.

!!! note "`--no-encrypt` drops the secrets component"
    Passing `--no-encrypt` doesn't write secrets in plaintext — it skips them entirely. Key material only ever leaves the host age-encrypted.

!!! note "Not the same as `./start.sh db-backup`"
    `./start.sh db-backup` / `db-restore` is a separate, much simpler path: a plain uncompressed, unencrypted `mysqldump` into `storage/backups/`, never uploaded, no retention. Convenient for a quick local snapshot — it is **not** the DR pipeline.

## Offsite Storage (S3)

Offsite upload uses the AWS CLI (`aws s3 cp`), configured in **`secrets/dr.env`** (mode 600, never committed — `config/` is replaced on every deploy, `secrets/` survives):

```bash
# secrets/dr.env
S3_BUCKET=mailyte-ent
S3_PREFIX=mailyte/backups
S3_ENDPOINT_URL=              # empty for AWS; set for MinIO/R2/B2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-west-2
DR_AGE_RECIPIENT=age1...      # PUBLIC key only — see Encryption below
```

Uploads land under `s3://<bucket>/<prefix>/mail/<hostname>/<date>/` with storage class `STANDARD_IA` (automatically retried without the class for S3-compatible stores that reject it). Offsite is considered configured when `S3_BUCKET` is set and `aws` is on the PATH — otherwise backups stay local and a full run fails its upload requirement.

A separate 15-minute job, `scripts/mail-sync.sh`, keeps maildir state synced to S3 with `aws s3 sync` (`--dry-run` supported).

## Encryption and Key Escrow

Backups are encrypted with **age** in public-key mode: the server holds only the *recipient* (public key) in `DR_AGE_RECIPIENT`; the identity (private key) is deliberately **not on the host**. Everything except `MANIFEST.json` and logs is encrypted before upload.

Escrow the key material that makes backups decryptable — mail_crypt keys, the encryption KEK, DB root password, `.env`, DKIM keys — with:

```bash
bash ./scripts/escrow-secrets.sh --verify      # list what would be bundled
bash ./scripts/escrow-secrets.sh --role mail   # encrypt + upload to escrow/ prefix
```

The bundle is age-encrypted before it touches disk and uploaded to `escrow/<role>/<host>/` (storage class `STANDARD`, plus `latest.tar.age`). Without the escrowed age identity, every backup is undecryptable ciphertext — treat the identity file as the crown jewels and keep it off both production hosts.

## Scheduling: systemd Timers (not cron)

Scheduling is done with systemd timers — they survive `docker compose down` and deploys, and the script must run host-side anyway. Units live in `deployment/systemd/`:

| Timer | Schedule | Runs |
|-------|----------|------|
| `mailyte-backup-full` | daily 02:30 | `backup.sh --full` |
| `mailyte-backup-incremental` | hourly at :15 | `backup.sh --incremental` |
| `mailyte-mail-sync` | every 15 min | `mail-sync.sh` |

Install and inspect:

```bash
sudo ./deployment/systemd/install-timers.sh          # role auto-detected
     ./deployment/systemd/install-timers.sh --status
systemctl list-timers 'mailyte-*' --all

# Run one manually and watch it
sudo systemctl start mailyte-backup-full.service
journalctl -u mailyte-backup -f
```

The full-backup unit runs from the deployment's `current` symlink with a 6-hour timeout and sets `BACKUP_RETENTION_DAYS=7`; the incremental unit conflicts with the full unit so they never overlap.

## Retention

- Local: after a successful full backup with a completed upload, the last `BACKUP_RETENTION_FULLS` (default 3) full backups are kept and older partials age out on `BACKUP_RETENTION_DAYS`. Without a confirmed upload, only conservative age-based pruning runs.
- "Full" is decided by reading `MANIFEST.json` (`"type": "full"` plus a mail-storage component), not by counting directories.
- S3 retention is handled by bucket lifecycle rules, managed outside the script.

## Restoring

`scripts/restore.sh` handles listing, downloading, decrypting, and restoring:

```bash
# What's restorable, locally and in S3
./scripts/restore.sh --list

# Rehearse without changing anything
./scripts/restore.sh --latest --dry-run

# Full restore from S3 with the escrowed age identity
./scripts/restore.sh --from-s3 20260825_023000 \
  --host server1 \
  --identity /path/to/escrowed-identity.txt

# Database only, without restarting services
./scripts/restore.sh --latest --identity /path/to/identity.txt --mysql-only --no-restart

# Single tenant — REPLACE INTO scoped by organization, services stay up
./scripts/restore.sh --latest --identity /path/to/identity.txt --organization ORG_ID
```

Component flags mirror the backup side (`--mysql-only`, `--redis-only`, `--mail-only`, `--dkim-only`, `--ssl-only`, `--config-only`). Every destructive step asks for confirmation (`--force` to skip; `--dry-run` to preview). Encrypted backups **hard-fail without `--identity`** — fetch the escrowed identity first; it is deliberately not on the host. MySQL restores also apply any incremental binlog dumps in order, and mail restores take a safety copy of the current maildir before extracting.

## Restore Drills

Prove the whole chain works from S3 alone — never against production:

```bash
DR_AGE_IDENTITY_FILE=~/.mailyte-dr/dr-age-identity.txt \
DR_CRED_DIR=~/.mailyte-dr \
S3_ENDPOINT_URL= \
./scripts/dr-drill.sh
```

The drill downloads the latest backup, decrypts it with the escrowed identity, restores MySQL into a throwaway container, and reads a real message through mail_crypt using the escrow-bundle key — printing PASS/FAIL counts and a measured RTO. (Unset/override `S3_ENDPOINT_URL`: the script's default still points at a local MinIO used during development.) Run it quarterly.

## Monitoring Backups

Backup health is wired into the monitoring stack — `monitoring/prometheus/rules/backup_alerts.yml` defines nine absence-first alerts, including:

- `NoRecentFullBackup` (critical) / `NoRecentIncrementalBackup` (warning)
- `BackupLastRunFailed`, `BackupNeverRun`
- `UnencryptedBackupsPresent`
- `ArchiveSpoolNotDraining` / `ArchiveStoreFailing`

Alerts route through Alertmanager to the webhooks service. If backups stop, the four causes to check first (in order, per the runbook): a missing `secrets/dr.env` after a host rebuild, rotated S3 credentials, a full disk, and timers that were never installed — `systemctl list-timers 'mailyte-backup-*'`.

## Deploy Integration

`deployment/deploy.sh` takes a backup before every release automatically: `--pre-deploy` when database migrations are pending, a lightweight `--config-only --no-upload` otherwise, and a full backup when `PREDEPLOY_FULL_BACKUP=1` is set. It also warns (non-fatally) when no full backup has succeeded in the last 48 hours.

## Backup Checklist

- [x] `secrets/dr.env` populated (bucket, credentials, `DR_AGE_RECIPIENT`)
- [x] `aws` CLI and `age` installed on the host
- [x] systemd timers installed (`install-timers.sh --status` shows them)
- [x] Secrets escrowed (`escrow-secrets.sh`), identity stored off-host
- [x] `./scripts/backup.sh --verify` passes
- [x] `./scripts/restore.sh --list` shows local **and** S3 backups
- [x] A restore drill has passed (`dr-drill.sh`)
- [x] Backup alerts firing into your notification channel
