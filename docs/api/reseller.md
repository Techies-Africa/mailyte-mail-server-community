---
edition: enterprise
---

# Reseller

Manage sub-organizations under a reseller parent: create tenants with plan limits, track their usage, and pull a consolidated billing summary.

**Base path:** `/api/v1/reseller`
**Auth:** every endpoint requires **platform scope with an operator session at role `admin` or above** (`require_api_key("admin", scope="platform", role="admin")`).

## The sub-organization object

| Field | Type | Description |
|---|---|---|
| `id` | string | Sub-organization ULID |
| `name` | string | Display name |
| `parent_organization_id` | string | The reseller parent |
| `admin_email` / `admin_name` | string \| null | Sub-organization admin contact |
| `status` | string | `active`, `inactive`, ... |
| `plan` | object | `max_users`, `max_domains`, `storage_limit`, `plan_name` |
| `created_at` / `updated_at` | string \| null | ISO 8601 |

The detail view adds `domain_count`, `user_count`, `storage_used` (bytes), and `domains` (list).

## Create Sub-Organization

Create a new sub-organization under a reseller parent, with a plan of configurable user, domain, and storage limits.

```
POST /api/v1/reseller/sub-organizations
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `parent_org_id` | string | Yes | ID of the parent reseller organization |
| `name` | string | Yes | Display name |
| `admin_email` | string | No | Admin email for the sub-organization |
| `admin_name` | string | No | Admin name |
| `max_users` | integer | No | Max mailboxes (default `50`) |
| `max_domains` | integer | No | Max domains (default `5`) |
| `storage_limit` | integer | No | Storage limit in bytes (default 10 GB) |
| `plan_name` | string | No | Plan tier name (default `starter`) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "parent_org_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
    "name": "Client Co",
    "admin_email": "admin@clientco.com",
    "max_users": 100,
    "plan_name": "business"
  }' \
  http://your-server:5000/api/v1/reseller/sub-organizations
```

Returns the created sub-organization object.

## List Sub-Organizations

All sub-organizations managed by a reseller, including usage metrics and billing summaries.

```
GET /api/v1/reseller/sub-organizations
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `parent_org_id` | string | Parent reseller organization |
| `status` | string | Filter by status (`active`, `inactive`, ...) |

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reseller/sub-organizations?parent_org_id=01J8ZJ...&status=active"
```

## Get Sub-Organization

Detailed information including plan, domain list, user count, and current storage usage.

```
GET /api/v1/reseller/sub-organizations/{org_id}
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/reseller/sub-organizations/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

## Update Sub-Organization Plan

Update plan and quota limits. Only fields included in the request body are changed.

```
PUT /api/v1/reseller/sub-organizations/{org_id}/plan
```

**Request Body** (all optional)

| Field | Type | Description |
|---|---|---|
| `max_users` | integer | New max users limit |
| `max_domains` | integer | New max domains limit |
| `storage_limit` | integer | New storage limit in bytes |
| `plan_name` | string | New plan tier name |

```bash
curl -X PUT -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"max_users": 250, "plan_name": "enterprise"}' \
  http://your-server:5000/api/v1/reseller/sub-organizations/01J9AB.../plan
```

## Deactivate Sub-Organization

Soft-delete: sets the sub-organization's status to `inactive`. Associated domains and mailboxes are preserved but become non-operational.

```
DELETE /api/v1/reseller/sub-organizations/{org_id}
```

```bash
curl -X DELETE -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/reseller/sub-organizations/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

## Billing Summary

Consolidated billing summary for all sub-organizations under a reseller: total users, domains, storage usage, and a per-organization breakdown.

```
GET /api/v1/reseller/billing/summary
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `parent_org_id` | string | Parent reseller organization. Omit for the instance-wide rollup (then `parent_org_id` in the response is `null`) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/reseller/billing/summary?parent_org_id=01J8ZJ..."
```

**Example Response**

```json
{
  "parent_org_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
  "total_sub_organizations": 12,
  "total_active_sub_organizations": 11,
  "total_users": 480,
  "total_domains": 31,
  "total_storage_used": 92341827584,
  "total_storage_allocated": 128849018880,
  "total_mailboxes": 480,
  "total_storage_bytes": 92341827584,
  "per_org_breakdown": [ { "org_id": "01J9AB...", "name": "Client Co", "users": 42 } ],
  "by_plan": [ { "plan_name": "business", "count": 7 } ]
}
```

`total_mailboxes`, `total_storage_bytes`, and `by_plan` are additive aliases the console reads — they duplicate the totals rather than replacing them, so existing consumers keep working.
