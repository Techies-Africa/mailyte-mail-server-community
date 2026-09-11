# Mailyte Deliverability Guide

An operator's guide to getting mail delivered — what the platform does for you, what you must configure, and what to check when messages land in spam. Everything here reflects the code as of 2026-08-30; the feature-level page is [docs/features/deliverability.md](features/deliverability.md).

## 1. Authentication: SPF, DKIM, DMARC

### What the platform does

- **DKIM signing is automatic** once a domain has a key. Creating a domain through the API (`POST /api/v1/domains`) generates a 2048-bit RSA keypair; Rspamd signs all authenticated/locally-originated mail with the key at `/var/lib/rspamd/dkim/{domain}.{selector}.key`. Domains without a key send unsigned (skipped, not rejected).
- **Record generation**: `GET /api/v1/domains/{id}` returns the records to publish; the autoconfig service's `GET /dns-records/{domain}` returns a fuller copy-paste set (including MTA-STS, TLS-RPT, and SRV records).
- **Live verification**: `GET /api/v1/domains/{id}/verify-dns` checks MX, SPF, DKIM (the active selector), and DMARC against real DNS. The SPF check performs genuine recursive evaluation (`include:` / `redirect=` / `ip4:` / `mx`, 10-lookup budget), so a record that authorizes the server without the literal include string still passes. This is a single verification engine — the two rival engines that could never both pass were consolidated on 2026-08-21.
- **Safe DKIM rotation**: `POST /api/v1/domains/{id}/dkim/rotate` creates a new selector; publish its TXT record, wait for propagation, then `POST /api/v1/domains/{id}/dkim/{selector}/activate` to cut signing over. Keep old selectors' records published until mail signed with them ages out of retry queues.

### What you must do

Publish, per customer domain:

```
MX      <domain>                    → 10 <mail hostname>.
TXT     <domain>                    → v=spf1 include:<spf host> ~all
TXT     <selector>._domainkey.<domain> → v=DKIM1; k=rsa; p=<public key>
TXT     _dmarc.<domain>             → v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@<domain>
```

The MX/advertised hostname comes from `MAIL_HOSTNAME`; the SPF include host from `MAIL_SPF_HOST` (falling back to `spf.<mail hostname>` — make sure that name actually resolves, or publish `v=spf1 mx a:<mail hostname> ~all` instead, which the recursive verifier also accepts).

Start DMARC at `p=none` or `p=quarantine` while you watch reports; move to `p=reject` when aggregate reports are clean.

## 2. Sender identity enforcement

Authenticated senders can only use addresses their login owns:

- On **587 and 465**, `reject_sender_login_mismatch` runs *before* `permit_sasl_authenticated` (order matters — reversed, the check is dead code).
- Ownership comes from `smtpd_sender_login_maps` (MySQL): a mailbox owns its own address and aliases; an [SMTP API key](features/smtp-credentials.md) owns every address at its domain.

**Never add `reject_sender_login_mismatch` to the global `smtpd_sender_restrictions` in `main.cf`.** Port 25 inherits the global value, and the check then rejects legitimate *inbound* mail from any external sender whose address happens to have a mailbox here (`553 5.7.1 ... not logged in`). This exact mistake broke inbound production mail on 2026-08-22 — the full analysis is in comments in `mailer/postfix/config/main.cf`. Submission-port enforcement only.

## 3. Outbound pacing, reputation, and warming

The Delivery Optimizer runs **on the live send path**: the Postfix content filter consults `POST /check` for every recipient domain before a message proceeds, sleeps the returned `delay_ms`, and tempfails (Postfix retries) when an hourly/daily/burst/warming cap is hit. Per-ISP defaults (Gmail 100/hr, Outlook 300/hr, Yahoo 200/hr, etc.) are seeded into Redis and adjustable via `PUT /isp-limits/{domain}`.

- **New IP?** Create a warming schedule (`POST /warming/schedule`) — the daily warming cap is enforced on the send path. Ramp over 2–4 weeks.
- **Reputation** (`GET /reputation/{domain}?organization_id=`) is a 0–100 score computed from bounce and complaint counters; `optimizer.reputation.changed` webhooks fire as it moves.
- If the optimizer is unreachable, the filter paces down by a fixed conservative delay rather than blasting unthrottled.

## 4. Bounces, complaints, and suppression

- Hard bounces (`POST /bounce`, `bounce_type: hard`) and abuse-type FBL reports (`POST /feedback-loop`) are recorded in `bounce_events` and written to the per-organization `suppression_list`. The tracking service's bounce/complaint/unsubscribe endpoints feed the same list with thresholds (hard = immediate, soft = after repeated failures).
- **Known gap:** nothing on the outbound path currently *enforces* the suppression list — a suppressed address still receives mail if your application sends to it. Check the list from your sending application until MTA-level enforcement ships.
- FBL enrollment (Google Postmaster Tools, Microsoft SNDS/JMRP, Yahoo CFL) is a manual, per-ISP registration; the platform processes reports once you receive them.

## 5. Inbound filtering choices that protect your outbound reputation

- Weighted postscreen DNSBLs (threshold-based) instead of a stack of hard-reject RBLs — a single aggressive list can no longer veto legitimate mail.
- Rspamd's after-scan actions: greylist ≥ 4, junk-folder header ≥ 6, `[SPAM]` subject ≥ 10, reject ≥ 15. Per-organization overrides sync from MySQL to Redis via `scripts/sync_rspamd_settings.py`.
- `milter_default_action = accept`: an Rspamd outage means unscored mail, not bounced mail.

## 6. Transport encryption

- Inbound/outbound SMTP: opportunistic TLS, TLS 1.2+ only, strong ciphers; submission requires TLS + SASL.
- **MTA-STS** is served by the autoconfig service (`/.well-known/mta-sts.txt`, routed for `mta-sts.<domain>` hosts). It starts in `testing` mode (`MTA_STS_MODE`); switch to `enforce` after confirming TLS delivery from major senders, and publish the `_mta-sts` TXT + `mta-sts` CNAME records.
- **TLS-RPT** record (`_smtp._tls`) is included in the generated DNS set so receivers can report TLS failures to you.

## 7. Monitoring delivery

- **Delivery events**: `email.delivered` / `email.bounced` / `email.deferred` / `email.rejected` webhooks and the `mail_logs` table come from the log ingestor tailing the Postfix log (`logs/mailer/postfix/mail.log` on the host).
- **Analytics**: per-domain volume, engagement, and deliverability at `/api/v1/analytics/...`.
- **Message trace**: `/api/v1/message-trace` follows a single message through the platform by its `X-Mailyte-ID`.

## 8. Troubleshooting checklist

1. `GET /api/v1/domains/{id}/verify-dns` — all four checks green?
2. Send to a Gmail account and inspect **Show original** — SPF/DKIM/DMARC all `PASS`? Which selector signed?
3. Check `mail_logs` (or the Email Logs UI) for the DSN code on bounces — `4.x.x` deferrals often mean ISP throttling (check optimizer counters), `5.7.x` means policy/authentication.
4. Reputation score < 70? Look at bounce and complaint rates per recipient domain.
5. Mail vanishing without log entries at all? Verify the `postlog` service and `maillog_file` are intact (Postfix logs nowhere without them), and check `transport_maps` (`/etc/postfix/custom/transport_cutover`) for stale cutover routes.
