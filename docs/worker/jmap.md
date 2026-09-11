---
edition: enterprise
---

# JMAP Worker

The JMAP worker implements **JMAP Core (RFC 8620)** and **JMAP Mail (RFC 8621)** -- the modern JSON-over-HTTP alternative to IMAP polling. It is a FastAPI service that fronts Dovecot: mailbox data is read and written over IMAPS using the IMAP master user for impersonation, so JMAP and IMAP clients always see the same mailboxes.

## What It Does

- **Session resource** at `/.well-known/jmap` (RFC 8620 §2)
- **JMAP API endpoint** at `/jmap` -- method calls for Mailbox, Email, Thread, and friends (RFC 8620 §3.3)
- **Blob upload/download** for attachments
- **Push via Server-Sent Events** at `/jmap/eventsource` (RFC 8620 §7.3) -- state changes are published to Redis (`jmap:push:{account_id}`) and streamed to subscribers
- Backed by Dovecot over IMAPS (`dovecot:993`) with master-user impersonation; Redis for push state

## API Endpoints

Copied from the route decorators in `worker/jmap/app.py`:

```
GET  /.well-known/jmap                              -- JMAP Session resource
POST /jmap                                          -- JMAP API endpoint (method calls)
POST /jmap/upload/{account_id}/                     -- Blob upload
GET  /jmap/download/{account_id}/{blob_id}/{name}   -- Blob download
GET  /jmap/eventsource                              -- Push (Server-Sent Events)
GET  /                                              -- Service info
GET  /health                                        -- Health check
GET  /metrics                                       -- Prometheus metrics
```

## Configuration

| Variable                                                                | Default                                         | Description                          |
| ----------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------ |
| `PORT`                                                                | `8098`                                        | Bind port                            |
| `IMAP_HOST` / `IMAP_PORT`                                           | `dovecot` / `993`                           | Dovecot backend                      |
| `IMAP_MASTER_USER` / `IMAP_MASTER_PASSWORD`                         | --                                              | Master credentials for impersonation |
| `REDIS_HOST` / `REDIS_PORT`                                         | `redis` / `6379`                            | Push state                           |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `mysql` / `3306` / `mailserver` / -- / -- | Account lookups                      |
| `ADMIN_TOKEN_SECRET`                                                  | --                                              | Admin auth                           |
| `CORS_ALLOWED_ORIGINS`                                                | --                                              | Browser clients                      |
| `LOG_LEVEL`                                                           | `INFO`                                        | Logging                              |

## Docker Configuration

```yaml
jmap:
  build: ./worker/jmap
  container_name: jmap
  ports:
    - "8098:8098"
  volumes:
    - ./shared:/app/shared     # build context excludes repo-root shared/
  depends_on:
    - mysql
    - migrate
    - redis
```

In production the host port is bound to `127.0.0.1`; Traefik routes `jmap.${DOMAIN}` here over HTTPS, which is the hostname the session resource advertises for API, upload, download, and eventsource URLs.

## Gotchas

!!! warning "Dovecot capability requirements"
    Master-user impersonation and per-login chroots mean Dovecot's capability set matters: `SYS_CHROOT` was found missing once and every login process crash-looped (see the comments on the `dovecot` service in `docker-compose.yml`). If JMAP suddenly cannot open any mailbox, check Dovecot's own health before this worker's.
