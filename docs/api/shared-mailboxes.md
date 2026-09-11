# Shared Mailboxes

Shared mailboxes let multiple users read and respond from a common address like `support@company.com`. Members are granted per-mailbox permission levels.

**Base path:** `/api/v1/shared-mailboxes`
**Auth:** `X-API-Key` (read for GETs, write for mutations) or a dashboard session. Tenant credentials operate only within their own organization; platform scope may read any organization's shared mailboxes.

**Permission levels:** `full_access`, `send_as`, `send_on_behalf`, `read_only`.

## List Shared Mailboxes

All shared mailboxes in the caller's organization.

```
GET /api/v1/shared-mailboxes
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/shared-mailboxes
```

**Example Response**

```json
[
  {
    "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
    "email": "support@acme.com",
    "name": "Customer Support",
    "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
    "status": "active",
    "member_count": 4,
    "created_at": "2026-05-12T09:00:00"
  }
]
```

## Create Shared Mailbox

Create a new shared mailbox and optionally add initial members. The mailbox email must belong to a verified domain in the organization.

```
POST /api/v1/shared-mailboxes
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Shared mailbox address (e.g. `support@acme.com`) |
| `name` | string | Yes | Display name (e.g. `Customer Support`) |
| `organization_id` | string | No | **Deprecated, ignored.** The organization is always derived from the caller's API key, never from client input |
| `auto_reply_enabled` | boolean | No | Default `false` |
| `auto_reply_message` | string | No | Auto-reply text |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "support@acme.com",
    "name": "Customer Support",
    "auto_reply_enabled": false
  }' \
  http://your-server:5000/api/v1/shared-mailboxes
```

## Get Shared Mailbox

Details including configuration and the full member list with permission levels.

```
GET /api/v1/shared-mailboxes/{mailbox_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/shared-mailboxes/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

**Example Response**

```json
{
  "mailbox": {
    "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
    "email": "support@acme.com",
    "name": "Customer Support",
    "organization_id": "01J8ZJ...",
    "mailbox_type": "shared",
    "status": "active"
  },
  "members": [
    {
      "email_account_id": "01J8ZM...",
      "member_email": "jane@acme.com",
      "member_name": "Jane Doe",
      "permission": "full_access"
    }
  ]
}
```

`404` if the mailbox does not exist or belongs to another organization (organization-scoped callers).

## Delete Shared Mailbox

Soft-delete: sets the mailbox status to `inactive`. All member associations are removed and the email address becomes available for reuse.

```
DELETE /api/v1/shared-mailboxes/{mailbox_id}
```

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/shared-mailboxes/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

## Add Member

Add a member with a permission level. If the member already exists, their permission is updated.

```
POST /api/v1/shared-mailboxes/{mailbox_id}/members
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email_account_id` | string | Yes | ULID of the email account to grant access |
| `permission` | string | No | `full_access` (default), `send_as`, `send_on_behalf`, or `read_only` |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email_account_id": "01J8ZM...", "permission": "send_as"}' \
  http://your-server:5000/api/v1/shared-mailboxes/01J9AB.../members
```

## Remove Member

Revoke all of a member's access. The member's own mailbox is not affected.

```
DELETE /api/v1/shared-mailboxes/{mailbox_id}/members/{member_account_id}
```

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/shared-mailboxes/01J9AB.../members/01J8ZM...
```

## Update Member Permission

```
PUT /api/v1/shared-mailboxes/{mailbox_id}/members/{member_account_id}
```

**Request Body:** same shape as [Add Member](#add-member).

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email_account_id": "01J8ZM...", "permission": "read_only"}' \
  http://your-server:5000/api/v1/shared-mailboxes/01J9AB.../members/01J8ZM...
```
