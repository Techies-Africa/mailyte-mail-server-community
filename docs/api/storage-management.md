# Storage Management

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Monitor storage usage and manage quotas at the domain and mailbox level.

## Domain Storage Usage

Get storage usage details for a domain.

```
GET /api/v1/storage/usage/domain/{domain}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `domain` | string | Domain name (e.g., `acme.com`) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/storage/usage/domain/acme.com
```

**Example Response**

```json
{
  "domain": "acme.com",
  "storage": {
    "total_used_bytes": 3221225472,
    "total_quota_bytes": 10737418240,
    "usage_percentage": 30.0,
    "mailbox_count": 20,
    "breakdown": {
      "emails": 2684354560,
      "attachments": 536870912
    }
  }
}
```

## Mailbox Storage Usage

Get storage usage details for a specific mailbox.

```
GET /api/v1/storage/usage/mailbox/{email}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/storage/usage/mailbox/john@acme.com
```

**Example Response**

```json
{
  "email": "john@acme.com",
  "storage": {
    "used_bytes": 104857600,
    "quota_bytes": 1073741824,
    "usage_percentage": 9.77,
    "breakdown": {
      "inbox": 52428800,
      "sent": 26214400,
      "drafts": 5242880,
      "attachments": 20971520
    }
  }
}
```

## Domain Storage Quotas

### Get Quotas

```
GET /api/v1/storage/quotas/domain/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/storage/quotas/domain/acme.com
```

**Example Response**

```json
{
  "domain": "acme.com",
  "quotas": {
    "total_quota_bytes": 10737418240,
    "default_mailbox_quota_bytes": 1073741824,
    "max_mailbox_quota_bytes": 5368709120,
    "warning_threshold_percent": 80,
    "critical_threshold_percent": 95
  }
}
```

### Set Quotas

```
POST /api/v1/storage/quotas/domain/{domain}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `total_quota_bytes` | integer | Total domain storage quota in bytes |
| `default_mailbox_quota_bytes` | integer | Default quota for new mailboxes |
| `max_mailbox_quota_bytes` | integer | Maximum allowed mailbox quota |
| `warning_threshold_percent` | integer | Send alert at this usage percentage |
| `critical_threshold_percent` | integer | Send critical alert at this percentage |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "total_quota_bytes": 21474836480,
    "default_mailbox_quota_bytes": 2147483648,
    "warning_threshold_percent": 85
  }' \
  http://your-server:5000/api/v1/storage/quotas/domain/acme.com
```

## Mailbox Storage Quotas

### Get Quotas

```
GET /api/v1/storage/quotas/mailbox/{email}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/storage/quotas/mailbox/john@acme.com
```

### Set Quotas

```
POST /api/v1/storage/quotas/mailbox/{email}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `quota_bytes` | integer | Storage quota in bytes |
| `warning_threshold_percent` | integer | Alert threshold |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "quota_bytes": 5368709120,
    "warning_threshold_percent": 90
  }' \
  http://your-server:5000/api/v1/storage/quotas/mailbox/john@acme.com
```

## Usage Summary

Get an overall storage usage summary across all domains and mailboxes.

```
GET /api/v1/usage/summary
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `organization_id` | string | Filter by organization |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/usage/summary?organization_id=acme"
```

**Example Response**

```json
{
  "organization_id": "acme",
  "summary": {
    "total_domains": 3,
    "total_mailboxes": 45,
    "total_storage_used_bytes": 5368709120,
    "total_quota_bytes": 32212254720,
    "usage_percentage": 16.67,
    "domains_over_warning": 1,
    "mailboxes_over_warning": 3
  }
}
```

## Storage Cleanup

Trigger storage cleanup operations (remove expired messages, compress old data).

```
POST /api/v1/storage/cleanup
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `domain` | string | Domain to clean up (optional, all domains if omitted) |
| `older_than_days` | integer | Remove messages older than this many days |
| `dry_run` | boolean | Preview what would be deleted without actually deleting |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "acme.com",
    "older_than_days": 365,
    "dry_run": true
  }' \
  http://your-server:5000/api/v1/storage/cleanup
```

**Example Response**

```json
{
  "status": "preview",
  "domain": "acme.com",
  "would_delete": {
    "messages": 1250,
    "bytes_freed": 524288000
  }
}
```

!!! tip "Always dry-run first"
    Use `"dry_run": true` to see what would be cleaned up before running the actual cleanup.
