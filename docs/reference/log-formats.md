---
title: Log Formats
description: Where every log file lives and how to read it — Postfix, Dovecot, Rspamd, API, and worker logs.
---

# Log Formats

Every Mailyte service writes logs. This reference tells you where they are and how to read them.

## Log Locations

| Service | Host Path | Container Path |
|---------|-----------|---------------|
| Postfix | `logs/mailer/postfix/maillog` | `/var/log/postfix/maillog` |
| Dovecot | `logs/mailer/dovecot/` | `/var/log/dovecot/` |
| Rspamd | `logs/mailer/rspamd/rspamd.log` | `/var/log/rspamd/rspamd.log` |
| API | `logs/worker/api/` | `/app/logs/` |
| Tracking | `logs/worker/tracking/` | `/app/logs/` |
| Webhooks | `logs/worker/webhooks/` | `/app/logs/` |
| Analytics | `logs/worker/analytics/` | `/app/logs/` |
| Rate Limiter | `logs/worker/rate-limiter/` | `/app/logs/` |
| Queue Manager | `logs/worker/queue-manager/` | `/app/logs/` |
| cert_manager | Docker stdout | `docker logs cert_manager` |

You can also use `docker logs <container_name>` for any service.

## Postfix Log Format

Postfix logs follow syslog format:

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
| `postfix/local` | | Local delivery |
| Queue ID | `ABC123` | Unique message identifier in Postfix |
| `delay=a/b/c/d` | `1.2/0.1/0.3/0.8` | Before qmgr / in queue / connect / transfer |
| `dsn=2.0.0` | | Delivery Status Notification code |
| `status=sent` | | Final delivery status |

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
grep "to=<user@example.com>" /path/to/maillog

# All bounces
grep "status=bounced" /path/to/maillog

# All rejections
grep "NOQUEUE: reject" /path/to/maillog

# All TLS connections
grep "TLS connection" /path/to/maillog

# Follow a message by queue ID
grep "ABC123" /path/to/maillog
```

## Dovecot Log Format

```
Mar 25 14:35:00 mail dovecot: imap-login: Login: user=<user@example.com>, method=PLAIN, rip=192.168.1.10, lip=10.0.0.5, mpid=1234, TLS, session=<abc123>
Mar 25 14:35:01 mail dovecot: imap(user@example.com)<1234><abc123>: Logged out in=512 out=2048 deleted=0 expunged=0 trashed=0 hdr_count=10 body_count=2
Mar 25 14:35:02 mail dovecot: auth: sql(user@example.com,192.168.1.10): Password mismatch
Mar 25 14:35:03 mail dovecot: lmtp(1235): Connect from local
Mar 25 14:35:03 mail dovecot: lmtp(1235, user@example.com): msgid=<msg@example.com>: saved mail to INBOX
```

### Key Fields

| Pattern | Meaning |
|---------|---------|
| `imap-login: Login:` | Successful IMAP login |
| `pop3-login: Login:` | Successful POP3 login |
| `auth: sql(...): Password mismatch` | Failed login attempt |
| `lmtp(...): saved mail to` | Incoming email delivered to mailbox |
| `imap(...): Logged out` | Session ended, shows bytes in/out |
| `rip=` | Remote IP (client) |
| `lip=` | Local IP (server) |

## Rspamd Log Format

```
2025-03-25 14:40:00 #1234(normal) <abc123>; task; rspamd_task_write_log: id: <abc123>, qid: <DEF456>, ip: 203.0.113.1, from: <sender@example.com>, (default: F (no action): [2.10/15.00] [SPF_ALLOW(-0.20),DKIM_ALLOW(-0.10),DMARC_POLICY_ALLOW(-0.50),R_SPF_ALLOW(-0.20),MIME_GOOD(-0.10),MID_CONTAINS_FROM(1.00)]), len: 4321, time: 150ms, dns: 3/4
```

### Key Fields

| Field | Meaning |
|-------|---------|
| `F (no action)` | Final action (no action / add header / reject / greylist) |
| `[2.10/15.00]` | Score / threshold |
| `[SYMBOLS...]` | Individual scoring symbols with weights |
| `len:` | Message size |
| `time:` | Processing time |
| `dns:` | DNS lookups (performed/total) |

### Action Codes

| Action | Meaning |
|--------|---------|
| `no action` | Clean message, delivered normally |
| `add header` | Spam header added, delivered |
| `greylist` | Temporarily deferred |
| `reject` | Message rejected |
| `soft reject` | Temporary rejection |

## API / Worker Log Format

Workers use Python's structured logging:

```
2025-03-25 14:45:00 [INFO] api.routes.domains: Domain created: example.com (org: my-org)
2025-03-25 14:45:01 [WARNING] worker.rate_limiter: Rate limit approaching for org my-org (80% of hourly limit)
2025-03-25 14:45:02 [ERROR] worker.webhooks: Webhook delivery failed: https://app.com/hook - 503 Service Unavailable
2025-03-25 14:45:03 [DEBUG] worker.tracking: Tracking pixel hit: email_id=abc123, ip=203.0.113.5
```

### Log Levels

| Level | When it's used |
|-------|---------------|
| `DEBUG` | Detailed internal state (noisy, usually off in production) |
| `INFO` | Normal operations (requests served, tasks completed) |
| `WARNING` | Something unusual but not broken (approaching limits) |
| `ERROR` | Something failed (webhook delivery, DB connection) |
| `CRITICAL` | Service-level failure (can't start, lost DB connection) |

## Docker Logs

For services without volume-mounted logs:

```bash
# View logs
docker logs <container_name> --tail 100

# Follow in real time
docker logs <container_name> -f

# Since a specific time
docker logs <container_name> --since 2025-03-25T14:00:00

# Last 30 minutes
docker logs <container_name> --since 30m
```

## Log Rotation

Set up logrotate to prevent logs from filling your disk:

```
# /etc/logrotate.d/mailyte
/path/to/mailyte/logs/mailer/postfix/maillog
/path/to/mailyte/logs/mailer/dovecot/*.log
/path/to/mailyte/logs/mailer/rspamd/*.log
/path/to/mailyte/logs/worker/*/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```
