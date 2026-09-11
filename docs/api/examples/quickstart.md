---
title: Quickstart
description: Authenticate and make your first request in curl, Python, JavaScript, or PHP.
---

# Quickstart

**Phase-05 note:** this used to be four ~350-400 line, hand-maintained workflow walkthroughs
(one per language) that inevitably drifted from the actual API as endpoints changed — nothing
regenerated them. They're replaced with this one short page. For the full, always-current
endpoint list — every parameter, every response field, every documented error code — use the
generated reference instead of hand-written prose:

- **[API Reference](/api-reference)** (Redoc) — browse by resource, see real response shapes
- **[Swagger UI](/api-docs)** — same spec, interactive "try it now" console
- **[openapi.json](/openapi.json)** — the raw spec, if you're generating a typed client

Every example below does the same thing: authenticate, then list your domains.

## Authentication

Every request needs `X-API-Key`. Get a key from `POST /api/v1/bootstrap` on a fresh install
(see the main [Getting Started](../../getting-started/index.md) guide), or from an existing
organization's dashboard.

## curl

```bash
export MAILYTE_URL="http://your-server:8083"
export MAILYTE_KEY="your-api-key-here"

curl "$MAILYTE_URL/api/v1/domains/" -H "X-API-Key: $MAILYTE_KEY"
```

## Python

```python
import requests

MAILYTE_URL = "http://your-server:8083"
MAILYTE_KEY = "your-api-key-here"

resp = requests.get(
    f"{MAILYTE_URL}/api/v1/domains/",
    headers={"X-API-Key": MAILYTE_KEY},
)
resp.raise_for_status()
print(resp.json()["data"])
```

## JavaScript

```javascript
const MAILYTE_URL = "http://your-server:8083";
const MAILYTE_KEY = "your-api-key-here";

const resp = await fetch(`${MAILYTE_URL}/api/v1/domains/`, {
  headers: { "X-API-Key": MAILYTE_KEY },
});
const { data } = await resp.json();
console.log(data);
```

## PHP

```php
<?php
$mailyteUrl = "http://your-server:8083";
$mailyteKey = "your-api-key-here";

$ch = curl_init("$mailyteUrl/api/v1/domains/");
curl_setopt($ch, CURLOPT_HTTPHEADER, ["X-API-Key: $mailyteKey"]);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
$response = json_decode(curl_exec($ch), true);
curl_close($ch);

print_r($response["data"]);
```

## Response envelope

Responses are shaped `{"type": "success"|"error", "msg": "...", "data": {...}}` — `data` is
omitted on responses that have nothing to return (e.g. a bare success message). The handful of
endpoints that proxy to internal services (analytics, rate limiter, storage, RAG, tracking)
pass the downstream service's own JSON through instead. See [Errors](../errors.md) for the
full status code table and `error_code` registry.

## Idempotent writes

Any mutating request (`POST`/`PUT`/`PATCH`/`DELETE`) made with an organization-scoped
credential accepts an optional `Idempotency-Key` header. Retry the exact same key + body
within 24h and you get the original response back, not a duplicate side effect — safe for
network retries. Reusing a key with a different body returns `422`
(`IDEMPOTENCY_KEY_REUSED`); retrying while the original is still running returns `409`
(`IDEMPOTENCY_IN_PROGRESS`).
