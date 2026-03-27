# Domains

Manage email domains within organizations, including DKIM configuration and quota settings.

## List Domains

Retrieve a paginated list of domains. Optionally filter by organization.

```
GET /api/v1/domains
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Filter by organization |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/domains?organization_id=acme&page=1"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domains retrieved successfully",
  "data": {
    "items": [
      {
        "id": 1,
        "domain": "acme.com",
        "organization_id": "acme",
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
        "created_at": "2025-01-15T10:30:00",
        "updated_at": "2025-03-20T14:22:00"
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

Retrieve a single domain with its email accounts and usage statistics.

```
GET /api/v1/domains/{domain_id}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `domain_id` | integer | The domain's numeric ID |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/domains/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain retrieved successfully",
  "data": {
    "id": 1,
    "domain": "acme.com",
    "organization_id": "acme",
    "organization": {
      "id": "acme",
      "name": "Acme Corp"
    },
    "active": true,
    "max_quota": 10737418240,
    "max_users": 1000,
    "dkim_enabled": true,
    "dkim_selector": "default",
    "email_accounts": [
      {
        "id": 1,
        "email": "john@acme.com",
        "name": "John Doe",
        "status": "ACTIVE",
        "storage_used": 104857600
      }
    ],
    "email_account_count": 20,
    "usage_statistics": {
      "account_usage_percentage": 2.0,
      "storage_usage_percentage": 30.0,
      "total_account_storage": 3221225472
    }
  }
}
```

## Create Domain

Add a new domain to an organization.

```
POST /api/v1/domains
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `domain` | string | Yes | Domain name (e.g., `example.com`) |
| `organization_id` | string | Yes | Organization this domain belongs to |
| `description` | string | No | Description |
| `active` | boolean | No | Whether the domain is active (default: `true`) |
| `max_quota` | integer | No | Max storage in bytes (default: 10 GB) |
| `max_users` | integer | No | Max email accounts (default: 1000) |
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
    "organization_id": "acme",
    "description": "Primary domain",
    "max_quota": 10737418240,
    "max_users": 500,
    "dkim_enabled": true,
    "dkim_selector": "mailyte"
  }' \
  http://your-server:5000/api/v1/domains
```

**Example Response** (`201 Created`)

```json
{
  "type": "success",
  "msg": "Domain created successfully",
  "data": {
    "id": 1,
    "domain": "acme.com",
    "organization_id": "acme",
    "active": true,
    "max_quota": 10737418240,
    "max_users": 500,
    "dkim_enabled": true,
    "dkim_selector": "mailyte",
    "created_at": "2025-03-25T10:30:00"
  }
}
```

!!! info "DKIM key generation"
    When you create a domain with `dkim_enabled: true`, the server generates a DKIM key pair automatically. Retrieve the public key from your DNS configuration panel to add it as a TXT record at `{dkim_selector}._domainkey.{domain}`.

!!! warning "Duplicate check"
    Domain names are checked case-insensitively. If `acme.com` already exists, creating `ACME.COM` returns `409 Conflict`.

## Update Domain

Update an existing domain. Only the fields you include are changed.

```
PUT /api/v1/domains/{domain_id}
```

**Request Body**

All fields from [Create Domain](#create-domain) except `domain` and `organization_id` are accepted.

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "max_users": 1000,
    "description": "Primary corporate domain"
  }' \
  http://your-server:5000/api/v1/domains/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain updated successfully",
  "data": {
    "id": 1,
    "domain": "acme.com",
    "max_users": 1000,
    "description": "Primary corporate domain",
    "updated_at": "2025-03-25T11:00:00"
  }
}
```

## Delete Domain

Delete a domain. The domain must have no email accounts.

```
DELETE /api/v1/domains/{domain_id}
```

!!! danger "Prerequisite"
    You must delete all email accounts on the domain before you can delete the domain itself.

**Example Request**

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/domains/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain deleted successfully"
}
```

## Get Domain Quotas

Get quota and usage details for a specific domain.

```
GET /api/v1/domains/{domain_id}/quotas
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/domains/1/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain quota information retrieved successfully",
  "data": {
    "domain_id": 1,
    "domain": "acme.com",
    "max_quota": 10737418240,
    "max_users": 1000,
    "storage_used": 3221225472,
    "usage_percentage": 30.0,
    "account_usage_percentage": 2.0,
    "rate_limits": {},
    "storage_quotas": {},
    "email_accounts": [
      {
        "id": 1,
        "email": "john@acme.com",
        "storage_quota": 1073741824,
        "storage_used": 104857600,
        "usage_percentage": 9.77
      }
    ]
  }
}
```

## Update Domain Quotas

Update quota settings for a domain.

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

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "max_quota": 21474836480,
    "max_users": 2000
  }' \
  http://your-server:5000/api/v1/domains/1/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain quotas updated successfully",
  "data": {
    "domain_id": 1,
    "max_quota": 21474836480,
    "max_users": 2000,
    "rate_limits": {},
    "storage_quotas": {}
  }
}
```

## Get Domain Statistics

Get statistics for a domain by name (mailbox count, alias count, quota usage).

```
GET /api/v1/get/domain/stats/{domain}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `domain` | string | The domain name (e.g., `acme.com`) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/get/domain/stats/acme.com
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
    "total_quota_used": 3221225472
  }
}
```
