---
title: Error Codes
description: API error codes, SMTP status codes, Rspamd action codes, and DSN codes with explanations.
---

# Error Codes

Three systems produce error codes: the API, SMTP (Postfix), and Rspamd. This page covers all of them.

## API Error Codes

### HTTP Status Codes

| Code | Meaning | When you'll see it |
|------|---------|-------------------|
| 200 | OK | Request succeeded |
| 201 | Created | Resource created |
| 400 | Bad Request | Invalid input, missing fields |
| 401 | Unauthorized | Missing or invalid API key |
| 403 | Forbidden | Valid key but insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Resource already exists (duplicate domain, email) |
| 422 | Unprocessable Entity | Validation failed (bad email format, etc.) |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Something broke on our end |
| 503 | Service Unavailable | Database or dependency down |

### API Response Format

**Success:**

```json
{
  "type": "success",
  "msg": ["action_completed", "item_name"],
  "log": ["entity", "action", "object", "data"]
}
```

**Error:**

```json
{
  "type": "error",
  "msg": "Error description"
}
```

### Common API Errors

| Error Message | Cause | Fix |
|--------------|-------|-----|
| `domain_already_exists` | Domain already added | Check existing domains |
| `domain_not_found` | Domain doesn't exist in DB | Verify domain name |
| `mailbox_already_exists` | Email address taken | Use a different local part |
| `mailbox_quota_exceeded` | Would exceed domain quota | Increase domain quota or reduce mailbox count |
| `invalid_domain_format` | Domain name malformed | Check for typos, use lowercase |
| `password_too_short` | Password doesn't meet requirements | Use at least 8 characters |
| `organization_not_found` | Org ID doesn't exist | Create the org first |
| `rate_limit_exceeded` | Too many API calls | Wait and retry, or increase limit |
| `api_key_expired` | API key past expiry date | Generate a new key |
| `ip_not_whitelisted` | Request from non-whitelisted IP | Add IP to key whitelist |

## SMTP Status Codes

SMTP uses 3-digit codes. The first digit indicates the category.

### 2xx — Success

| Code | Meaning |
|------|---------|
| 250 | OK, message accepted |
| 251 | User not local, will forward |

### 4xx — Temporary Failure (will retry)

| Code | Meaning | What to do |
|------|---------|------------|
| 421 | Service not available | Server is overloaded, retry later |
| 450 | Mailbox unavailable | Temporary issue, will retry |
| 451 | Local error | Server-side issue, check logs |
| 452 | Insufficient storage | Disk full on receiving server |

### 5xx — Permanent Failure (won't retry)

| Code | Meaning | What to do |
|------|---------|------------|
| 500 | Syntax error | Malformed command |
| 501 | Parameter syntax error | Bad email address format |
| 502 | Command not implemented | Server doesn't support that command |
| 503 | Bad sequence | Commands sent in wrong order |
| 504 | Parameter not implemented | Feature not available |
| 550 | Mailbox unavailable | User doesn't exist, or policy rejection |
| 551 | User not local | Recipient not on this server |
| 552 | Storage exceeded | Recipient's mailbox is full |
| 553 | Mailbox name invalid | Bad email address |
| 554 | Transaction failed | General rejection (blacklisted, policy) |

## Enhanced Status Codes (DSN)

DSN codes are 3-part codes (`x.y.z`) that give more detail. The first digit mirrors the SMTP category.

### Class 2.x.x — Success

| Code | Meaning |
|------|---------|
| 2.0.0 | Generic success |
| 2.1.0 | Address valid |
| 2.1.5 | Destination address valid |
| 2.6.0 | Content accepted |

### Class 4.x.x — Temporary Failure

| Code | Meaning |
|------|---------|
| 4.0.0 | Temporary failure |
| 4.1.0 | Address temporarily unavailable |
| 4.2.0 | Mailbox temporarily unavailable |
| 4.2.2 | Mailbox full (temporary) |
| 4.3.0 | Mail system full |
| 4.3.1 | Mail system not accepting messages |
| 4.3.2 | System not accepting network messages |
| 4.4.1 | Connection timed out |
| 4.4.2 | Connection dropped |
| 4.7.0 | Temporary authentication failure |
| 4.7.1 | Delivery not authorized (greylisting) |

### Class 5.x.x — Permanent Failure

| Code | Meaning |
|------|---------|
| 5.0.0 | Generic permanent failure |
| 5.1.0 | Bad destination address |
| 5.1.1 | Bad destination mailbox (user unknown) |
| 5.1.2 | Bad destination system (domain doesn't exist) |
| 5.1.3 | Bad destination mailbox syntax |
| 5.2.0 | Mailbox disabled |
| 5.2.1 | Mailbox full (permanent) |
| 5.2.2 | Mailbox full |
| 5.3.0 | Mail system is full |
| 5.3.4 | Message too big |
| 5.4.1 | No answer from host |
| 5.4.4 | Unable to route |
| 5.5.0 | Protocol error |
| 5.7.0 | Security or policy failure |
| 5.7.1 | Delivery not authorized (rejected) |
| 5.7.13 | Sender not authenticated |
| 5.7.23 | SPF validation failed |
| 5.7.25 | Reverse DNS validation failed |
| 5.7.26 | Authentication failure (multiple mechanisms) |

## Rspamd Action Codes

Rspamd assigns a score and takes an action based on configured thresholds.

### Actions

| Action | Default Threshold | Effect |
|--------|------------------|--------|
| `no action` | < 4 | Message delivered normally |
| `greylist` | >= 4 | Temporary rejection (defer) |
| `add header` | >= 6 | Add `X-Spam: Yes` header, deliver |
| `rewrite subject` | >= 6 | Prepend `[SPAM]` to subject |
| `soft reject` | >= 10 | Temporary rejection with 4xx |
| `reject` | >= 15 | Permanent rejection with 5xx |

### Common Rspamd Symbols

| Symbol | Score | Meaning |
|--------|-------|---------|
| `SPF_ALLOW` | -0.2 | SPF check passed |
| `SPF_FAIL` | +4.0 | SPF check failed |
| `DKIM_ALLOW` | -0.1 | DKIM signature valid |
| `DKIM_REJECT` | +6.0 | DKIM signature invalid |
| `DMARC_POLICY_ALLOW` | -0.5 | DMARC policy passed |
| `DMARC_POLICY_REJECT` | +4.0 | DMARC policy failed |
| `BAYES_SPAM` | +5.0 | Bayesian filter says spam |
| `BAYES_HAM` | -3.0 | Bayesian filter says not spam |
| `RBL_SPAMHAUS_ZEN` | +4.0 | Listed on Spamhaus ZEN |
| `MIME_GOOD` | -0.1 | Well-formed MIME |
| `MIME_BAD` | +1.0 | Malformed MIME |
| `FORGED_SENDER` | +3.0 | From header doesn't match envelope |
| `R_DKIM_ALLOW` | -0.2 | Reputation-adjusted DKIM pass |
| `MISSING_MID` | +2.5 | No Message-ID header |
| `URL_REDIRECTOR` | +1.5 | Links through URL shortener |
| `PHISHING` | +6.0 | Phishing URL detected |

## Postfix Rejection Codes

These appear in the Postfix log as `NOQUEUE: reject`:

| Message Pattern | Meaning |
|----------------|---------|
| `Relay access denied` | Trying to send through your server without auth |
| `Recipient address rejected: User unknown` | Mailbox doesn't exist |
| `Sender address rejected: Domain not found` | Sender domain has no MX/A record |
| `Client host rejected: Access denied` | IP is in a block list |
| `Message size exceeds fixed limit` | Email too large |
| `too many errors after RCPT` | Client sent too many invalid recipients |
