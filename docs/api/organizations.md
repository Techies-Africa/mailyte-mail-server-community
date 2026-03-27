# Organizations

Manage organizations -- the top-level container for domains, email accounts, and settings.

## List Organizations

Retrieve a paginated list of all organizations with usage statistics.

```
GET /api/v1/organizations
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/organizations?page=1&per_page=10"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organizations retrieved successfully",
  "data": {
    "items": [
      {
        "id": "acme",
        "name": "Acme Corp",
        "external_id": "cust_12345",
        "description": "Main customer account",
        "admin_email": "admin@acme.com",
        "admin_name": "Jane Smith",
        "active": true,
        "domain_count": 3,
        "email_account_count": 45,
        "total_storage_used": 5368709120,
        "created_at": "2025-01-15T10:30:00",
        "updated_at": "2025-03-20T14:22:00"
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 10,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

## Get Organization

Retrieve a single organization with detailed statistics, including its domains and storage breakdown.

```
GET /api/v1/organizations/{organization_id}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `organization_id` | string | The organization ID |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/acme
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization retrieved successfully",
  "data": {
    "id": "acme",
    "name": "Acme Corp",
    "external_id": "cust_12345",
    "description": "Main customer account",
    "admin_email": "admin@acme.com",
    "admin_name": "Jane Smith",
    "active": true,
    "settings": {},
    "rate_limits": {
      "inbound_hourly": 5000,
      "outbound_hourly": 2000
    },
    "storage_quotas": {
      "storage_quota_mb": 51200
    },
    "webhook_urls": [],
    "domain_count": 3,
    "email_account_count": 45,
    "domains": [
      {
        "id": 1,
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

Look up an organization using your own system's identifier.

```
GET /api/v1/organizations/by-external-id/{external_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/by-external-id/cust_12345
```

The response format is identical to [Get Organization](#get-organization).

## Create Organization

Create a new organization.

```
POST /api/v1/organizations
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | Yes | Unique ID (alphanumeric, hyphens, underscores) |
| `name` | string | Yes | Display name (max 255 characters) |
| `external_id` | string | No | Your own system's identifier (max 255 characters) |
| `description` | string | No | Description |
| `admin_email` | string | No | Admin contact email |
| `admin_name` | string | No | Admin contact name |
| `settings` | object | No | Custom settings (default: `{}`) |
| `rate_limits` | object | No | Rate limit configuration |
| `storage_quotas` | object | No | Storage quota configuration |
| `webhook_urls` | array | No | Webhook endpoint URLs |
| `webhook_secret` | string | No | Secret for signing webhook payloads |
| `active` | boolean | No | Whether the org is active (default: `true`) |

**Rate Limits Object**

| Field | Type | Description |
|---|---|---|
| `inbound_hourly` | integer | Max inbound messages per hour |
| `outbound_hourly` | integer | Max outbound messages per hour |

**Storage Quotas Object**

| Field | Type | Description |
|---|---|---|
| `storage_quota_mb` | integer | Total storage quota in megabytes |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme",
    "name": "Acme Corp",
    "external_id": "cust_12345",
    "admin_email": "admin@acme.com",
    "rate_limits": {
      "inbound_hourly": 5000,
      "outbound_hourly": 2000
    },
    "storage_quotas": {
      "storage_quota_mb": 51200
    }
  }' \
  http://your-server:5000/api/v1/organizations
```

**Example Response** (`201 Created`)

```json
{
  "type": "success",
  "msg": "Organization created successfully",
  "data": {
    "id": "acme",
    "name": "Acme Corp",
    "external_id": "cust_12345",
    "admin_email": "admin@acme.com",
    "active": true,
    "rate_limits": {
      "inbound_hourly": 5000,
      "outbound_hourly": 2000
    },
    "storage_quotas": {
      "storage_quota_mb": 51200
    },
    "created_at": "2025-03-25T10:30:00"
  }
}
```

!!! warning "Duplicate check"
    If an organization with the same `id` or `external_id` already exists, the API returns `409 Conflict`.

## Update Organization

Update an existing organization. Only the fields you include in the request body are changed.

```
PUT /api/v1/organizations/{organization_id}
```

**Request Body**

All fields from [Create Organization](#create-organization) except `id` are accepted. Only include the fields you want to change.

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Corporation",
    "rate_limits": {
      "inbound_hourly": 10000,
      "outbound_hourly": 5000
    }
  }' \
  http://your-server:5000/api/v1/organizations/acme
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization updated successfully",
  "data": {
    "id": "acme",
    "name": "Acme Corporation",
    "rate_limits": {
      "inbound_hourly": 10000,
      "outbound_hourly": 5000
    },
    "updated_at": "2025-03-25T11:00:00"
  }
}
```

## Delete Organization

Delete an organization. The organization must have no domains or email accounts.

```
DELETE /api/v1/organizations/{organization_id}
```

!!! danger "Prerequisite"
    You must delete all domains and email accounts belonging to the organization before you can delete it. The API returns `400 Bad Request` if any remain.

**Example Request**

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/acme
```

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

Get detailed quota and usage information for an organization.

```
GET /api/v1/organizations/{organization_id}/quotas
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/acme/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization quota information retrieved successfully",
  "data": {
    "organization_id": "acme",
    "organization_quotas": {
      "storage_quota_mb": 51200
    },
    "total_domains": 3,
    "total_email_accounts": 45,
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

## Update Organization Quotas

Update storage quotas and rate limits for an organization.

```
PUT /api/v1/organizations/{organization_id}/quotas
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `storage_quotas` | object | Updated storage quota settings |
| `rate_limits` | object | Updated rate limit settings |

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "storage_quotas": {"storage_quota_mb": 102400},
    "rate_limits": {"inbound_hourly": 10000, "outbound_hourly": 5000}
  }' \
  http://your-server:5000/api/v1/organizations/acme/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Organization quotas updated successfully",
  "data": {
    "organization_id": "acme",
    "storage_quotas": {"storage_quota_mb": 102400},
    "rate_limits": {"inbound_hourly": 10000, "outbound_hourly": 5000}
  }
}
```
