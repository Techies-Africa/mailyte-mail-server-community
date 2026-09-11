# Filters (Sieve)

Manage Dovecot Sieve filter scripts per user: list, create, activate, and delete scripts, plus a vacation-responder helper and a library of pre-built templates.

**Base path:** `/api/v1/filters`
**Auth:** `X-API-Key` (read for GETs, write for mutations) or a dashboard session. Most endpoints take the target user via an `email` query parameter; the caller must be entitled to that account.

## List Sieve Scripts

All Sieve scripts for a user, including each script's content, size, creation date, and whether it is the currently active script.

```
GET /api/v1/filters
```

**Query Parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | User email address |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/filters?email=john@acme.com"
```

**Example Response**

```json
[
  {
    "name": "move-newsletters",
    "content": "require \"fileinto\"; if header :contains \"List-Unsubscribe\" \"\" { fileinto \"Newsletters\"; }",
    "active": true,
    "size": 96,
    "created_at": "2026-07-14T08:00:00"
  }
]
```

## List Filter Templates

Pre-built Sieve templates (forward, auto-reply, move by subject/sender, block sender, attachment filter) that users can customize. No `email` parameter — the templates are static.

```
GET /api/v1/filters/templates
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/filters/templates
```

**Example Response** (per template)

```json
{
  "id": "block-sender",
  "name": "Block a sender",
  "description": "Discard all mail from a given address",
  "sieve_content": "require \"reject\"; if address :is \"from\" \"spammer@example.com\" { discard; }"
}
```

## Get Sieve Script

```
GET /api/v1/filters/{name}?email={email}
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/filters/move-newsletters?email=john@acme.com"
```

Returns one script object (same shape as the list).

## Create or Update Sieve Script

Create or overwrite a Sieve script. Script names must be alphanumeric (hyphens/underscores allowed). If `active` is `true` the script is immediately set as the active Dovecot Sieve script.

```
POST /api/v1/filters?email={email}
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Unique script name |
| `content` | string | Yes | Sieve script content |
| `active` | boolean | No | Activate immediately (default `false`) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "move-newsletters",
    "content": "require \"fileinto\"; if header :contains \"List-Unsubscribe\" \"\" { fileinto \"Newsletters\"; }",
    "active": true
  }' \
  "http://your-server:5000/api/v1/filters?email=john@acme.com"
```

## Delete Sieve Script

Delete a script by name. If it was the active script, the active symlink is removed too; the compiled `.sievec` file is cleaned up as well.

```
DELETE /api/v1/filters/{name}?email={email}
```

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/filters/move-newsletters?email=john@acme.com"
```

## Activate Sieve Script

Set the named script as the user's active script (updates the Dovecot `.dovecot.sieve` symlink).

```
PUT /api/v1/filters/{name}/activate?email={email}
```

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/filters/move-newsletters/activate?email=john@acme.com"
```

## Vacation Responder

Enable or disable the vacation auto-reply. When enabled, a Sieve vacation script is generated with optional start/end dates and set as the active script; the account's `vacation_enabled` flag is kept in sync.

```
POST /api/v1/filters/vacation?email={email}
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | Yes | Enable or disable the responder |
| `subject` | string | No | Auto-reply subject (default `Out of Office`) |
| `message` | string | No | Auto-reply body |
| `start_date` | string | No | ISO 8601 date (e.g. `2026-04-01`) |
| `end_date` | string | No | ISO 8601 date |
| `reply_interval` | integer | No | Minimum seconds between replies to the same sender (default `86400`) |
| `external_only` | boolean | No | Only reply to external senders (default `false`) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "subject": "Out of office",
    "message": "I am away until 15 April. For urgent matters contact support@acme.com.",
    "start_date": "2026-04-01",
    "end_date": "2026-04-15"
  }' \
  "http://your-server:5000/api/v1/filters/vacation?email=john@acme.com"
```
