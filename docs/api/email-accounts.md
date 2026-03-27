# Email Accounts

Manage email accounts (mailboxes) within domains and organizations.

## List Email Accounts

Retrieve a paginated list of email accounts. Filter by domain, organization, or status.

```
GET /api/v1/email-accounts
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `domain_id` | integer | -- | Filter by domain ID |
| `organization_id` | string | -- | Filter by organization ID |
| `status` | string | -- | Filter by status (`ACTIVE`, `SUSPENDED`, `DISABLED`) |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/email-accounts?organization_id=acme&status=ACTIVE"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Email accounts retrieved successfully",
  "data": {
    "items": [
      {
        "id": 1,
        "email": "john@acme.com",
        "local_part": "john",
        "name": "John Doe",
        "domain_id": 1,
        "domain_name": "acme.com",
        "organization_id": "acme",
        "organization_name": "Acme Corp",
        "status": "ACTIVE",
        "storage_quota": 1073741824,
        "storage_used": 104857600,
        "forward_enabled": false,
        "vacation_enabled": false,
        "created_at": "2025-01-20T09:00:00"
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

Retrieve a single email account with domain and organization details.

```
GET /api/v1/email-accounts/{account_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/email-accounts/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Email account retrieved successfully",
  "data": {
    "id": 1,
    "email": "john@acme.com",
    "local_part": "john",
    "name": "John Doe",
    "status": "ACTIVE",
    "storage_quota": 1073741824,
    "storage_used": 104857600,
    "forward_enabled": false,
    "forward_destination": null,
    "vacation_enabled": false,
    "vacation_message": null,
    "domain": {
      "id": 1,
      "domain": "acme.com",
      "active": true
    },
    "organization": {
      "id": "acme",
      "name": "Acme Corp"
    }
  }
}
```

## Create Email Account

Create a new email account. The domain portion of the email address must already exist.

```
POST /api/v1/email-accounts
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Full email address (e.g., `john@acme.com`) |
| `password` | string | Yes | Password (min 8 chars, must contain letters and numbers) |
| `name` | string | No | Display name |
| `status` | string | No | Account status: `ACTIVE`, `SUSPENDED`, `DISABLED` (default: `ACTIVE`) |
| `storage_quota` | integer | No | Storage quota in bytes (default: 1 GB) |
| `rate_limits` | object | No | Account-level rate limits |
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
    "password": "SecurePass123",
    "name": "John Doe",
    "storage_quota": 2147483648
  }' \
  http://your-server:5000/api/v1/email-accounts
```

**Example Response** (`201 Created`)

```json
{
  "type": "success",
  "msg": "Email account created successfully",
  "data": {
    "id": 1,
    "email": "john@acme.com",
    "local_part": "john",
    "name": "John Doe",
    "domain_id": 1,
    "organization_id": "acme",
    "status": "ACTIVE",
    "storage_quota": 2147483648,
    "storage_used": 0,
    "created_at": "2025-03-25T10:30:00"
  }
}
```

!!! warning "Validation"
    - The domain in the email address must exist and be active.
    - The domain must not have reached its `max_users` limit.
    - Passwords must be at least 8 characters with both letters and numbers.

## Update Email Account

Update an existing email account. Only the fields you include are changed.

```
PUT /api/v1/email-accounts/{account_id}
```

**Request Body**

All fields from [Create Email Account](#create-email-account) except `email` are accepted. Include only the fields you want to change.

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John A. Doe",
    "storage_quota": 5368709120,
    "forward_enabled": true,
    "forward_destination": "john.personal@gmail.com"
  }' \
  http://your-server:5000/api/v1/email-accounts/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Email account updated successfully",
  "data": {
    "id": 1,
    "email": "john@acme.com",
    "name": "John A. Doe",
    "storage_quota": 5368709120,
    "forward_enabled": true,
    "forward_destination": "john.personal@gmail.com",
    "updated_at": "2025-03-25T11:00:00"
  }
}
```

## Delete Email Account

Delete an email account and update domain counters.

```
DELETE /api/v1/email-accounts/{account_id}
```

**Example Request**

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/email-accounts/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Email account deleted successfully"
}
```

## Get Account Quotas

Get detailed quota and usage information for a specific email account.

```
GET /api/v1/email-accounts/{account_id}/quotas
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/email-accounts/1/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Account quota information retrieved successfully",
  "data": {
    "account_id": 1,
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
    "last_storage_calculation": "2025-03-25T08:00:00",
    "over_threshold": false
  }
}
```

## Update Account Quotas

Update storage quota and rate limits for an email account.

```
PUT /api/v1/email-accounts/{account_id}/quotas
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `storage_quota` | integer | Storage quota in bytes |
| `rate_limits` | object | Account-level rate limits |
| `storage_quotas` | object | Additional storage quota settings |

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "storage_quota": 5368709120,
    "rate_limits": {"outbound_hourly": 100}
  }' \
  http://your-server:5000/api/v1/email-accounts/1/quotas
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Account quotas updated successfully",
  "data": {
    "account_id": 1,
    "storage_quota": 5368709120,
    "rate_limits": {"outbound_hourly": 100},
    "storage_quotas": {}
  }
}
```

## Bulk Operations

### Add Mailbox (Legacy)

Create a mailbox using the legacy endpoint format.

```
POST /api/v1/add/mailbox
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `local_part` | string | Yes | Local part of the email (before the @) |
| `domain` | string | Yes | Domain name |
| `password` | string | Yes | Password |
| `name` | string | No | Display name |
| `quota` | integer | No | Quota in MB (default: 3072) |
| `active` | integer | No | `1` for active, `0` for inactive |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "jane",
    "domain": "acme.com",
    "password": "SecurePass456",
    "name": "Jane Smith",
    "quota": 5120
  }' \
  http://your-server:5000/api/v1/add/mailbox
```

### Edit Mailbox (Bulk)

Update multiple mailboxes at once.

```
POST /api/v1/edit/mailbox
```

**Request Body**

```json
{
  "items": ["john@acme.com", "jane@acme.com"],
  "attr": {
    "quota": 10240,
    "active": 1
  }
}
```

### Delete Mailboxes (Bulk)

Delete multiple mailboxes at once.

```
POST /api/v1/delete/mailbox
```

**Request Body**

```json
["john@acme.com", "jane@acme.com"]
```

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
      "stats": {
        "aliases_deleted": 2,
        "messages_deleted": 150
      }
    }
  ]
}
```
