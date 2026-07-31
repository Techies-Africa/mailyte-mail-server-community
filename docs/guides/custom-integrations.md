---
title: Custom Integrations
description: Connect Mailyte to your CRM, ticketing system, or any application using webhooks and the REST API.
---

# Custom Integrations

Mailyte fires webhooks and exposes a REST API. That's all you need to integrate it with pretty much anything — CRMs, ticketing systems, Slack, custom dashboards, whatever.

## Webhooks: Real-Time Event Streaming

Webhooks push events to your application as they happen. Every email sent, received, bounced, opened, or clicked triggers a webhook.

### Setting Up a Webhook Endpoint

Register your endpoint via the API:

```bash
curl -X POST http://mail.yourdomain.com:8083/api/v1/add/webhook \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CRM Integration",
    "url": "https://your-app.com/webhooks/mailyte",
    "event_types": ["email.smtp.inbound", "email.smtp.outbound", "email.imap.read"],
    "service_types": ["smtp", "imap"],
    "webhook_secret": "your-webhook-secret",
    "active": true
  }'
```

### Webhook Payload

Every webhook follows this structure:

```json
{
  "event": "email.smtp.inbound",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "inbound",
    "protocol": "smtp",
    "metadata": {
      "message_id": "<abc123@example.com>",
      "subject": "Support request #4521",
      "from": "customer@gmail.com",
      "to": "support@mycompany.com"
    },
    "delivery_info": {
      "recipient": "support@mycompany.com",
      "sender": "customer@gmail.com",
      "delivery_status": "received"
    },
    "security": {
      "spf_result": "pass",
      "dkim_result": "pass"
    }
  }
}
```

### Verifying Webhook Signatures

Every request includes an `X-Webhook-Signature` header. Always verify it:

```python
import hmac
import hashlib
import json


def verify_webhook(payload: dict, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"), json.dumps(payload, sort_keys=True).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature == f"sha256={expected}"
```

!!! warning "Always verify signatures"
    Without verification, anyone who discovers your webhook URL can send fake events.

## Integration Examples

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
    payload = request.get_json()
    event = payload.get("event")

    if event == "email.smtp.inbound":
        sender = payload["payload"]["metadata"]["from"]
        subject = payload["payload"]["metadata"]["subject"]

        # Create or update contact in HubSpot
        requests.post(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers={
                "Authorization": f"Bearer {HUBSPOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"properties": {"email": sender, "last_email_subject": subject}},
        )

    return jsonify({"status": "ok"})
```

### Ticketing System (Jira / Linear)

Auto-create tickets from incoming support emails:

```python
@app.route("/webhooks/mailyte", methods=["POST"])
def handle_support_email():
    payload = request.get_json()

    if payload.get("event") != "email.smtp.inbound":
        return jsonify({"status": "skipped"})

    meta = payload["payload"]["metadata"]
    to_address = meta["to"]

    # Only process emails to support@
    if not to_address.startswith("support@"):
        return jsonify({"status": "skipped"})

    # Create Jira ticket
    requests.post(
        "https://your-org.atlassian.net/rest/api/3/issue",
        auth=("email@company.com", "JIRA_API_TOKEN"),
        json={
            "fields": {
                "project": {"key": "SUP"},
                "summary": meta["subject"],
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": f"Email from {meta['from']}"}],
                        }
                    ],
                },
                "issuetype": {"name": "Task"},
            }
        },
    )

    return jsonify({"status": "created"})
```

### Slack Notifications

Post to a Slack channel when specific events happen:

```python
import requests

SLACK_WEBHOOK = "https://hooks.slack.com/services/T.../B.../xxx"


@app.route("/webhooks/mailyte", methods=["POST"])
def slack_notify():
    payload = request.get_json()
    event = payload.get("event")
    meta = payload.get("payload", {}).get("metadata", {})

    messages = {
        "email.smtp.inbound": f"New email from {meta.get('from', 'unknown')}: {meta.get('subject', '(no subject)')}",
        "email.smtp.outbound": f"Email sent to {meta.get('to', 'unknown')}: {meta.get('subject', '(no subject)')}",
    }

    msg = messages.get(event)
    if msg:
        requests.post(SLACK_WEBHOOK, json={"text": msg})

    return jsonify({"status": "ok"})
```

## API Automation Examples

### Bulk Provisioning

Create domains and mailboxes for new customers automatically:

```python
import requests

API = "http://mail.yourdomain.com:8083/api/v1"
HEADERS = {"X-API-Key": "YOUR_API_KEY", "Content-Type": "application/json"}


def provision_customer(org_id: str, org_name: str, domain: str, admin_email: str):
    """Set up everything a new customer needs."""

    # 1. Create organization
    requests.post(
        f"{API}/add/organization",
        headers=HEADERS,
        json={"id": org_id, "name": org_name, "admin_email": admin_email},
    )

    # 2. Add domain
    requests.post(
        f"{API}/add/domain",
        headers=HEADERS,
        json={
            "domain": domain,
            "organization_id": org_id,
            "mailboxes": 50,
            "aliases": 200,
        },
    )

    # 3. Generate DKIM
    requests.post(
        f"{API}/add/dkim",
        headers=HEADERS,
        json={"domains": domain, "dkim_selector": "default", "key_size": "2048"},
    )

    # 4. Create admin mailbox
    local_part = admin_email.split("@")[0]
    requests.post(
        f"{API}/add/mailbox",
        headers=HEADERS,
        json={
            "local_part": local_part,
            "domain": domain,
            "password": generate_temp_password(),
            "name": "Admin",
            "force_pw_update": 1,
        },
    )

    # 5. Set up catch-all alias
    requests.post(
        f"{API}/add/alias",
        headers=HEADERS,
        json={"address": f"@{domain}", "goto": admin_email, "active": 1},
    )

    # 6. Get DKIM public key for DNS instructions
    dkim = requests.get(f"{API}/get/dkim/{domain}", headers=HEADERS).json()

    return {
        "org_id": org_id,
        "domain": domain,
        "dkim_record": dkim,
        "instructions": f"Add these DNS records for {domain}...",
    }
```

### Usage Reporting

Pull stats for billing or dashboards:

```python
def get_org_usage(org_id: str) -> dict:
    """Get usage stats for an organization."""

    stats = requests.get(
        f"{API}/get/status/stats", headers=HEADERS, params={"organization_id": org_id}
    ).json()

    domains = requests.get(
        f"{API}/get/domain/all", headers=HEADERS, params={"organization_id": org_id}
    ).json()

    total_storage = sum(d.get("total_storage_used", 0) for d in domains)
    total_mailboxes = sum(d.get("total_email_accounts", 0) for d in domains)

    return {
        "organization_id": org_id,
        "total_domains": len(domains),
        "total_mailboxes": total_mailboxes,
        "total_storage_bytes": total_storage,
        "total_storage_gb": round(total_storage / (1024**3), 2),
    }
```

### Scheduled Maintenance

Automate common maintenance tasks:

```python
def cleanup_old_tracking_data(days: int = 90):
    """Remove tracking data older than N days."""
    requests.post(
        f"{API}/admin/cleanup",
        headers=HEADERS,
        json={"target": "tracking_data", "older_than_days": days},
    )


def rotate_dkim_keys(domain: str, new_selector: str):
    """Generate new DKIM keys with a new selector."""
    requests.post(
        f"{API}/add/dkim",
        headers=HEADERS,
        json={"domains": domain, "dkim_selector": new_selector, "key_size": "2048"},
    )


def check_domain_health(domain: str) -> dict:
    """Verify DNS and authentication for a domain."""
    return requests.get(f"{API}/get/domain/health/{domain}", headers=HEADERS).json()
```

## Webhook Reliability Tips

1. **Respond quickly** — return 200 within 5 seconds, process async
2. **Idempotency** — webhooks may fire twice, use `message_id` to deduplicate
3. **Queue internally** — push webhooks into your own queue (Redis, RabbitMQ) for processing
4. **Monitor failures** — check the webhook delivery logs via the API
5. **Set up retries** — Mailyte retries failed deliveries with exponential backoff

```bash
# Check webhook delivery status
curl http://mail.yourdomain.com:8081/webhook/status
```
