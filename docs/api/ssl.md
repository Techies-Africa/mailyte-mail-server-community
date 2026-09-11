# SSL Certificates

Read the health and expiry of the TLS certificates the platform manages per domain, inspect ACME account health, and (nominally) trigger renewals.

**Base path:** `/api/v1/ssl`
**Auth:** platform scope with an operator session — reads require role `support` or above; the renew action requires `operator`; the ACME accounts listing requires `admin`. Responses use the standard `{"type", "msg", "data"}` envelope except where noted.

## Get SSL Status

Overall certificate system status: aggregate counts of active, failed, and expired certificates, ACME account health, and the timestamp of the last successful issuance.

```
GET /api/v1/ssl/status
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `include_accounts` | string | `true` | Include the per-ACME-account breakdown (`false` to omit) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/ssl/status?include_accounts=false"
```

## List Certificates

Paginated list of every domain certificate with its expiry, issuer, and owning organization — the read behind the console's certificate screen, which sorts by expiry.

```
GET /api/v1/ssl/certificates
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page |
| `status` | string | -- | Filter by certificate status |
| `domain` | string | -- | Substring match on the domain name |
| `expiring_days` | integer | -- | Only certificates expiring within this many days |
| `sort_by` | string | `valid_until` | `domain` \| `valid_until` \| ... |
| `sort_dir` | string | `asc` | `asc` \| `desc` |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/ssl/certificates?expiring_days=30&sort_by=valid_until"
```

## Get Certificate for a Domain

The certificate record for one domain: status, issuer, validity window, days until expiry, and owning organization. Carries the same fields as the list view.

```
GET /api/v1/ssl/certificates/{domain}
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/ssl/certificates/acme.com
```

## Trigger Certificate Renewal

Requests an out-of-band renewal from the certificate manager.

```
POST /api/v1/ssl/certificates/{domain}/renew
```

**Auth:** operator session, role `operator` or above.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | Yes | Why this certificate is being renewed out of band — audited |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Certificate flagged by external scanner"}' \
  http://your-server:5000/api/v1/ssl/certificates/acme.com/renew
```

!!! warning "Returns 501 on this deployment"
    The renewal endpoint answers `501 Not Implemented`, deliberately: the `cert_manager` service has no trigger interface of any kind. The response message explains this. Renewal happens on `cert_manager`'s own schedule.

## List ACME Accounts

All ACME accounts registered with certificate authorities, with per-account issuance statistics, success/failure rates, and last-used timestamps.

```
GET /api/v1/ssl/accounts
```

**Auth:** operator session, role `admin` or above.

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/ssl/accounts
```

**Example Response**

```json
{
  "type": "success",
  "msg": "ACME accounts retrieved successfully",
  "data": {
    "accounts": [
      {
        "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
        "email": "certs@mailyte.com",
        "certs_issued": 27,
        "success_count": 27,
        "failure_count": 3,
        "success_rate": 90.0,
        "last_used": "2026-08-28T04:12:00",
        "registered_at": "2026-05-01T00:00:00"
      }
    ],
    "total": 1
  }
}
```
