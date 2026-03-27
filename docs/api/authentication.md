# Authentication

Every API request must be authenticated using either an API key or an admin password.

## API Key Authentication

Pass your API key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key-here" \
  http://your-server:5000/api/v1/organizations
```

API keys are stored in the `api_keys` table and have these properties:

| Field | Description |
|---|---|
| `api_key` | The key string you pass in the header |
| `permissions` | `read`, `write`, or `admin` |
| `read_only` | If `true`, the key can only access GET endpoints |
| `admin_access` | If `true`, the key can access admin-only endpoints |
| `organization_id` | Limits the key to a specific organization (multi-tenant isolation) |
| `active` | Whether the key is enabled |
| `expires_at` | Optional expiration timestamp |

### Permission Levels

| Level | Can do |
|---|---|
| `read` | GET requests only |
| `write` | GET, POST, PUT, DELETE requests |
| `admin` | Everything, including admin-only operations |

When a key with `read_only = true` tries to call a write endpoint, the API returns:

```json
{
  "type": "error",
  "msg": "Write access required"
}
```

**HTTP status:** `403 Forbidden`

### Organization Scoping

If an API key has an `organization_id`, it can only access resources belonging to that organization. This is how multi-tenant isolation works. Keys without an organization scope can access all organizations.

### Creating an API Key

API keys are created directly in the database. There is no self-service endpoint for key creation.

```sql
INSERT INTO api_keys (api_key, description, permissions, read_only, admin_access, active)
VALUES ('mk_live_abc123...', 'Production read-write key', 'write', FALSE, FALSE, TRUE);
```

!!! tip "Key format"
    We recommend prefixing keys with `mk_live_` for production and `mk_test_` for testing so you can tell them apart at a glance.

### Key Rotation

To rotate a key:

1. Create a new key in the database.
2. Update your applications to use the new key.
3. Set `active = 0` on the old key.

The `last_used` column is updated on every request, so you can check whether the old key is still in use before disabling it.

## Admin Password Authentication

Some endpoints (service restarts, auto-healing) require admin privileges. Pass the admin password in the `X-Admin-Password` header (or `X-Admin-Token` -- both are accepted):

```bash
curl -X POST \
  -H "X-Admin-Password: your-admin-password" \
  http://your-server:5000/api/v1/monitoring/services/postfix/restart
```

The admin password is set through the `ADMIN_PASSWORD` or `ADMIN_TOKEN_SECRET` environment variable on the server.

## Authentication Errors

| Status | Response | Meaning |
|---|---|---|
| `401` | `"API key required"` | No `X-API-Key` header was sent |
| `401` | `"Invalid API key"` | The key does not exist or is inactive |
| `401` | `"Admin authentication required"` | Admin endpoint called without admin credentials |
| `401` | `"Invalid admin token"` | Wrong admin password |
| `403` | `"Write access required"` | Read-only key used on a write endpoint |
| `403` | `"Admin access required"` | Non-admin key used on an admin endpoint |

## Example: Full Authentication Flow

=== "curl"

    ```bash
    # Read-only request
    curl -H "X-API-Key: mk_live_readonly_key" \
      http://your-server:5000/api/v1/organizations

    # Write request
    curl -X POST \
      -H "X-API-Key: mk_live_readwrite_key" \
      -H "Content-Type: application/json" \
      -d '{"id": "acme", "name": "Acme Corp"}' \
      http://your-server:5000/api/v1/organizations

    # Admin request
    curl -X POST \
      -H "X-Admin-Password: super-secret-password" \
      http://your-server:5000/api/v1/monitoring/services/postfix/restart
    ```

=== "Python"

    ```python
    import requests

    BASE = "http://your-server:5000/api/v1"
    HEADERS = {"X-API-Key": "mk_live_readwrite_key"}

    # List organizations
    resp = requests.get(f"{BASE}/organizations", headers=HEADERS)
    print(resp.json())
    ```

=== "JavaScript"

    ```javascript
    const BASE = "http://your-server:5000/api/v1";
    const headers = { "X-API-Key": "mk_live_readwrite_key" };

    const resp = await fetch(`${BASE}/organizations`, { headers });
    const data = await resp.json();
    console.log(data);
    ```
