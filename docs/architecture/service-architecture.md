# Service Architecture

Every service in the Mailyte stack — what it does, where it listens, what it depends on, and what breaks if it goes down.

The single source of truth for the service inventory is `docker-compose.yml` (plus `docker-compose.prod.yml` for production overrides). This page mirrors those files as of 2026-08-30.

---

## Service Map

All services run as containers in a single Docker Compose deployment. Ports are shown as `host:container` from the base compose file; in production (`docker-compose.prod.yml`) every internal port is re-bound to `127.0.0.1` and external HTTP traffic goes through Traefik on 443 instead.

### Edge & lifecycle

| Service | Port(s) | Role |
|---------|---------|------|
| **traefik** (prod only) | 80, 443, 127.0.0.1:8080 | Reverse proxy, TLS termination, per-hostname routing |
| **secrets-check** | — | Fail-closed validation of security-critical secrets; every service waits for it |
| **migrate** | — | Alembic schema migrations; runs once per `up`, gates everything that touches the schema |
| **docker-proxy** | — (internal_only network) | Scoped Docker socket proxy (list/inspect/restart only) for `monitoring` and `cert_manager` |
| **acme_webroot** | — | Serves ACME HTTP-01 challenge files written by `cert_manager`; Traefik routes `/.well-known/acme-challenge/*` here |

### Mail infrastructure

| Service | Port(s) | Depends On |
|---------|---------|------------|
| **postfix** | 25, 587, 465, 10026 (loopback in prod) | mysql, redis, rspamd, migrate |
| **dovecot** | 143, 993, 110, 995, 4190 | mysql, redis, migrate |
| **rspamd** | 11332, 11334 (loopback in prod) | redis |
| **cert_manager** | — | mysql, docker-proxy |
| **log_ingestor** | — | mysql (tails the Postfix log) |

### API gateway & workers

| Service | Host port (dev) | Bind port | Role |
|---------|-----------------|-----------|------|
| **api** | 8083 | 8080 | FastAPI gateway — 34 route modules, the management plane |
| **webhooks** | 8081 | 8081 | Event delivery to external URLs |
| **rate_limiter** | 8082 | 8082 | Rate limit counters (Redis-backed); consulted by Postfix policy service |
| **tracking** | 8086 | 8086 | Open/click tracking backend |
| **monitoring** | 8085 | 8085 | Service health monitoring; restarts containers via docker-proxy |
| **analytics** | 8087 | 8085 | Aggregated analytics + scheduled report delivery |
| **archiver** | 8089 | 8083 | Mail archive to S3, age-encrypted, with local spool |
| **dashboard** | 8088 | 8088 | Analytics dashboard service |
| **queue_manager** | 8090 | 8090 | Postfix spool queue management (`postqueue` over the shared spool volume) |
| **rag** | 8091 | 8090 | AI search — embeddings + Qdrant queries |
| **storage_usage** | 8092 | 8092 | Mailbox storage measurement via Dovecot IMAP QUOTA |
| **encryption** | 8093 | 8084 | S/MIME certificate operations |
| **delivery_optimizer** | 8094 | 8088 | ISP throttling, IP warming, bounce processing |
| **templates** | 8095 | 8089 | Jinja2 email template management |
| **url_protection** | 8096 | 8090 | Safe Links — click-time URL verification |
| **oauth** | 8097 | 8091 | OAuth 2.0 / XOAUTH2 authorization server for IMAP/SMTP |
| **jmap** | 8098 | 8098 | JMAP protocol (RFC 8620/8621) over IMAP impersonation |
| **migration** | 8099 | 8099 | IMAP-to-IMAP mailbox migration jobs |
| **autoconfig** | 8100 | 8100 | Client auto-configuration (Thunderbird autoconfig, Outlook autodiscover, MTA-STS) |
| **caldav** | 8101 | 8101 | CalDAV/CardDAV management API in front of Radicale |
| **activesync** | 8084 | 80 | Mobile sync (Apache-based) |

### Security services

| Service | Port | Role |
|---------|------|------|
| **dlp** | 8102 | Data Loss Prevention — PII/keyword/regex scanning, per-org policies |
| **totp** | 8103 | TOTP service — backs operator MFA in the gateway |
| **geo_blocking** | 8104 | GeoIP-based access policy |

### Data stores & brokers

| Service | Port | Role |
|---------|------|------|
| **mysql** | — (Docker network only) | Primary database (MySQL 8.0.35, pinned) |
| **redis** | — (Docker network only) | Cache, rate-limit counters, Rspamd backend |
| **qdrant** | 6333 (loopback in prod) | Vector database for RAG |
| **kafka** + **zookeeper** | 9092 (loopback in prod) | Provisioned message broker — see note below |
| **S3-compatible object storage** | external | Mail archive (archiver) and backups; `shared/object_storage.py` |

!!! note "Kafka is provisioned but not yet wired in"
    `kafka` and `zookeeper` run in the stack and `shared/kafka_client.py` provides a client library, but as of 2026-08-30 no service imports it — inter-service communication is HTTP over the Docker network plus Redis. Do not design against Kafka topics that nothing produces to.

### Monitoring stack

| Service | Port | Role |
|---------|------|------|
| **prometheus** | 9090 (loopback in prod) | Metrics collection (30d retention) |
| **grafana** | 3000 (loopback in prod; `grafana.${DOMAIN}` via Traefik) | Dashboards |
| **alertmanager** | 9093 (loopback in prod) | Alert routing |
| **mysql-exporter** / **redis-exporter** | 9104 / 9121 (loopback in prod) | Infrastructure metrics |

### Web frontends

| Service | Port | Role |
|---------|------|------|
| **docs** | 8000 (dev), `docs.${DOMAIN}` via Traefik | This handbook (MkDocs) |
| **console** | 3100 (compose profile `console`) | Staff admin panel — IP-allowlisted behind Traefik, separate repo, prebuilt image |
| **webmail** | 127.0.0.1:3200 (compose profile `webmail`) | End-user webmail ([mailyte-webmail](https://github.com/Techies-Africa/mailyte-webmail)) — public by design behind Traefik |
| **roundcube** | 8880 (compose profile `roundcube`) | Optional Roundcube webmail |
| **sogo** | 8881 (compose profile `sogo`) | Optional SOGo groupware |

!!! info "Internal ports are loopback-bound in production"
    Since 2026-08-22, `docker-compose.prod.yml` republishes every internal service on `127.0.0.1`. Before that, ~30 services (including unauthenticated Prometheus and Qdrant) were reachable from the public internet. The only ports on `0.0.0.0` in production are the mail protocols (25, 465, 587, 143, 993, 110, 995, 4190) and Traefik's 80/443. Docker publishes past `ufw` by writing its own iptables rules, so the bind address — not the host firewall — is the control.

---

## Mail Infrastructure

### Postfix — SMTP Server

**What it does**: Sends and receives email. It's the front door for all email traffic.

**Ports**:

- **25** — Server-to-server SMTP (receiving email from the internet). No tracking content filter and no sender-login check — this port carries unauthenticated inbound mail by definition.
- **587** — Client submission with STARTTLS.
- **465** — Client submission with implicit TLS.
- **10587** (container-internal) — Submission listener for the gateway/webmail send path; carries the tracking content filter.
- **10026** (loopback) — Reinjection listener for the tracking content filter.

**Talks to**: Dovecot (SASL auth via `smtpd_sasl_type = dovecot`, delivery via LMTP to `dovecot:24`), Rspamd (milter on `rspamd:11332` — spam scoring inbound, DKIM signing outbound), MySQL (virtual domain/mailbox/alias lookups), rate_limiter (via the in-container `rate_limit_policy.py` policy service, at the SMTP DATA phase), tracking/delivery_optimizer/archiver (via the in-container `tracking_injector.py` content filter on submission ports).

**Enforcement worth knowing** (verified against `main.cf`/`master.cf`, current as of 2026-08-22 fixes):

- `reject_sender_login_mismatch` is enforced on **587/465 only**, listed ahead of `permit_sasl_authenticated`. It is deliberately absent from the global `smtpd_sender_restrictions` — putting it there once broke **all** inbound mail on port 25 for any sender who owned a local mailbox.
- The rate-limit policy service runs at the **DATA phase and nowhere else** on the inbound path. Running it in multiple restriction lists once triple-counted every message and deferred essentially all inbound mail.

**If it goes down**: No email gets sent or received. This is the most critical service. Remote servers queue mail for later retry (typically up to 5 days). The Postfix spool is a named volume (`postfix_spool`), so queued-but-undelivered mail survives container recreation.

---

### Dovecot — IMAP/POP3 Server

**What it does**: Lets email clients read mail. Handles SMTP authentication for Postfix, and receives final delivery over LMTP.

**Ports**:

- **143** / **993** — IMAP (STARTTLS) / IMAPS
- **110** / **995** — POP3 (STARTTLS) / POP3S
- **4190** — ManageSieve (remote Sieve script management; the gateway's filter endpoints speak this protocol)
- **24** (internal) — LMTP delivery from Postfix
- **24180** (internal) — doveadm HTTP API, used by the gateway to flush the auth cache when an SMTP credential is revoked/rotated

**Talks to**: MySQL (account lookups, auth — including a dedicated SQL config for SMTP API credentials), local filesystem (Maildir storage under `/var/mail/vhosts`), archiver (the global Sieve pipes every delivered message to `archive-message`).

**Auth caching**: `auth_cache_ttl = 1 hour`. Deleting, suspending, or changing the password of a mailbox does **not** take effect on already-cached credentials until the cache is flushed — which is exactly what the gateway's doveadm calls do for SMTP credentials.

**If it goes down**: Email clients can't check mail, and Postfix can't authenticate submissions on 587/465. Inbound delivery queues in Postfix until Dovecot returns.

---

### Rspamd — Spam Filter & DKIM Signer

**What it does**: Scores inbound email (Bayes, SPF/DKIM/DMARC verification, RBLs), and **signs outbound mail with per-domain DKIM keys**. Both directions run through the milter (`smtpd_milters = inet:rspamd:11332`).

**Ports**: 11332 (milter), 11334 (web UI/controller — loopback-only in production).

**Talks to**: Redis (Bayes data, greylisting state), MySQL (DKIM selector lookups for `scripts/generate_dkim.py`).

**If it goes down**: `milter_default_action = accept` is the configured behaviour — mail flows unscanned (and outbound goes out unsigned) rather than deferring. That is a deliberate availability-over-filtering trade-off; change `milter_default_action` to `tempfail` if you prefer to defer.

!!! warning "ClamAV is not deployed"
    `mailer/rspamd/config/local.d/antivirus.conf` ships with `enabled = false` and no `clamav` service exists in any compose file. Attachment antivirus scanning is **off by default**; the config file documents how to add a ClamAV container and enable it.

---

### cert_manager — TLS Automation

**What it does**: Issues and renews Let's Encrypt certificates for the mail hostnames, Traefik's admin subdomains (`api`, `autoconfig`, `jmap`, `caldav`, `docs`, `grafana`, `traefik`, `console`, plus `TRAEFIK_EXTRA_HOSTNAMES`), and per-domain SNI certs. Writes the SNI map that Postfix/Dovecot/Traefik read.

**How**: ACME HTTP-01 via `--webroot` — the `acme_webroot` nginx container serves the challenge files, and Traefik routes `/.well-known/acme-challenge/*` to it. Traefik's own built-in ACME is deliberately unused (it swallowed certbot's challenges in production once). After a rotation, cert_manager SIGHUPs Postfix/Dovecot through docker-proxy — it never touches the Docker socket directly.

**If it goes down**: Certificates stop renewing. Existing certs work until expiry.

---

### log_ingestor — Mail Log Producer

**What it does**: Tails Postfix's log (read-only mount of `logs/mailer/postfix/mail.log`) and turns it into per-message rows in `mail_logs`, plus `email.delivered` / `email.bounced` / `email.deferred` / `email.rejected` webhook events.

**Why it exists**: Added 2026-08-22. Before it, `mail_logs` and the delivery-event feed had **no producer at all** — the tables, the platform API endpoints that read them, and the Email Logs UIs all existed around a component that had never been built, so every log view correctly rendered an empty set.

**Design**: Idempotent by construction — row keys are derived deterministically from (queue id, recipient, status, timestamp) and inserted with `INSERT IGNORE`, so replaying a log region cannot create duplicates. Log rotation is detected by inode. Its read offset lives in the `log_ingestor_state` volume. It also carries the (default-off) `AUTO_SUSPEND_ENABLED` hook for auto-suspending abusive SMTP credentials.

**If it goes down**: Mail keeps flowing, but `mail_logs` and delivery webhooks go stale until it catches up — which it does from its saved offset.

---

## API Gateway & Workers

### api — FastAPI Gateway

**What it does**: The management plane. A FastAPI app with 34 route modules: organizations, domains, mailboxes, aliases, analytics, monitoring, queue, webhooks, rate-limiter, storage, tracking, RAG, filters (Sieve over ManageSieve), shared mailboxes, message trace, transport rules, white-label, reseller, compliance, migration, SSL, SMTP credentials (+ reports), capabilities, bootstrap, auth, platform auth, platform, security, reputation, mailbox auth, and mailbox (the webmail backend).

**Port**: binds 8080; published as 8083 in dev. In production it runs with **2 replicas**, no host port, behind Traefik at `api.${DOMAIN}` (which also serves the bare-domain landing page).

**Proxy modules**: the `analytics`, `tracking`, `rate_limiter`, and queue/storage/monitoring route modules hold no logic of their own — they proxy to the sibling containers (`analytics:8085`, `tracking:8086`, `rate_limiter:8082`, `queue_manager:8090`, `storage_usage:8092`, `monitoring:8085`, `delivery_optimizer:8088`). These proxies were built out on 2026-08-08; before that the gateway called loopback and every proxied call failed.

**Send path**: webmail/API sends submit over SMTP to Postfix's internal listener `postfix:10587` (`MAIL_SUBMIT_HOST`/`MAIL_SUBMIT_PORT`) — not port 25 — because 10587 carries the tracking content filter and 25 must never track inbound mail.

**SMTP credentials**: live since 2026-08-27 — create/rotate/revoke flows flush Dovecot's auth cache through the doveadm HTTP API (`dovecot:24180`) so revocation takes effect immediately instead of after the 1-hour cache TTL.

**If it goes down**: Programmatic management and webmail stop. Postfix/Dovecot keep working — mail clients still send and receive.

---

### webhooks — Event Delivery

**What it does**: Delivers HTTP callbacks for system events. All services fire events through `shared/webhook_dispatcher.py` (`dispatch_event()`), which signs payloads (HMAC + timestamp for replay protection), retries with backoff, and writes permanently failed deliveries to the `webhook_dead_letters` table. Event families cover the SMTP lifecycle (`email.accepted/delivered/bounced/deferred/rejected/…`), IMAP/POP3 user actions, tracking, storage quotas, auth/security, and more.

**Production**: 2 replicas. **If it goes down**: events queue/retry; permanently failed ones land in the dead-letter table.

### rate_limiter — Rate Limit Counters

**What it does**: Redis-backed counters and limit configuration. Consulted from two directions: the gateway's `/api/v1/rate-limiter` proxy (management), and Postfix's `rate_limit_policy.py` policy service (enforcement — inbound at the DATA phase, outbound via the submission restriction class).

**If it goes down**: the Postfix policy service's failure mode decides whether mail defers or flows unmetered; management endpoints 502.

### tracking — Opens & Clicks

**What it does**: Generates and records tracking pixels and rewritten links. The tracking injector in the Postfix container calls it at send time; recipients hit `https://api.${DOMAIN}/api/v1/tracking/...` (the gateway proxies pixel and click-redirect requests through to this service). Dispatches `tracking.open` / `tracking.click` webhooks.

**Production**: 2 replicas.

### monitoring — Service Health

**What it does**: Health-checks the stack and can restart unhealthy containers — through **docker-proxy**, which exposes only list/inspect/restart (no exec, no images, no volumes). Also backs the console's monitoring screens via the gateway proxy.

### analytics — Aggregates & Reports

**What it does**: Aggregated email analytics plus scheduled report generation and SMTP delivery of reports (through Postfix on 25).

### queue_manager — Postfix Queue Management

**What it does**: Lists, holds, releases, flushes, and deletes messages in **Postfix's own spool** using `postqueue`/`postsuper` over the shared `postfix_spool` volume (it needs gid 106 to traverse `public/`). There is no separate application-level outbound queue: the mail queue *is* Postfix's queue.

### storage_usage — Quota Measurement

**What it does**: Reads per-mailbox usage from **Dovecot over IMAP QUOTA** using a master user — not by walking Maildirs (it runs unprivileged and cannot traverse them; the filesystem approach measured 105 of 107 mailboxes as 0 bytes). Dispatches `storage.quota.warning` / `storage.quota.exceeded` webhooks.

### delivery_optimizer — Deliverability

**What it does**: ISP-specific throttling, IP warming schedules, and bounce processing. The tracking injector consults it on the send path.

### templates / url_protection / oauth / encryption

- **templates** — Jinja2 template storage and rendering.
- **url_protection** — Safe Links: HMAC-signed click-time URL verification (`SAFE_LINK_BASE_URL`).
- **oauth** — OAuth 2.0 authorization server for XOAUTH2 IMAP/SMTP sign-in.
- **encryption** — S/MIME certificate operations; mounts the same envelope-encryption KEK as the gateway.

### jmap / caldav + radicale / activesync

- **jmap** — JMAP (RFC 8620/8621) served by impersonating the user over IMAP against Dovecot with a master credential. Behind Traefik at `jmap.${DOMAIN}`.
- **caldav** — management API in front of **radicale**, the actual CalDAV/CardDAV server. Behind Traefik at `caldav.${DOMAIN}`.
- **activesync** — Exchange ActiveSync endpoint (Apache-based).

### migration — Mailbox Migration

**What it does**: IMAP-to-IMAP migration jobs (import/export) with a concurrency cap (`MIGRATION_MAX_CONCURRENT_JOBS`, default 5). Driven via the gateway's `/api/v1/migration` module.

### autoconfig — Client Auto-Setup

**What it does**: Serves Thunderbird autoconfig (`/mail/config-v1.1.xml`), Outlook autodiscover (POX and JSON v2), MTA-STS policies (`/.well-known/mta-sts.txt`), and per-domain DNS record listings — answering MX/DKIM lookups from the database.

**Routing**: publicly routed since 2026-08-27 via a Traefik `HostRegexp` rule that matches `autoconfig.*`, `autodiscover.*`, and `mta-sts.*` for **every** customer domain — not just the server's own. (The original single-literal-host rule 404'd for every customer domain.)

### archiver — Mail Archive

**What it does**: Receives full message copies from both directions (Dovecot's global Sieve on delivery; the tracking injector on send), encrypts each object with `age` to both an escrow recipient and a service recipient, and writes to a dedicated S3 bucket. A local disk spool holds messages whenever S3 is unreachable and drains on a timer.

### rag + qdrant — AI Search

**What it does**: Generates vector embeddings from email content, stores them in Qdrant, and answers semantic search queries via the gateway's `/api/v1/rag` module.

### dashboard — Analytics Dashboard

**What it does**: Real-time email analytics/reporting service (FastAPI) on 8088.

---

## Security Services

- **dlp** (8102) — a milter-style DLP scanner: Luhn-validated credit cards, SSNs, IBANs, per-org keyword/regex policies from the `dlp_policies` table, with block/quarantine/encrypt/notify/log actions and Redis-cached policy lookups.
- **totp** (8103) — TOTP secrets and verification; the gateway's operator MFA (`/api/v1/platform/auth`) calls this rather than reimplementing TOTP.
- **geo_blocking** (8104) — GeoIP (MaxMind) country-based access policy.

!!! note "What is *not* deployed"
    `mailer/intrusion_detection/` (fail2ban configs) and `mailer/log_analyzer/` exist in the repository but appear in **no compose file** — no container runs them. Brute-force tracking that actually operates lives in the gateway (`failed_auth_attempts`, IP access rules, login-attempt throttling) and in Postfix's own connection/rate limits.

---

## Data Stores

### MySQL

**What it does**: Primary relational database — organizations, domains, accounts, SMTP credentials, mail logs, tracking, webhooks, analytics, audit trail. Image pinned to **8.0.35** (newer 8.0.x builds require the x86-64-v2 microarchitecture level and crash-loop on some VPS CPU models). The root password is file-mounted (`secrets/db_root_password`), not an env var. Schema is owned entirely by the Alembic `migrate` service.

**Port**: not published to the host — Docker network only.

**If it goes down**: Almost everything stops — the API, Dovecot auth, most workers.

### Redis

**What it does**: Caching, rate-limit counters, Rspamd's Bayes/greylisting backend, ephemeral state. AOF persistence, 256 MB LRU cap.

**If it goes down**: rate limiting and caching degrade; MySQL remains the source of truth.

### Qdrant

**What it does**: Vector embeddings for RAG search. **If it goes down**: AI search stops; everything else is unaffected.

### S3 object storage

**What it does**: The mail archive (dedicated bucket, archiver-scoped credentials) and backups. Any S3-compatible endpoint works (`S3_ENDPOINT_URL` for MinIO/R2/B2).
