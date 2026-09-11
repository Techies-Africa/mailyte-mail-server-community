---
title: Log Formats
description: Where every log lives and how to read it — Postfix, Dovecot, Rspamd, and the worker services, plus the log ingestor that turns Postfix logs into database rows.
---

# Log Formats

Every Mailyte service writes logs. This reference tells you where they are and how to read them.

## Log Locations

| Service | Where the logs go |
|---------|-------------------|
| Postfix | `/var/log/postfix/mail.log` inside the container (`maillog_file` + postlogd — no syslog daemon) |
| Dovecot | **stderr only** (`log_path = /dev/stderr`) — read with `docker logs dovecot` |
| Rspamd | container stdout/stderr — `docker logs rspamd` |
| Workers (api, tracking, webhooks, …) | `logs/<service>/<service>.log` inside each container (repo `logs/` tree when volume-mounted), plus container stdout |
| Worker errors | `logs/<service>/<service>_errors.log` (WARNING and above) |
| Worker performance | `logs/<service>/<service>_performance.log` |
| Traefik (production) | `/var/log/traefik/access.log`, JSON format |
| Everything else (mysql, redis, prometheus, …) | `docker logs <container_name>` |

Worker file logging is best-effort: if the log directory isn't writable the service falls back to console-only logging rather than failing.

## Postfix Log Format

Postfix logs in the classic syslog line format to `/var/log/postfix/mail.log`:

```
Mar 25 14:32:01 mail postfix/smtpd[1234]: connect from sender.example.com[203.0.113.1]
Mar 25 14:32:01 mail postfix/smtpd[1234]: NOQUEUE: reject: RCPT from sender.example.com[203.0.113.1]: 550 5.1.1 <unknown@example.com>: Recipient address rejected: User unknown
Mar 25 14:32:02 mail postfix/cleanup[1235]: ABC123: message-id=<msg@example.com>
Mar 25 14:32:02 mail postfix/qmgr[100]: ABC123: from=<sender@example.com>, size=4321, nrcpt=1 (queue active)
Mar 25 14:32:03 mail postfix/smtp[1236]: ABC123: to=<recipient@gmail.com>, relay=gmail-smtp-in.l.google.com[142.250.x.x]:25, delay=1.2/0.1/0.3/0.8, delays=0.1/0.01/0.29/0.8, dsn=2.0.0, status=sent (250 2.0.0 OK)
```

### Key Fields

| Field | Example | Meaning |
|-------|---------|---------|
| `postfix/smtpd` | | Incoming SMTP connection handler |
| `postfix/smtp` | | Outgoing SMTP delivery |
| `postfix/qmgr` | | Queue manager |
| `postfix/cleanup` | | Message preprocessing |
| `postfix/lmtp` | | Delivery to Dovecot |
| Queue ID | `ABC123` | Unique message identifier in Postfix |
| `delay=a/b/c/d` | `1.2/0.1/0.3/0.8` | Before qmgr / in queue / connect / transfer |
| `dsn=2.0.0` | | Delivery Status Notification code |
| `status=sent` | | Final delivery status |
| `sasl_username=` | | Authenticated sender (mailbox or SMTP credential) |

### Status Values

| Status | Meaning |
|--------|---------|
| `sent` | Successfully delivered |
| `deferred` | Temporary failure, will retry |
| `bounced` | Permanent failure, gave up |
| `expired` | Exceeded max queue lifetime |

### Useful Grep Patterns

```bash
# All deliveries for a specific address
docker exec postfix grep "to=<user@example.com>" /var/log/postfix/mail.log

# All bounces
docker exec postfix grep "status=bounced" /var/log/postfix/mail.log

# All rejections
docker exec postfix grep "NOQUEUE: reject" /var/log/postfix/mail.log

# Follow a message by queue ID
docker exec postfix grep "ABC123" /var/log/postfix/mail.log
```

## The Log Ingestor (Postfix logs → database)

Since 2026-08-22, the `log_ingestor` service tails `/var/log/postfix/mail.log` (env `POSTFIX_LOG_PATH`), correlates lines by queue ID, and writes one row per final delivery attempt into the **`mail_logs`** table — this is what powers the Email Logs UI and delivery reporting.

- **Status mapping:** Postfix `sent` → `delivered`, `bounced` → `bounced`, `deferred` → `deferred`, `expired` → `bounced`; `NOQUEUE: reject` lines → `rejected` (or `deferred` when the SMTP reply was 4xx). It never writes `queued`/`sending`/`sent`.
- **Columns populated:** `timestamp`, `sender`, `recipient`, `organization_id` (resolved from `domains`), `status`, `message_id`, `size`, `relay`, `delays`, `dsn`, `bounce_reason`, `subject`, `sasl_username`.
- **Deduplication:** internal `tracking-filter`/`webhook-filter` hops are skipped (`INGESTOR_INTERNAL_RELAYS`), and the row ID is a deterministic hash of `(queue_id, recipient, status, timestamp)` inserted with `INSERT IGNORE`, so re-reading the log never duplicates rows.
- **State:** the tail position survives restarts via `INGESTOR_STATE_PATH` (default `/var/lib/log_ingestor/state`), with rotation/truncation detection.
- **Side effects:** each fresh row dispatches the matching webhook (`email.delivered` / `email.bounced` / `email.deferred` / `email.rejected` — downstream systems build their delivery-event feeds from these), touches `smtp_credentials.last_used_at` for SMTP-credential senders (throttled, default 60s), and — only when `AUTO_SUSPEND_ENABLED=true` — auto-suspends credentials whose recent sends are mostly failing.

There is no `delivery_events` table in this repository — that table lives in the downstream Laravel application, fed by these webhooks.

## Dovecot Log Format

Dovecot writes to stderr; view with `docker logs dovecot`:

```
imap-login: Login: user=<user@example.com>, method=PLAIN, rip=192.168.1.10, lip=10.0.0.5, mpid=1234, TLS, session=<abc123>
imap(user@example.com)<1234><abc123>: Logged out in=512 out=2048 deleted=0 expunged=0 trashed=0 hdr_count=10 body_count=2
auth: sql(user@example.com,192.168.1.10): Password mismatch
lmtp(1235): Connect from local
lmtp(1235, user@example.com): msgid=<msg@example.com>: saved mail to INBOX
```

### Key Fields

| Pattern | Meaning |
|---------|---------|
| `imap-login: Login:` | Successful IMAP login |
| `pop3-login: Login:` | Successful POP3 login |
| `auth: sql(...): Password mismatch` | Failed login attempt |
| `lmtp(...): saved mail to` | Incoming email delivered to mailbox |
| `rip=` | Remote IP (client) |
| `lip=` | Local IP (server) |

## Rspamd Log Format

```
2026-03-25 14:40:00 #1234(normal) <abc123>; task; rspamd_task_write_log: id: <abc123>, qid: <DEF456>, ip: 203.0.113.1, from: <sender@example.com>, (default: F (no action): [2.10/15.00] [SPF_ALLOW(-0.20),DKIM_ALLOW(-0.10),...]), len: 4321, time: 150ms, dns: 3/4
```

| Field | Meaning |
|-------|---------|
| `F (no action)` | Final action (no action / greylist / add header / rewrite subject / reject) |
| `[2.10/15.00]` | Score / reject threshold |
| `[SYMBOLS...]` | Individual scoring symbols with weights |
| `len:` / `time:` / `dns:` | Message size / processing time / DNS lookups |

## Worker Log Format

Workers use `shared/logging_config.py` — plain text, not JSON. Two formats:

**Console** (`simple`): `%(asctime)s | %(levelname)s | %(message)s`

**File** (`detailed`, in `logs/<service>/<service>.log`):

```
2026-03-25 14:45:00 | tracking | INFO | app:214 | Tracking pixel hit: email_id=abc123
```

i.e. `%(asctime)s | %(name)s | %(levelname)s | %(module)s:%(lineno)d | %(message)s`.

**The API service** reformats its handlers to include a correlation ID:

```
2026-03-25 14:45:00,123 - routes.domains - INFO - [req_01J5X8...] - Domain created: example.com
```

The correlation ID matches the `X-Correlation-Id` response header, so a failing API request can be traced from client to log line. `GET /platform/logs` parses these files server-side (log root: `MAILYTE_LOG_DIR`, default `logs/`).

Log files rotate at 10 MB (main, 5 backups) / 5 MB (errors and performance, 3 backups). A `RedactingFilter` masks anything that looks like `password=`, `secret=`, `token=`, `api_key=`, or `private_key=` in every handler.

### Log Levels

| Level | When it's used |
|-------|---------------|
| `DEBUG` | Detailed internal state (noisy, usually off in production) |
| `INFO` | Normal operations (requests served, tasks completed) |
| `WARNING` | Something unusual but not broken (approaching limits) |
| `ERROR` | Something failed (webhook delivery, DB connection) |
| `CRITICAL` | Service-level failure (can't start, lost DB connection) |

Set per service with `LOG_LEVEL` (default `INFO`).

## Docker Logs

For everything that logs to stdout/stderr:

```bash
docker logs <container_name> --tail 100
docker logs <container_name> -f
docker logs <container_name> --since 30m
```

## Log Rotation

Worker file logs self-rotate (RotatingFileHandler, sizes above). The Postfix `mail.log` inside the container does **not** self-rotate — if you bind-mount it, add logrotate on the host:

```
# /etc/logrotate.d/mailyte
/path/to/mailyte/logs/mailer/postfix/mail.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

The log ingestor detects rotation (inode change) and truncation automatically, so `copytruncate` is safe.
