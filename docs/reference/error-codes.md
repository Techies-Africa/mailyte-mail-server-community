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
| 403 | Forbidden | Valid key but insufficient permissions (or `mfa_required`) |
| 404 | Not Found | Resource doesn't exist — also returned for resources belonging to *another* organization, so tenant existence never leaks |
| 409 | Conflict | Resource already exists, or state prevents the operation (see codes below) |
| 422 | Unprocessable Entity | Validation failed (FastAPI validation errors, idempotency key reuse) |
| 429 | Too Many Requests | Brute-force lockout on API-key auth (20 invalid keys / 5 min per IP) |
| 500 | Internal Server Error | Something broke on our end |
| 501 | Not Implemented | Operation unavailable in this deployment (e.g. `CERT_RENEW_UNAVAILABLE`) |
| 503 | Service Unavailable | Dependency down or feature not configured (`ai_not_configured`, `sieve_not_configured`) |

### API Response Format

Success responses are typed per endpoint (Pydantic response models) — there is no single success envelope. Helper-built responses look like:

```json
{
  "type": "success",
  "msg": "Human-readable message",
  "data": { "...": "optional payload" }
}
```

**Error** responses share one envelope. A custom exception handler in the `api` service strips FastAPI's usual `detail` wrapper:

```json
{
  "type": "error",
  "msg": "Error description",
  "error_code": "MAILBOX_ALREADY_EXISTS",
  "correlation_id": "..."
}
```

- `type` and `msg` are always present.
- `error_code` is a stable machine-readable identifier, present only on the subset of errors that carry one (see the table below).
- `correlation_id` is injected by middleware into every JSON error body (status ≥ 400) and echoed in the `X-Correlation-Id` response header — quote it when reporting problems.
- Validation failures (422) from FastAPI itself still use the framework's `{"detail": [...]}` shape.

Other worker services (`tracking`, `rag`, `monitoring`, `webhooks`, …) have their own ad-hoc shapes — most emit FastAPI's default `{"detail": "..."}`.

### Stable `error_code` Values

These are the machine-readable codes the API actually emits (the casing really is mixed):

| `error_code` | HTTP | Where | Meaning |
|--------------|------|-------|---------|
| `DOMAIN_ALREADY_CLAIMED` | 409 | domains | Domain already exists (possibly under another organization) |
| `MAILBOX_ALREADY_EXISTS` | 409 | mailboxes | Email address taken |
| `IDEMPOTENCY_KEY_REUSED` | 422 | any idempotent write | Same `Idempotency-Key` reused with a different request body |
| `IDEMPOTENCY_IN_PROGRESS` | 409 | any idempotent write | Concurrent request with the same key still running (`Retry-After: 1` header set) |
| `CERT_RENEW_UNAVAILABLE` | 501 | ssl | Certificate renewal not available in this deployment |
| `legal_hold_active` | 409 | compliance | Deletion blocked by an active legal hold (`data.holds` lists them) |
| `mfa_required` | 403 | mailbox auth | Login requires a TOTP code |
| `not_in_trash` | 409 | mailbox | Permanent delete attempted on a message not in Trash |
| `cannot_revoke_current` | 409 | mailbox | Attempt to revoke the session in use |
| `ai_not_configured` | 503 | mailbox | AI features requested but no provider configured |
| `sieve_not_configured` | 503 | mailbox | Sieve/filter operation requested but ManageSieve not configured |

A replayed idempotent request returns the original response with the header `Idempotency-Replayed: true`.

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

Rspamd assigns a score and takes an action based on configured thresholds. The thresholds actually shipped (`mailer/rspamd/config/local.d/actions.conf`):

### Actions

| Action | Threshold | Effect |
|--------|-----------|--------|
| `no action` | < 4 | Message delivered normally |
| `greylist` | >= 4 | Temporary rejection (defer); skipped for authenticated/local senders and DKIM/SPF/DMARC-valid mail |
| `add header` | >= 6 | Add spam headers, deliver (the global Sieve script files it into Junk) |
| `rewrite subject` | >= 10 | Prepend `[SPAM]` to the subject |
| `reject` | >= 15 | Permanent rejection at SMTP time |

Per-organization overrides from the `settings.spam_policy` sync can replace these thresholds for a tenant's domains.

### Common Rspamd Symbols

Symbol scores are Rspamd's stock defaults (Mailyte doesn't override individual symbol scores), plus the Mailyte-specific multimap symbols with fixed scores:

| Symbol | Meaning |
|--------|---------|
| `SPF_ALLOW` / `SPF_FAIL` | SPF check passed / failed |
| `DKIM_ALLOW` / `DKIM_REJECT` | DKIM signature valid / invalid |
| `DMARC_POLICY_ALLOW` / `DMARC_POLICY_REJECT` | DMARC evaluation result |
| `BAYES_SPAM` / `BAYES_HAM` | Bayesian classifier verdict (active after 200 learns) |
| `RBL_SPAMHAUS_ZEN` | Listed on Spamhaus ZEN |
| `PHISHING` | Phishing URL detected (OpenPhish/PhishTank feeds) |
| `NEURAL_SPAM` | Neural-network classifier verdict |
| `LOCAL_FUZZY_DENIED` | Matches locally learned fuzzy spam hash |
| `ORG_SENDER_WHITELIST` (−5.0) / `ORG_SENDER_BLACKLIST` (+10.0) / `ORG_DOMAIN_WHITELIST` (−3.0) | Per-organization sender lists from Redis |
| `DISPOSABLE_EMAIL` (+3.0) | Sender uses a disposable-mail domain |
| `CLAM_VIRUS` | Virus detected — only if ClamAV has been deployed (disabled by default) |

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
