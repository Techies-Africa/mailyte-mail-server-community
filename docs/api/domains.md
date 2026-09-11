# Domains

Manage email domains within organizations, including DNS verification, DKIM key
management, and quota settings.

Domain IDs are 26-character ULID strings (e.g. `01J1DOM0000000000000000000`), not
integers.

## List Domains

Retrieve a paginated list of domains. A tenant credential always sees only its own
organization's domains; a platform-scoped credential sees every organization and may
narrow with `organization_id`.

```
GET /api/v1/domains/
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | -- | Search the domain name (substring) |
| `organization_id` | string | -- | Platform scope only -- narrow to one organization. Ignored for tenant credentials |
| `active` | string | -- | `true` or `false` (anything else is `422`) |
| `sort_by` | string | `domain` | `domain`, `created_at`, `total_storage_used` |
| `sort_dir` | string | `asc` | `asc` or `desc` |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:8083/api/v1/domains/?q=acme&page=1"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domains retrieved successfully",
  "data": {
    "items": [
      {
        "id": "01J1DOM0000000000000000000",
        "domain": "acme.com",
        "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
        "organization_name": "Acme Corp",
        "description": "Primary domain",
        "active": true,
        "max_quota": 10737418240,
        "max_users": 1000,
        "total_storage_used": 3221225472,
        "dkim_enabled": true,
        "dkim_selector": "default",
        "email_account_count": 20,
        "external_id": null,
        "created_at": "2026-01-15T10:30:00",
        "updated_at": "2026-03-20T14:22:00"
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

## Get Domain

Retrieve a single domain with its email accounts, usage statistics, current DKIM
record, and the full set of DNS records to publish.

```
GET /api/v1/domains/{domain_id}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `domain_id` | string | The domain's ULID |

**Example Response** (abridged)

```json
{
  "type": "success",
  "msg": "Domain retrieved successfully",
  "data": {
    "id": "01J1DOM0000000000000000000",
    "domain": "acme.com",
    "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
    "organization": { "id": "01J1ABCDEF2345GHJKMNPQRSTV", "name": "Acme Corp" },
    "active": true,
    "max_quota": 10737418240,
    "max_users": 1000,
    "dkim_enabled": true,
    "dkim_selector": "default",
    "dkim_record": "v=DKIM1; k=rsa; p=MIIBIjANBg...",
    "dkim_dns_name": "default._domainkey.acme.com",
    "dns_records": [
      {"type": "MX", "name": "acme.com", "value": "mx.mailyte.com.", "priority": 10, "description": "Routes all email for this domain to Mailyte"},
      {"type": "TXT", "name": "acme.com", "value": "v=spf1 include:spf.mx.mailyte.com ~all", "description": "Authorizes Mailyte servers to send email for this domain"},
      {"type": "TXT", "name": "_dmarc.acme.com", "value": "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@acme.com", "description": "Policy for handling emails that fail SPF/DKIM checks"},
      {"type": "TXT", "name": "default._domainkey.acme.com", "value": "v=DKIM1; k=rsa; p=MIIBIjANBg...", "description": "Public key for DKIM signature verification"}
    ],
    "email_accounts": [ ... ],
    "email_account_count": 20,
    "usage_statistics": {
      "account_usage_percentage": 2.0,
      "storage_usage_percentage": 30.0,
      "total_account_storage": 3221225472
    }
  }
}
```

The `dns_records` are regenerated on every read from the current server hostname and
active DKIM key, so they always reflect the configuration in force right now.

## Create Domain

Add a new domain. A tenant credential's domain is always created in its own
organization (a supplied `organization_id` is ignored); a platform-scoped caller
must supply `organization_id`.

```
POST /api/v1/domains/
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `domain` | string | Yes | Domain name (e.g., `example.com`) |
| `organization_id` | string | Platform callers only | Owning organization (ignored for tenant credentials) |
| `description` | string | No | Description (HTML is stripped) |
| `active` | boolean | No | Whether the domain is active (default: `true`) |
| `max_quota` | integer | No | Max storage in bytes (default: `10737418240` = 10 GB) |
| `max_users` | integer | No | Max email accounts (default: `1000`) |
| `dkim_enabled` | boolean | No | Enable DKIM signing (default: `true`) |
| `dkim_selector` | string | No | DKIM selector name (default: `"default"`) |
| `rate_limits` | object | No | Domain-level rate limits |
| `storage_quotas` | object | No | Domain-level storage quotas |
| `external_id` | string | No | Your own system's identifier |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "acme.com",
    "description": "Primary domain",
    "max_users": 500
  }' \
  http://your-server:8083/api/v1/domains/
```

**Example Response** (`201 Created`)

The full domain record, plus `dkim_record`, `dkim_selector`, `dkim_dns_name`, and
the generated `dns_records` array (see [Get Domain](#get-domain)).

!!! info "DKIM key generation"
    When you create a domain with `dkim_enabled: true`, the server generates a
    2048-bit RSA key pair automatically (the private key is stored
    envelope-encrypted and never returned by any endpoint), and exports the key
    file + selector-map entry to rspamd's key directory (best-effort
    write-through; `scripts/generate_dkim.py sync` reconciles any drift).
    Publish the returned `dkim_record` as a TXT record at
    `{dkim_selector}._domainkey.{domain}` -- the `dns_records` array names the
    domain's **actual** selector (it previously hardcoded `default._domainkey`
    even for rotated domains).

    `dkim_selector` must be a single DNS label (letters, digits, `-`, `_`; no
    dots; max 63 chars) -- anything else is rejected with a validation error,
    both here and on update.

!!! warning "Duplicate check"
    Domain names are globally unique and checked case-insensitively. A collision
    returns `409` with `error_code: "DOMAIN_ALREADY_CLAIMED"`. If your own
    organization already owns the domain, `data.existing_id` carries the existing
    domain's ID; if another organization owns it, no detail is disclosed.

## Update Domain

Update an existing domain. Only the fields you include are changed.

```
PUT /api/v1/domains/{domain_id}
```

**Request Body**

Accepted fields: `description`, `active`, `max_quota`, `max_users`, `dkim_enabled`,
`dkim_selector`, `rate_limits`, `storage_quotas`, `external_id`. The domain name and
owning organization cannot be changed. A duplicate `external_id` returns `409`.

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"max_users": 1000, "description": "Primary corporate domain"}' \
  http://your-server:8083/api/v1/domains/01J1DOM0000000000000000000
```

The response `data` is the full updated domain record.

## Delete Domain

Delete a domain. The domain must have no email accounts.

```
DELETE /api/v1/domains/{domain_id}
```

!!! danger "Prerequisite"
    You must delete all email accounts on the domain first, or the API returns
    `400`: `"Cannot delete domain with {n} email accounts. Delete them first."`

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain deleted successfully"
}
```

## Verify DNS Records

Perform live DNS lookups to check whether the domain's MX, SPF, DKIM, and DMARC
records are correctly configured. SPF is evaluated mechanism-by-mechanism (RFC 7208,
including `include:`/`redirect=` chains and the 10-lookup budget), not by substring
match.

```
GET /api/v1/domains/{domain_id}/verify-dns
```

**Example Response**

```json
{
  "type": "success",
  "msg": "DNS verification completed",
  "data": {
    "domain": "acme.com",
    "verification": {
      "mx":   {"status": "pass", "expected": "mx.mailyte.com", "actual": "mx.mailyte.com"},
      "spf":  {"status": "pass", "found": true, "expected": "include:spf.mx.mailyte.com", "actual": "v=spf1 include:spf.mx.mailyte.com ~all"},
      "dkim": {"status": "pass", "found": true},
      "dmarc": {"status": "pass", "policy": "quarantine"}
    }
  }
}
```

Each check reports `status: "pass"` or `"fail"`; failing checks include a `message`
explaining why (e.g. multiple SPF records, missing DKIM record, over-budget SPF).

## Get Domain Quotas

```
GET /api/v1/domains/{domain_id}/quotas
```

Returns `max_quota`, `max_users`, `storage_used`, `usage_percentage`,
`account_usage_percentage`, `rate_limits`, `storage_quotas`, and a per-account
breakdown (`email_accounts[]` with `storage_quota`, `storage_used`,
`usage_percentage`).

## Update Domain Quotas

```
PUT /api/v1/domains/{domain_id}/quotas
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `max_quota` | integer | Max storage in bytes |
| `max_users` | integer | Max email accounts |
| `rate_limits` | object | Domain-level rate limits |
| `storage_quotas` | object | Domain-level storage quotas |

The response `data` echoes the updated values.

## DKIM Key Management

Safe DKIM rotation is a two-step flow: mint a key under a new selector, publish its
TXT record, wait for propagation, then cut signing over. Private key material is
never returned by any of these endpoints in any form.

!!! note "Operator console only"
    These three endpoints are role-gated (`support` to list, `operator` to
    rotate/activate) and therefore reachable only with an operator session.

### List DKIM keys

```
GET /api/v1/domains/{domain_id}/dkim
```

Returns every DKIM key on the domain with the exact TXT record to publish for each:

```json
{
  "type": "success",
  "msg": "DKIM keys retrieved",
  "data": {
    "domain": "acme.com",
    "domain_id": "01J1DOM0000000000000000000",
    "dkim_enabled": true,
    "signing_selector": "default",
    "items": [
      {
        "selector": "default",
        "algorithm": "rsa",
        "key_size": 2048,
        "active": true,
        "created_at": "2026-01-15T10:30:00",
        "updated_at": "2026-01-15T10:30:00",
        "record_name": "default._domainkey.acme.com",
        "record_type": "TXT",
        "record_value": "v=DKIM1; k=rsa; p=MIIBIjANBg..."
      }
    ],
    "total": 1
  }
}
```

### Rotate (step 1): generate a new key

```
POST /api/v1/domains/{domain_id}/dkim/rotate
```

**Request Body:** `{"reason": "<why -- recorded in the audit log>"}`

Mints a fresh 2048-bit RSA key under a new timestamped selector
(`mailyte<YYYYMMDDHHMMSS>`) and leaves the existing key active and signing. Returns
`201` with the new selector, the DNS record to publish, the currently active
selectors, and an `activate_url`. Nothing changes about what is signing until you
activate.

### Activate (step 2): cut signing over

```
POST /api/v1/domains/{domain_id}/dkim/{selector}/activate
```

**Request Body:** `{"reason": "..."}`

Makes the named selector the signing key and deactivates every other selector on
the domain. Call only once the selector's TXT record resolves publicly. `404` if the
selector does not exist on the domain.

!!! note "Rspamd write-through"
    Both endpoints export to rspamd automatically: rotate writes the new
    selector's key file immediately (so it is on disk while the DNS record
    propagates), and activate rewrites the domain->selector map. Each response
    carries `rspamd_synced` -- `true` means the export ran; `false` means the
    key state is safe in MySQL but disk was not updated (e.g. the api container
    cannot reach the shared key directory): run `scripts/generate_dkim.py sync`
    on the docker host before relying on the cutover. Rspamd re-reads the
    selector map on its map watch interval (about a minute).

## Legacy Endpoints

These mailcow-compatible endpoints predate the ID-based CRUD above and operate on
domain **names**.

### Edit domains (bulk)

```
POST /api/v1/domains/edit
```

**Request Body**

```json
{
  "items": ["acme.com"],
  "attr": {"active": 1, "description": "updated"}
}
```

Allowed `attr` keys: `description`, `aliases`, `mailboxes`, `maxquota`, `quota`,
`defquota`, `transport`, `backupmx`, `active`, `relay_all_recipients`, `rl_value`,
`rl_frame`, `gal`. Returns per-domain success/error results.

### Delete domains (bulk)

```
POST /api/v1/domains/delete/domain
```

**Request Body:** a JSON array of domain names, e.g. `["acme.com"]`. Deletes each
domain together with its mailboxes, aliases, and DKIM keys, and returns per-domain
results with `stats` (`mailboxes_deleted`, `aliases_deleted`).

### Get domain policy

```
GET /api/v1/domains/get/domain/policy/{domain}
```

Returns the domain's spam/security policy: `policy_bl_only`, `policy_reject_spam`,
`policy_greylist`, `policy_rbl`, plus `rate_limits` and `active` from the domain
record.

### Edit domain policy

```
POST /api/v1/domains/edit/domain/policy
```

**Request Body**

| Field | Type | Default | Description |
|---|---|---|---|
| `domain` | string | required | Domain name |
| `policy_bl_only` | integer | `0` | Blacklist-only mode (0/1) |
| `policy_reject_spam` | integer | `0` | Reject detected spam (0/1) |
| `policy_greylist` | integer | `1` | Enable greylisting (0/1) |
| `policy_rbl` | integer | `1` | Enable RBL checks (0/1) |

Upsert semantics -- the policy row is created if it does not exist.

### Get domain statistics

```
GET /api/v1/domains/stats/{domain}
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain statistics retrieved successfully",
  "data": {
    "domain": "acme.com",
    "mailbox_count": 20,
    "alias_count": 15,
    "total_quota_used": 3221225472,
    "domain_details": { ... }
  }
}
```
