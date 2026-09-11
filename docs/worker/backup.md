# Backups

!!! note "Not a worker container"
    There is no `backup` service in any compose file. Backups are performed by the host-side script `scripts/backup.sh`, scheduled by systemd timers (`deployment/systemd/`). It runs on the host rather than in a container because it needs the Docker socket and the project's storage directories directly. This page stays in the worker section for discoverability; the API surface that exposes backup history lives on the [archiver](archiver.md) worker.

`scripts/backup.sh` covers MySQL (full + incremental), Redis, mail storage, secrets, DKIM keys, SSL certificates, and configuration files. Backups are written to local disk and optionally uploaded to S3, client-side encrypted with `age`.

## What It Does

- **MySQL**: full dumps and binlog-based incrementals
- **Redis**: data snapshot
- **Mail storage**: archive of `storage/mail_data` (the Maildir tree)
- **Secrets / DKIM / SSL / config**: archived; `secrets/` is always encrypted, and the script refuses `--no-encrypt` when secrets are included
- **Run recording**: every run is recorded in the `backup_history` MySQL table, which `GET /backup/history` on the archiver worker reads
- **Retention**: keeps `BACKUP_RETENTION_FULLS` (default 3) local full backups once offsite upload works, with an age-based prune fallback (`BACKUP_RETENTION_DAYS`, default 30) when offsite is down

The pipeline order is deliberate: components → verify → manifest → encrypt → upload → prune. Verification reads inside gzip/tar, which is impossible after encryption, and pruning only happens aggressively after a successful upload -- a broken offsite path can never cost the local copies too.

## Usage

```bash
./scripts/backup.sh [--full | --incremental | --mysql-only | --redis-only |
                     --mail-only | --secrets-only | --config-only |
                     --pre-deploy | --verify | --no-upload | --no-encrypt]
```

`--pre-deploy` (database + config only, local, no upload) is invoked by `deployment/deploy.sh` before every release.

## Scheduling

Systemd timers, installed by `deployment/systemd/install-timers.sh`:

| Timer | Schedule | Runs |
|-------|----------|------|
| `mailyte-backup-full` | Daily at 02:30 | `backup.sh --full` |
| `mailyte-backup-incremental` | Hourly at :15 | `backup.sh --incremental` |

## Configuration

Offsite settings live in `secrets/dr.env` (not `config/`, not `.env` -- `secrets/` and `storage/` are symlinked through deploys, so they survive a release):

```
S3_BUCKET=...
S3_PREFIX=mailyte/backups
S3_ENDPOINT_URL=            # empty for real AWS; set for MinIO/R2/B2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
DR_AGE_RECIPIENT=age1...    # PUBLIC key only -- the identity never lands on this host
```

The rest comes from the environment:

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_DIR` | `<project_root>/storage/backups` | Local backup destination |
| `DB_PASSWORD` | (required) | For the MySQL dump |
| `DB_NAME` | `mailyte_mail` | Database to dump |
| `MAIL_DATA_DIR` | `/var/mail/vhosts` | Mail storage to back up |
| `BACKUP_RETENTION_FULLS` | `3` | Local full backups kept once offsite works |
| `BACKUP_RETENTION_DAYS` | `30` | Fallback age-based prune when offsite is down |

Encryption uses `age` in public-key mode on purpose: a compromised host holds only the public recipient key and cannot read its own backups. The matching identity is kept offsite (see `scripts/escrow-secrets.sh` and `plans/06-operations/00-PRD-disaster-recovery.md`).

## Restore

Use `scripts/restore.sh` -- it is the tested inverse of `backup.sh`, and `scripts/dr-drill.sh` exercises the whole cycle. Do not hand-untar mail storage over a live Dovecot.

## Gotchas

!!! warning "Backup size"
    Mail storage backups can be very large. Incremental runs exist for exactly this reason -- do not schedule hourly fulls.

!!! warning "Offsite is configuration, not default"
    With `secrets/dr.env` absent or incomplete, backups still run but never leave the server. `backup.sh --verify` and the `backup_history` table (status column) are the way to confirm what is actually happening.

!!! tip "Test your restores"
    A backup you have never restored is not a backup. `scripts/dr-drill.sh` exists to make that routine.
