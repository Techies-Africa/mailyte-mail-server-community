---
title: "Troubleshooting: Email Delivery Issues"
description: Email not arriving? Step-by-step diagnosis for transport misrouting, queue backlogs, bounces, DNS problems, spam filtering, and blacklisting.
---

# Email Delivery Issues

Email isn't arriving. Let's figure out why. Work through these checks in order — they're arranged from most common to least common causes.

## Check Zero: Is the Domain Being Relayed Away?

**Check this first when inbound mail for a hosted domain silently vanishes.** Postfix loads a transport map from `config/mailer/postfix/transport_cutover` — domains listed there are routed by public MX **instead of delivered locally** (used during migrations while a domain's MX still points elsewhere). A stale entry after DNS cutover means every inbound message for the domain is relayed straight back out and lost, while every service looks perfectly healthy. This exact failure ate five days of inbound mail for 13 domains before it was found on 2026-08-27.

```bash
# Is the domain listed?
docker exec postfix cat /etc/postfix/custom/transport_cutover

# After removing a cut-over domain's line:
docker exec postfix postfix reload
```

A domain whose MX now points at this server must **not** be in that file.

## Quick Diagnosis

Run this first to get a snapshot of the system:

```bash
# Service health
docker compose ps

# Mail queue status
docker exec postfix postqueue -p

# Recent delivery attempts (host copy: logs/mailer/postfix/mail.log)
docker exec postfix tail -50 /var/log/postfix/mail.log

# Rspamd status
docker exec rspamd rspamc stat
```

You can also search delivery history through the API — every accepted message is logged to `mail_logs`:

```bash
curl "https://api.yourdomain.com/api/v1/message-trace/trace?recipient=user@example.com" \
  -H "X-API-Key: YOUR_API_KEY"

# Full lifecycle of one message
curl "https://api.yourdomain.com/api/v1/message-trace/trace/MESSAGE_ID" \
  -H "X-API-Key: YOUR_API_KEY"
```

## Problem: Email Stuck in Queue

### Check Queue Size

```bash
# Summary line at the end
docker exec postfix postqueue -p | tail -1

# Or via the API
curl https://api.yourdomain.com/api/v1/queue/queue/status \
  -H "X-API-Key: YOUR_API_KEY"
```

### View a Specific Queued Message

```bash
# Get the queue ID from postqueue -p, then:
docker exec postfix postcat -q QUEUE_ID
```

### Common Causes

**DNS resolution failure:**

```bash
# Test the same lookups from the host
dig MX gmail.com +short
dig A gmail-smtp-in.l.google.com +short

# Check the container's DNS config
docker exec postfix cat /etc/resolv.conf
```

**Remote server rejecting connections:**

Look for these patterns in the log:

```
# Temporary rejection (will retry)
status=deferred (host ... said: 451 4.7.1 ... try again later)

# Permanent rejection (won't retry)
status=bounced (host ... said: 550 5.1.1 ... User unknown)
```

**Rate limiting by remote server:**

```
# Gmail rate limiting
status=deferred (host gmail-smtp-in.l.google.com said: 421-4.7.28 ... rate limit exceeded)
```

Solution: reduce `smtp_destination_concurrency_limit` in `mailer/postfix/config/main.cf` (baked into the image — rebuild the postfix container after editing).

### Flush the Queue

```bash
# Retry all deferred messages
docker exec postfix postqueue -f

# Or via the API
curl -X POST https://api.yourdomain.com/api/v1/queue/mail-queue/flush \
  -H "X-API-Key: YOUR_API_KEY"

# Delete all messages (careful!)
docker exec postfix postsuper -d ALL
```

## Problem: Email Bouncing

### Check Bounce Reasons

```bash
# Recent bounces
docker exec postfix grep "status=bounced" /var/log/postfix/mail.log | tail -20
```

### Common Bounce Codes

| Code | Meaning | Fix |
|------|---------|-----|
| 550 5.1.1 | User unknown | Check recipient address spelling |
| 550 5.7.1 | Relay access denied / sender mismatch | See below — on 587/465 the authenticated user must own the From address |
| 552 5.2.2 | Mailbox full | Recipient needs to clean up |
| 553 5.1.3 | Invalid address | Malformed email address |
| 554 5.7.1 | Rejected by policy | You're probably blacklisted |

!!! note "Sender-login mismatch on submission ports"
    Since 2026-08-22, ports 587 and 465 enforce that the authenticated login owns the `MAIL FROM` address. An application authenticating with one account (or an SMTP credential scoped to one domain) and sending as another gets `553/550` rejections. Fix the From address or mint a credential for the right domain — don't weaken the restriction.

### Check If You're Blacklisted

Run from the host (or any machine with `dig`):

```bash
IP="YOUR_SERVER_IP"
for bl in zen.spamhaus.org b.barracudacentral.org bl.spamcop.net; do
  reversed=$(echo $IP | awk -F. '{print $4"."$3"."$2"."$1}')
  result=$(dig +short ${reversed}.${bl})
  echo "$bl: ${result:-clean}"
done
```

## Problem: Email Going to Spam

### Check Authentication

```bash
# Verify SPF record
dig TXT yourdomain.com +short | grep spf

# Verify DKIM record
dig TXT default._domainkey.yourdomain.com +short

# Verify DMARC record
dig TXT _dmarc.yourdomain.com +short

# Verify PTR (reverse DNS)
dig -x YOUR_SERVER_IP +short
```

All four must be present and correct. The server-side check does all of them at once:

```bash
curl https://api.yourdomain.com/api/v1/domains/DOMAIN_ID/verify-dns \
  -H "X-API-Key: YOUR_API_KEY"
```

!!! warning "A published DKIM record does not mean mail is signed"
    Rspamd signs only when the domain's key file exists at `/var/lib/rspamd/dkim/` inside the rspamd container. If receivers report `dkim=none`, see [Setting Up DKIM](../setting-up-dkim.md).

### Test with mail-tester.com

1. Go to [mail-tester.com](https://www.mail-tester.com/)
2. Send an email to the address shown
3. Check your score — aim for 9/10 or higher

### Check Rspamd Scoring

```bash
# Scan statistics
docker exec rspamd rspamc stat

# Recent rejections / spam verdicts (host copy: logs/mailer/rspamd/rspamd.log)
docker logs rspamd 2>&1 | grep -iE "reject|spam|add header" | tail -20
```

## Problem: Inbound Email Not Arriving

First, rule out **Check Zero** above — a `transport_cutover` entry overrides everything else.

### Check MX Records

```bash
dig MX yourdomain.com +short
```

The MX record should point to your Mailyte server.

### Check Port 25 is Reachable

From an external server:

```bash
nc -zv mail.yourdomain.com 25
```

If it fails, your firewall or hosting provider is blocking port 25.

### Check the Domain and Recipient Exist and are Active

Postfix accepts mail only for domains with `active = 1`, and delivers only to accounts with status `active` (or matching aliases):

```bash
# Does the account exist?
curl "https://api.yourdomain.com/api/v1/mailboxes/email-accounts?q=user@yourdomain.com" \
  -H "X-API-Key: YOUR_API_KEY"
```

If the mailbox doesn't exist and no alias matches, Postfix rejects the message at RCPT time.

## Problem: Email Delayed (Slow Delivery)

### Check Delivery Times

```bash
# Look for the "delay=" field in Postfix logs
docker exec postfix grep "delay=" /var/log/postfix/mail.log | tail -20
```

The `delays` field format is `a/b/c/d` where:

- `a` = time before queue manager
- `b` = time in queue before active delivery
- `c` = connection setup time
- `d` = message transfer time

### Common Delay Causes

| High Value | Likely Cause |
|-----------|--------------|
| `a` is high | Rspamd scanning is slow, or content filter backlog |
| `b` is high | Queue is congested, increase process limits |
| `c` is high | DNS lookup slow, or remote server is slow to accept connections |
| `d` is high | Large message, or slow network |

## Problem: TLS/SSL Errors

```bash
# Check for TLS errors in the Postfix log
docker exec postfix grep -i "tls\|ssl\|certificate" /var/log/postfix/mail.log | tail -20
```

### Certificate Issues

```bash
# Check certificate (openssl is available in the postfix container)
docker exec postfix sh -c "openssl s_client -connect localhost:587 -starttls smtp < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject"
```

See [SSL Certificate Issues](ssl-certificate-issues.md) for more.

## Useful Log Locations

| Service | Where | What's in It |
|---------|-------|-------------|
| Postfix | `logs/mailer/postfix/mail.log` (host) = `/var/log/postfix/mail.log` (container) | Delivery attempts, bounces, connections |
| Dovecot | `docker logs dovecot` (logs to stderr) | IMAP/POP3 logins, LMTP delivery, errors |
| Rspamd | `logs/mailer/rspamd/rspamd.log` or `docker logs rspamd` | Spam scoring, DKIM results |
| API | `logs/worker/api/` | API requests, errors |
| Delivery history | `GET /api/v1/message-trace/trace` | Searchable per-message lifecycle |

## Still Stuck?

1. Check [Network Connectivity](network-connectivity.md) — port or DNS issues
2. Check [Service Failures](service-failures.md) — container might be crashing
3. Check [Database Performance](database-performance.md) — slow queries can delay delivery
4. Trace the specific message: find its queue ID in the Postfix log, or use `GET /api/v1/message-trace/trace/{message_id}` for the full journey
