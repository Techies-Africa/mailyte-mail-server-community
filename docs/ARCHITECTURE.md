# Architecture

> This top-level file is a quick orientation for people browsing the repository.
> The maintained architecture documentation lives in the handbook under
> [`docs/architecture/`](architecture/index.md) — start there for the full picture.
>
> (Note: this path existed as an empty directory until 2026-08-30 — a Docker
> bind-mount artifact, not a lost document.)

## The stack in one paragraph

Mailyte is a multi-tenant email platform deployed as a single Docker Compose stack (~45 containers): **Postfix** (SMTP), **Dovecot** (IMAP/POP3/ManageSieve, LMTP delivery), and **Rspamd** (spam filtering inbound, per-domain DKIM signing outbound) form the mail core; a **FastAPI gateway** (`worker/api`, 34 route modules) is the entire management surface and webmail backend, fronting ~20 single-purpose worker services (webhooks, tracking, rate limiting, analytics, archiving, JMAP, CalDAV, OAuth, autoconfig, migration, and more) plus three security services (DLP, TOTP, geo-blocking). **MySQL 8.0.35** is the source of truth, **Redis** the cache/counter store, **Qdrant** the vector DB for AI search, and **S3-compatible object storage** holds the encrypted mail archive. In production, **Traefik** terminates TLS on 443 and routes per hostname; every internal service port is bound to `127.0.0.1` (locked down 2026-08-22) — only the mail protocol ports and 80/443 face the internet.

## Where to read more

| Topic | Page |
|-------|------|
| Layers, diagram, service inventory, design decisions | [architecture/index.md](architecture/index.md) |
| Every service: ports, dependencies, failure modes | [architecture/service-architecture.md](architecture/service-architecture.md) |
| How mail flows (inbound, outbound, API-initiated) | [architecture/data-flow.md](architecture/data-flow.md) |
| Schema and models (ULID keys, Alembic) | [architecture/database-design.md](architecture/database-design.md) |
| AuthN/AuthZ, TLS, network exposure, abuse controls | [architecture/security-model.md](architecture/security-model.md) |
| Multi-tenancy | [architecture/organization-model.md](architecture/organization-model.md), [architecture/multi-tenant.md](architecture/multi-tenant.md) |
| Scaling | [architecture/scaling-strategy.md](architecture/scaling-strategy.md) |

## Ground truth

When documentation and deployment disagree, these win:

- `docker-compose.yml` — the service inventory, ports, networks, volumes (heavily commented; the comments record real production findings).
- `docker-compose.prod.yml` — Traefik routing, replicas (api/webhooks/tracking ×2), loopback port binding, resource limits.
- `mailer/postfix/config/main.cf` + `master.cf` — restriction ordering, the tracking content filter, LMTP transport, sender-login enforcement (587/465 only, never port 25).
- `worker/api/app.py` — the gateway's route-module registry.
- `alembic/versions/` — the schema.
