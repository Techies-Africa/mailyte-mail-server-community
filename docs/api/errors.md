# Errors

How the Mailyte API reports errors, what the status codes mean, and how to handle them.

## Error Response Format

All error responses follow the same structure:

```json
{
  "type": "error",
  "msg": "Human-readable error description"
}
```

Some errors include a `data` field with additional detail:

```json
{
  "type": "error",
  "msg": "Validation failed",
  "data": {
    "errors": [
      "Organization name is required",
      "Invalid admin email format"
    ]
  }
}
```

## HTTP Status Codes

| Code | Name | When it happens |
|---|---|---|
| `400` | Bad Request | Missing required fields, invalid data, validation failure |
| `401` | Unauthorized | No API key provided, invalid key, expired key |
| `403` | Forbidden | Key lacks the required permission level |
| `404` | Not Found | Resource does not exist |
| `409` | Conflict | Trying to create a resource that already exists |
| `429` | Too Many Requests | API rate limit exceeded |
| `500` | Internal Server Error | Unexpected server failure |
| `503` | Service Unavailable | A downstream service (analytics, tracking, RAG) is not reachable |

## Common Error Messages

### Authentication Errors (401)

| Message | Cause | Fix |
|---|---|---|
| `"API key required"` | No `X-API-Key` header | Add the header to your request |
| `"Invalid API key"` | Key does not exist, is inactive, or is expired | Check the key value; verify it is active in the database |
| `"Authentication failed"` | Internal error during key validation | Check server logs; likely a database issue |
| `"Admin authentication required"` | Admin endpoint called without admin credentials | Use the `X-Admin-Password` header |
| `"Invalid admin token"` | Wrong admin password | Check the `ADMIN_PASSWORD` environment variable |

### Authorization Errors (403)

| Message | Cause | Fix |
|---|---|---|
| `"Write access required"` | Read-only key used on a POST/PUT/DELETE endpoint | Use a key with write permissions |
| `"Admin access required"` | Non-admin key used on an admin endpoint | Use a key with `admin_access = true` |

### Validation Errors (400)

| Message | Cause |
|---|---|
| `"No data provided"` | Request body is empty or not valid JSON |
| `"Validation failed"` | One or more fields failed validation (check `data.errors` for details) |
| `"Organization ID is required"` | Missing `id` field when creating an organization |
| `"Organization name is required"` | Missing `name` field when creating an organization |
| `"Organization ID must contain only alphanumeric characters, hyphens, and underscores"` | Invalid characters in organization ID |
| `"Invalid domain name format"` | Domain name failed format validation |
| `"Organization ID is required"` | Missing `organization_id` when creating a domain |
| `"Invalid email format"` | Email address failed format validation |
| `"Password must be at least 8 characters long"` | Password too short |
| `"Password must contain letters"` | Password needs alphabetic characters |
| `"Password must contain numbers"` | Password needs numeric characters |
| `"Max quota must be non-negative"` | Negative value for quota |
| `"url is required"` | Missing URL when creating a webhook |
| `"url must start with http:// or https://"` | Invalid webhook URL scheme |
| `"event_types must be a list or null"` | Wrong type for webhook event_types |

### Not Found Errors (404)

| Message | Cause |
|---|---|
| `"Organization not found"` | No organization with that ID or external_id |
| `"Domain not found"` | No domain with that ID or name |
| `"Email account not found"` | No email account with that ID |
| `"Webhook endpoint not found"` | No webhook with that ID, or it belongs to a different organization |
| `"Mailbox not found"` | No mailbox with that email address |
| `"Domain {name} not found"` | The domain in the email address does not exist |

### Conflict Errors (409)

| Message | Cause |
|---|---|
| `"Organization with this ID already exists"` | Duplicate organization ID |
| `"Organization with this external ID already exists"` | Duplicate external_id |
| `"Domain {name} already exists"` | Domain name already registered (case-insensitive) |
| `"Domain with this external_id already exists"` | Duplicate external_id on domain |
| `"Email account {email} already exists"` | Email address already in use |
| `"Alias {address} already exists"` | Alias address already registered |
| `"Mailbox {email} already exists"` | Legacy endpoint - mailbox already exists |

### Dependency Errors (400)

| Message | Cause |
|---|---|
| `"Cannot delete organization with {n} domains and {n} email accounts. Delete them first."` | Organization has child resources |
| `"Cannot delete domain with {n} email accounts. Delete them first."` | Domain has child resources |
| `"Domain has reached maximum user limit ({n})"` | Max users quota exceeded |

### Service Unavailable (503)

| Message | Cause |
|---|---|
| `"Analytics service unavailable"` | Analytics worker is not running |
| `"Tracking service unavailable"` | Tracking worker is not running |
| `"RAG service unavailable"` | RAG worker is not running |
| `"Rate limiter service unavailable"` | Rate limiter worker is not running |
| `"Storage service unavailable"` | Storage worker is not running |

## Error Handling Best Practices

1. **Always check the `type` field.** If it is `"error"`, something went wrong.

2. **Check `data.errors` for validation failures.** When the message is `"Validation failed"`, the `data.errors` array tells you exactly which fields are wrong.

3. **Retry on 503.** Downstream services may be temporarily unavailable. Wait a few seconds and retry.

4. **Do not retry on 4xx.** Client errors (400, 401, 403, 404, 409) will not succeed on retry without changing the request.

5. **Log the full response.** When debugging, log the entire JSON body, not just the status code.

## Example: Handling Errors in Code

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "http://your-server:5000/api/v1/organizations",
        headers={"X-API-Key": "YOUR_KEY"},
        json={"id": "acme", "name": "Acme Corp"}
    )

    body = resp.json()

    if body["type"] == "error":
        if resp.status_code == 400 and "errors" in body.get("data", {}):
            for err in body["data"]["errors"]:
                print(f"Validation error: {err}")
        elif resp.status_code == 409:
            print("Resource already exists")
        elif resp.status_code == 401:
            print("Check your API key")
        else:
            print(f"Error: {body['msg']}")
    else:
        print(f"Success: {body['msg']}")
    ```

=== "JavaScript"

    ```javascript
    const resp = await fetch("http://your-server:5000/api/v1/organizations", {
      method: "POST",
      headers: {
        "X-API-Key": "YOUR_KEY",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ id: "acme", name: "Acme Corp" })
    });

    const body = await resp.json();

    if (body.type === "error") {
      if (resp.status === 400 && body.data?.errors) {
        body.data.errors.forEach(err => console.error("Validation:", err));
      } else if (resp.status === 409) {
        console.error("Resource already exists");
      } else {
        console.error("Error:", body.msg);
      }
    } else {
      console.log("Success:", body.msg);
    }
    ```
