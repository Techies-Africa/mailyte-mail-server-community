# Email Accounts

Manage email accounts (mailboxes) within domains and organizations.

All routes in this module live under the `/api/v1/mailboxes` prefix. Account IDs
are 26-character ULID strings. Account status values are lowercase: `active`,
`inactive`, `suspended`.

!!! note "Password policy"
    Everywhere a password is accepted, it must be **at least 12 characters** and
    contain both letters and numbers; a short list of very common passwords is also
    rejected. Passwords are bcrypt-hashed and never returned by any endpoint.

## List Email Accounts

Retrieve a paginated list of email accounts. A tenant credential always sees only
its own organization; platform scope sees every organization and may narrow with
`organization_id`.

```
GET /api/v1/mailboxes/email-accounts
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | -- | Search the email address or display name (substring) |
| `organization_id` | string | -- | Platform scope only; ignored for tenant credentials |
| `domain_id` | string | -- | Filter by domain ULID |
| `status` | string | -- | `active`, `inactive`, or `suspended` (anything else is `422`) |
| `sort_by` | string | `email` | `email`, `created_at`, `storage_used`, `last_login` |
| `sort_dir` | string | `asc` | `asc` or `desc` |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:8083/api/v1/mailboxes/email-accounts?status=active&q=john"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Email accounts retrieved successfully",
  "data": {
    "items": [
      {
        "id": "01J1ACC0000000000000000000",
        "email": "john@acme.com",
        "local_part": "john",
        "name": "John Doe",
        "domain_id": "01J1DOM0000000000000000000",
        "domain_name": "acme.com",
        "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
        "organization_name": "Acme Corp",
        "status": "active",
        "storage_quota": 1073741824,
        "storage_used": 104857600,
        "forward_enabled": false,
        "vacation_enabled": false,
        "created_at": "2026-01-20T09:00:00"
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 45,
      "total_pages": 1
    }
  }
}
```

!!! note "Passwords are never returned"
    The `password` field is always stripped from API responses.

## Get Email Account

Retrieve a single email account with its full domain and organization records.

```
GET /api/v1/mailboxes/email-accounts/{account_id}
```

**Example Response** (abridged)

```json
{
  "type": "success",
  "msg": "Email account retrieved successfully",
  "data": {
    "id": "01J1ACC0000000000000000000",
    "email": "john@acme.com",
    "local_part": "john",
    "name": "John Doe",
    "status": "active",
    "storage_quota": 1073741824,
    "storage_used": 104857600,
    "forward_enabled": false,
    "forward_destination": null,
    "vacation_enabled": false,
    "vacation_message": null,
    "domain": { "id": "01J1DOM0000000000000000000", "domain": "acme.com", "active": true },
    "organization": { "id": "01J1ABCDEF2345GHJKMNPQRSTV", "name": "Acme Corp" }
  }
}
```

## Create Email Account

Create a new email account. The domain part of the address must already exist (and,
for tenant credentials, belong to your organization -- otherwise `404`). The new
account always belongs to the domain's organization.

```
POST /api/v1/mailboxes/email-accounts
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Full email address (e.g., `john@acme.com`) |
| `password` | string | Yes | Min 12 chars, letters and numbers |
| `name` | string | No | Display name (HTML is stripped) |
| `status` | string | No | `active`, `inactive`, `suspended` (default: `active`) |
| `storage_quota` | integer | No | Storage quota in bytes (default: `1073741824` = 1 GB) |
| `rate_limits` | object | No | Account-level rate limits |
| `storage_quotas` | object | No | Account-level storage quota settings |
| `forward_enabled` | boolean | No | Enable email forwarding (default: `false`) |
| `forward_destination` | string | No | Forwarding destination email |
| `vacation_enabled` | boolean | No | Enable vacation auto-reply (default: `false`) |
| `vacation_message` | string | No | Vacation auto-reply message |
| `external_id` | string | No | Your own system's identifier |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "john@acme.com",
    "password": "secure-pass-1234",
    "name": "John Doe",
    "storage_quota": 2147483648
  }' \
  http://your-server:8083/api/v1/mailboxes/email-accounts
```

**Example Response** (`201 Created`) -- the full account record (minus password).

!!! warning "Validation"
    - The domain in the email address must exist (tenant callers: in your org).
    - The domain must not have reached its `max_users` limit (`400`).
    - A duplicate address or `external_id` returns `409`.
    - Validation failures return `400` with messages in `data.errors`.

## Update Email Account

Update an existing email account. Only the fields you include are changed. A
`password` field is re-validated and re-hashed.

```
PUT /api/v1/mailboxes/email-accounts/{account_id}
```

**Request Body**

All fields from [Create Email Account](#create-email-account) except `email` are
accepted (the address itself cannot be changed).

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John A. Doe",
    "forward_enabled": true,
    "forward_destination": "john.personal@gmail.com"
  }' \
  http://your-server:8083/api/v1/mailboxes/email-accounts/01J1ACC0000000000000000000
```

The response `data` is the full updated record.

## Delete Email Account

Delete an email account and update domain counters.

```
DELETE /api/v1/mailboxes/email-accounts/{account_id}
```

**Responses**

- `200` with `{"type": "success", "msg": "Email account deleted successfully"}` when
  the account was deleted.
- `204 No Content` when the account is already absent (never existed, or belongs to
  another organization) -- deletes are retry-safe by design.

## Get Account Quotas

Get detailed quota and usage information for a specific email account.

```
GET /api/v1/mailboxes/email-accounts/{account_id}/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Account quota information retrieved successfully",
  "data": {
    "account_id": "01J1ACC0000000000000000000",
    "email": "john@acme.com",
    "storage_quota": 2147483648,
    "storage_used": 104857600,
    "attachment_storage_used": 52428800,
    "email_storage_used": 52428800,
    "usage_percentage": 4.88,
    "total_files": 150,
    "total_attachments": 75,
    "total_emails": 320,
    "rate_limits": {},
    "storage_quotas": {},
    "last_storage_calculation": "2026-03-25T08:00:00",
    "over_threshold": false
  }
}
```

## Update Account Quotas

```
PUT /api/v1/mailboxes/email-accounts/{account_id}/quotas
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `storage_quota` | integer | Storage quota in bytes |
| `rate_limits` | object | Account-level rate limits |
| `storage_quotas` | object | Additional storage quota settings |

The response `data` echoes `account_id`, `storage_quota`, `rate_limits`, and
`storage_quotas`.

## Legacy Endpoints

These raw-SQL endpoints predate the `email-accounts` CRUD above and operate on
mailbox **addresses**.

### Add mailbox

```
POST /api/v1/mailboxes/add
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `local_part` | string | Yes | Local part (before the `@`); letters, numbers, dots, hyphens, underscores only |
| `domain` | string | Yes | Domain name (must exist, be active, and -- for tenant callers -- belong to your org) |
| `password` | string | Yes | Same 12+ character policy as everywhere else |
| `name` | string | No | Display name |
| `quota` | integer | No | Storage quota in **bytes** (default: `5368709120` = 5 GB) |
| `active` | integer | No | `1` for active (default), `0` for inactive |
| `email` | string | No | If supplied, must equal `local_part@domain` exactly |

A duplicate address returns `409` with `error_code: "MAILBOX_ALREADY_EXISTS"` and
`data.existing_id`.

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "jane",
    "domain": "acme.com",
    "password": "secure-pass-4567",
    "name": "Jane Smith"
  }' \
  http://your-server:8083/api/v1/mailboxes/add
```

### Get mailbox information

```
GET /api/v1/mailboxes/get/{mailbox_id}
```

`mailbox_id` may be an email address, an account ULID, or `all` (lists all
**active** mailboxes in your scope, unpaginated). Each row includes a small `stats`
object (`message_count`, `total_size`) and `quota_usage_percent`.

### Edit mailboxes (bulk)

```
POST /api/v1/mailboxes/edit
```

**Request Body**

```json
{
  "items": ["john@acme.com", "jane@acme.com"],
  "attr": {"name": "Renamed", "active": 1}
}
```

Accepted `attr` keys: `name`, `quota` (bytes, stored as `storage_quota`),
`active` (maps onto `status`: truthy → `active`, falsy → `inactive`), and
`password`. Mailcow-era keys (`force_pw_update`, `tls_enforce_in`,
`tls_enforce_out`, `quarantine_notification`, `quarantine_category`,
`rl_value`, `rl_frame`) have no column in this schema and are ignored --
before 2026-08-30 passing them (or any edit at all, via a nonexistent
`modified` column) failed the batch with `500`. Returns per-mailbox
success/error results. A `password` or `active` change also flushes
Dovecot's auth cache for that mailbox, so it takes effect on the next login
rather than after the cache TTL.

### Delete mailboxes (bulk)

```
POST /api/v1/mailboxes/delete
```

**Request Body:** a JSON array of addresses, e.g. `["john@acme.com"]`.

**Example Response**

```json
{
  "type": "success",
  "msg": "Mailbox deletion completed",
  "data": [
    {
      "mailbox": "john@acme.com",
      "status": "success",
      "msg": "Mailbox john@acme.com deleted successfully",
      "stats": {"aliases_deleted": 2, "messages_deleted": 0}
    }
  ]
}
```

`messages_deleted` is always `0` -- this endpoint removes the account row and its
aliases; message files on disk are not touched by it.

### Legacy quota endpoints

```
GET  /api/v1/mailboxes/get/quota/{mailbox}
POST /api/v1/mailboxes/edit/quota
```

`GET /get/quota/{mailbox}` returns `email`, `storage_quota`, `storage_used`
(bytes), `usage_percent`, `available`, and `folder_breakdown`.
`folder_breakdown` is always an empty list: per-folder sizes live in the
Maildir on disk (Dovecot owns them), not in any database table.

`POST /edit/quota` takes `{"mailbox": "...", "quota": <bytes>}` and updates
`storage_quota`; a non-integer or negative quota returns `400`.

(Both endpoints returned `500` on every call before 2026-08-30 -- a
FROM-less folder query and writes to nonexistent `quota`/`modified`
columns. The modern [Get Account Quotas](#get-account-quotas) and
[Update Account Quotas](#update-account-quotas) endpoints remain the
preferred interface.)

### Get mailbox statistics

```
GET /api/v1/mailboxes/get/stats/{mailbox}
```

Returns `mailbox_info` (email, created, last_login, storage figures),
`message_stats` from the delivery log (`total_messages`, `avg_message_size`,
`largest_message_size`, `oldest_message`, `newest_message`; `unread_messages` is
always `null` -- read state lives in the Maildir, not the database), and
`login_stats` for the last 30 days (`total_logins`, `last_login`, `unique_ips`,
successful logins only).
