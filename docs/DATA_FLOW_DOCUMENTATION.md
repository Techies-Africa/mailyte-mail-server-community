# Data Flow Documentation

> This top-level file is a quick orientation for people browsing the repository.
> The maintained, diagrammed version lives in the handbook at
> [`docs/architecture/data-flow.md`](architecture/data-flow.md).
>
> (Note: this path existed as an empty directory until 2026-08-30 — a Docker
> bind-mount artifact, not a lost document.)

All flows below are verified against `mailer/postfix/config/main.cf`, `master.cf`, and the compose files as of 2026-08-30.

## Inbound (internet → mailbox)

```
Remote MTA → Postfix :25
  → client/recipient restrictions (RBLs, MySQL domain/mailbox lookups, per-org IP policy)
  → Rspamd milter (SPF/DKIM/DMARC verification, Bayes; milter_default_action=accept)
  → rate-limit policy service at the DATA phase (HTTP → rate_limiter service)
  → LMTP → Dovecot :24 → Maildir under /var/mail/vhosts
  → global Sieve pipes a copy → archiver (age-encrypted → S3, local spool fallback)
  → log_ingestor tails mail.log → mail_logs row + email.delivered/... webhook
```

No sender-login check and no tracking filter on port 25 — both are deliberate (each broke production when applied there; see the comments in `main.cf`/`master.cf`).

## Outbound (client → internet)

```
Client → Postfix :587/:465 (SASL via Dovecot; mailbox password or SMTP credential)
  → reject_sender_login_mismatch (before permit_sasl_authenticated)
  → rate-limit restriction class
  → content_filter=tracking-filter → tracking_injector.py
      (tracking service: pixel + link rewrite; delivery_optimizer; archiver copy)
  → reinjection :10026 → Rspamd milter signs DKIM → Postfix spool → remote MX
  → log_ingestor → mail_logs + email.delivered/bounced/deferred webhooks
```

## Webmail / API sends

The gateway (`worker/api`) submits over SMTP to Postfix's **internal** submission listener `postfix:10587` (which carries the tracking filter) — never port 25. There is **no application-level outbound queue**: the queue is Postfix's own spool (a named volume), and `queue_manager` manages it via `postqueue`.

## Tracking events

Opens hit `https://api.<domain>/api/v1/tracking/pixel/{id}`; clicks hit the tracking redirect. The gateway proxies both to the `tracking` service, which records to MySQL and fires `tracking.open` / `tracking.click` webhooks.

## Logs and their producers

| Data | Table / location | Producer |
|------|------------------|----------|
| Per-message delivery records | `mail_logs` | `mailer/log_ingestor` (added 2026-08-22; before that the table had no producer) |
| Open/click events | `email_tracking`, `tracking_statistics` | tracking service |
| Webhook attempts / failures | `webhook_delivery_logs`, `webhook_dead_letters` | `shared/webhook_dispatcher.py` |
| API mutations | `audit_logs` | gateway |
| Auth failures | `failed_auth_attempts` | gateway |
