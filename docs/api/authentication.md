# Authentication

The API accepts four kinds of credentials. Every route resolves them through the same
auth layer (`worker/api/utils/auth.py`), so a route only cares about the *scope* and
*permission* of the caller, not which credential type produced it.

| Credential | How it is sent | Who uses it | Scope |
|---|---|---|---|
| API key | `X-API-Key` header | Server-side integrations, the hosted control plane | `organization` (or `platform` for platform-issued keys) |
| Dashboard session | `mailyte_session` cookie (+ CSRF header on writes) | Browser users of the tenant dashboard | `organization` |
| Operator session | `mailyte_operator_session` cookie (+ CSRF header on writes) | Platform staff (console), MFA-gated | `platform` |
| Mailbox session | `mailyte_mailbox_session` cookie or `Authorization: Bearer` token | Webmail users (mailbox holders) | Single mailbox |

## API Key Authentication

Pass your API key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key-here" \
  http://your-server:8083/api/v1/domains/
```

Keys live in the `api_keys` table. The value you send is hashed (SHA-256) and
matched against the `key_hash` column of an `active` row — the raw key is
never stored (since 2026-08-30; migration `0019`). Expired keys
(`expires_at` in the past) are rejected with `401 API key expired`.

| Field | Description |
|---|---|
| `key_id` | Non-secret display identifier (key prefix + row ULID) |
| `key_hash` | SHA-256 of the key you pass in the `X-API-Key` header |
| `name` | Human-readable label |
| `permissions` | JSON object -- see permission checks below |
| `organization_id` | The organization the key is bound to (multi-tenant isolation) |
| `scope` | `organization` (default) or `platform` |
| `active` | Whether the key is enabled (`active = 0` disables it immediately) |
| `last_used` | Updated on every authenticated request |

### Permission checks

Each route declares a required permission level. The check reads the key's
`permissions` JSON:

| Level | Check |
|---|---|
| `read` | Any active key passes |
| `write` | Denied with `403` if `permissions.read_only` is true |
| `admin` | Requires `permissions.admin_access` to be true |

A read-only key calling a write endpoint gets:

```json
{
  "type": "error",
  "msg": "Write access required"
}
```

**HTTP status:** `403 Forbidden`

### Organization scoping

A key with `scope = "organization"` only ever sees and mutates resources belonging
to its own `organization_id`. Where an endpoint accepts an `organization_id`
parameter, the value is **silently ignored** for organization-scoped callers -- it is
never honoured, so passing another tenant's ID cannot work. Cross-tenant access
attempts return `404`, never `403`, so the existence of other tenants' resources is
not leaked.

A key with `scope = "platform"` sees every organization ("sudo sees all") and may use
`organization_id` parameters to narrow queries. Note that some platform routes carry
an additional **role** requirement (see operator sessions below); a bare
platform-scope API key has no role and cannot reach role-gated routes at all.

### Failed-key throttling

Invalid API keys are rate-limited per client IP: 20 invalid keys within 5 minutes
triggers a lockout, with exponential backoff starting from the 5th failure. A
locked-out client receives:

```json
{
  "type": "error",
  "msg": "Too many failed attempts. Try again later."
}
```

**HTTP status:** `429 Too Many Requests`

### Getting your first API key

There is no self-service key-creation endpoint in this API. Keys are created by:

1. **`POST /api/v1/bootstrap/`** on a fresh install -- creates the first
   organization, domain, mailbox, dashboard user, and API key in one call. It is
   guarded by the single-use `X-Bootstrap-Token` header; the API writes the token to
   `/app/data/bootstrap-token` at startup when no API keys exist yet, and deletes it
   once bootstrap completes.
2. The hosted control plane (mailyte-api), which provisions keys for hosted tenants.

**Bootstrap request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `organization_name` | string | Yes | Display name for the first organization |
| `admin_email` | string | Yes | Admin mailbox to create; its domain part becomes the first domain |
| `admin_password` | string | Yes | Password for both the admin mailbox and the dashboard login (12+ characters, letters and numbers) |

```bash
curl -X POST \
  -H "X-Bootstrap-Token: $(cat /app/data/bootstrap-token)" \
  -H "Content-Type: application/json" \
  -d '{"organization_name": "Acme", "admin_email": "admin@acme.com", "admin_password": "correct-horse-42"}' \
  http://your-server:8083/api/v1/bootstrap/
```

The response `data` includes the new `organization_id`, `domain`, `mailbox`, and the
plaintext `api_key` -- the only time the key is ever returned. Bootstrap refuses with
`409` once any organization exists, regardless of token validity.

!!! warning "Key expiry is not enforced"
    The `api_keys` table has an `expires_at` column, but as of 2026-08-30 the
    authentication path does not check it -- only `active = 1` is enforced. To revoke
    a key, set `active = 0`; do not rely on `expires_at`.

## Dashboard Sessions (browser login)

Community Edition dashboards talk to this API directly from the browser, so instead
of holding a long-lived API key client-side, users log in for a short-lived HttpOnly
session cookie.

### `POST /api/v1/auth/login`

Unauthenticated by design. Body: `{"email": "...", "password": "..."}`. Rate-limited
by IP **and** by email: 5 failures in 15 minutes returns `429`, with exponential
backoff starting from the 3rd failure. Failure responses are identical (message and
timing) whether or not the email exists.

On success the response sets two cookies and returns the user, organization, and
capability manifest:

| Cookie | Flags | Purpose |
|---|---|---|
| `mailyte_session` | HttpOnly, SameSite=Lax | The session credential (only a SHA-256 hash is stored server-side) |
| `mailyte_csrf` | SameSite=Lax (readable by JS) | Double-submit CSRF token |

Session lifetimes: **8 hours idle** (sliding -- extended on every authenticated
request) with a **30-day absolute cap**. Cookies get the `Secure` flag unless
`MAILYTE_COOKIE_SECURE=false` is set (local HTTP testing only).

### CSRF on state-changing requests

Cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` requests must echo the
`mailyte_csrf` cookie value in the `X-CSRF-Token` header, or the request is rejected
with `403` (`"CSRF token missing or invalid"`). API-key requests never need CSRF --
they carry no ambient browser credential. GETs are exempt.

### Other session endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/auth/me` | GET | Current user, organization, capabilities, and `auth_method` (`session` or `api_key`) |
| `/api/v1/auth/logout` | POST | Revokes the session and clears cookies (no-op success for API-key callers) |
| `/api/v1/auth/password` | POST | Change own password. Body: `{"current_password", "new_password"}`. Session-only (`400` for API keys); revokes every other session for the user |
| `/api/v1/auth/sessions/revoke-all` | POST | Revokes every active session for the current user, including this one. Session-only |

## Platform Operator Sessions

Platform-only endpoints (service restarts, cross-tenant listings, dead-letter replay,
DKIM rotation, and similar) require a **platform-scoped** credential. Operator
sessions are the staff path: individually revocable identities with mandatory MFA and
a role tier (`support` < `operator` < `admin` < `owner`). They live in a separate
cookie and table from tenant sessions -- a tenant credential can never reach platform
scope, and vice versa.

Login is always two steps:

1. **`POST /api/v1/platform/auth/login`** -- body `{"email", "password"}`. Issues a
   *partial* session (cookie `mailyte_operator_session` + `mailyte_operator_csrf`)
   that grants nothing except the MFA endpoints. Rate-limited harder than tenant
   login: 3 failures in 15 minutes.
2. **`POST /api/v1/platform/auth/mfa`** -- body `{"token": "<6-digit TOTP code>"}`.
   Promotes the session to fully authenticated. If the operator has no TOTP secret
   yet, call **`POST /api/v1/platform/auth/mfa/setup`** first (returns the
   `otpauth://` URI and backup codes), then confirm with `/mfa`.

Operator session properties:

- **4 hours idle / 12 hours absolute** -- shorter than tenant sessions by design.
- **IP-bound**: a cookie replayed from a different client IP is rejected with `401`.
- CSRF applies to state-changing requests, using `mailyte_operator_csrf` /
  `X-CSRF-Token`.

Other operator endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/platform/auth/bootstrap` | POST | One-time first-operator creation (role `owner`). Guarded by `X-Bootstrap-Token` from `/app/data/operator-bootstrap-token`, written at startup when no operator exists. Independent of the org bootstrap token. `409` once any operator exists |
| `/api/v1/platform/auth/me` | GET | Current operator id, email, role (requires a full, MFA-satisfied session) |
| `/api/v1/platform/auth/logout` | POST | Revokes the operator session |

!!! note "Roles gate actions, not just scope"
    Routes documented elsewhere as requiring, e.g., *platform scope + role
    `operator`* can only be reached by an operator session at or above that tier.
    A platform-scope API key satisfies the scope check but has no role, so it is
    rejected by any role-gated route.

## Mailbox Sessions (webmail)

Mailbox holders sign in to the webmail with their own mailbox credentials:

### `POST /api/v1/mailbox-auth/login`

Unauthenticated by design; rate-limited by IP and by address. Body:

| Field | Type | Required | Description |
|---|---|---|---|
| `email_address` | string | Yes | The mailbox address |
| `password` | string | Yes | Verified by IMAP LOGIN against Dovecot -- never against a stored copy |
| `two_factor_code` | string | No | Supplied on the second attempt after a `two_factor_required` response |

If the mailbox has two-factor enabled, the first successful password check returns
`{"two_factor_required": true}` with **no session issued**; retry with
`two_factor_code` set.

On success the response sets `mailyte_mailbox_session` / `mailyte_mailbox_csrf`
cookies **and** returns the session token in the body (`data.token`,
`data.csrf_token`, `data.expires_at`, `data.email_account`) for server-side callers,
which replay it as `Authorization: Bearer <token>` instead of cookies. Lifetimes:
**8 hours idle / 7 days absolute**.

`POST /api/v1/mailbox-auth/logout` revokes the session and clears cookies
(idempotent; also works for a pending pre-MFA session).

## Endpoints that require no authentication

| Endpoint | Why |
|---|---|
| `GET /health` | Container/uptime healthcheck |
| `GET /api/v1/capabilities/` | Capability manifest, fetched by clients before login |
| `POST /api/v1/auth/login` | There is no credential to present yet (rate-limited instead) |
| `POST /api/v1/mailbox-auth/login` | Same reasoning as `/auth/login` |
| `GET /api/v1/tracking/pixel/{tracking_id}`, `/click/{tracking_id}`, `/unsubscribe/{tracking_id}` | Loaded by recipients' mail clients, which hold no credential; the unguessable `tracking_id` is the identity |
| `POST /api/v1/bootstrap/`, `POST /api/v1/platform/auth/bootstrap` | Guarded by their single-use `X-Bootstrap-Token` instead |

## Authentication Errors

| Status | Response `msg` | Meaning |
|---|---|---|
| `401` | `"API key required"` | No `X-API-Key` header and no session cookie was sent |
| `401` | `"Invalid API key"` | The key does not exist or is inactive |
| `401` | `"Authentication required"` | No credential of any kind on the request |
| `401` | `"Session expired or invalid"` | Session cookie revoked, expired, or unknown |
| `401` | `"Invalid email or password"` | Login failure (identical whether the account exists or not) |
| `401` | `"Operator session IP mismatch"` | Operator cookie replayed from a different IP |
| `403` | `"Write access required"` | Read-only key used on a write endpoint |
| `403` | `"Admin access required"` | Key without `admin_access` on an admin endpoint |
| `403` | `"Platform scope required"` | Tenant credential on a platform-only endpoint |
| `403` | `"Insufficient operator role"` | Operator below the route's role floor, or a bare platform key on a role-gated route |
| `403` | `"CSRF token missing or invalid"` | Cookie-authenticated write without a matching `X-CSRF-Token` |
| `403` | `"MFA required"` | Operator session that has not completed MFA |
| `429` | `"Too many failed attempts. Try again later."` | Login or API-key failure throttle tripped |

## Example: Full Authentication Flow

=== "curl (API key)"

    ```bash
    # Read request
    curl -H "X-API-Key: $MAILYTE_KEY" \
      http://your-server:8083/api/v1/domains/

    # Write request
    curl -X POST \
      -H "X-API-Key: $MAILYTE_KEY" \
      -H "Content-Type: application/json" \
      -d '{"domain": "example.com"}' \
      http://your-server:8083/api/v1/domains/
    ```

=== "curl (browser session)"

    ```bash
    # Log in, saving cookies
    curl -c cookies.txt -X POST \
      -H "Content-Type: application/json" \
      -d '{"email": "admin@acme.com", "password": "correct-horse-42"}' \
      http://your-server:8083/api/v1/auth/login

    # Authenticated GET (no CSRF needed)
    curl -b cookies.txt http://your-server:8083/api/v1/auth/me

    # Authenticated write: echo the CSRF cookie in X-CSRF-Token
    CSRF=$(grep mailyte_csrf cookies.txt | awk '{print $7}')
    curl -b cookies.txt -X POST \
      -H "X-CSRF-Token: $CSRF" \
      -H "Content-Type: application/json" \
      -d '{"domain": "example.com"}' \
      http://your-server:8083/api/v1/domains/
    ```

=== "Python"

    ```python
    import requests

    BASE = "http://your-server:8083/api/v1"
    HEADERS = {"X-API-Key": "your-api-key-here"}

    # List domains
    resp = requests.get(f"{BASE}/domains/", headers=HEADERS)
    print(resp.json())
    ```

=== "JavaScript"

    ```javascript
    const BASE = "http://your-server:8083/api/v1";
    const headers = { "X-API-Key": "your-api-key-here" };

    const resp = await fetch(`${BASE}/domains/`, { headers });
    const data = await resp.json();
    console.log(data);
    ```
