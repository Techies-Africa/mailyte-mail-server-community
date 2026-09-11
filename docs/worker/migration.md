---
edition: enterprise
---

# Migration Worker

The migration worker moves mailboxes from external IMAP servers into Mailyte (and exports them back out). It is a FastAPI service that runs migration jobs on background worker threads with MySQL-backed job state, so progress survives restarts and is queryable in real time.

## What It Does

- **Full mailbox migration** from any IMAP-compatible source
- **Incremental delta sync** for cutover windows -- re-run against the same job to pick up only new messages
- **Bulk migration** from account lists, with a concurrency cap so a burst of jobs doesn't open every source connection at once
- **Export jobs** (single and bulk) out of Mailyte over IMAP
- **Folder mapping** (e.g. source `INBOX.Sent` -> target `Sent`)
- **Per-message error handling** -- skip-and-continue, with an error listing and a retry-failed operation per job
- **Progress tracking** with speed metrics
- Webhook events: `migration.started` / `migration.progress` / `migration.completed` / `migration.failed` / `migration.cancelled`

## API Endpoints

Copied from the route decorators in `worker/migration/app.py`:

```
POST /migrate                        -- Start a migration job (201)
GET  /migrate                        -- List jobs
GET  /migrate/{job_id}               -- Job status and progress
POST /migrate/{job_id}/cancel        -- Cancel a running job
POST /migrate/bulk                   -- Start bulk migrations (201)
POST /migrate/{job_id}/delta         -- Incremental delta sync for a completed job
POST /migrate/{job_id}/retry-failed  -- Retry the messages that failed (201)
GET  /migrate/{job_id}/errors        -- Per-message error list
POST /export                         -- Start an export job (201)
POST /export/bulk                    -- Start bulk exports (201)
GET  /health                         -- Health check
GET  /metrics                        -- Prometheus metrics
```

The API gateway fronts tenant-facing migration management at `/api/v1/migration/*` (`worker/api/routes/migration.py`).

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8099` | Bind port |
| `IMAP_HOST` / `IMAP_PORT` | `dovecot` / `993` | The migration **target** (this server's Dovecot) |
| `MIGRATION_MAX_CONCURRENT_JOBS` | `5` | Cap on simultaneously running import/export jobs; bulk requests queue the rest |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `mysql` / `3306` / `mailserver` / -- / -- | Job state persistence |

## Docker Configuration

```yaml
migration:
  build:
    context: .
    dockerfile: ./worker/migration/Dockerfile
  container_name: migration
  ports:
    - "8099:8099"
  depends_on:
    - mysql
    - migrate
```

In production (`docker-compose.prod.yml`) the host port is bound to `127.0.0.1` only.

## Gotchas

!!! warning "Be conservative with concurrency against live sources"
    Each job holds IMAP connections open on the source server. When the source is a production system you don't control the load on, keep `MIGRATION_MAX_CONCURRENT_JOBS` low.

!!! warning "IMAP migration needs working source credentials"
    This service logs into the source over IMAP -- never reset live source-system mailbox passwords just to run a migration; that breaks the users still on the old system. For sources where credentials aren't available, a filesystem-level Maildir sync (see `scripts/mail-sync.sh` and `scripts/migrate_maildir_from_mailcow.sh`) is the alternative path -- with the caveat that encrypted-at-rest sources (e.g. mailcow's `mail_crypt`) need their keys migrated too.
