# Errors

How the Mailyte API reports errors, what the status codes mean, and how to handle them.

## Error Response Format

Error responses follow one envelope:

```json
{
  "type": "error",
  "msg": "Human-readable error description",
  "correlation_id": "a1b2c3d4e5f6"
}
```

- Every response carries an `X-Correlation-Id` header, and every JSON error body
  (status ≥ 400) has the same `correlation_id` folded in. Quote it when reporting
  problems -- every server log line for the request carries it too.
- Errors that need a stable machine-readable identifier additionally carry an
  `error_code` field (see the [registry](#error_code-registry) below).
- Some errors include a `data` field with additional detail:

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

!!! note "Two validation shapes"
    Endpoints that validate request data in their handlers return the envelope
    above with `data.errors` and status `400`. Endpoints whose request bodies are
    Pydantic models (and bad query-parameter values on newer endpoints) return
    `422` -- Pydantic body failures use FastAPI's standard shape
    `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`, while query-value
    failures (unknown sort key, bad enum) use the normal envelope.

## HTTP Status Codes

| Code | Name | When it happens |
|---|---|---|
| `400` | Bad Request | Missing required fields, invalid data, handler-level validation failure |
| `401` | Unauthorized | No credential, invalid API key, expired/revoked session, bad login |
| `403` | Forbidden | Insufficient permission level, wrong scope, insufficient operator role, missing/invalid CSRF token, MFA not completed |
| `404` | Not Found | Resource does not exist -- or belongs to another organization (deliberately indistinguishable) |
| `409` | Conflict | Duplicate resource, bootstrap already done, quota override standing, idempotency replay in progress, unreplayable dead letter |
| `422` | Unprocessable Entity | Pydantic body validation, bad query parameter values, over-limit bulk requests, `Idempotency-Key` reuse with a different body |
| `429` | Too Many Requests | An authentication throttle tripped (login or invalid-API-key rate limiting) |
| `500` | Internal Server Error | Unexpected server failure (`{"type": "error", "msg": "Internal server error"}` from the global handler) |
| `503` | Service Unavailable | A downstream microservice (analytics, tracking, RAG, rate limiter, storage, TOTP) is not reachable |

`404` is always preferred over `403` for cross-tenant access: a `403` would confirm
the resource exists.

## error_code Registry

| `error_code` | Status | Where |
|---|---|---|
| `DOMAIN_ALREADY_CLAIMED` | `409` | Creating a domain that already exists (yours or another tenant's). If your own org owns it, `data.existing_id` is included |
| `MAILBOX_ALREADY_EXISTS` | `409` | Creating a mailbox address that already exists (`data.existing_id` included) |
| `IDEMPOTENCY_KEY_REUSED` | `422` | An `Idempotency-Key` was reused with a different request body |
| `IDEMPOTENCY_IN_PROGRESS` | `409` | The original request for this `Idempotency-Key` is still executing -- retry shortly |
| `CERT_RENEW_UNAVAILABLE` | -- | SSL certificate renewal is not available on this deployment |

New codes are additive; the absence of an `error_code` on older errors is expected.

## Common Error Messages

### Authentication and authorization (401/403)

See [Authentication -- Authentication Errors](authentication.md#authentication-errors)
for the full table. Highlights:

| Status | Message | Fix |
|---|---|---|
| `401` | `"API key required"` / `"Invalid API key"` | Send a valid `X-API-Key` header |
| `401` | `"Session expired or invalid"` | Log in again |
| `403` | `"Write access required"` | Use a key without `read_only` |
| `403` | `"Admin access required"` | Use a key with `admin_access` |
| `403` | `"Platform scope required"` | Endpoint is platform-only; a tenant credential can never reach it |
| `403` | `"Insufficient operator role"` | Log in as an operator at or above the route's role floor |
| `403` | `"CSRF token missing or invalid"` | Echo the CSRF cookie in `X-CSRF-Token` on cookie-authenticated writes |
| `429` | `"Too many failed attempts. Try again later."` | Wait for the throttle window to pass |

### Validation errors (400)

| Message | Cause |
|---|---|
| `"No data provided"` | Request body is empty or not valid JSON |
| `"Validation failed"` | One or more fields failed validation (check `data.errors`) |
| `"Organization ID is required"` / `"Organization name is required"` | Missing fields when creating an organization |
| `"Organization ID must contain only alphanumeric characters, hyphens, and underscores"` | Invalid characters in organization ID |
| `"Invalid domain name format"` | Domain name failed format validation |
| `"Invalid email format"` | Email address failed format validation |
| `"Password must be at least 12 characters long"` | Password too short |
| `"Password must contain letters"` / `"Password must contain numbers"` | Password composition |
| `"Password is too common; choose something less predictable"` | Password on the common-password blocklist |
| `"Max quota must be non-negative"` | Negative quota value |
| `"url is required"` / `"url must start with http:// or https://"` | Webhook endpoint URL problems |
| `"event_types must be a list or null"` | Wrong type for webhook `event_types` |
| `"Domain {name} not found or inactive"` | The domain for a new mailbox/alias does not exist, is inactive, or is not yours |

### Not found (404)

| Message | Cause |
|---|---|
| `"Organization not found"` | No such org, or it belongs to another tenant |
| `"Domain not found"` | No such domain, or it belongs to another tenant |
| `"Email account not found"` / `"Mailbox not found"` | No such account, or cross-tenant |
| `"Webhook endpoint not found"` | No such endpoint, or cross-tenant |
| `"Dead letter not found"` | No such dead-letter row |

### Conflicts (409)

| Message | Cause |
|---|---|
| `"Organization with this ID already exists"` | Duplicate organization ID |
| `"Organization with this external ID already exists"` | Duplicate org `external_id` |
| `"Domain {name} already exists"` | Domain already claimed (`error_code: DOMAIN_ALREADY_CLAIMED`) |
| `"Domain with this external_id already exists"` | Duplicate domain `external_id` |
| `"Email account {email} already exists"` | Duplicate address |
| `"Mailbox {email} already exists"` | Duplicate address, legacy endpoint (`error_code: MAILBOX_ALREADY_EXISTS`) |
| `"Alias {address} already exists"` | Duplicate alias source address |
| `"Already bootstrapped -- an organization already exists"` | `POST /api/v1/bootstrap/` on a provisioned install |
| `"Quota override in place: set by {who} at {when}..."` | Automated quota write while an operator override stands (pass `?force=true` or clear the override) |

### Dependency errors (400)

| Message | Cause |
|---|---|
| `"Cannot delete organization with {n} domains and {n} email accounts. Delete them first."` | Organization has child resources |
| `"Cannot delete domain with {n} email accounts. Delete them first."` | Domain has child resources |
| `"Domain has reached maximum user limit ({n})"` | `max_users` quota exceeded |

### Service unavailable (503)

| Message | Cause |
|---|---|
| `{"error": "Analytics service unavailable"}` | Analytics worker unreachable |
| `{"error": "Rate limiter service unavailable"}` | Rate limiter worker unreachable |
| `{"error": "Storage service unavailable"}` | Storage worker unreachable |
| `{"error": "RAG service unavailable"}` | RAG worker unreachable |
| `"MFA service unavailable"` | TOTP service unreachable during operator login |

Note that proxied 503s use the downstream `{"error": ...}` shape rather than the
`type`/`msg` envelope.

## Error Handling Best Practices

1. **Always check the `type` field.** If it is `"error"`, something went wrong.

2. **Check `data.errors` for validation failures.** When the message is `"Validation failed"`, the `data.errors` array tells you exactly which fields are wrong.

3. **Branch on `error_code` where present.** It is stable across releases; `msg` wording is not.

4. **Retry on 503.** Downstream services may be temporarily unavailable. Wait a few seconds and retry -- with an `Idempotency-Key` on mutating requests so retries are safe.

5. **Do not retry on 4xx** (except `409` `IDEMPOTENCY_IN_PROGRESS`, which asks you to retry shortly). Client errors will not succeed without changing the request.

6. **Log the `correlation_id`.** It links your request to every server-side log line about it.

## Example: Handling Errors in Code

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "http://your-server:8083/api/v1/domains/",
        headers={"X-API-Key": "YOUR_KEY"},
        json={"domain": "acme.com"},
    )

    body = resp.json()

    if body.get("type") == "error":
        code = body.get("error_code")
        if code == "DOMAIN_ALREADY_CLAIMED":
            print("Domain already exists:", body.get("data", {}).get("existing_id"))
        elif resp.status_code == 400 and "errors" in body.get("data", {}):
            for err in body["data"]["errors"]:
                print(f"Validation error: {err}")
        elif resp.status_code == 401:
            print("Check your API key")
        else:
            print(f"Error [{body.get('correlation_id')}]: {body['msg']}")
    else:
        print(f"Success: {body['msg']}")
    ```

=== "JavaScript"

    ```javascript
    const resp = await fetch("http://your-server:8083/api/v1/domains/", {
      method: "POST",
      headers: {
        "X-API-Key": "YOUR_KEY",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ domain: "acme.com" })
    });

    const body = await resp.json();

    if (body.type === "error") {
      if (body.error_code === "DOMAIN_ALREADY_CLAIMED") {
        console.error("Domain already exists:", body.data?.existing_id);
      } else if (resp.status === 400 && body.data?.errors) {
        body.data.errors.forEach(err => console.error("Validation:", err));
      } else {
        console.error(`Error [${body.correlation_id}]:`, body.msg);
      }
    } else {
      console.log("Success:", body.msg);
    }
    ```
