# Backups

**Set this up before you put real mail on the server.** It takes one command.

`scripts/backup.sh` covers MySQL, the mail store, Redis, DKIM keys, SSL
certificates and configuration. Historically nothing ran it — which in practice
means no backups at all, discovered at the worst possible moment.

---

## 1. Turn it on

```bash
sudo ./deployment/systemd/install-timers.sh
```

That installs and enables two systemd timers:

| Timer | Schedule | What it captures |
|---|---|---|
| `mailyte-backup-full` | 02:30 daily | MySQL dump, mail store, Redis, DKIM, SSL, config |
| `mailyte-backup-incremental` | hourly at :15 | MySQL binlog + mail changed since the last run |

Verify:

```bash
sudo ./deployment/systemd/install-timers.sh --status
sudo systemctl start mailyte-backup-full.service   # run one now
journalctl -u mailyte-backup-full -f
ls -la storage/backups/
```

Host systemd is used rather than a container on purpose: a timer survives
`docker compose down`, and `backup.sh` needs the Docker socket and the
`storage/` tree from the host anyway.

---

## 2. What you have now, and what you do not

Backups land in `storage/backups/<timestamp>/` **on this machine**.

That protects you from the things that go wrong most often: a bad migration, a
deletion in the wrong directory, a corrupted table, a config change you want to
undo.

It does **not** protect you from losing the machine. Disk failure, a provider
account problem, or ransomware takes the backups with the data, because they are
on the same disk. For that you need a copy somewhere else.

---

## 3. Add an offsite copy (recommended)

`backup.sh` uploads to any S3-compatible storage. Set four values in `.env`:

```bash
S3_BUCKET=your-backup-bucket
S3_PREFIX=mailyte/backups
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-west-1
```

Install the AWS CLI (`backup.sh` skips the upload and says so if it is missing),
then run a backup and confirm the manifest names your bucket:

```bash
sudo systemctl start mailyte-backup-full.service
grep -A3 '"s3"' storage/backups/*/MANIFEST.json | tail -5
aws s3 ls "s3://${S3_BUCKET}/mailyte/backups/"
```

Works with AWS S3, Cloudflare R2, Backblaze B2, Wasabi or MinIO — non-AWS
providers need `--endpoint-url`, which you can set through the CLI's usual
`AWS_ENDPOINT_URL` environment variable.

**Give the upload credential `PutObject` but not `DeleteObject`.** A server that
is compromised can then add to your backup history but not erase it, which is
the difference between an incident and a catastrophe. If your provider supports
object locking or versioning, turn it on.

---

## 4. Two things worth knowing before you need them

**Your backups contain private keys.** The archive includes every DKIM private
key and your `.env`. Anyone who can read a backup can sign mail as your domains.
Keep the directory at mode 700, and encrypt before uploading anywhere shared:

```bash
# One-off example with age (https://age-encryption.org)
age -r <your-public-key> -o backup.tar.age backup.tar
```

Keep the decryption key **somewhere other than this server**. A key stored
next to the backups it protects protects nothing.

**An untested backup is a hypothesis.** Restore into a scratch environment
before you are relying on it:

```bash
./scripts/restore.sh --list
./scripts/restore.sh --date <backup_id> --dry-run
```

Check afterwards that mailbox and domain counts match what you expect, and that
you can actually read a message — not just that the commands exited 0.

---

## 5. Retention

`BACKUP_RETENTION_DAYS` in `.env` (default 30) controls how long local backups
are kept. A full backup is roughly the size of your mail store, so watch disk:

```bash
du -sh storage/backups/
df -h .
```

If the disk fills, backups fail and mail delivery can fail with it. Either lower
the retention or move the backups off the machine (§3).
