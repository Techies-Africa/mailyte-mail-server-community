# Webhooks

Configure webhook endpoints to receive real-time notifications when email events happen.

Webhooks are scoped to the organization associated with your API key. If your key is org-scoped, you only see and manage webhooks for that organization.

## Event Types

| Event | Description |
|---|---|
| `email.sent` | An email was sent from your server |
| `email.delivered` | An email was successfully delivered |
| `email.bounced` | An email bounced (hard or soft bounce) |
| `email.opened` | A recipient opened the email |
| `email.clicked` | A recipient clicked a link in the email |

Set `event_types` to `null` to subscribe to all events.

## List Webhook Endpoints

Retrieve all webhook endpoints for your organization.

```
GET /api/v1/webhooks/endpoints
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/webhooks/endpoints
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoints retrieved",
  "data": {
    "items": [
      {
        "id": 1,
        "organization_id": "acme",
        "url": "https://hooks.acme.com/mailyte",
        "description": "Production webhook",
        "active": 1,
        "event_types": ["email.sent", "email.delivered", "email.bounced"],
        "created_at": "2025-01-20T09:00:00",
        "updated_at": "2025-03-20T14:22:00"
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

!!! note "Secrets are not returned"
    The signing secret is never included in list or get responses. You only see it once, when you create the endpoint.

## Create Webhook Endpoint

Register a new webhook URL.

```
POST /api/v1/webhooks/endpoints
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | Yes | The URL to receive events (must start with `http://` or `https://`) |
| `description` | string | No | Description of the endpoint |
| `active` | boolean | No | Whether the endpoint is active (default: `true`) |
| `event_types` | array | No | List of event types to subscribe to (`null` = all events) |
| `secret` | string | No | Signing secret (auto-generated if not provided) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.acme.com/mailyte",
    "description": "Production webhook",
    "event_types": ["email.sent", "email.delivered", "email.bounced"]
  }' \
  http://your-server:5000/api/v1/webhooks/endpoints
```

**Example Response** (`201 Created`)

```json
{
  "type": "success",
  "msg": "Webhook endpoint created",
  "data": {
    "id": 1,
    "url": "https://hooks.acme.com/mailyte",
    "secret": "a1b2c3d4e5f6...",
    "active": true
  }
}
```

!!! warning "Save the secret"
    The `secret` is only returned once in the create response. Store it securely -- you will need it to verify incoming webhook signatures.

## Get Webhook Endpoint

Retrieve a specific webhook endpoint.

```
GET /api/v1/webhooks/endpoints/{endpoint_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/webhooks/endpoints/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoint retrieved",
  "data": {
    "id": 1,
    "organization_id": "acme",
    "url": "https://hooks.acme.com/mailyte",
    "description": "Production webhook",
    "active": 1,
    "event_types": ["email.sent", "email.delivered", "email.bounced"],
    "created_at": "2025-01-20T09:00:00",
    "updated_at": "2025-03-20T14:22:00"
  }
}
```

## Update Webhook Endpoint

Update a webhook endpoint's URL, events, or active status.

```
PUT /api/v1/webhooks/endpoints/{endpoint_id}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `url` | string | New webhook URL |
| `description` | string | New description |
| `active` | boolean | Enable or disable the endpoint |
| `event_types` | array | Updated list of event types (`null` = all) |
| `secret` | string | New signing secret |

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "event_types": ["email.sent", "email.delivered", "email.bounced", "email.opened", "email.clicked"],
    "active": true
  }' \
  http://your-server:5000/api/v1/webhooks/endpoints/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoint updated",
  "data": {
    "id": 1
  }
}
```

## Delete Webhook Endpoint

Remove a webhook endpoint. Events will no longer be sent to this URL.

```
DELETE /api/v1/webhooks/endpoints/{endpoint_id}
```

**Example Request**

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/webhooks/endpoints/1
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Webhook endpoint deleted"
}
```

## Test Webhook Endpoint

Send a test event to a webhook endpoint to verify it is receiving events correctly. The endpoint must be active.

```
POST /api/v1/webhooks/endpoints/{endpoint_id}/test
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/webhooks/endpoints/1/test
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Test event dispatched",
  "data": {
    "endpoint_id": 1
  }
}
```

## Delivery History

View the delivery log for all webhook events sent to your organization.

```
GET /api/v1/webhooks/deliveries
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `status` | string | -- | Filter by delivery status (`SUCCESS`, `FAILED`, `PENDING`) |
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/webhooks/deliveries?status=FAILED"
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Delivery log retrieved",
  "data": {
    "items": [
      {
        "id": 42,
        "event_type": "email.bounced",
        "webhook_url": "https://hooks.acme.com/mailyte",
        "delivery_status": "FAILED",
        "http_status_code": 500,
        "attempts": 3,
        "error_message": "Connection timeout",
        "created_at": "2025-03-25T10:30:00",
        "delivered_at": null
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

## Dead Letter Queue

Events that fail after all retry attempts end up in the dead letter queue.

```
GET /api/v1/webhooks/dead-letters
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `per_page` | integer | `50` | Items per page (max 200) |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/webhooks/dead-letters
```

**Example Response**

```json
{
  "type": "success",
  "msg": "Dead letter queue retrieved",
  "data": {
    "items": [
      {
        "id": 5,
        "event_type": "email.bounced",
        "webhook_url": "https://hooks.acme.com/mailyte",
        "error_message": "Connection refused after 5 retries",
        "retry_count": 5,
        "created_at": "2025-03-24T08:15:00"
      }
    ],
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```
