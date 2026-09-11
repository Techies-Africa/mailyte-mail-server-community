# Aliases

Manage email aliases -- forwarding addresses that route mail to one or more
destinations.

All alias routes live under the `/api/v1/aliases` prefix. The request fields are
`source` (the alias address) and `destination` (where mail goes) -- both must be
full, valid email addresses. Tenant credentials can only manage aliases on domains
their organization owns.

## Create Alias

Create a new email alias.

```
POST /api/v1/aliases/add
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `source` | string | Yes | The alias email address (e.g., `info@acme.com`). Must be a full, valid address on an existing, active domain in your organization |
| `destination` | string | Yes | Comma-separated destination addresses; every entry must be a valid email address |
| `active` | integer | No | `1` for active (default), `0` for inactive |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "info@acme.com",
    "destination": "john@acme.com, jane@acme.com",
    "active": 1
  }' \
  http://your-server:8083/api/v1/aliases/add
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Alias info@acme.com created successfully",
  "data": {
    "alias_id": "01J6EXAMPLEULID0000000000A",
    "source": "info@acme.com"
  }
}
```

`alias_id` is the created row's ULID. (Before 2026-08-30 it always read `0` --
the handler overwrote the ULID with `cursor.lastrowid`, which only tracks
AUTO_INCREMENT columns; responses from older deployments may still show `0`.)

Alias source addresses are globally unique; a duplicate returns `409`. A domain that
does not exist, is inactive, or belongs to another organization returns `400`
(`"Domain {domain} not found or inactive"`).

!!! note "No catch-all syntax"
    The `source` field must be a complete email address -- a bare `@domain.com`
    catch-all is rejected by validation. Catch-all delivery is configured at the
    domain level, not through this endpoint.

## List Aliases

Retrieve aliases by ID, by source address, or list everything.

```
GET /api/v1/aliases/get/{alias_id}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `alias_id` | string | Alias ULID, alias source address, or `all` to list every **active** alias in your scope |

**Query Parameters** (when `alias_id` is `all`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request -- List All**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:8083/api/v1/aliases/get/all?page=1&per_page=25"
```

**Example Request -- Get by Address**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:8083/api/v1/aliases/get/info@acme.com
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Aliases retrieved successfully",
  "data": [
    {
      "id": "01J1ALS0000000000000000000",
      "source": "info@acme.com",
      "destination": "john@acme.com, jane@acme.com",
      "domain_id": "01J1DOM0000000000000000000",
      "domain_description": "Primary domain",
      "organization_id": "01J1ABCDEF2345GHJKMNPQRSTV",
      "active": 1,
      "destination_count": 2,
      "destinations": ["john@acme.com", "jane@acme.com"],
      "monthly_forwards": null,
      "created_at": "2026-01-20T09:00:00",
      "updated_at": "2026-03-20T14:22:00"
    }
  ]
}
```

!!! note "monthly_forwards is best-effort"
    Per-alias forwarding counts depend on a `message_forwards` table that no
    migration currently creates, so `monthly_forwards` is `null` on standard
    deployments rather than a real count.

## Update Aliases

Batch-update one or more aliases -- change destinations or toggle active status.

```
POST /api/v1/aliases/edit
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | array | Yes | List of alias ULIDs or source addresses to update |
| `attr` | object | Yes | Fields to update |

**Allowed `attr` Fields**

| Field | Type | Description |
|---|---|---|
| `destination` | string | New comma-separated destination addresses (each validated) |
| `active` | integer | `1` or `0` |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": ["info@acme.com"],
    "attr": {
      "destination": "john@acme.com, jane@acme.com, support@acme.com",
      "active": 1
    }
  }' \
  http://your-server:8083/api/v1/aliases/edit
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Alias update completed",
  "data": [
    {
      "alias": "info@acme.com",
      "status": "success",
      "msg": "Alias info@acme.com updated successfully"
    }
  ]
}
```

## Delete Aliases

Delete one or more aliases. Deleted aliases stop forwarding immediately.

```
POST /api/v1/aliases/delete
```

**Request Body**

An array of alias ULIDs or source addresses:

```json
["info@acme.com", "sales@acme.com"]
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Alias deletion completed",
  "data": [
    {
      "alias": "info@acme.com",
      "source": "info@acme.com",
      "status": "success",
      "msg": "Alias info@acme.com deleted successfully"
    },
    {
      "alias": "sales@acme.com",
      "source": "sales@acme.com",
      "status": "success",
      "msg": "Alias sales@acme.com deleted successfully"
    }
  ]
}
```

## Get Alias Statistics

Get alias statistics for a specific domain.

```
GET /api/v1/aliases/get/stats/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:8083/api/v1/aliases/get/stats/acme.com
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Alias statistics for acme.com retrieved successfully",
  "data": {
    "basic_stats": {
      "total_aliases": 15,
      "active_aliases": 12,
      "inactive_aliases": 3
    },
    "top_destinations": [
      {
        "destination": "john@acme.com",
        "usage_count": 5
      }
    ],
    "monthly_volume": []
  }
}
```

`monthly_volume` depends on the same missing `message_forwards` table as
`monthly_forwards` above and is an empty array on standard deployments.

## Bulk Create Aliases

Create multiple aliases in a single request. Each alias is validated independently
-- invalid entries are skipped and reported while valid ones are created.

```
POST /api/v1/aliases/add/bulk
```

**Request Body**

```json
{
  "aliases": [
    {"source": "sales@acme.com", "destination": "john@acme.com", "active": 1},
    {"source": "support@acme.com", "destination": "jane@acme.com, helpdesk@acme.com"}
  ]
}
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Bulk alias creation completed",
  "data": {
    "summary": {
      "total": 2,
      "success": 2,
      "errors": 0
    },
    "results": [
      {"source": "sales@acme.com", "status": "success", "msg": "Alias created successfully"},
      {"source": "support@acme.com", "status": "success", "msg": "Alias created successfully"}
    ]
  }
}
```
