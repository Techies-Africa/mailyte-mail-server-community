# Statistics & Analytics

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Retrieve delivery statistics, engagement metrics, and email tracking data.

The analytics endpoints proxy to an internal analytics service. All data is scoped by domain.

## Dashboard Data

Get a summary of analytics data for a domain, suitable for dashboards.

```
GET /api/v1/analytics/dashboard/{domain}
```

**Path Parameters**

| Parameter | Type | Description |
|---|---|---|
| `domain` | string | Domain name (e.g., `acme.com`) |

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `start_date` | string | Start date (`YYYY-MM-DD`) |
| `end_date` | string | End date (`YYYY-MM-DD`) |
| `period` | string | Aggregation period (`hourly`, `daily`, `weekly`, `monthly`) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/analytics/dashboard/acme.com?start_date=2025-03-01&end_date=2025-03-25"
```

## Email Volume

Get email volume statistics (sent, received, bounced) for a domain.

```
GET /api/v1/analytics/email-volume/{domain}
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `start_date` | string | Start date |
| `end_date` | string | End date |
| `period` | string | Aggregation period |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/analytics/email-volume/acme.com?period=daily&start_date=2025-03-01&end_date=2025-03-25"
```

**Example Response**

```json
{
  "domain": "acme.com",
  "period": "daily",
  "data": [
    {
      "date": "2025-03-25",
      "sent": 245,
      "received": 1032,
      "bounced": 3,
      "rejected": 12
    }
  ]
}
```

## Engagement Metrics

Get open and click rates for a domain.

```
GET /api/v1/analytics/engagement/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/analytics/engagement/acme.com?start_date=2025-03-01&end_date=2025-03-25"
```

**Example Response**

```json
{
  "domain": "acme.com",
  "data": {
    "total_sent": 5000,
    "total_opened": 3200,
    "total_clicked": 800,
    "open_rate": 64.0,
    "click_rate": 16.0,
    "click_to_open_rate": 25.0
  }
}
```

## Deliverability Metrics

Get deliverability statistics (bounce rates, spam complaints) for a domain.

```
GET /api/v1/analytics/deliverability/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/analytics/deliverability/acme.com"
```

**Example Response**

```json
{
  "domain": "acme.com",
  "data": {
    "delivery_rate": 98.5,
    "bounce_rate": 1.2,
    "hard_bounce_rate": 0.3,
    "soft_bounce_rate": 0.9,
    "spam_complaint_rate": 0.01,
    "total_delivered": 4925,
    "total_bounced": 60
  }
}
```

## Domain Metrics

Get general metrics for a domain.

```
GET /api/v1/analytics/metrics/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/analytics/metrics/acme.com"
```

## Tracking Statistics

### Domain Tracking Stats

Get email tracking statistics (opens, clicks) for a domain.

```
GET /api/v1/stats/domain/{domain}
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `start_date` | string | Start date |
| `end_date` | string | End date |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/stats/domain/acme.com?start_date=2025-03-01&end_date=2025-03-25"
```

### Single Email Tracking

Get tracking data for a specific email by its tracking ID.

```
GET /api/v1/stats/email/{email_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/stats/email/msg_abc123
```

## Reports

### Generate Report

Create a new analytics report.

```
POST /api/v1/reports/generate
```

**Request Body**

```json
{
  "domain": "acme.com",
  "report_type": "deliverability",
  "start_date": "2025-03-01",
  "end_date": "2025-03-25",
  "format": "json"
}
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "acme.com",
    "report_type": "deliverability",
    "start_date": "2025-03-01",
    "end_date": "2025-03-25"
  }' \
  http://your-server:5000/api/v1/reports/generate
```

### Get Report

Retrieve a previously generated report.

```
GET /api/v1/reports/{report_id}
```

### Scheduled Reports

List and create scheduled reports.

```
GET /api/v1/reports/scheduled
```

```
POST /api/v1/reports/scheduled
```

**Request Body for Creating a Schedule**

```json
{
  "domain": "acme.com",
  "report_type": "engagement",
  "frequency": "weekly",
  "recipients": ["admin@acme.com"],
  "format": "pdf"
}
```

## Suppression List

### Add to Suppression List

Add an email address to the suppression list (no more tracking events for this address).

```
POST /api/v1/tracking/suppress
```

**Request Body**

```json
{
  "email": "unsubscribed@example.com",
  "reason": "user_request"
}
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "unsubscribed@example.com", "reason": "user_request"}' \
  http://your-server:5000/api/v1/tracking/suppress
```

### Remove from Suppression List

Remove an email address from the suppression list.

```
DELETE /api/v1/tracking/suppress/{email}
```

**Example Request**

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/tracking/suppress/unsubscribed@example.com
```

## Analytics Health

Check the health status of the analytics service.

```
GET /api/v1/analytics/health
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/analytics/health
```
