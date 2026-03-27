# Rate Limiting

View and manage email sending rate limits per domain and mailbox. Rate limits control how many emails can be sent within a given time period.

## Get Domain Rate Limits

Retrieve the current rate limits for a domain.

```
GET /api/v1/rate-limits/domain/{domain}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `domain` | string | Domain name (e.g., `acme.com`) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/rate-limits/domain/acme.com
```

**Example Response**

```json
{
  "domain": "acme.com",
  "rate_limits": {
    "hourly": 500,
    "daily": 5000,
    "monthly": 100000
  },
  "current_usage": {
    "hourly": 42,
    "daily": 350,
    "monthly": 12500
  }
}
```

## Set Domain Rate Limits

Update rate limits for a domain.

```
POST /api/v1/rate-limits/domain/{domain}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `hourly` | integer | Max emails per hour |
| `daily` | integer | Max emails per day |
| `monthly` | integer | Max emails per month |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "hourly": 1000,
    "daily": 10000,
    "monthly": 200000
  }' \
  http://your-server:5000/api/v1/rate-limits/domain/acme.com
```

## Get Mailbox Rate Limits

Retrieve rate limits for a specific mailbox.

```
GET /api/v1/rate-limits/mailbox/{email}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/rate-limits/mailbox/john@acme.com
```

**Example Response**

```json
{
  "email": "john@acme.com",
  "rate_limits": {
    "hourly": 50,
    "daily": 500,
    "monthly": 10000
  },
  "current_usage": {
    "hourly": 5,
    "daily": 45,
    "monthly": 1200
  }
}
```

## Set Mailbox Rate Limits

Update rate limits for a specific mailbox.

```
POST /api/v1/rate-limits/mailbox/{email}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `hourly` | integer | Max emails per hour |
| `daily` | integer | Max emails per day |
| `monthly` | integer | Max emails per month |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "hourly": 100,
    "daily": 1000,
    "monthly": 20000
  }' \
  http://your-server:5000/api/v1/rate-limits/mailbox/john@acme.com
```

## Get Domain Usage

Check current sending usage against limits for a domain.

```
GET /api/v1/rate-limits/usage/{domain}
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `period` | string | Time period: `hourly`, `daily`, or `monthly` |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/rate-limits/usage/acme.com?period=daily"
```

**Example Response**

```json
{
  "domain": "acme.com",
  "period": "daily",
  "limit": 5000,
  "used": 350,
  "remaining": 4650,
  "usage_percentage": 7.0,
  "resets_at": "2025-03-26T00:00:00Z"
}
```

## Reset Rate Limit Counters

Reset rate limit counters for a domain or mailbox. Useful after fixing a deliverability issue.

```
POST /api/v1/rate-limits/reset
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `domain` | string | No | Domain to reset (provide `domain` or `email`) |
| `email` | string | No | Mailbox to reset |
| `period` | string | No | Period to reset: `hourly`, `daily`, `monthly`, or `all` (default: `all`) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "acme.com",
    "period": "hourly"
  }' \
  http://your-server:5000/api/v1/rate-limits/reset
```

## Get Domain Quotas

Get daily and monthly sending quotas for a domain.

```
GET /api/v1/quotas/domain/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/quotas/domain/acme.com
```

**Example Response**

```json
{
  "domain": "acme.com",
  "quotas": {
    "daily_limit": 5000,
    "daily_used": 350,
    "daily_remaining": 4650,
    "monthly_limit": 100000,
    "monthly_used": 12500,
    "monthly_remaining": 87500
  }
}
```

## Set Domain Quotas

Update daily and monthly sending quotas for a domain.

```
POST /api/v1/quotas/domain/{domain}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `daily_limit` | integer | Max emails per day |
| `monthly_limit` | integer | Max emails per month |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "daily_limit": 10000,
    "monthly_limit": 250000
  }' \
  http://your-server:5000/api/v1/quotas/domain/acme.com
```
