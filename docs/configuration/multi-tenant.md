# Multi-Tenant Configuration

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Per-organization settings, quota defaults, rate limit defaults, and webhook configuration per tenant.

---

Mailyte supports multiple organizations on a single server. Each tenant gets their own domains, mailboxes, and configuration — but they all share the same infrastructure. This page covers how to set up and manage per-tenant settings.

## How Multi-Tenancy Works

Every tenant in Mailyte is an **organization**. An organization owns one or more domains, and each domain has mailboxes and aliases. The hierarchy looks like this:

```
Organization (tenant)
  └── Domain (yourdomain.com)
        ├── Mailbox (user@yourdomain.com)
        ├── Mailbox (admin@yourdomain.com)
        └── Alias  (info@yourdomain.com → admin@yourdomain.com)
```

All tenant data lives in MySQL. Postfix and Dovecot query the database for every lookup, so tenant isolation happens at the data layer — there's no need for separate config files per tenant.

## Creating an Organization

Use the Mailyte API to provision new tenants:

```bash
curl -X POST http://localhost:5000/api/v1/organizations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "name": "Acme Corp",
    "slug": "acme-corp",
    "plan": "business",
    "admin_email": "admin@acme.com"
  }'
```

The `plan` field determines default quotas and rate limits (see below).

## Per-Organization Quotas

Each organization has a quota configuration that controls how much storage their users get by default.

### Default Quotas by Plan

| Plan | Default Mailbox Quota | Max Mailboxes | Total Storage |
|------|----------------------|---------------|---------------|
| `starter` | 1 GB | 10 | 10 GB |
| `business` | 5 GB | 100 | 200 GB |
| `enterprise` | 25 GB | Unlimited | Unlimited |

These defaults are applied when a new mailbox is created. You can override the quota for individual mailboxes.

### Setting Organization Quotas

```bash
curl -X PATCH http://localhost:5000/api/v1/organizations/acme-corp/quota \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "default_mailbox_quota_mb": 5120,
    "max_mailboxes": 200,
    "total_storage_mb": 512000
  }'
```

### Per-Mailbox Quota Override

```bash
curl -X PATCH http://localhost:5000/api/v1/mailboxes/ceo@acme.com/quota \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "quota_mb": 25600
  }'
```

> [!NOTE]
> The per-mailbox quota override flows into Dovecot through the MySQL `user_query`. When Dovecot checks a user's quota, it gets the value from the database, which includes any overrides. No config file changes needed.

## Per-Organization Rate Limits

Rate limits prevent any single tenant from overwhelming the server or harming the sender reputation of other tenants.

### Default Rate Limits by Plan

| Plan | Messages/Hour | Messages/Day | Recipients/Message |
|------|--------------|-------------|-------------------|
| `starter` | 100 | 500 | 50 |
| `business` | 1,000 | 10,000 | 200 |
| `enterprise` | 10,000 | 100,000 | 500 |

### Setting Organization Rate Limits

```bash
curl -X PATCH http://localhost:5000/api/v1/organizations/acme-corp/rate-limits \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "messages_per_hour": 2000,
    "messages_per_day": 20000,
    "recipients_per_message": 300
  }'
```

Rate limits are enforced in two places:

1. **API layer** — The FastAPI server checks limits in Redis before accepting a send request. This catches most overages early.
2. **Postfix layer** — SMTP-level rate limits act as a safety net for users who send directly through Postfix (via their email client).

> [!TIP]
> Start with conservative rate limits for new tenants. It's much easier to increase limits for a good sender than to recover your IP reputation after a tenant sends spam.

## Per-Organization Webhooks

Each organization can receive webhook notifications for events that happen in their domain.

### Configuring Webhooks

```bash
curl -X POST http://localhost:5000/api/v1/organizations/acme-corp/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "url": "https://app.acme.com/webhooks/email",
    "events": ["delivery", "bounce", "spam_complaint", "open", "click"],
    "secret": "org-specific-webhook-secret"
  }'
```

### Webhook Events

| Event | Description |
|-------|-------------|
| `delivery` | Message was accepted by the recipient's server |
| `bounce` | Message bounced (hard or soft) |
| `spam_complaint` | Recipient marked the message as spam |
| `open` | Recipient opened the message (requires tracking) |
| `click` | Recipient clicked a link (requires tracking) |
| `deferred` | Delivery was temporarily delayed |

### Webhook Payload Format

Every webhook request includes:

- **`X-Mailyte-Signature`** header — HMAC-SHA256 signature of the payload, using the organization's webhook secret.
- **`X-Mailyte-Event`** header — The event type.

```json
{
  "event": "delivery",
  "timestamp": "2026-03-25T10:30:00Z",
  "organization": "acme-corp",
  "message_id": "<abc123@acme.com>",
  "from": "sales@acme.com",
  "to": "customer@example.com",
  "details": {
    "smtp_response": "250 OK",
    "remote_server": "mx.example.com"
  }
}
```

### Global vs. Per-Organization Webhooks

The `WEBHOOK_URLS` environment variable defines global webhooks that receive events for all organizations. Per-organization webhooks (configured through the API) only receive events for that specific organization.

Both fire independently. If a global webhook and an org webhook are configured, both get notified.

> [!WARNING]
> Webhook URLs must use HTTPS. HTTP endpoints are rejected to prevent webhook secrets from leaking in transit.

## Per-Organization DKIM

Each organization's domain needs its own DKIM key. When you add a domain through the API, Mailyte generates a DKIM key automatically and returns the DNS record the tenant needs to add.

```bash
curl -X POST http://localhost:5000/api/v1/organizations/acme-corp/domains \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{
    "domain": "acme.com"
  }'
```

Response:

```json
{
  "domain": "acme.com",
  "status": "pending_verification",
  "dkim_record": {
    "type": "TXT",
    "name": "mail._domainkey.acme.com",
    "value": "v=DKIM1; k=rsa; p=MIIBIjANBg..."
  },
  "spf_record": "v=spf1 include:mail.yourdomain.com -all",
  "mx_record": "10 mail.yourdomain.com"
}
```

The API returns the exact DNS records the tenant needs to configure. Once they're in place, use the verification endpoint to confirm:

```bash
curl -X POST http://localhost:5000/api/v1/organizations/acme-corp/domains/acme.com/verify \
  -H "Authorization: Bearer $API_TOKEN"
```

## Tenant Isolation

Even though all tenants share the same Postfix and Dovecot instances, they're isolated in several ways:

- **Database-level:** Each query filters by domain, which is tied to an organization.
- **Filesystem-level:** Maildir storage is organized by domain (`/var/mail/vhosts/domain.com/user/`).
- **Rate-limit-level:** Each organization has independent rate limits tracked in Redis.
- **Quota-level:** Each organization's total storage is tracked independently.

> [!NOTE]
> Mailyte does not provide process-level isolation between tenants. If you need full isolation (separate Postfix/Dovecot instances per tenant), you'll need to deploy separate Mailyte stacks. For most use cases, database and quota isolation is sufficient.
