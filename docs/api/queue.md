# Queue

Postfix mail-queue visibility and control, proxied through the API gateway to the internal `queue_manager` service (`http://queue_manager:8090`), which shells out to `postqueue`/`postsuper`.

**Base path:** `/api/v1/queue`
**Auth:** platform scope with an operator session — reads require role `support` or above, the flush action requires `operator` or above. Tenant API keys cannot call these endpoints.

!!! warning "Two endpoints answer 501 by design"
    `queue_manager` parses `postqueue -p` wholesale and has no per-domain view. The two per-domain endpoints below answer `501 Not Implemented` with a hint, rather than a misleading empty result.

## Get Mail Queue Status

Current state of the Postfix mail queue: total, active, deferred, and held message counts, the raw message list, and a per-recipient-domain breakdown.

```
GET /api/v1/queue/queue/status
```

(The double `queue` segment is real: the router is mounted at `/api/v1/queue` and the route path is `/queue/status`.)

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/queue/queue/status
```

**Example Response**

```json
{
  "total_messages": 14,
  "summary": { "active": 2, "deferred": 11, "hold": 1 },
  "messages": [
    {
      "queue_id": "4Xk2Yz1",
      "status": "deferred",
      "sender": "news@acme.com",
      "recipients": ["user@example.org"],
      "size": 48211,
      "date": "Sat Aug 30 09:12:44"
    }
  ],
  "total": 14,
  "active": 2,
  "deferred": 11,
  "hold": 1,
  "by_domain": [ { "domain": "example.org", "count": 9 } ]
}
```

`total`/`active`/`deferred`/`hold`/`by_domain` are gateway-computed additive aliases over `queue_manager`'s native `total_messages`/`summary` shape; `by_domain` counts per **recipient** domain. If `queue_manager` cannot read the Postfix queue, the gateway promotes its 200-with-error into a `502` with a hint, so a broken queue never renders as an empty one.

## Get Domain Queue Status

```
GET /api/v1/queue/queue/domain/{domain}
```

**Always returns `501`** — `queue_manager` exposes no per-domain view:

```json
{
  "error": "Per-domain queue status is not implemented by the queue service",
  "hint": "Use GET /api/v1/queue/queue/status and group by domain client-side."
}
```

## Get Deferred Mail Queue

All messages currently in the Postfix deferred queue, derived by the gateway from the full queue listing (each entry carries queue ID, sender, recipients, deferral status, and timestamps).

```
GET /api/v1/queue/mail-queue/deferred
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/queue/mail-queue/deferred
```

**Example Response**

```json
{
  "messages": [
    { "queue_id": "4Xk2Yz1", "status": "deferred", "sender": "news@acme.com",
      "recipients": ["user@example.org"], "date": "Sat Aug 30 09:12:44",
      "arrival_time": "Sat Aug 30 09:12:44" }
  ],
  "total": 11,
  "queue_error": null
}
```

`queue_error` is passed through so the caller can tell "no deferred mail" from "the queue could not be read at all".

## Flush Mail Queue

Triggers an immediate delivery attempt for queued messages. Can be scoped by domain, queue name, recipient, or message age.

```
POST /api/v1/queue/mail-queue/flush
```

**Auth:** operator session, role `operator` or above.

**Request Body** (all optional)

| Field | Type | Description |
|---|---|---|
| `domain` | string | Limit flush to a specific domain; omit to flush the entire queue |
| `queue_name` | string | `deferred`, `hold`, or `all` (default `all`) |
| `older_than_minutes` | integer | Only flush messages queued longer than this |
| `recipient` | string | Only flush messages addressed to this recipient |
| `force` | boolean | Force immediate delivery attempt, bypassing backoff timers (default `false`) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"queue_name": "deferred", "older_than_minutes": 60, "force": true}' \
  http://your-server:5000/api/v1/queue/mail-queue/flush
```

The upstream response is passed through verbatim.

## Get Domain Queue Jobs

```
GET /api/v1/queue/jobs/{domain}
```

**Always returns `501`** — same reason as the per-domain status endpoint:

```json
{
  "error": "Per-domain queue jobs are not implemented by the queue service",
  "hint": "Use GET /api/v1/queue/queue/status for the full queue."
}
```

## Queue Health Check

Health of the queue processing subsystem, proxied from `queue_manager`'s `/health`.

```
GET /api/v1/queue/health
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/queue/health
```

Returns `503` with `{"error": "Queue service unavailable"}` when the queue microservice cannot be reached.
