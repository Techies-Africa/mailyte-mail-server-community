---
title: Custom Integrations
description: Connect Mailyte to your CRM, ticketing system, or any application using webhooks and the REST API.
---

# Custom Integrations

Mailyte fires webhooks and exposes a REST API. This guide covers how event delivery actually works, how to verify signatures, and common automation patterns against the API.

!!! info "API base URL"
    Examples use `https://api.yourdomain.com` (Traefik publishes the API at `api.<your-domain>` in production; a dev checkout exposes it at `http://localhost:8083`). All requests authenticate with the `X-API-Key` header.

## How Event Delivery Works (as of 2026-08-30)

There are two delivery paths, and they receive different events:

| Path | Events | Configured by |
|------|--------|---------------|
| **Global dispatcher** — one platform-wide URL | Mail-flow and platform events: `email.inbound`, `email.outbound`, `email.delivered`, `email.bounced`, `email.deferred`, `tracking.open`, `tracking.click`, `delivery.bounce.hard`, domain/mailbox lifecycle events, and more | `WEBHOOK_URL` + `WEBHOOK_SECRET` environment variables on the server |
| **Registered endpoints** — per-organization URLs in the `webhook_urls` table | Events emitted by the tracking, rate-limiter, and storage-usage services (e.g. `email.opened`, `email.clicked`, rate-limit and storage alerts) | `POST /api/v1/webhooks/endpoints` |

!!! warning "Registered endpoints do not receive mail-flow events"
    As of 2026-08-30 the unified dispatcher delivers only to the single global `WEBHOOK_URL`. Endpoints registered via the API receive events from the tracking / rate-limiter / storage services (matched by `service_types` and `event_types`), plus test deliveries — not the SMTP inbound/outbound stream. If your integration needs the full mail-flow stream, set `WEBHOOK_URL` on the deployment and fan out in your own receiver.

## The Global Dispatcher

### Envelope

Every event delivered to `WEBHOOK_URL` carries a consistent envelope:

```json
{
  "id": "0d4f6c1e-6a0e-4f3f-9d3c-0b8b1a2c3d4e",
  "event": "email.delivered",
  "timestamp": "2026-08-30T14:30:00+00:00",
  "source": "log_ingestor",
  "org_id": "acme-corp",
  "domain": "example.com",
  "tags": [],
  "user_variables": {},
  "data": {
    "message_id": "<abc123@example.com>",
    "recipient": "user@example.com"
  },
  "metadata": {},
  "signature": {
    "timestamp": 1756564200,
    "token": "3f9a...",
    "signature": "9c1b..."
  }
}
```

Request headers include `X-Webhook-Id`, `X-Webhook-Event`, `X-Webhook-Source`, `X-Webhook-Timestamp`, and `X-Webhook-Signature: sha256=<hex>`.

### Verifying Global-Dispatcher Signatures

Two complementary checks — the header signs the **raw request body**, and the inline block prevents replays:

```python
import hashlib
import hmac
import time


def verify_header_signature(raw_body: bytes, header_value: str, secret: str) -> bool:
    """X-Webhook-Signature is HMAC-SHA256 over the exact request bytes."""
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header_value, f"sha256={expected}")


def verify_inline_signature(payload: dict, secret: str, max_age: int = 900) -> bool:
    """payload['signature'] is HMAC-SHA256 over str(timestamp) + token."""
    sig = payload.get("signature", {})
    if abs(time.time() - sig.get("timestamp", 0)) > max_age:
        return False  # replay protection: reject events older than 15 minutes
    expected = hmac.new(
        secret.encode(),
        (str(sig["timestamp"]) + sig["token"]).encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig.get("signature", ""), expected)
```

!!! warning "Always verify signatures"
    Without verification, anyone who discovers your webhook URL can send fake events.

### Retries and 406

Failed deliveries retry on a Mailgun-style schedule — 7 retries over roughly 8 hours (10m, 10m, 15m, 30m, 1h, 2h, 4h). Responding with HTTP **406** permanently stops delivery of that event (no retry, no dead-letter). Events that exhaust their retries land in the dead-letter queue (below).

## Registered Endpoints

### Setting Up a Webhook Endpoint

```bash
curl -X POST https://api.yourdomain.com/api/v1/webhooks/endpoints \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CRM Integration",
    "url": "https://your-app.com/webhooks/mailyte",
    "description": "Tracking events into the CRM",
    "event_types": ["email.opened", "email.clicked"],
    "service_types": ["tracking"],
    "active": true
  }'
```

- `event_types: null` means "all events" the producing services emit.
- `service_types` selects which services deliver to this endpoint — `"tracking"`, `"rate_limiter"`, `"storage_usage"`, or `["all"]`.
- If you omit `secret`, the server generates one and returns it in the response — store it; it signs every delivery to this endpoint.

Manage endpoints with `GET /api/v1/webhooks/endpoints`, `GET/PUT/DELETE /api/v1/webhooks/endpoints/{id}`, and fire a test delivery with `POST /api/v1/webhooks/endpoints/{id}/test`.

### Verifying Registered-Endpoint Signatures

These services sign the payload serialized with sorted keys (not the raw bytes), in the same `X-Webhook-Signature: sha256=<hex>` header:

```python
import hashlib
import hmac
import json


def verify_endpoint_webhook(payload: dict, header_value: str, secret: str) -> bool:
    payload_json = json.dumps(payload, sort_keys=True)
    expected = hmac.new(secret.encode(), payload_json.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(header_value, f"sha256={expected}")
```

## Delivery Logs and Dead Letters

```bash
# Delivery attempts (status, HTTP code, timing)
curl https://api.yourdomain.com/api/v1/webhooks/deliveries \
  -H "X-API-Key: YOUR_API_KEY"

# Events that exhausted their retries
curl https://api.yourdomain.com/api/v1/webhooks/dead-letters \
  -H "X-API-Key: YOUR_API_KEY"

# Inspect one, then replay it
curl https://api.yourdomain.com/api/v1/webhooks/dead-letters/DEAD_LETTER_ID \
  -H "X-API-Key: YOUR_API_KEY"
curl -X POST https://api.yourdomain.com/api/v1/webhooks/dead-letters/DEAD_LETTER_ID/replay \
  -H "X-API-Key: YOUR_API_KEY"

# Or replay several at once
curl -X POST https://api.yourdomain.com/api/v1/webhooks/dead-letters/replay-bulk \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dead_letter_ids": ["1041", "1042"]}'
```

## Integration Examples

The examples below consume the global-dispatcher envelope (`event` + `data`).

### CRM Integration (HubSpot / Salesforce)

Create a contact or log activity whenever Mailyte receives an email:

```python
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

HUBSPOT_TOKEN = "pat-xxx"
WEBHOOK_SECRET = "your-secret"


@app.route("/webhooks/mailyte", methods=["POST"])
def handle_mailyte_webhook():
    if not verify_header_signature(
        request.get_data(), request.headers.get("X-Webhook-Signature", ""), WEBHOOK_SECRET
    ):
        return jsonify({"status": "invalid signature"}), 401

    payload = request.get_json()
    if payload.get("event") == "email.inbound":
        data = payload.get("data", {})
        sender = data.get("from") or data.get("sender")
        if sender:
            requests.post(
                "https://api.hubapi.com/crm/v3/objects/contacts",
                headers={
                    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"properties": {"email": sender}},
            )
    return jsonify({"status": "ok"})
```

### Ticketing System (Jira / Linear)

Auto-create tickets from incoming support emails:

```python
@app.route("/webhooks/mailyte", methods=["POST"])
def handle_support_email():
    payload = request.get_json()
    if payload.get("event") != "email.inbound":
        return jsonify({"status": "skipped"})

    data = payload.get("data", {})
    recipient = data.get("recipient", "")
    if not recipient.startswith("support@"):
        return jsonify({"status": "skipped"})

    requests.post(
        "https://your-org.atlassian.net/rest/api/3/issue",
        auth=("email@company.com", "JIRA_API_TOKEN"),
        json={
            "fields": {
                "project": {"key": "SUP"},
                "summary": data.get("subject", "(no subject)"),
                "issuetype": {"name": "Task"},
            }
        },
    )
    return jsonify({"status": "created"})
```

### Slack Notifications

```python
import requests

SLACK_WEBHOOK = "https://hooks.slack.com/services/T.../B.../xxx"


@app.route("/webhooks/mailyte", methods=["POST"])
def slack_notify():
    payload = request.get_json()
    event = payload.get("event")
    data = payload.get("data", {})

    messages = {
        "email.bounced": f"Bounce for {data.get('recipient', 'unknown')}",
        "delivery.bounce.hard": f"Hard bounce: {data.get('recipient', 'unknown')}",
        "email.delivered": f"Delivered to {data.get('recipient', 'unknown')}",
    }
    msg = messages.get(event)
    if msg:
        requests.post(SLACK_WEBHOOK, json={"text": msg})
    return jsonify({"status": "ok"})
```

## API Automation Examples

### Provisioning a Customer End to End

```python
import requests

API = "https://api.yourdomain.com/api/v1"
HEADERS = {"X-API-Key": "YOUR_ADMIN_API_KEY", "Content-Type": "application/json"}


def provision_customer(org_id: str, org_name: str, domain: str, admin_email: str):
    """Set up everything a new customer needs."""

    # 1. Create organization (admin platform key required)
    requests.post(
        f"{API}/organizations/",
        headers=HEADERS,
        json={"id": org_id, "name": org_name, "admin_email": admin_email},
    )

    # 2. Add domain — DKIM is generated automatically and the
    #    response carries every DNS record the customer must publish
    domain_resp = requests.post(
        f"{API}/domains/",
        headers=HEADERS,
        json={"domain": domain, "organization_id": org_id},
    ).json()["data"]
    domain_id = domain_resp["domain_id"]

    # 3. Create the admin mailbox
    requests.post(
        f"{API}/mailboxes/email-accounts",
        headers=HEADERS,
        json={
            "email": admin_email,
            "password": generate_temp_password(),
            "name": "Admin",
            "domain_id": domain_id,
        },
    )

    # 4. Mint an SMTP credential so their application can send
    smtp_cred = requests.post(
        f"{API}/smtp-credentials/",
        headers=HEADERS,
        json={"domain_id": domain_id, "name": f"{org_id} app sending"},
    ).json()["data"]
    # smtp_cred["secret"] is shown exactly once — hand it over securely

    return {
        "org_id": org_id,
        "domain_id": domain_id,
        "dns_records": domain_resp["dns_records"],
        "dkim_record": domain_resp.get("dkim_record"),
        "smtp_username": smtp_cred["username"],
    }
```

### Usage Reporting

```python
def get_org_usage(org_id: str) -> dict:
    """Quota limits and current usage for an organization."""
    quotas = requests.get(f"{API}/organizations/{org_id}/quotas", headers=HEADERS).json()["data"]
    return quotas
```

Per-domain sending stats come from the analytics endpoints — `GET /api/v1/analytics/email-volume/{domain}`, `GET /api/v1/analytics/deliverability/{domain}` — and delivery history is searchable via `GET /api/v1/message-trace/trace`.

### Domain Health Automation

```python
def check_domain_health(domain_id: str) -> dict:
    """Live MX/SPF/DKIM/DMARC verification for a domain."""
    return requests.get(f"{API}/domains/{domain_id}/verify-dns", headers=HEADERS).json()


def rotate_dkim(domain_id: str) -> dict:
    """Step one of a safe DKIM rotation — publish the returned record,
    then call the activate endpoint once it resolves."""
    return requests.post(
        f"{API}/domains/{domain_id}/dkim/rotate",
        headers=HEADERS,
        json={"reason": "scheduled rotation"},
    ).json()
```

## Webhook Reliability Tips

1. **Respond quickly** — return 2xx within the timeout, process async
2. **Idempotency** — deliveries can repeat; deduplicate on the envelope `id`
3. **Queue internally** — push webhooks into your own queue (Redis, RabbitMQ) for processing
4. **Monitor failures** — `GET /api/v1/webhooks/deliveries` and `.../dead-letters`
5. **Replay, don't lose** — dead-lettered events can be replayed via the API once your endpoint is healthy again
6. **406 means stop** — only return HTTP 406 when you genuinely never want that event again
