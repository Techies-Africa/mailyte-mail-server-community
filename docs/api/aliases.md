# Aliases

Manage email aliases -- forwarding addresses that route mail to one or more destinations.

## Create Alias

Create a new email alias.

```
POST /api/v1/add/alias
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | Yes | The alias email address (e.g., `info@acme.com`) |
| `goto` | string | Yes | Comma-separated destination addresses |
| `active` | integer | No | `1` for active, `0` for inactive (default: `1`) |

The domain in the `address` field must already exist and be active.

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "info@acme.com",
    "goto": "john@acme.com, jane@acme.com",
    "active": 1
  }' \
  http://your-server:5000/api/v1/add/alias
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Alias info@acme.com created successfully",
  "data": {
    "alias_id": 1,
    "address": "info@acme.com"
  }
}
```

### Catch-All Aliases

To create a catch-all alias that receives mail for any address at a domain, use `@domain.com` as the address:

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "@acme.com",
    "goto": "catchall@acme.com"
  }' \
  http://your-server:5000/api/v1/add/alias
```

!!! info "How catch-all works"
    Any email sent to a non-existent address at the domain is delivered to the catch-all destination. For example, if `random123@acme.com` does not exist, the message goes to `catchall@acme.com`.

## List Aliases

Retrieve all aliases, or look up a specific alias by ID or address.

```
GET /api/v1/get/alias/{alias_id}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `alias_id` | string | Alias numeric ID, email address, or `all` to list everything |

**Query Parameters** (when `alias_id` is `all`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request -- List All**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/get/alias/all?page=1&per_page=25"
```

**Example Request -- Get by Address**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/get/alias/info@acme.com
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Aliases retrieved successfully",
  "data": [
    {
      "id": 1,
      "address": "info@acme.com",
      "goto": "john@acme.com, jane@acme.com",
      "domain": "acme.com",
      "active": 1,
      "destination_count": 2,
      "destinations": ["john@acme.com", "jane@acme.com"],
      "monthly_forwards": 142,
      "created": "2025-01-20T09:00:00",
      "modified": "2025-03-20T14:22:00"
    }
  ]
}
```

## Update Alias

Update one or more aliases. You can change the destination addresses or toggle active status.

```
POST /api/v1/edit/alias
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | array | Yes | List of alias IDs or addresses to update |
| `attr` | object | Yes | Fields to update |

**Allowed `attr` Fields**

| Field | Type | Description |
|---|---|---|
| `goto` | string | New comma-separated destination addresses |
| `active` | integer | `1` or `0` |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": ["info@acme.com"],
    "attr": {
      "goto": "john@acme.com, jane@acme.com, support@acme.com",
      "active": 1
    }
  }' \
  http://your-server:5000/api/v1/edit/alias
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

Delete one or more aliases.

```
POST /api/v1/delete/alias
```

**Request Body**

An array of alias IDs or addresses:

```json
["info@acme.com", "sales@acme.com"]
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '["info@acme.com", "sales@acme.com"]' \
  http://your-server:5000/api/v1/delete/alias
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Alias deletion completed",
  "data": [
    {
      "alias": "info@acme.com",
      "address": "info@acme.com",
      "status": "success",
      "msg": "Alias info@acme.com deleted successfully"
    },
    {
      "alias": "sales@acme.com",
      "address": "sales@acme.com",
      "status": "success",
      "msg": "Alias sales@acme.com deleted successfully"
    }
  ]
}
```

## Get Alias Statistics

Get alias statistics for a specific domain, including forwarding volume.

```
GET /api/v1/get/alias/stats/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/get/alias/stats/acme.com
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
        "goto": "john@acme.com",
        "usage_count": 5
      }
    ],
    "monthly_volume": [
      {
        "date": "2025-03-25",
        "forwards_count": 42
      }
    ]
  }
}
```

## Bulk Create Aliases

Create multiple aliases in a single request.

```
POST /api/v1/add/alias/bulk
```

**Request Body**

```json
{
  "aliases": [
    {
      "address": "sales@acme.com",
      "goto": "john@acme.com",
      "active": 1
    },
    {
      "address": "support@acme.com",
      "goto": "jane@acme.com, helpdesk@acme.com",
      "active": 1
    }
  ]
}
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "aliases": [
      {"address": "sales@acme.com", "goto": "john@acme.com"},
      {"address": "support@acme.com", "goto": "jane@acme.com, helpdesk@acme.com"}
    ]
  }' \
  http://your-server:5000/api/v1/add/alias/bulk
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
      {
        "address": "sales@acme.com",
        "status": "success",
        "msg": "Alias created successfully"
      },
      {
        "address": "support@acme.com",
        "status": "success",
        "msg": "Alias created successfully"
      }
    ]
  }
}
```
