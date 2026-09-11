# Organizations

Manage organizations -- the top-level container for domains, email accounts, and settings.

!!! info "Who can call what"
    Organization **lifecycle** (list all, create, delete) and **quota writes** are
    platform-level actions: they require a platform-scoped credential *and* an
    operator role (listing: `support`+; create/delete/quota writes: `admin`), which
    in practice means an operator console session. A tenant credential can read and
    update **its own** organization only.

## List Organizations

Retrieve a paginated, cross-tenant directory of all organizations with usage
statistics.

```
GET /api/v1/organizations/
```

**Auth:** platform scope, operator role `support` or higher.

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | -- | Search: organization name (substring), exact organization id, external id (substring), or the owner of a matching domain or mailbox address |
| `active` | string | -- | `true` or `false` -- filter on the active flag (anything else is `422`) |
| `over_quota` | string | -- | `true` -- only organizations whose summed domain storage exceeds their summed domain quota |
| `sort_by` | string | `name` | `name`, `created_at`, `domain_count`, `email_account_count`, `total_storage_used` |
| `sort_dir` | string | `asc` | `asc` or `desc` |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

An unknown `sort_by`/`sort_dir` or a non-boolean `active`/`over_quota` value returns
`422`.

**Example Request**

```bash
curl -H "X-API-Key: PLATFORM_KEY" \
  "http://your-server:8083/api/v1/organizations/?q=acme&sort_by=total_storage_used&sort_dir=desc"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organizations retrieved successfully",
  "data": {
    "items": [
      {
        "id": "01J1ABCDEF2345GHJKMNPQRSTV",
        "name": "Acme Corp",
        "external_id": "cust_12345",
        "description": "Main customer account",
        "admin_email": "admin@acme.com",
        "admin_name": "Jane Smith",
        "active": true,
        "domain_count": 3,
        "email_account_count": 45,
        "total_storage_used": 5368709120,
        "storage_quota": 32212254720,
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

## Get Organization

Retrieve a single organization with detailed statistics, including its domains and
storage breakdown.

```
GET /api/v1/organizations/{organization_id}
```

**Auth:** any credential with read access. A tenant credential can only fetch its
own organization -- any other ID returns `404`. Platform scope can fetch any.

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `organization_id` | string | The organization ID (a 26-character ULID unless the org was created with a custom ID) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:8083/api/v1/organizations/01J1ABCDEF2345GHJKMNPQRSTV
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization retrieved successfully",
  "data": {
    "id": "01J1ABCDEF2345GHJKMNPQRSTV",
    "name": "Acme Corp",
    "external_id": "cust_12345",
    "description": "Main customer account",
    "admin_email": "admin@acme.com",
    "admin_name": "Jane Smith",
    "active": true,
    "settings": {},
    "rate_limits": {},
    "storage_quotas": {},
    "webhook_urls": [],
    "domain_count": 3,
    "email_account_count": 45,
    "domains": [
      {
        "id": "01J1DOM0000000000000000000",
        "domain": "acme.com",
        "active": true,
        "max_quota": 10737418240
      }
    ],
    "storage_statistics": {
      "total_storage_used": 5368709120,
      "total_quota": 32212254720,
      "usage_percentage": 16.67
    }
  }
}
```

## Get Organization by External ID

Look up an organization using your own system's identifier. Subject to the same
scoping as [Get Organization](#get-organization).

```
GET /api/v1/organizations/by-external-id/{external_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:8083/api/v1/organizations/by-external-id/cust_12345
```

The response format is identical to [Get Organization](#get-organization).

## Create Organization

Create a new organization.

```
POST /api/v1/organizations/
```

**Auth:** platform scope, operator role `admin` or higher, admin permission.
Creating tenants is a platform-level action; resellers use
`POST /api/v1/reseller/sub-organizations` instead.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | Yes | Unique ID (alphanumeric, hyphens, underscores) |
| `name` | string | Yes | Display name (max 255 characters) |
| `external_id` | string | No | Your own system's identifier (max 255 characters) |
| `description` | string | No | Description (HTML is stripped) |
| `admin_email` | string | No | Admin contact email |
| `admin_name` | string | No | Admin contact name |
| `settings` | object | No | Custom settings (default: `{}`) |
| `rate_limits` | object | No | Rate limit configuration (free-form JSON) |
| `storage_quotas` | object | No | Storage quota configuration (free-form JSON) |
| `webhook_urls` | array | No | Webhook endpoint URLs (stored on the org record) |
| `webhook_secret` | string | No | Secret for signing webhook payloads |
| `active` | boolean | No | Whether the org is active (default: `true`) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme",
    "name": "Acme Corp",
    "external_id": "cust_12345",
    "admin_email": "admin@acme.com"
  }' \
  http://your-server:8083/api/v1/organizations/
```

**Example Response** (`201 Created`)

The full organization record is returned in `data`.

!!! warning "Duplicate check"
    If an organization with the same `id` or `external_id` already exists, the API returns `409 Conflict`.

Validation failures return `400` with the individual messages in `data.errors`.

## Update Organization

Update an existing organization. Only the fields you include in the request body are changed.

```
PUT /api/v1/organizations/{organization_id}
```

**Auth:** write access. A tenant credential can only update its own organization
(`404` otherwise); platform scope can update any.

**Request Body**

All fields from [Create Organization](#create-organization) except `id` are accepted. Only include the fields you want to change.

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corporation"}' \
  http://your-server:8083/api/v1/organizations/01J1ABCDEF2345GHJKMNPQRSTV
```

The response `data` is the full updated organization record. A duplicate
`external_id` returns `409`.

## Delete Organization

Delete an organization. The organization must have no domains or email accounts.

```
DELETE /api/v1/organizations/{organization_id}
```

**Auth:** platform scope, operator role `admin` or higher.

!!! danger "Prerequisite"
    You must delete all domains and email accounts belonging to the organization before you can delete it. The API returns `400 Bad Request` if any remain.

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization deleted successfully"
}
```

**Error when resources remain:**

```json
{
  "type": "error",
  "msg": "Cannot delete organization with 3 domains and 45 email accounts. Delete them first."
}
```

## Get Organization Quotas

Get detailed quota and usage information for an organization, including the
operator-override bookkeeping.

```
GET /api/v1/organizations/{organization_id}/quotas
```

**Auth:** read access; tenants see their own org only.

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization quota information retrieved successfully",
  "data": {
    "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
    "organization_quotas": {},
    "total_domains": 3,
    "total_email_accounts": 45,
    "quota_override": false,
    "quota_override_at": null,
    "quota_override_by": null,
    "storage_summary": {
      "total_storage_used": 5368709120,
      "total_quota": 32212254720,
      "domains": [
        {
          "domain": "acme.com",
          "storage_used": 3221225472,
          "quota": 10737418240,
          "usage_percentage": 30.0,
          "email_accounts": 20,
          "max_users": 1000
        }
      ]
    }
  }
}
```

`quota_override` is true when a human operator has set this organization's quotas by
hand; `quota_override_at`/`quota_override_by` say when and by whom.

## Update Organization Quotas

Update storage quotas and rate limits for an organization.

```
PUT /api/v1/organizations/{organization_id}/quotas
```

**Auth:** platform scope, operator role `admin` or higher. Raising quota is a
plan/commercial decision, not tenant self-service.

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force` | boolean | `false` | Automated (non-operator) callers only: overwrite a standing operator override and clear the override flag |

**Request Body**

| Field | Type | Description |
|---|---|---|
| `storage_quotas` | object | Updated storage quota settings |
| `rate_limits` | object | Updated rate limit settings |

**Override semantics**

- A write by a human **operator** sets the `quota_override` flag (re-stamped on
  every operator write).
- A write by an **automated** platform caller while an override stands is refused
  with `409` naming who set the override and when, unless `?force=true` is passed
  (which also clears the flag).

**Example Request**

```bash
curl -X PUT -b operator-cookies.txt -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"storage_quotas": {"storage_quota_mb": 102400}}' \
  http://your-server:8083/api/v1/organizations/01J1ABCDEF2345GHJKMNPQRSTV/quotas
```

The response `data` echoes `organization_id`, `storage_quotas`, `rate_limits`, and
the current `quota_override` state.

## Clear a Quota Override

Drop the operator override flag so automated plan sync resumes managing this
organization's quotas. The quota values themselves are left untouched -- the next
plan sync is what restores plan-derived numbers.

```
POST /api/v1/organizations/{organization_id}/quotas/clear-override
```

**Auth:** platform scope, operator role `admin` or higher.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | Yes | Why the override is being dropped (recorded in the audit log) |

Idempotent: clearing an organization that has no override still succeeds.

**Example Response**

```json
{
  "type": "success",
  "msg": "Quota override cleared; plan sync will resume managing this organization",
  "data": {
    "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
    "quota_override": false
  }
}
```
