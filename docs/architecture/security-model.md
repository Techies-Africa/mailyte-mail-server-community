# Security Model

How Mailyte protects itself, its tenants, and the email it handles — from authentication to encryption to threat detection. Verified against the deployed configuration as of 2026-08-30.

---

## Authentication

Mailyte has several authentication paths, each scoped to a different audience.

### API Keys (`X-API-Key` header)

The primary method for programmatic access:

```http
GET /api/v1/domains
X-API-Key: mlt_a1b2c3d4e5f6...
```

API keys are:

- Scoped to a single organization (tenant isolation).
- Stored hash-only in MySQL (SHA-256 in `key_hash`; `key_id` is a non-secret display id) — raw keys are never stored, since 2026-08-30 / migration `0019`.
- Expiring: `expires_at` is enforced at authentication (expired keys get 401).
- Revocable at any time (`active = 0` takes effect on the next request).
- `ip_whitelist` and per-key `rate_limit` columns exist but are **not yet enforced** by the auth path.

**Platform scope**: cross-tenant operations (creating organizations, service restarts, compliance actions, the cross-tenant directory) require a credential with `scope='platform'`. A tenant-scoped key can never reach these endpoints, regardless of its own permission flags.

!!! info "There is no `X-Admin-Password` anymore"
    Earlier versions used an `X-Admin-Password` header for cross-org operations. That path has no remaining consumer — it was replaced by platform-scoped API keys and real operator identities. (`X-Admin-Token` survives only as a pass-through to the monitoring service's own restart gate, layered *on top of* an admin-scoped API key.)

### Operator sessions (console)

Platform operators sign in through `POST /api/v1/platform/auth/login` with **mandatory TOTP MFA** (backed by the `totp` service). Operator sessions are **IP-bound**, and the console itself sits behind a Traefik IP allowlist that fails closed (loopback-only unless `CONSOLE_ALLOWED_IPS` is set). First-boot operator creation uses a single-use bootstrap token written by the API only while no operator exists.

### Browser sessions

For deployments where a browser talks to this API directly, `POST /api/v1/auth/*` exchanges credentials for a short-lived HttpOnly session cookie with CSRF protection — nothing long-lived sits in `localStorage`.

### Webmail (mailbox holder) sessions

Webmail login (`/api/v1/mailbox-auth`) verifies the password **against Dovecot over IMAP** — not against a separately stored copy. (A stale password copy is exactly what broke webmail after the 2026 migration; fixed 2026-08-21.) The session then holds the SMTP credential used for sends.

### Email protocol authentication

When clients connect via SMTP (587/465) or IMAP/POP3, Postfix delegates SASL auth to Dovecot (`smtpd_sasl_type = dovecot`), which checks MySQL:

- Mailbox passwords are **bcrypt** hashes.
- **Per-mailbox SMTP credentials** (live since 2026-08-27) authenticate through a dedicated Dovecot SQL passdb, so an application can send without holding the mailbox password.
- `reject_sender_login_mismatch` on 587/465 (evaluated *before* `permit_sasl_authenticated`) means you can only send from addresses your login owns. It is deliberately **not** applied to port 25, which carries unauthenticated inbound mail.

!!! warning "Revocation vs. the Dovecot auth cache"
    Dovecot caches auth results for up to 1 hour (`auth_cache_ttl`). Revoking or rotating an SMTP credential through the API flushes the cache via the doveadm HTTP API (internal port 24180) so revocation is immediate, and since 2026-08-30 mailbox mutations through the API (password change, suspend/deactivate, delete — CRUD and legacy routes) flush the same way. Changes made any other way (direct SQL edits, restore scripts) keep authenticating until the cache expires — flush it or the change isn't enforced.

## Encryption (TLS Everywhere)

Every external-facing connection uses TLS:

| Protocol | Port | TLS Mode |
|----------|------|----------|
| SMTP (server-to-server) | 25 | Opportunistic STARTTLS |
| SMTP (client submission) | 587 | Required STARTTLS |
| SMTP (client submission) | 465 | Implicit TLS |
| IMAP / IMAPS | 143 / 993 | STARTTLS / Implicit TLS |
| POP3 / POP3S | 110 / 995 | STARTTLS / Implicit TLS |
| ManageSieve | 4190 | STARTTLS |
| All HTTP surfaces (API, webmail, console, docs, JMAP, CalDAV, autoconfig) | 443 | TLS terminated by Traefik |

Internal service-to-service communication within the Docker network is unencrypted — it never leaves the host.

**cert_manager** issues and renews all certificates (ACME HTTP-01 via a shared webroot; Traefik's built-in ACME is deliberately disabled because two ACME clients fought over the same challenges in production). It also maintains per-domain **SNI** certificates so customers can point `mail.theirdomain.com` at the server, and reloads Postfix/Dovecot after rotation — through the scoped docker-proxy, never the raw Docker socket.

## Secrets & Key Material

- **Fail-closed startup**: the `secrets-check` container validates every security-critical secret (DB passwords, webhook/OAuth/URL-HMAC secrets, admin credentials, Grafana password) before anything else starts. A missing or known-weak value stops the whole stack — the alternative was an internet-facing mail server running on published default passwords.
- **File-mounted root credential**: MySQL's root password comes from a 0400-mounted file, not an environment variable (env vars on a running container are readable by anything with Docker API access, for the container's whole lifetime).
- **Envelope encryption**: DKIM private keys (and PGP/S-MIME material) are wrapped with a KEK mounted read-only at `/run/secrets/encryption_kek` — a database dump alone does not expose signing keys.
- **Scoped Docker access**: nothing in the stack touches `/var/run/docker.sock` except the `docker-proxy` container, which exposes exactly list/inspect/restart — no exec, no images, no volumes — on an isolated internal-only network.
- **Container hardening**: mail containers run with `no-new-privileges`, `cap_drop: ALL`, and only the specific capabilities each daemon's own privilege-separation model needs.

## DKIM Signing

Every outgoing email is signed with a per-domain DKIM signature — by **Rspamd**, via the milter, not by Postfix itself:

1. When a domain is added via the API, Mailyte generates a DKIM keypair.
2. The private key is stored envelope-encrypted in MySQL; the public key is handed back as the DNS TXT record to publish.
3. Rspamd resolves the signing key per sending domain and signs headers and body.
4. Receiving servers verify against the public key in DNS.

Combined with the SPF and DMARC records the API also provides (and verifies), this is what keeps mail out of spam folders.

## Network Exposure

Locked down on 2026-08-22:

- In production, **only** the mail protocol ports (25, 465, 587, 143, 993, 110, 995, 4190) and Traefik's 80/443 listen on `0.0.0.0`. Every internal service — including Prometheus, Qdrant, the Rspamd UI, and Kafka, all of which were previously answering the public internet without authentication — is bound to `127.0.0.1`.
- Docker publishes ports past `ufw` by writing its own iptables rules, so **the bind address is the control**, not the host firewall.
- MySQL and Redis publish no host port at all.
- The staff console is additionally IP-allowlisted at Traefik and fails closed.

## Threat Detection & Abuse Controls

What actually runs (the fail2ban-based `mailer/intrusion_detection/` configs exist in the repo but are **not deployed** — no compose file includes them):

- **RBLs at the edge**: Postfix rejects clients listed on `zen.spamhaus.org` before the message body is ever accepted.
- **Connection limits**: Postfix's own per-client connection and message rate limits.
- **Rate limiting as policy**: the `rate_limiter` service is consulted by Postfix at the SMTP DATA phase (inbound) and on the submission path (outbound), plus per-key API rate limits at the gateway.
- **Failed-auth tracking**: the gateway records failed logins (`failed_auth_attempts`), throttles repeated attempts, and uses constant-time dummy hashing so user enumeration by timing doesn't work.
- **Per-org IP access rules**: enforced by Postfix's `ip_access_policy` service against the `ip_access_rules` table.
- **DLP**: the `dlp` service scans for PII (Luhn-validated card numbers, SSNs, IBANs) and per-org keyword/regex policies, with block/quarantine/notify actions and violations logged to MySQL.
- **Geo-blocking**: the `geo_blocking` service applies GeoIP country policies.
- **Auto-suspension hook**: `log_ingestor` can auto-suspend abusive SMTP credentials (`AUTO_SUSPEND_ENABLED`, default **off** until thresholds are validated against real traffic).

## Rate Limiting as Security

| What | Scope | Enforced where |
|------|-------|----------------|
| Inbound mail | Per sender | Postfix DATA-phase policy → rate_limiter |
| Outbound submissions | Per authenticated sender/org | Postfix submission restriction class → rate_limiter |
| API requests | Per API key (`rate_limit` column) | Gateway |
| Login attempts | Per account/IP | Gateway lockout logic |
| Connections/messages | Per client IP | Postfix built-ins |

## Data Isolation Per Organization

1. **API layer**: every request is scoped to the org that owns the credential; cross-tenant reach requires platform scope.
2. **Database layer**: queries include `WHERE organization_id = ?`.
3. **Worker layer**: workers process per-org and never mix tenants.
4. **Mail layer**: Dovecot's virtual mailbox layout isolates Maildirs; Postfix's sender-login maps stop cross-account sending.

See [Multi-Tenant Isolation](multi-tenant.md) for the full breakdown.

## Audit Trail

Significant actions land in `audit_logs` (who — user/API key/operator, what, which resource, when, from where), and every gateway request carries a correlation ID that is stamped on the response and folded into error bodies, so an upstream system can log it alongside its own job IDs. Operator actions are additionally recorded with the operator's email denormalised, so the trail stays readable after an operator account is renamed or removed.

## Security Checklist

When deploying Mailyte, make sure:

- [ ] `scripts/generate-secrets.sh` has been run and `secrets-check` passes — the stack will not start on weak defaults.
- [ ] API keys use the minimum permissions needed; platform scope only where genuinely required.
- [ ] TLS certificates are valid and auto-renewing (`cert_manager` logs, not just browser checks).
- [ ] DKIM, SPF, and DMARC records are configured and verified for every domain.
- [ ] `docker-compose.prod.yml` is in use — it is what binds internal ports to loopback.
- [ ] The console's `CONSOLE_ALLOWED_IPS` is set (it fails closed without it — nobody gets in).
- [ ] Rate limits are set to sane defaults.
- [ ] MySQL and Redis publish no host ports (they don't, in the shipped compose files — keep it that way).
- [ ] Backups exist, are encrypted, and at least one copy is off the mail host.
