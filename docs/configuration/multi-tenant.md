# Multi-Tenant Configuration

Per-organization settings, quotas, rate limits, SMTP credentials, and webhook routing.

---

Mailyte supports multiple organizations on a single server. Each tenant gets their own domains, mailboxes, and configuration — but they all share the same infrastructure.

## How Multi-Tenancy Works

Every tenant is an **organization**. An organization owns one or more domains, and each domain has mailboxes, aliases, and SMTP credentials:

```
Organization (tenant)
  └── Domain (acme.com)
        ├── Mailbox (user@acme.com)
        ├── Alias   (info@acme.com → user@acme.com)
        └── SMTP credential (acme-com-smtp-a1b2c3d4)
```

All tenant data lives in MySQL. Postfix and Dovecot query the database for every lookup, so tenant isolation happens at the data layer — there's no per-tenant config file anywhere.

The API is reached with an `X-API-Key` header. Keys are scoped: an **organization-scoped** key sees only its own tenant's resources (cross-tenant lookups return 404, never 403), while a **platform-scoped** key (and platform operators) can manage all tenants.

## Creating an Organization

```bash
curl -X POST https://api.yourdomain.com/api/v1/organizations/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $PLATFORM_API_KEY" \
  -d '{
    "id": "acme-corp",
    "name": "Acme Corp",
    "admin_email": "admin@acme.com",
    "external_id": "billing-4711"
  }'
```

Creating tenants requires an admin/platform-scoped key. For first-time setup on a fresh install, use `./scripts/setup-first-user.sh`, which drives `POST /api/v1/bootstrap`.

## Per-Organization Quotas

Storage quotas live at three levels, all in the database:

- `organizations.storage_quotas` (JSON) — org-wide quotas
- `domains.max_quota` (default 10 GB) — per-mailbox cap within a domain
- `email_accounts.storage_quota` (default 1 GB) — per-mailbox quota, enforced live by Dovecot through the SQL `user_query`

```bash
# Update an organization's quotas and rate limits
curl -X PUT https://api.yourdomain.com/api/v1/organizations/acme-corp/quotas \
  -H "Content-Type: application/json" -H "X-API-Key: $ADMIN_KEY" \
  -d '{"storage_quotas": {"total_storage_mb": 512000}, "rate_limits": {"outbound_hourly": 2000}}'
```

> [!NOTE]
> A quota write by a human operator sets an **override flag** on the organization; automated plan-sync writes are then refused with 409 until the override is cleared via `POST /api/v1/organizations/{id}/quotas/clear-override`. This keeps a hand-set quota from being silently reverted by billing sync.

Per-mailbox overrides go through the mailbox endpoints (`PUT /api/v1/mailboxes/email-accounts/{account_id}` with `storage_quota`, or the dedicated `.../email-accounts/{account_id}/quotas`); Dovecot picks the new value up on the next login — no reload needed.

## Per-Organization Rate Limits

Rate limits are enforced by the `rate_limiter` service, consulted by Postfix's policy daemon at DATA time. Limits resolve through a hierarchy — organization → domain → mailbox (or SMTP credential) — with database values overriding environment defaults:

| Level | Outbound hourly default | Env var family |
|-------|------------------------|----------------|
| Organization | 10,000 | `ORG_{INBOUND,OUTBOUND}_{SECOND,MINUTE,HOURLY,DAILY,MONTHLY,BURST}_DEFAULT` |
| Domain | 2,000 | `DOMAIN_*_DEFAULT` |
| Mailbox | 200 | `MAILBOX_*_DEFAULT` |

Manage limits through the API gateway:

```bash
# Read / set domain limits
curl -H "X-API-Key: $KEY" https://api.yourdomain.com/api/v1/rate-limiter/rate-limits/domain/acme.com
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  https://api.yourdomain.com/api/v1/rate-limiter/rate-limits/domain/acme.com \
  -d '{"outbound_hourly": 5000}'

# Same shape for a mailbox
.../api/v1/rate-limiter/rate-limits/mailbox/user@acme.com
```

**SMTP credentials carry their own per-key limits** (`hourly_limit` / `daily_limit` on the credential; `NULL` = inherit the organization's limits). An authenticated sender without an `@` in its SASL username is resolved as an SMTP credential and checked as organization → domain → credential.

When a limit trips, the send is deferred at SMTP time and a `rate_limit.exceeded` webhook fires; warning/critical thresholds (80% / 95%) fire `rate_limit.threshold_breach`.

> [!TIP]
> Start with conservative rate limits for new tenants. It's much easier to raise limits for a good sender than to recover IP reputation after a tenant sends spam.

## Per-Organization SMTP Credentials

Each domain can have any number of SMTP API keys (`/api/v1/smtp-credentials/`): create, rotate, revoke, enable, per-key IP allowlists, expiry, and per-key rate limits — with an append-only audit trail (`GET .../events`) and usage reporting (`GET .../usage`, backed by `mail_logs.sasl_username`). The secret is returned exactly once at create/rotate. Authentication is enforced by Dovecot directly against the `smtp_credentials` table, so revocation applies at the next AUTH (the API flushes Dovecot's auth cache via doveadm when `DOVEADM_API_KEY` is configured).

## Per-Organization Webhooks

Two layers:

1. **Global** — the `WEBHOOK_URLS` environment endpoint receives every event, signed with `WEBHOOK_SECRET` (the canonical dispatcher; see [Webhook Events](../reference/webhook-events.md)).
2. **Per-organization endpoints** — rows in `webhook_urls`, managed via the API:

```bash
curl -X POST https://api.yourdomain.com/api/v1/webhooks/endpoints \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{
    "name": "Acme app",
    "url": "https://app.acme.com/webhooks/email",
    "event_types": ["email.delivered", "email.bounced", "tracking.open", "tracking.click"],
    "webhook_secret": "org-specific-webhook-secret"
  }'
```

Endpoint CRUD lives at `/api/v1/webhooks/endpoints[/{id}]`, with `POST /api/v1/webhooks/endpoints/{id}/test` to fire a test event. Deliveries are signed with the endpoint's own secret in the `X-Webhook-Signature: sha256=<hex>` header; event names use the dotted catalogue (`email.delivered`, `tracking.open`, …), not legacy short names.

> [!WARNING]
> Use HTTPS webhook URLs. The signature protects integrity, not confidentiality — payloads include message metadata.

## Per-Organization Spam Policy

Each organization's `settings.spam_policy` JSON (thresholds, quarantine, sender white/blacklists) is synced into Redis for Rspamd by `scripts/sync_rspamd_settings.py` — see [Rspamd Configuration](rspamd-configuration.md#per-organization-overrides).

## Per-Organization DKIM

Adding a domain generates its DKIM key automatically (RSA-2048, selector `default`) and the API returns the DNS records to publish; verification is `POST /api/v1/domains/{domain_id}/verify-dns`. See [DNS Setup](dns-setup.md).

## Tenant Isolation

- **Database-level:** every API query is scoped by the key's `organization_id`; cross-tenant IDs return 404.
- **Filesystem-level:** Maildir storage is per-domain (`/var/mail/vhosts/domain.com/user/`).
- **Sender-level:** Postfix's `smtpd_sender_login_maps` stops one tenant's credentials from sending as another tenant's addresses (`reject_sender_login_mismatch` on 587/465).
- **Rate-limit-level:** independent counters per org/domain/mailbox/credential in Redis.
- **Quota-level:** storage tracked per organization, domain, and mailbox.

> [!NOTE]
> Mailyte does not provide process-level isolation between tenants. If you need fully separate Postfix/Dovecot instances per tenant, deploy separate stacks. For most use cases, data-layer isolation plus sender-login enforcement is sufficient.
