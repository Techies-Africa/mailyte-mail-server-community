# Disaster Recovery

When things go really wrong — database corruption, server failure, or accidental deletion — here's how to get back up.

!!! info "The operational runbook is the authority"
    This page explains the mechanics. During a real incident, use the operational runbook at `docs/operations/disaster-recovery.md` in the repository — it is drilled, reviewed, and lists the exact escrow/backup locations for the production deployment. The design rationale lives in `plans/06-operations/00-PRD-disaster-recovery.md`.

## Before You Restore Anything

**Do not start restoring until you know what actually broke.** A restore over healthy data is worse than the outage. Senders retry for 48-72 hours against a dead MX — a mail server that is down is not losing mail; a mail server restored badly is.

```bash
# What's running?
docker compose ps -a

# Is mail flowing? Empty queue + recent log lines = mail is fine,
# your problem is somewhere else.
docker compose exec postfix postqueue -p | tail -3
tail -5 logs/mailer/postfix/mail.log

# Disk, Docker, system
df -h
systemctl status docker
journalctl --since "1 hour ago" | tail -50
```

## What Exists, and Where

| Thing | Where | Restored by |
|-------|-------|-------------|
| MySQL, Redis, mail storage, DKIM, SSL, config, secrets | `storage/backups/<backup_id>/` locally, mirrored to S3 (age-encrypted) | `scripts/restore.sh` |
| The unrecoverable secrets (age identity, `encryption_kek`, mail_crypt keys, `.env`) | The escrow bundle written by `scripts/escrow-secrets.sh` | Manual unpack, then `restore.sh` |
| Maildir folder/flag state, at most 15 minutes old | S3 mail-state sync (`mailyte-mail-sync.timer`) | `scripts/mail-sync.sh` tooling |
| Per-message raw archive | The archiver's S3 bucket | `GET /api/v1/.../archive` or bulk copy |

## The Restore Tool

Every scenario below goes through `scripts/restore.sh`:

```bash
./scripts/restore.sh --list                      # what backups exist
./scripts/restore.sh --latest --dry-run          # rehearse, touch nothing
./scripts/restore.sh --latest                    # full restore, latest set
./scripts/restore.sh --date 20260829_023000      # a specific set
./scripts/restore.sh --from-s3 <BACKUP_ID> --identity /path/to/age-identity
                                                 # download + decrypt + restore
# Component restores:
./scripts/restore.sh --latest --mysql-only       # also: --redis-only, --mail-only,
                                                 # --dkim-only, --ssl-only, --config-only
# Tenant-scoped restore:
./scripts/restore.sh --latest --organization <ORG_ID>
```

Restoring from S3 needs the **age identity** — the private key that never lives on the server. It comes from escrow. If you cannot locate the escrow bundle, stop and find it before doing anything else.

## Scenario 1: Database Corruption or Loss

```bash
# 1. Rehearse
./scripts/restore.sh --latest --mysql-only --dry-run

# 2. Restore (the script stops/starts dependent services around the import)
./scripts/restore.sh --latest --mysql-only

# 3. Verify
docker compose exec -T mysql sh -c \
  'mysql -u root -p"$(cat /run/secrets/db_root_password)" "${MYSQL_DATABASE:-mailserver}"' <<'SQL'
SELECT COUNT(*) AS organizations FROM organizations;
SELECT COUNT(*) AS domains FROM domains;
SELECT COUNT(*) AS accounts FROM email_accounts;
SQL

# 4. Bring the stack back to full health
docker compose up -d
```

An incremental chain (hourly binlogs) narrows data loss to at most an hour beyond the last full dump.

## Scenario 2: Mail Data Recovery

Mail lives in the bind-mounted `storage/mail_data/` directory (not a Docker named volume).

```bash
# 1. Stop mail services so nothing writes mid-restore
docker compose stop postfix dovecot

# 2. Restore the Maildirs
./scripts/restore.sh --latest --mail-only

# 3. Restart and verify a known mailbox
docker compose up -d postfix dovecot
docker compose exec dovecot doveadm mailbox list -u someone@yourdomain.com
```

For folder/flag state newer than the last backup, the 15-minute S3 mail-state sync (`mailyte-mail-sync.timer`) has a fresher copy.

!!! warning "Encrypted mailboxes"
    Mailboxes migrated from a mail_crypt-encrypted source need the mail_crypt keys (in `secrets/mail_crypt`, escrowed) or every message FETCH fails while SEARCH still counts them — mailboxes look full and read empty. Restore the keys before concluding the mail data is bad.

## Scenario 3: Full Server Rebuild

The entire server is gone. Starting from a new machine.

```bash
# 1. Set up the new server
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 2. Clone the repository
git clone https://github.com/Techies-Africa/mailyte-email-server.git
cd mailyte-email-server

# 3. Recover the escrow bundle (from your escrow location, NOT from this
#    server's backups) and unpack it: .env, secrets/encryption_kek,
#    secrets/mail_crypt, the age identity, secrets/dr.env

# 4. Restore everything from S3
./scripts/restore.sh --from-s3 <BACKUP_ID> --identity /path/to/age-identity

# 5. Start the stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 6. Update DNS if the IP changed (A record + PTR), and CERT_SERVER_IPS in .env

# 7. Reinstall the backup timers -- a restored server with no backups is
#    one incident away from an unrecoverable one
sudo ./deployment/systemd/install-timers.sh
```

## Scenario 4: Single Organization Restore

Someone deleted a tenant's data, and every other tenant is fine. Do **not** roll the whole database back:

```bash
./scripts/restore.sh --latest --organization <ORG_ID> --dry-run
./scripts/restore.sh --latest --organization <ORG_ID>
```

## Post-Recovery Checklist

- [ ] All containers running: `docker compose ps` (`secrets-check`/`migrate` exited 0)
- [ ] API healthy: `curl -s https://api.yourdomain.com/health`
- [ ] Database queries work (see Scenario 1 verification)
- [ ] Can send email: authenticated submission on 587
- [ ] Can receive email: send from an external address
- [ ] Can read email: IMAP login, and message *content* opens (not just counts — catches missing mail_crypt keys)
- [ ] Monitoring is working: Grafana + Prometheus targets
- [ ] Backup timers re-enabled: `systemctl list-timers 'mailyte-backup-*'`
- [ ] DNS is correct: `dig mail.yourdomain.com` and `dig -x <IP>`
- [ ] TLS certificates valid: `openssl s_client -connect mail.yourdomain.com:993`

## Prevention

1. **Test backups monthly** — `./scripts/restore.sh --latest --dry-run`, and a full `./scripts/dr-drill.sh` periodically
2. **Keep the escrow current** — re-run `./scripts/escrow-secrets.sh` whenever a secret changes
3. **Monitor backup freshness** — `backup_alerts.yml` fires when backups go stale; make sure someone receives it
4. **Watch disk space** — alert at 80%
5. **Keep the runbook reachable when the server is not** — `docs/operations/disaster-recovery.md` printed, or in a shared doc

> **Tip:** The single most dangerous asset is `secrets/encryption_kek` and the mail_crypt keys — no quantity of backups compensates for losing them. Verify the escrow bundle exists *today*.
