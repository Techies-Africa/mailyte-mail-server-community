# Storage Management

Monitor storage usage and manage quota configuration at the domain and mailbox
level. The per-entity routes proxy to the internal storage service; the summary is
served straight from the database.

Reads verify that the domain or mailbox belongs to your organization (`404`
otherwise). Storage figures are as fresh as the storage service's last calculation
pass.

## Domain Storage Usage

Get storage usage details for a domain.

```
GET /api/v1/storage/usage/domain/{domain}
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force_calculate` | string | `false` | `true` forces a fresh recalculation instead of returning the cached figure |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:8083/api/v1/storage/usage/domain/acme.com
```

**Example Response**

```json
{
  "success": true,
  "entity_type": "domain",
  "identifier": "acme.com",
  "usage": { ... },
  "timestamp": "2026-08-30T10:30:00Z"
}
```

The `usage` object is the storage service's calculation record for the entity,
passed through verbatim. `404` (`{"error": "Usage data not found"}`) when no usage
has been calculated yet.

## Mailbox Storage Usage

```
GET /api/v1/storage/usage/mailbox/{email}
```

Same shape as the domain read, with `entity_type: "mailbox"`.

## Storage Quota Configuration

### Get quota configuration

```
GET /api/v1/storage/quotas/domain/{domain}
GET /api/v1/storage/quotas/mailbox/{email}
```

Returns the effective quota rule for the entity (with inheritance --
mailbox falls back to domain, domain to organization):

```json
{
  "success": true,
  "entity_type": "domain",
  "identifier": "acme.com",
  "quota_configuration": {
    "total_storage_limit": 10737418240,
    "attachment_storage_limit": 5368709120,
    "max_file_size": 26214400,
    "max_attachment_size": 26214400,
    "max_email_size": 52428800,
    "enforce_limits": true,
    "warning_threshold": 80,
    "critical_threshold": 95,
    "allowed_file_types": null,
    "blocked_file_types": null
  },
  "timestamp": "2026-08-30T10:30:00Z"
}
```

### Set quota configuration

```
POST /api/v1/storage/quotas/domain/{domain}
POST /api/v1/storage/quotas/mailbox/{email}
```

**Auth:** platform scope, operator role `admin` or higher (operator console
session).

**Domain request body**

| Field | Type | Description |
|---|---|---|
| `default_mailbox_quota_mb` | integer | Default per-mailbox quota in **MB** for newly created mailboxes |
| `domain_quota_mb` | integer | Total storage cap in **MB** for the entire domain |
| `warning_threshold_percent` | integer | Usage percentage that triggers a warning (default 80) |

**Mailbox request body**

| Field | Type | Description |
|---|---|---|
| `quota_mb` | integer | Storage quota for this mailbox in **MB** |
| `warning_threshold_percent` | integer | Warning threshold (default 80) |

Writes merge into the entity's `storage_quotas` configuration (fields not
mentioned are left untouched). The MB-denominated fields are stored
canonically: `domain_quota_mb`/`quota_mb` become `total_storage_limit` in
**bytes** and `warning_threshold_percent` becomes `warning_threshold`, so the
quota **read** endpoints above reflect the change immediately.
`default_mailbox_quota_mb` is stored as given for mailbox provisioning.

Responds `500` (`"Failed to update configuration"`) when the domain/mailbox
does not exist or a value is out of range (thresholds must be 1-100, sizes
positive).

!!! note "Fixed 2026-08-30"
    These writes previously failed with `500` on every request -- the storage
    service called a configuration-update operation that did not exist. The
    operation is now implemented (`update_storage_quota_config` in
    `worker/storage_usage/services/config_service.py`).

## Usage Summary

Aggregated storage usage. Served from the database (the same
`domains`/`email_accounts` columns the storage service writes after each
calculation pass).

```
GET /api/v1/storage/usage/summary
```

**Auth:** platform scope, operator role `support` or higher.

**Example Response**

```json
{
  "total_storage_used": 5368709120,
  "total_quota": 32212254720,
  "usage_percentage": 16.67,
  "domain_count": 3,
  "account_count": 45,
  "top_domains": [
    {"domain": "acme.com", "storage_used": 3221225472, "max_quota": 10737418240}
  ],
  "top_accounts": [
    {"email": "john@acme.com", "storage_used": 104857600, "storage_quota": 1073741824}
  ]
}
```

`top_domains` and `top_accounts` list the ten largest consumers.

## Storage Cleanup

Trigger a cleanup pass in the storage service.

```
POST /api/v1/storage/cleanup
```

**Auth:** platform scope, operator role `operator` or higher.

**Request Body**

| Field | Type | Default | Description |
|---|---|---|---|
| `domain` | string | -- | Limit cleanup to a specific domain |
| `older_than_days` | integer | `30` | Age threshold |
| `target` | string | `trash` | `trash`, `spam`, `expired`, or `all` |
| `dry_run` | boolean | `false` | Preview without deleting |

All four fields are honored (fixed 2026-08-30 -- the service previously read
only a `days_to_keep` value and ignored every documented field, including
`dry_run`):

- `older_than_days` sets the age threshold (legacy `days_to_keep` is still
  accepted; `older_than_days` wins when both are present)
- `domain` limits the pass to that domain's records (the domain entity and its
  `user@domain` mailboxes)
- `dry_run: true` counts matching records without deleting anything (reported
  as `matched_records`; `cleaned_records` stays `0`)
- `target` is validated (`400` on other values)

**Example Response**

```json
{
  "success": true,
  "dry_run": false,
  "older_than_days": 30,
  "target": "trash",
  "domain": null,
  "cleaned_records": 14,
  "matched_records": 12,
  "cache_entries": 2,
  "days_to_keep": 30,
  "note": "Cleanup purges this service's storage-calculation records; mail data is never deleted.",
  "timestamp": "2026-08-30T10:30:00Z"
}
```

!!! warning "Scope of cleanup"
    Whatever the `target`, cleanup purges the storage service's own
    **storage-calculation records** (plus expired cache entries on a real run).
    It never deletes mail from mailboxes -- there is no mail-deletion engine
    behind this endpoint, and the response says so in its `note` field.

## Errors

| Status | Meaning |
|---|---|
| `400` | Cleanup body invalid (`target` not one of the allowed values, or `older_than_days` not a positive integer) |
| `404` | Domain/mailbox not found or not owned by your organization, or no usage data calculated yet |
| `422` | Request body failed validation |
| `500` | Database connection failed during the ownership check, a quota write to a nonexistent entity or with out-of-range values, or a storage-service internal error |
| `503` | Storage service unreachable (`{"error": "Storage service unavailable"}`) |
