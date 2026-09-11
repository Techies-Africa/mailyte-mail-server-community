# Tracking

Email engagement tracking (open pixel, click redirect, unsubscribe) and suppression-list management. The gateway routes proxy to the internal tracking service (`http://tracking:8086`) or query the tracking tables directly.

**Base path:** `/api/v1/tracking`
**Auth:** mixed by design —

| Endpoints | Auth |
|---|---|
| `pixel`, `click`, `unsubscribe` | **None (public).** Loaded by recipients' mail clients; recipients have no account |
| `stats/*`, `suppress` | `X-API-Key` (read for stats, write for suppress) |
| `suppressions` (list) | Operator session, role `support` or above |
| `suppressions/bulk` | Operator session, role `operator` or above |

## Tracking Pixel (Open Event)

Returns a 1x1 transparent pixel. When loaded by the recipient's email client, records an open event with timestamp, IP, and user agent.

```
GET /api/v1/tracking/pixel/{tracking_id}
```

```bash
curl -o pixel.png \
  http://your-server:5000/api/v1/tracking/pixel/tr_01J9AB2CD3EF4GH5JK6MN7PQ8R
```

**Response:** `image/png` body (not JSON). Public and unauthenticated by design.

## Click Tracking (Redirect)

Records a click event for the tracked link and returns an HTTP `302` redirect to the original destination URL. Captures timestamp, IP, and user agent.

```
GET /api/v1/tracking/click/{tracking_id}
```

```bash
curl -i http://your-server:5000/api/v1/tracking/click/tr_01J9AB2CD3EF4GH5JK6MN7PQ8R
# HTTP/1.1 302 Found
# Location: https://original-destination.example.com/page
```

## Unsubscribe (GET)

Processes a one-click unsubscribe request via GET — typically triggered by the `List-Unsubscribe` header in email clients. Removes the recipient from future mailings for the associated campaign or sender.

```
GET /api/v1/tracking/unsubscribe/{tracking_id}
```

## Unsubscribe (POST)

Processes an unsubscribe request via POST, optionally accepting a JSON body with a reason or feedback. Conforms to RFC 8058 one-click unsubscribe.

```
POST /api/v1/tracking/unsubscribe/{tracking_id}
```

**Request Body** (optional)

| Field | Type | Description |
|---|---|---|
| `reason` | string | Optional reason for unsubscribing |
| `feedback` | string | Optional feedback from the recipient |

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"reason": "too frequent"}' \
  http://your-server:5000/api/v1/tracking/unsubscribe/tr_01J9AB...
```

## Domain Tracking Statistics

Aggregated tracking statistics (opens, clicks, bounces) for all emails sent from a domain. Supports optional query parameters for date-range filtering. The caller must own the domain (cross-organization domains answer `404`).

```
GET /api/v1/tracking/stats/domain/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/tracking/stats/domain/acme.com
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Domain statistics retrieved successfully",
  "data": {
    "domain": "acme.com",
    "total_sent": 15200,
    "total_opens": 6320,
    "total_clicks": 1804
  }
}
```

## Email Tracking Statistics

Detailed tracking statistics for a single email: open count, click count, first/last engagement timestamps, and per-link click breakdown.

```
GET /api/v1/tracking/stats/email/{email_id}
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/tracking/stats/email/em_01J9AB...
```

## Add to Suppression List

Adds an email address to the suppression list, preventing future messages from being sent to it. Use for manual unsubscribes, known bounces, or compliance removals.

```
POST /api/v1/tracking/suppress
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Address to suppress |
| `reason` | string | No | e.g. `bounce`, `complaint`, `manual` |
| `organization_id` | string | Platform only | A tenant credential always writes to its own organization — a supplied value is overwritten, never honoured. A platform credential must name the owning org and gets `422` otherwise |

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "bounced@example.com", "reason": "bounce"}' \
  http://your-server:5000/api/v1/tracking/suppress
```

## Remove from Suppression List

```
DELETE /api/v1/tracking/suppress/{email}
```

Scoped like adding: a tenant credential removes from its own organization only;
a platform credential names the org with an `organization_id` query parameter
(`422` otherwise).

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/tracking/suppress/bounced@example.com
```

## List Suppressed Addresses

Paginated, filterable view over the suppression list — the read side the console's Suppressions screen uses. Platform scope sees every organization and may narrow to one with `organization_id`; a tenant sees only its own.

```
GET /api/v1/tracking/suppressions
```

**Auth:** operator session, role `support` or above.

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `organization_id` | string | -- | Platform scope only: narrow to one org |
| `suppression_type` | string | -- | Filter by type (e.g. `MANUAL`, `BOUNCE`, `COMPLAINT`, `UNSUBSCRIBE`) |
| `active` | boolean | -- | Filter on the active flag |
| `q` | string | -- | Substring match on the email address |
| `date_from` / `date_to` | string | -- | ISO date/datetime bounds |
| `sort_by` | string | `created_at` | `created_at` \| `email` |
| `sort_dir` | string | `desc` | `asc` \| `desc` |
| `page` / `per_page` | integer | `1` / `50` | Pagination |

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/tracking/suppressions?suppression_type=BOUNCE&page=1"
```

## Bulk Suppress

Adds up to 1000 addresses to the suppression list in one call and reports the outcome of each. Individual failures come back in `failed` with their reason — they are never swallowed.

```
POST /api/v1/tracking/suppressions/bulk
```

**Auth:** operator session, role `operator` or above.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `emails` | array | Yes | Addresses to suppress (max 1000) |
| `suppression_type` | string | No | Default `MANUAL` |
| `reason` | string | Yes | Why these addresses are being suppressed — stored on every row and shown in the console |
| `organization_id` | string | Platform only | Owning organization. Required for platform-scope callers; ignored for tenant credentials, which always write to their own org |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "emails": ["a@example.com", "b@example.com"],
    "suppression_type": "COMPLAINT",
    "reason": "FBL batch 2026-08-29",
    "organization_id": "01J8ZJ..."
  }' \
  http://your-server:5000/api/v1/tracking/suppressions/bulk
```
