# Bootstrap

One-time platform bootstrap for a fresh install. Resolves the bootstrap paradox — you need an API key to call the API, but a fresh install has none yet.

At startup, when no organizations exist, the API writes a **single-use bootstrap token** to stdout and to `/app/data/bootstrap-token` (mode `0600`) inside the api container. This endpoint accepts that token and creates the first organization, domain, mailbox, API key, and dashboard user in one call.

**Base path:** `/api/v1/bootstrap`
**Auth:** the `X-Bootstrap-Token` header — no API key exists yet. This module contains a single endpoint.

## Bootstrap the Platform

```
POST /api/v1/bootstrap
```

**Headers**

| Header | Required | Description |
|---|---|---|
| `X-Bootstrap-Token` | Yes | The single-use token from stdout / `/app/data/bootstrap-token` |

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `organization_name` | string | Yes | Display name for the first organization (1–255 chars) |
| `admin_email` | string | Yes | Admin mailbox to create — **its domain part becomes the first domain** |
| `admin_password` | string | Yes | Password for the admin mailbox; must pass the platform password-strength policy |

**Example Request**

```bash
# Read the token from the running container first:
# docker exec api cat /app/data/bootstrap-token

curl -X POST \
  -H "X-Bootstrap-Token: YOUR_BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_name": "Acme Corp",
    "admin_email": "admin@acme.com",
    "admin_password": "a-Strong-Passw0rd!"
  }' \
  http://your-server:5000/api/v1/bootstrap
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Bootstrap complete",
  "data": {
    "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
    "domain": "acme.com",
    "mailbox": "admin@acme.com",
    "api_key": "dGhpcy1pcy15b3VyLW9ubHktY29weQ..."
  }
}
```

!!! warning "The API key is returned once"
    `data.api_key` is your organization's first API key (read + write). It is stored only as a hash — save it now.

**What gets created**

1. The first **organization**
2. The first **domain** (the domain part of `admin_email`)
3. The **admin mailbox** — the same email/password pair logs into IMAP/webmail *and* the dashboard (`POST /api/v1/auth/login`)
4. A **dashboard user** with role `admin`
5. An **API key** with `{"read": true, "write": true}` permissions

The bootstrap token file is deleted after use — single use, regardless of outcome — and an `ORG_CREATED` webhook event is dispatched.

**Error Responses**

| Status | Condition |
|---|---|
| `401` | Invalid bootstrap token |
| `409` | Already bootstrapped — an organization exists (checked first, regardless of token validity) — or the token file is absent |
| `422` | Invalid email format or password fails the strength policy |
| `500` | Database failure during creation |
