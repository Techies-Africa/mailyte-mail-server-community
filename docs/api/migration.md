---
edition: enterprise
---

# Migration

IMAP mailbox migration in both directions: import mail **from** an external provider (Google Workspace, Microsoft 365, any standard IMAP server) **into** Mailyte mailboxes, or export from Mailyte to an external server. Jobs run asynchronously with per-message progress tracking, structured error logs, retry of failed messages, and incremental (delta) sync for cutover windows.

**Base path:** `/api/v1/migration`
**Auth:** `X-API-Key` (write to start/cancel/retry jobs, read for status) or a dashboard session. All target (import) or source (export) mailboxes must belong to the requesting organization.

## Start Import

Start an IMAP import job from an external email provider. Progress is tracked via the [job status endpoint](#get-job-status).

```
POST /api/v1/migration/import
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `source_host` | string | Yes | Source IMAP server (e.g. `imap.gmail.com`) |
| `source_port` | integer | No | Default `993` |
| `source_ssl` | boolean | No | Default `true` |
| `source_username` | string | Yes | Source account username/email |
| `source_password` | string | Yes | Source account password or app password |
| `target_email` | string | Yes | Mailyte mailbox to import into |
| `target_password` | string | Yes | Mailyte mailbox password |
| `folder_mapping` | array | No | Custom mappings: `[{"source": "Sent Items", "target": "Sent"}]` |
| `exclude_folders` | array | No | Folders to skip (e.g. `["[Gmail]/All Mail"]`) |
| `webhook_url` | string | No | URL to receive progress/completion webhooks |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source_host": "imap.gmail.com",
    "source_username": "john@gmail.com",
    "source_password": "app-password",
    "target_email": "john@acme.com",
    "target_password": "mailbox-password",
    "exclude_folders": ["[Gmail]/All Mail"]
  }' \
  http://your-server:5000/api/v1/migration/import
```

**Example Response**

```json
{
  "success": true,
  "job_id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
  "direction": "import",
  "status": "pending",
  "message": "Import job started",
  "timestamp": "2026-08-30T10:15:00"
}
```

## Bulk Import

Start import jobs for multiple accounts in a single request. Each account gets its own independent job with separate progress tracking.

```
POST /api/v1/migration/import/bulk
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `accounts` | array | Yes | Per-account objects: `source_host`, `source_port`, `source_ssl`, `source_username`, `source_password`, `target_email`, `target_password` |
| `folder_mapping` | array | No | Shared mappings applied to all accounts |
| `exclude_folders` | array | No | Folders to skip for all accounts |
| `webhook_url` | string | No | Webhook for bulk progress notifications |

**Example Response**

```json
{
  "success": true,
  "direction": "import",
  "total_jobs": 3,
  "jobs": [ { "job_id": "01J9AB...", "target_email": "a@acme.com" } ],
  "message": "3 import jobs started",
  "timestamp": "2026-08-30T10:15:00"
}
```

## Start Export

Migrate emails from a Mailyte mailbox **to** an external IMAP server. The Mailyte mailbox is the source; the external server is the destination.

```
POST /api/v1/migration/export
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `source_email` | string | Yes | Mailyte mailbox to export from |
| `source_password` | string | Yes | Mailyte mailbox password |
| `target_host` | string | Yes | Destination IMAP server |
| `target_port` | integer | No | Default `993` |
| `target_ssl` | boolean | No | Default `true` |
| `target_username` | string | Yes | Destination account username/email |
| `target_password` | string | Yes | Destination account password |
| `folder_mapping` / `exclude_folders` / `webhook_url` | -- | No | As for import |

Response shape matches [Start Import](#start-import) with `direction: "export"`.

## Bulk Export

```
POST /api/v1/migration/export/bulk
```

Same shape as [Bulk Import](#bulk-import), with per-account export fields (`source_email`, `source_password`, `target_host`, `target_port`, `target_ssl`, `target_username`, `target_password`).

## List Jobs

Paginated list of all migration jobs for the requesting organization.

```
GET /api/v1/migration/jobs
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `status` | string | -- | `pending`, `running`, `completed`, `failed`, `cancelled` |
| `direction` | string | -- | `import` \| `export` |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | 1–200 |

**Example Response**

```json
{
  "success": true,
  "total": 12,
  "page": 1,
  "per_page": 50,
  "jobs": [
    {
      "job_id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
      "status": "running",
      "direction": "import",
      "source_host": "imap.gmail.com",
      "source_username": "john@gmail.com",
      "target_email": "john@acme.com",
      "total_messages": 8200,
      "migrated_messages": 3100,
      "failed_messages": 2,
      "current_folder": "INBOX",
      "speed": 14.2,
      "is_delta": false,
      "is_retry": false,
      "parent_job_id": null,
      "started_at": "2026-08-30T10:15:05",
      "completed_at": null
    }
  ],
  "timestamp": "2026-08-30T10:20:00"
}
```

## Get Job Status

Detailed status of one job: overall progress (total, migrated, failed message counts), current processing speed, the folder currently being processed, and per-message failure counts.

```
GET /api/v1/migration/jobs/{job_id}
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/migration/jobs/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

## Get Job Errors

Paginated, structured error log for a job. Each entry includes the folder name, message UID, error type, and human-readable details.

```
GET /api/v1/migration/jobs/{job_id}/errors
```

**Query Parameters:** `page` (default `1`), `per_page` (default `50`, max 200).

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/migration/jobs/01J9AB.../errors?page=1"
```

## Cancel Job

Cancels a running or pending job. Messages already migrated are retained; the job status is set to `cancelled` and no further processing occurs.

```
POST /api/v1/migration/jobs/{job_id}/cancel
```

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/migration/jobs/01J9AB.../cancel
```

## Retry Failed Messages

Creates a new **child job** that re-attempts only the messages that failed in the original job. The parent job must be `completed` or `failed` with at least one failed message.

```
POST /api/v1/migration/jobs/{job_id}/retry-failed
```

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/migration/jobs/01J9AB.../retry-failed
```

## Delta Sync

Runs an incremental sync on a **completed** job, fetching only messages newer than the last sync timestamp. Useful during cutover periods when users are still receiving email at the old provider.

```
POST /api/v1/migration/jobs/{job_id}/delta
```

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/migration/jobs/01J9AB.../delta
```

## Migration Summary

Aggregated summary of all migration activity for the organization: job counts by status and direction, total messages migrated/failed, average speed, and recent errors.

```
GET /api/v1/migration/summary
```

**Example Response**

```json
{
  "success": true,
  "org_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
  "summary": {
    "total_jobs": 12,
    "completed_jobs": 9,
    "running_jobs": 1,
    "failed_jobs": 1,
    "pending_jobs": 0,
    "cancelled_jobs": 1,
    "total_messages": 84200,
    "total_migrated": 83950,
    "total_failed": 250,
    "avg_speed": 12.7,
    "import_jobs": 10,
    "export_jobs": 2,
    "first_job_at": "2026-07-01T08:00:00",
    "last_activity_at": "2026-08-30T10:20:00"
  },
  "recent_errors": [],
  "timestamp": "2026-08-30T10:25:00"
}
```
