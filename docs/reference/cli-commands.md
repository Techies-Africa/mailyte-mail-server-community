---
title: CLI Commands
description: The management CLIs (start.sh, manage.py, scripts/) plus docker exec commands for Postfix, Dovecot, Rspamd, MySQL, and Redis.
---

# CLI Commands

Mailyte has three layers of command-line tooling: the stack control CLI (`./start.sh` → `scripts/mailyte-ctl.sh`), the migration wrapper (`manage.py`), and the operational scripts in `scripts/`. Below those, the usual `docker exec` commands work against the containers (container names match the compose service names: `postfix`, `dovecot`, `rspamd`, `mysql`, `redis`, …).

## Stack Control — `./start.sh`

Run with no arguments for an interactive menu, or pass a command (everything except `test` is routed to `scripts/mailyte-ctl.sh`):

```bash
./start.sh <command> [flags] [services...]
```

| Group | Commands |
|-------|----------|
| Modes | `dev`, `prod`, `cloud` |
| Services | `start`, `stop`, `restart`, `rebuild`, `status` (`ps`), `logs`, `health`, `shell <service>`, `exec <service> <cmd...>`, `stats` |
| Database | `db-shell`, `db-backup`, `db-restore <file>`, `db-status` |
| Mail | `mail-queue`, `mail-flush`, `mail-test <email>` |
| Maintenance | `update`, `clean`, `reset [--force]`, `migrate`, `migrate-status`, `dkim`, `console-token` |
| Info | `services`, `ports`, `version`, `config`, `diagnostic`, `setup` |
| Flags | `--rebuild`, `--no-cache`, `--clean`, `--force`/`-f`, `--dev`, `--prod`, `--cloud` |
| Tests | `./start.sh test` |

## Database Migrations — `manage.py`

Laravel-style wrapper around Alembic (uses `.venv/bin/alembic` when present):

```bash
python manage.py migrate                    # alembic upgrade head
python manage.py migrate:rollback --steps 1 # alembic downgrade -1
python manage.py migrate:status             # alembic history --verbose
python manage.py migrate:current            # alembic current --verbose
python manage.py migrate:create <name>      # alembic revision --autogenerate
python manage.py migrate:fresh [--force]    # downgrade base + upgrade head (prompts!)
python manage.py migrate:reset [--force]    # downgrade base
```

In the running stack, migrations are applied by the dedicated **`migrate` compose service** (its own image, `worker/api/Dockerfile.migrate`, running `scripts/run_migrations.py`) — it runs once per `docker compose up`, and dependent services wait for it via `service_completed_successfully`. To apply new migrations to a live deployment:

```bash
docker compose build migrate && docker compose run --rm migrate
```

> [!WARNING]
> The `migrate` image goes stale like any other: if you pull new migrations, **rebuild it first** — a stale image silently no-ops.

## Operational Scripts — `scripts/`

| Script | Purpose |
|--------|---------|
| `generate-secrets.sh` | Write a complete `.env` with strong random secrets (the 8 required ones) |
| `generate_dkim.py` | DKIM key management: `python3 scripts/generate_dkim.py <domain> [--selector default] [--key-size 2048] [--all] [--rotate DOMAIN] [--rotate-all] [--dns DOMAIN] [--update-map]` |
| `sync_rspamd_settings.py` | Sync per-org spam policies MySQL → Redis: `[--watch] [--org <id>] [--interval N]` |
| `setup-first-user.sh` | Bootstrap first org/domain/mailbox/API key via `POST /api/v1/bootstrap` (env `API_BASE`, default `http://localhost:8083`) |
| `quick-start.sh` | Guided first-run setup |
| `staged-startup.sh` | Bring the stack up in 8 dependency-ordered stages |
| `docker-dev.sh` | Dev compose up/down/restart/logs |
| `mailyte-monitor.sh` | Container monitor: `status`, `logs [svc]`, `errors`, `health`, `resources`, `fix` |
| `container-health-monitor.py` | `[--no-fix] [--continuous] [--interval 60]` — detect and auto-fix container issues |
| `diagnostic.py` | Full system diagnostic |
| `backup.sh` | Backups (MySQL full/incremental, Redis, mail, secrets, DKIM, SSL, config → disk + S3, age-encrypted): `[--full] [--incremental] [--mysql-only] [--pre-deploy] [--verify] [--no-upload] [--no-encrypt] ...` |
| `restore.sh` | Restore: `[--list] [--latest] [--date TS] [--from-s3 ID] [--organization ORG_ID] [--dry-run] ...` |
| `dr-drill.sh` | Disaster-recovery drill from S3 + escrowed age identity only |
| `escrow-secrets.sh` | Age-encrypt and ship the secrets bundle offsite: `[--role mail|web] [--local-only] [--verify]` |
| `mail-sync.sh` | Incremental Maildir → S3 sync (`[--dry-run]`) |
| `extract_from_mailcow.sh` / `migrate_maildir_from_mailcow.sh` / `apply_mailcow_password_hashes.sh` | Mailcow migration trio (dump metadata on the source; rsync Maildirs; import bcrypt hashes) |
| `migrate_mailbox_settings_from_laravel.py` | One-time settings import: `--dry-run` \| `--apply` (required, mutually exclusive) |
| `dns/cloudflare_apply.sh` | Apply MX/SPF/DKIM/DMARC across Cloudflare zones — **dry-run by default**; `--apply`, `--prune-mx`; needs `CLOUDFLARE_API_TOKEN` |
| `dns/export_dkim_records.sh` | Export every domain's DKIM public key (TSV or `--zonefile`) |
| `test_runner.py` | `python3 scripts/test_runner.py [unit|integration|api|lint|deps|all|quick]` |
| `run_mypy.sh` | Per-unit mypy sweep for the CI baseline gate |

Deployment lives outside `scripts/`: `deployment/deploy.sh` (production deploy; `FORCE_REBUILD=1` for `--no-cache`) and `deployment/systemd/install-timers.sh` (backup/mail-sync systemd timers).

## Postfix

### Queue Management

```bash
docker exec postfix postqueue -p            # view the queue
docker exec postfix postqueue -f            # flush (retry all deferred)
docker exec postfix postcat -q QUEUE_ID     # view a queued message
docker exec postfix postsuper -d QUEUE_ID   # delete one message
docker exec postfix postsuper -d ALL deferred  # delete all deferred
docker exec postfix postsuper -h QUEUE_ID   # hold
docker exec postfix postsuper -H QUEUE_ID   # release
docker exec postfix postsuper -r QUEUE_ID   # requeue
```

### Configuration

```bash
docker exec postfix postconf -n             # non-default settings
docker exec postfix postconf smtpd_milters  # one setting
docker exec postfix postfix check           # validate
docker exec postfix postfix reload          # reload without restart
```

### Logs

```bash
docker exec postfix tail -100 /var/log/postfix/mail.log
docker exec postfix grep "user@example.com" /var/log/postfix/mail.log
docker exec postfix grep "QUEUE_ID" /var/log/postfix/mail.log
```

## Dovecot

### User Management

```bash
docker exec dovecot doveadm who                          # active sessions
docker exec dovecot doveadm user user@example.com        # userdb lookup
docker exec dovecot doveadm quota get -u user@example.com
docker exec dovecot doveadm quota recalc -u user@example.com
docker exec dovecot doveadm auth cache flush             # flush ALL cached credentials
docker exec dovecot doveadm auth cache flush user@example.com
```

> [!NOTE]
> The auth cache TTL is 1 hour — after deleting/suspending an account or changing a password by hand, flush the cache or the old credentials keep working.

### Mailbox Operations

```bash
docker exec dovecot doveadm mailbox list -u user@example.com
docker exec dovecot doveadm mailbox status -u user@example.com messages INBOX
docker exec dovecot doveadm search -u user@example.com mailbox INBOX subject "test"
docker exec dovecot doveadm index -u user@example.com INBOX
docker exec dovecot doveadm purge -u user@example.com
```

### Diagnostics

```bash
docker exec dovecot doveconf -n
docker exec dovecot doveadm log errors
docker exec dovecot doveadm reload
docker logs dovecot --tail 100        # Dovecot logs to stderr
```

## Rspamd

```bash
docker exec rspamd rspamc stat                    # statistics
docker exec rspamd rspamc counters               # per-symbol counters
docker exec -i rspamd rspamc learn_spam < msg.eml
docker exec -i rspamd rspamc learn_ham < msg.eml
docker exec -i rspamd rspamc < msg.eml           # score a message
docker exec rspamd rspamadm configtest           # validate config
docker exec rspamd rspamadm configdump           # effective config
docker exec rspamd ls -la /var/lib/rspamd/dkim/  # DKIM keys
```

## MySQL

The database port is **not** published to the host — go through the container. The root password lives in `secrets/db_root_password` (and as `DB_ROOT_PASSWORD` in `.env`):

```bash
# Interactive shell
docker exec -it mysql sh -c 'mysql -u root -p"$(cat /run/secrets/db_root_password)" mailserver'

# One-liner (root password from .env on the host)
source .env
docker exec mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "SELECT COUNT(*) FROM domains;"
```

### Useful Queries

```bash
# Entity counts
docker exec mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "
SELECT
  (SELECT COUNT(*) FROM organizations) AS orgs,
  (SELECT COUNT(*) FROM domains) AS domains,
  (SELECT COUNT(*) FROM email_accounts) AS mailboxes,
  (SELECT COUNT(*) FROM aliases) AS aliases,
  (SELECT COUNT(*) FROM smtp_credentials) AS smtp_keys;"

# Delivery outcomes, last 24h (populated by log_ingestor)
docker exec mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "
SELECT status, COUNT(*) FROM mail_logs
WHERE timestamp > NOW() - INTERVAL 1 DAY GROUP BY status;"

# Recent bounces
docker exec mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "
SELECT sender, recipient, bounce_reason, timestamp
FROM mail_logs WHERE status = 'bounced'
ORDER BY timestamp DESC LIMIT 10;"

# Storage usage by domain
docker exec mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "
SELECT domain, total_storage_used, total_email_accounts
FROM domains ORDER BY total_storage_used DESC LIMIT 10;"

# Current schema revision
docker exec mysql mysql -u root -p"$DB_ROOT_PASSWORD" mailserver -e "SELECT * FROM alembic_version;"
```

### Maintenance

```bash
docker exec mysql mysqlcheck -u root -p"$DB_ROOT_PASSWORD" mailserver
docker exec mysql mysqldump -u root -p"$DB_ROOT_PASSWORD" --single-transaction mailserver > backup.sql
```

(For real backups use `scripts/backup.sh`, which also covers Redis, Maildirs, DKIM keys, and secrets, and encrypts before upload.)

## Redis

```bash
docker exec -it redis redis-cli
docker exec redis redis-cli info memory
docker exec redis redis-cli --scan --pattern "rspamd_settings:*" | head
docker exec redis redis-cli monitor          # live command stream (verbose!)
```

## Docker Compose

```bash
docker compose up -d                 # start everything (add --profile console for the console)
docker compose down
docker compose restart postfix
docker compose logs -f postfix
docker compose up -d --build api     # rebuild + restart one service
docker compose run --rm migrate      # apply DB migrations
docker stats --no-stream
```

Compose service names (base file): `secrets-check`, `redis`, `mysql`, `migrate`, `docker-proxy`, `postfix`, `dovecot`, `rspamd`, `cert_manager`, `acme_webroot`, `api`, `webhooks`, `rate_limiter`, `tracking`, `monitoring`, `activesync`, `analytics`, `log_ingestor`, `archiver`, `dashboard`, `encryption`, `queue_manager`, `rag`, `qdrant`, `storage_usage`, `delivery_optimizer`, `templates`, `url_protection`, `oauth`, `zookeeper`, `kafka`, `radicale`, `caldav`, `jmap`, `migration` (the mailbox-migration worker — distinct from `migrate`), `autoconfig`, `prometheus`, `grafana`, `alertmanager`, `mysql-exporter`, `redis-exporter`, `dlp`, `totp`, `geo_blocking`, `docs`, plus the profile-gated `webmail`, `console`, `roundcube`, `sogo`.
