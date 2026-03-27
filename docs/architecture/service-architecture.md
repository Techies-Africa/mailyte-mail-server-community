# Service Architecture

Every service in the Mailyte stack — what it does, where it listens, what it depends on, and what breaks if it goes down.

---

## Service Map

All services run as containers in a single Docker Compose deployment. Here's the full roster:

| Service | Port(s) | Category | Depends On |
|---------|---------|----------|------------|
| **Postfix** | 25, 587, 465 | Mail Infrastructure | Dovecot, Rspamd, MySQL |
| **Dovecot** | 143, 993, 110, 995 | Mail Infrastructure | MySQL |
| **Rspamd** | 11332 | Mail Infrastructure | ClamAV, Redis |
| **ClamAV** | — (internal) | Mail Infrastructure | — |
| **FastAPI** | 5000 | API | MySQL, Redis, Qdrant |
| **Health Monitor** | 8080 | API | MySQL, Redis, Postfix, Dovecot |
| **Tracking Worker** | — | Worker | MySQL, Redis |
| **Webhook Worker** | — | Worker | MySQL, Redis |
| **Rate Limiter** | — | Worker | Redis |
| **Analytics Worker** | — | Worker | MySQL, Redis |
| **RAG Worker** | — | Worker | MySQL, Qdrant |
| **Queue Manager** | — | Worker | MySQL, Redis, Postfix |
| **Storage Usage** | — | Worker | MySQL |
| **Backup Worker** | — | Worker | MySQL |
| **Cloud Sync** | — | Worker | MySQL, Redis |
| **Log Analyzer** | — | Worker | MySQL |
| **Cert Manager** | — | Worker | Postfix, Dovecot |
| **Intrusion Detection** | — | Worker | Redis |
| **MySQL** | 3306 | Data Store | — |
| **Redis** | 6379 | Data Store | — |
| **Qdrant** | 6333 | Data Store | — |

!!! note "Workers don't expose ports"
    Most workers are internal processes. They communicate with other services over internal Docker networks, not via exposed ports. The only externally reachable services are the ones with listed ports.

---

## Mail Infrastructure

### Postfix — SMTP Server

**What it does**: Sends and receives email. It's the front door for all email traffic.

**Ports**:

- **25** — Server-to-server SMTP (receiving email from the internet)
- **587** — Client submission with STARTTLS (your email client sends through here)
- **465** — Client submission with implicit TLS

**Talks to**: Dovecot (for SMTP AUTH and local delivery), Rspamd (spam filtering via milter), MySQL (virtual mailbox lookups), Queue Manager (accepts queued messages).

**If it goes down**: No email gets sent or received. This is the most critical service. Email clients can't submit messages, and remote servers will queue mail for later retry (typically up to 5 days). The health monitor will immediately flag this.

---

### Dovecot — IMAP/POP3 Server

**What it does**: Lets email clients read mail from mailboxes. Also handles SMTP authentication for Postfix (Postfix asks Dovecot "is this password valid?").

**Ports**:

- **143** — IMAP (with STARTTLS)
- **993** — IMAPS (implicit TLS)
- **110** — POP3 (with STARTTLS)
- **995** — POP3S (implicit TLS)

**Talks to**: MySQL (account lookups, auth), local filesystem (mailbox storage).

**If it goes down**: Email clients can't check their mail. Postfix also can't authenticate outbound submissions (port 587/465 auth will fail). Inbound delivery still works — Postfix queues mail locally and delivers once Dovecot is back.

---

### Rspamd — Spam Filter

**What it does**: Scores incoming email for spam using machine learning (Bayesian classifier), checks SPF/DKIM/DMARC, scans attachments with ClamAV, and applies custom rules.

**Port**: 11332 (milter protocol, internal only)

**Talks to**: ClamAV (virus scanning), Redis (Bayesian training data, rate limits, greylisting state).

**If it goes down**: Depends on your Postfix configuration. By default, if the milter is unavailable, Postfix will either tempfail (safe but delays mail) or accept without scanning (dangerous). Configure `milter_default_action` accordingly.

!!! warning "Don't skip spam filtering"
    If you configure Postfix to accept mail when Rspamd is down, you're wide open to spam and malware. Prefer `tempfail` — legitimate senders will retry.

---

### ClamAV — Virus Scanner

**What it does**: Scans email attachments for malware. Rspamd delegates to it as part of the spam-checking pipeline.

**Ports**: None exposed — Rspamd talks to it over a Unix socket or internal TCP.

**Talks to**: Nothing else. It's a leaf service.

**If it goes down**: Rspamd skips virus scanning but continues with other checks. Emails won't be scanned for malware until ClamAV is restored.

---

## API + Workers

### FastAPI REST API

**What it does**: The management plane. Create organizations, add domains, provision mailboxes, send email, query analytics, search with RAG — all via REST endpoints.

**Port**: 5000

**Talks to**: MySQL (primary data store), Redis (caching, rate limits), Qdrant (RAG search queries).

**If it goes down**: Your application can't manage the email server programmatically, and API-initiated email sending stops. But existing mailboxes keep working — Postfix and Dovecot operate independently. Email clients can still send and receive.

---

### Health Monitor

**What it does**: Checks the health of all other services and exposes a single endpoint your load balancer or monitoring system can poll.

**Port**: 8080

**Talks to**: MySQL, Redis, Postfix, Dovecot (health checks only — read-only probes).

**If it goes down**: You lose visibility into system health. Everything else keeps running.

---

### Tracking Worker

**What it does**: Processes open-tracking pixel requests and click-tracking redirects. When a recipient opens an email or clicks a link, this worker logs the event.

**Talks to**: MySQL (store events), Redis (fast counters).

**If it goes down**: Tracking events are lost or delayed. Email delivery is unaffected.

---

### Webhook Worker

**What it does**: Fires HTTP callbacks to your application when events happen — delivery, bounce, open, click, spam complaint.

**Talks to**: MySQL (read event queue, webhook configs), Redis (job queue).

**If it goes down**: Webhooks stop firing. Events accumulate in the queue and will be delivered when the worker comes back (assuming you have retry logic).

---

### Rate Limiter

**What it does**: Enforces per-org, per-domain, and per-IP sending limits. Prevents abuse and protects your sending reputation.

**Talks to**: Redis (counter storage — fast increment/check with TTLs).

**If it goes down**: Rate limits are not enforced. Depending on your fail-open/fail-closed config, either all mail gets through (risky) or all mail is temporarily rejected (safe but disruptive).

---

### Analytics Worker

**What it does**: Aggregates raw events into time-series analytics — sends, deliveries, bounces, opens, clicks per org/domain/hour/day.

**Talks to**: MySQL (read events, write aggregates), Redis (intermediate counters).

**If it goes down**: Analytics dashboards go stale. Raw events are still logged; the worker will catch up when restarted.

---

### RAG Worker

**What it does**: Generates vector embeddings from email content and stores them in Qdrant for AI-powered semantic search.

**Talks to**: MySQL (read email content), Qdrant (store/query embeddings).

**If it goes down**: New emails aren't indexed for AI search. Existing search still works against already-indexed emails.

---

### Queue Manager

**What it does**: Manages the outbound mail queue. Picks up messages queued by the API and submits them to Postfix for delivery. Handles retries and backoff.

**Talks to**: MySQL (queue state), Redis (signals for new messages), Postfix (submit via SMTP).

**If it goes down**: API-queued emails stop sending. Emails submitted directly via SMTP (email client -> Postfix) are unaffected.

---

### Storage Usage Worker

**What it does**: Calculates per-org and per-mailbox disk usage and checks against quotas.

**Talks to**: MySQL (quota records), filesystem (mailbox sizes).

**If it goes down**: Quota tracking goes stale. Users might temporarily exceed their quotas.

---

### Backup Worker

**What it does**: Runs scheduled backups of MySQL data and mailbox files.

**Talks to**: MySQL (database dumps).

**If it goes down**: Backups stop running. Set up alerts for this one — you don't want to discover it was down after you need a restore.

---

### Cloud Sync Worker

**What it does**: Syncs backups and mail data to external cloud storage (S3-compatible).

**Talks to**: MySQL (sync state), Redis (job queue), cloud storage APIs.

**If it goes down**: Off-site backups stop. Local backups still work if the backup worker is running.

---

### Log Analyzer

**What it does**: Parses Postfix and Dovecot logs, extracts structured events, and writes them to MySQL for querying.

**Talks to**: MySQL (write parsed events), log files (read).

**If it goes down**: Structured log data goes stale. Raw log files are still written by Postfix/Dovecot.

---

### Cert Manager

**What it does**: Manages TLS certificates for mail services — renewal, deployment, and reloading. Think of it as a Let's Encrypt integration for your mail server.

**Talks to**: Postfix and Dovecot (deploy certs and trigger reload).

**If it goes down**: Certificates won't auto-renew. Existing certs keep working until they expire. Set up alerts well before expiry.

!!! danger "Don't ignore cert manager failures"
    An expired TLS certificate means email clients will refuse to connect, and other mail servers may reject your messages. Monitor this closely.

---

### Intrusion Detection (fail2ban)

**What it does**: Monitors auth failures across Postfix, Dovecot, and the API. Bans IPs that fail too many times.

**Talks to**: Redis (ban list storage), log files (auth failure patterns).

**If it goes down**: Brute-force protection is disabled. Auth still works, but attackers won't get auto-banned.

---

## Data Stores

### MySQL

**What it does**: Primary relational database. Stores organizations, domains, accounts, mail logs, analytics, tracking events, webhook configs — basically all persistent state.

**Port**: 3306

**If it goes down**: Almost everything stops. The API can't serve requests, Dovecot can't authenticate users, workers can't read or write data. Redis caching might keep some reads alive briefly. This is your most critical data store.

---

### Redis

**What it does**: In-memory data store used for caching, rate limit counters, job queues, and ephemeral state.

**Port**: 6379

**If it goes down**: Rate limiting stops working, caches go cold (more load on MySQL), job queues stall. The system degrades but doesn't fully stop — MySQL is still the source of truth.

---

### Qdrant

**What it does**: Vector database that stores email embeddings for AI-powered semantic search (RAG).

**Port**: 6333

**If it goes down**: AI search stops working. Everything else — sending, receiving, basic search — is unaffected.
