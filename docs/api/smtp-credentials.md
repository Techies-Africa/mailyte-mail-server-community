# SMTP Credentials

Domain-scoped SMTP API keys, distinct from mailbox passwords. A credential authenticates **only over SMTP** (ports 587/465, via a protocol-scoped Dovecot passdb) and may send as any address at its domain. This module went live in production on 2026-08-27.

The API server is the single source of truth for the whole lifecycle: secrets are generated server-side, returned **exactly once** in the create/rotate response, and stored only as a bcrypt hash plus a short display prefix. `active`, `expires_at`, and `allowed_ips` are enforced inside the Dovecot passdb query; every auth-affecting mutation additionally flushes Dovecot's auth cache for the username, so a revoked key stops authenticating on the next AUTH attempt instead of after the 1-hour cache TTL.

Mailbox-password SMTP auth is untouched — the credentials table is additive.

**Base path:** `/api/v1/smtp-credentials`
**Auth:** `X-API-Key` (read for GETs, write for mutations) or a dashboard session. Tenant credentials are scoped to their own organization; platform-scope credentials see every organization.

Routes are split across two modules that share the same prefix: `routes/smtp_credentials.py` (lifecycle) and `routes/smtp_credential_reports.py` (events + usage reads).

## The credential object

All lifecycle endpoints return this shape in `data`:

| Field | Type | Description |
|---|---|---|
| `id` | string | ULID of the credential |
| `organization_id` | string | Owning organization (derived from the domain) |
| `domain_id` | string | ULID of the domain the key may send as |
| `username` | string | SASL login name (unique) |
| `name` | string \| null | Human label shown in key lists |
| `prefix` | string \| null | First characters of the secret, for display after the one-time reveal |
| `created_by` | string \| null | Attribution (who created the key) |
| `allowed_ips` | array | IPs / CIDR networks the key may authenticate from |
| `ip_allowlist_enabled` | boolean | Whether the allowlist is enforced |
| `expires_at` | string \| null | ISO 8601; the key stops working after this |
| `hourly_limit` | integer \| null | Per-key outbound cap; `null` inherits the org's limits |
| `daily_limit` | integer \| null | Per-key outbound cap; `null` inherits the org's limits |
| `last_used_at` | string \| null | Written by the log ingestor, never by the auth path |
| `active` | boolean | `false` = revoked |
| `created_at` / `updated_at` | string | ISO 8601 timestamps |

The plaintext `secret` appears **only** in create (when generated) and rotate responses. It is never retrievable again.

## List SMTP Credentials

```
GET /api/v1/smtp-credentials
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `domain_id` | string | -- | Filter by domain ULID |
| `organization_id` | string | -- | Platform-scope callers only: narrow to one organization (ignored for tenant credentials) |
| `limit` | integer | `100` | Max rows (capped at 500) |
| `offset` | integer | `0` | Pagination offset |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/smtp-credentials?domain_id=01J8ZK...&limit=50"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credentials retrieved successfully",
  "data": {
    "credentials": [
      {
        "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
        "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
        "domain_id": "01J8ZK1A2B3C4D5E6F7G8H9J0K",
        "username": "smtp-acme-com-x7k2",
        "name": "Marketing sender",
        "prefix": "mk_7f3a2b",
        "created_by": "admin@acme.com",
        "allowed_ips": ["203.0.113.0/24"],
        "ip_allowlist_enabled": true,
        "expires_at": null,
        "hourly_limit": 500,
        "daily_limit": 5000,
        "last_used_at": "2026-08-29T14:03:22",
        "active": true,
        "created_at": "2026-08-27T09:15:00",
        "updated_at": "2026-08-29T14:03:22"
      }
    ],
    "total": 1
  }
}
```

## Create SMTP Credential

Provision a domain-scoped SMTP API key. When `username`/`password` are omitted the server generates them and the response carries the plaintext secret **exactly once**.

```
POST /api/v1/smtp-credentials
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `domain_id` | string | Yes | ULID of the domain this credential may send as |
| `username` | string | No | SASL login; generated when omitted |
| `password` | string | No | Plaintext secret; generated (and returned once) when omitted |
| `name` | string | No | Human label shown in key lists |
| `allowed_ips` | array | No | IPs / CIDR networks the key may authenticate from |
| `ip_allowlist_enabled` | boolean | No | Enforce the allowlist (default `false`) |
| `expires_at` | string | No | ISO 8601; key stops working after |
| `hourly_limit` | integer | No | Per-key hourly send cap (min 1) |
| `daily_limit` | integer | No | Per-key daily send cap (min 1) |
| `created_by` | string | No | Attribution override when a service creates on a user's behalf |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain_id": "01J8ZK1A2B3C4D5E6F7G8H9J0K",
    "name": "Marketing sender",
    "allowed_ips": ["203.0.113.0/24"],
    "ip_allowlist_enabled": true,
    "hourly_limit": 500,
    "daily_limit": 5000
  }' \
  http://your-server:5000/api/v1/smtp-credentials
```

**Example Response** (`201 Created`)

```json
{
  "type": "success",
  "msg": "SMTP credential created successfully",
  "data": {
    "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
    "username": "smtp-acme-com-x7k2",
    "prefix": "mk_7f3a2b",
    "secret": "mk_7f3a2b9c1d4e...",
    "active": true,
    "...": "full credential object"
  }
}
```

!!! warning "The secret is returned once"
    `secret` is present only in this response (and in rotate responses). Store it immediately — the server keeps only a bcrypt hash and the display `prefix`.

!!! info "Error cases"
    - `404` — `domain_id` not found, or belongs to another organization (cross-org IDs answer 404, not 403)
    - `409` — the supplied `username` already exists
    - `422` — invalid `allowed_ips` entry, `ip_allowlist_enabled: true` with an empty list (an enabled empty allowlist would be a lockout), or an unparseable `expires_at`

## Get SMTP Credential

```
GET /api/v1/smtp-credentials/{credential_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

Returns the credential object (no secret). `404` if the ID does not exist or belongs to another organization.

## Update SMTP Credential

Partial update — only the keys present in the request body are applied.

```
PATCH /api/v1/smtp-credentials/{credential_id}
```

**Request Body** (all fields optional; at least one required)

| Field | Type | Description |
|---|---|---|
| `name` | string | New label |
| `allowed_ips` | array | Replaces the allowlist |
| `ip_allowlist_enabled` | boolean | Toggle allowlist enforcement |
| `expires_at` | string | ISO 8601; empty string clears the expiry, absent leaves it unchanged |
| `hourly_limit` | integer | Per-key hourly cap (min 1) |
| `daily_limit` | integer | Per-key daily cap (min 1) |

**Example Request**

```bash
curl -X PATCH -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"expires_at": "2026-12-31T23:59:59", "daily_limit": 10000}' \
  http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credential updated successfully",
  "data": {
    "...": "full credential object",
    "cache_flushed": true
  }
}
```

`422` if no updatable field is provided, or on allowlist/expiry validation failure. When `allowed_ips`, `ip_allowlist_enabled`, or `expires_at` change, the response includes `cache_flushed` — whether Dovecot's auth cache was flushed for the username (those values are read at AUTH time from the passdb query).

## Rotate SMTP Credential

Generates a new secret for the **same username**, so SMTP clients only change their password. The response carries the new plaintext exactly once.

```
POST /api/v1/smtp-credentials/{credential_id}/rotate
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R/rotate
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credential rotated successfully",
  "data": {
    "...": "full credential object",
    "secret": "mk_9e2c1a8b...",
    "cache_flushed": true
  }
}
```

The old secret stops working on the next AUTH attempt (cache flushed after commit), not after the 1-hour auth-cache TTL.

## Revoke SMTP Credential

Sets `active: false` and flushes Dovecot's auth cache for the username, so the key stops authenticating on the next AUTH attempt — no auth-cache window. Idempotent.

```
POST /api/v1/smtp-credentials/{credential_id}/revoke
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R/revoke
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credential revoked",
  "data": {
    "...": "full credential object (active: false)",
    "cache_flushed": true
  }
}
```

!!! tip "Retry on `cache_flushed: false`"
    Revocation is only complete once the Dovecot cache entry is gone. The cache is flushed even on an idempotent re-revoke, so a caller whose first revoke reported `cache_flushed: false` can simply revoke again.

## Re-enable SMTP Credential

Undo a revocation.

```
POST /api/v1/smtp-credentials/{credential_id}/enable
```

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R/enable
```

Same response shape as revoke, with `msg: "SMTP credential enabled"`.

## Delete SMTP Credential

Permanently delete a credential. Idempotent — deleting an already-absent credential also returns success, matching the mailbox/domain delete convention. An audit event row is written first and survives the delete.

```
DELETE /api/v1/smtp-credentials/{credential_id}
```

**Example Request**

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credential deleted successfully",
  "data": { "cache_flushed": true }
}
```

## Credential Events (Audit Trail)

Append-only key events (`created`, `updated`, `rotated`, `revoked`, `enabled`, `deleted`, `suspended`), newest first. Event rows are FK-free — they survive the deletion of the credential and organization they describe.

```
GET /api/v1/smtp-credentials/{credential_id}/events
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | `100` | Max events (capped at 500) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R/events?limit=20"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credential events retrieved successfully",
  "data": {
    "events": [
      {
        "id": "01J9AC3DE4FG5HJ6KL7MN8PQ9S",
        "credential_id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
        "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
        "username": "smtp-acme-com-x7k2",
        "event": "rotated",
        "actor": "admin@acme.com",
        "source_ip": "198.51.100.7",
        "detail": null,
        "created_at": "2026-08-29T10:12:45"
      }
    ]
  }
}
```

## Credential Usage (Delivery Counts)

Messages attributed to this key via `mail_logs.sasl_username` (populated by the log ingestor from smtpd's `client=` lines).

```
GET /api/v1/smtp-credentials/{credential_id}/usage
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | integer | `30` | Lookback window (1–365) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/smtp-credentials/01J9AB2CD3EF4GH5JK6MN7PQ8R/usage?days=7"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "SMTP credential usage retrieved successfully",
  "data": {
    "window_days": 7,
    "since": "2026-08-23T00:00:00",
    "total": 1240,
    "delivered": 1198,
    "bounced": 12,
    "deferred": 25,
    "rejected": 5,
    "by_status": { "delivered": 1198, "bounced": 12, "deferred": 25, "rejected": 5 },
    "last_used_at": "2026-08-29T14:03:22"
  }
}
```

!!! info "Counts start with attribution"
    Attribution of traffic to keys started when the `sasl_username` ingestor migration (0018) shipped — earlier traffic was never attributed and is honestly absent from these counts, not zero-filled.
