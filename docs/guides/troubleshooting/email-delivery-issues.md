---
title: "Troubleshooting: Email Delivery Issues"
description: Email not arriving? Step-by-step diagnosis for queue backlogs, bounces, DNS problems, spam filtering, and blacklisting.
---

# Email Delivery Issues

Email isn't arriving. Let's figure out why. Work through these checks in order — they're arranged from most common to least common causes.

## Quick Diagnosis

Run this first to get a snapshot of the system:

```bash
# Service health
docker compose ps

# Mail queue status
docker exec -it postfix postqueue -p

# Recent delivery attempts
docker exec -it postfix tail -50 /var/log/postfix/maillog

# Rspamd status
docker exec -it rspamd rspamc stat
```

## Problem: Email Stuck in Queue

### Check Queue Size

```bash
# Count queued messages
docker exec -it postfix postqueue -p | tail -1

# List all queued messages
docker exec -it postfix postqueue -p
```

### View a Specific Queued Message

```bash
# Get the queue ID from postqueue -p, then:
docker exec -it postfix postcat -q QUEUE_ID
```

### Common Causes

**DNS resolution failure:**

```bash
# Test DNS from inside the container
docker exec -it postfix dig MX gmail.com +short
docker exec -it postfix dig A gmail-smtp-in.l.google.com +short
```

If DNS fails, check your container's DNS config:

```bash
docker exec -it postfix cat /etc/resolv.conf
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

Solution: reduce `smtp_destination_concurrency_limit` in Postfix config.

### Flush the Queue

```bash
# Retry all deferred messages
docker exec -it postfix postqueue -f

# Delete all messages (careful!)
docker exec -it postfix postsuper -d ALL
```

## Problem: Email Bouncing

### Check Bounce Reasons

```bash
# Recent bounces
docker exec -it postfix grep "status=bounced" /var/log/postfix/maillog | tail -20
```

### Common Bounce Codes

| Code | Meaning | Fix |
|------|---------|-----|
| 550 5.1.1 | User unknown | Check recipient address spelling |
| 550 5.7.1 | Relay access denied | Your server isn't authorized for the domain |
| 552 5.2.2 | Mailbox full | Recipient needs to clean up |
| 553 5.1.3 | Invalid address | Malformed email address |
| 554 5.7.1 | Rejected by policy | You're probably blacklisted |

### Check If You're Blacklisted

```bash
# Quick blacklist check
IP="YOUR_SERVER_IP"
for bl in zen.spamhaus.org b.barracudacentral.org bl.spamcop.net; do
  result=$(docker exec -it postfix dig +short $(echo $IP | awk -F. '{print $4"."$3"."$2"."$1}').$bl)
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

All four must be present and correct.

### Test with mail-tester.com

1. Go to [mail-tester.com](https://www.mail-tester.com/)
2. Send an email to the address shown
3. Check your score — aim for 9/10 or higher

### Check Rspamd Scoring

```bash
# See how Rspamd scores your outgoing mail
docker exec -it rspamd rspamc stat

# Check for specific issues
docker exec -it rspamd grep -i "reject\|spam\|add header" /var/log/rspamd/rspamd.log | tail -20
```

## Problem: Inbound Email Not Arriving

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

### Check Postfix is Listening

```bash
docker exec -it postfix ss -tlnp | grep 25
```

### Check if the Recipient Exists

```bash
# Check via API
curl http://mail.yourdomain.com:8083/api/v1/get/mailbox/user@yourdomain.com \
  -H "X-API-Key: YOUR_API_KEY"
```

If the mailbox doesn't exist and there's no catch-all alias, Postfix rejects the email.

## Problem: Email Delayed (Slow Delivery)

### Check Delivery Times

```bash
# Look for the "delay=" field in Postfix logs
docker exec -it postfix grep "delay=" /var/log/postfix/maillog | tail -20
```

The `delay` field format is `a/b/c/d` where:

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
# Check for TLS errors in Postfix log
docker exec -it postfix grep -i "tls\|ssl\|certificate" /var/log/postfix/maillog | tail -20
```

### Certificate Issues

```bash
# Check certificate
docker exec -it postfix openssl s_client -connect localhost:587 -starttls smtp < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject
```

See [SSL Certificate Issues](ssl-certificate-issues.md) for more.

## Useful Log Locations

| Service | Log Path | What's in It |
|---------|----------|-------------|
| Postfix | `logs/mailer/postfix/maillog` | Delivery attempts, bounces, connections |
| Dovecot | `logs/mailer/dovecot/` | IMAP/POP3 logins, errors |
| Rspamd | `logs/mailer/rspamd/rspamd.log` | Spam scoring, DKIM results |
| API | `logs/worker/api/` | API requests, errors |
| Webhooks | `logs/worker/webhooks/` | Webhook deliveries |

## Still Stuck?

1. Check [Network Connectivity](network-connectivity.md) — port or DNS issues
2. Check [Service Failures](service-failures.md) — container might be crashing
3. Check [Database Performance](database-performance.md) — slow queries can delay delivery
4. Check the Postfix log for the specific message ID and trace its journey
