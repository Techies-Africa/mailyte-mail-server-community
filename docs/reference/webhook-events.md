---
title: Webhook Events
description: Every webhook event type with complete payload structure, examples, and delivery details.
---

# Webhook Events

Mailyte sends webhooks for email lifecycle events. Each event is an HTTP POST with a JSON payload and an HMAC signature in the `X-Webhook-Signature` header.

## Common Payload Structure

Every webhook follows this shape:

```json
{
  "event": "event.type.name",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "inbound|outbound|imap|pop3",
    "protocol": "smtp|imap|pop3",
    "metadata": {},
    "delivery_info": {},
    "security": {}
  },
  "attachments": [],
  "eml_file": {}
}
```

## Signature Verification

Every request includes:

```
X-Webhook-Signature: sha256=a1b2c3d4e5f6...
```

Verify it:

```python
import hmac, hashlib, json

def verify(payload: dict, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature == f"sha256={expected}"
```

## SMTP Events

### email.smtp.inbound

Fired when an email arrives via SMTP.

```json
{
  "event": "email.smtp.inbound",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "inbound",
    "protocol": "smtp",
    "server": "postfix",
    "metadata": {
      "message_id": "<unique@example.com>",
      "subject": "Meeting Tomorrow",
      "from": "sender@external.com",
      "to": "recipient@yourdomain.com",
      "cc": "",
      "bcc": "",
      "date": "Tue, 25 Mar 2025 14:30:00 +0000",
      "reply_to": "",
      "return_path": "sender@external.com",
      "content_type": "text/html; charset=UTF-8",
      "size": 12345,
      "has_attachments": true,
      "attachment_count": 1,
      "attachment_names": ["report.pdf"]
    },
    "delivery_info": {
      "recipient": "recipient@yourdomain.com",
      "sender": "sender@external.com",
      "helo": "mail.external.com",
      "client_ip": "203.0.113.1",
      "client_hostname": "mail.external.com",
      "delivery_status": "received"
    },
    "security": {
      "tls_used": true,
      "spf_result": "pass",
      "dkim_result": "pass",
      "dmarc_result": "pass",
      "spam_score": 2.1
    }
  },
  "attachments": [
    {
      "filename": "report.pdf",
      "content_type": "application/pdf",
      "size": 524288,
      "content_base64": "JVBERi0xLjQK..."
    }
  ],
  "eml_file": {
    "content_base64": "UmVjZWl2ZWQ6...",
    "size": 12345
  }
}
```

### email.smtp.outbound

Fired when an email is sent via SMTP.

```json
{
  "event": "email.smtp.outbound",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "outbound",
    "protocol": "smtp",
    "server": "postfix",
    "metadata": {
      "message_id": "<outbound@yourdomain.com>",
      "subject": "Project Update",
      "from": "sender@yourdomain.com",
      "to": "recipient@external.com",
      "size": 8192,
      "has_attachments": false,
      "attachment_count": 0
    },
    "delivery_info": {
      "sender": "sender@yourdomain.com",
      "recipients": ["recipient@external.com"],
      "relay_host": "smtp.external.com",
      "queue_id": "A1B2C3D4E5",
      "delivery_status": "sent",
      "delivery_delay": 150,
      "dsn_status": "2.0.0"
    },
    "security": {
      "tls_used": true,
      "auth_user": "sender@yourdomain.com",
      "encryption_protocol": "TLSv1.3"
    }
  }
}
```

## IMAP Events

### email.imap.login

```json
{
  "event": "email.imap.login",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "imap",
    "protocol": "imap",
    "action": "login",
    "user": "user@yourdomain.com",
    "client_info": {
      "ip_address": "192.168.1.100",
      "user_agent": "Mozilla Thunderbird",
      "connection_id": "conn_abc123"
    }
  }
}
```

### email.imap.read

```json
{
  "event": "email.imap.read",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "imap",
    "protocol": "imap",
    "action": "read",
    "user": "user@yourdomain.com",
    "mailbox": "INBOX",
    "message_uid": "12345",
    "client_info": {
      "ip_address": "192.168.1.100",
      "user_agent": "Mozilla Thunderbird",
      "connection_id": "conn_abc123"
    },
    "message_info": {
      "message_id": "<message@example.com>",
      "subject": "Meeting Tomorrow",
      "flags": ["\\Seen"],
      "size": 4096
    }
  }
}
```

### Other IMAP Actions

All follow the same structure with different `action` values:

| Action | Trigger |
|--------|---------|
| `login` | User authenticated |
| `logout` | Session ended |
| `read` | Message marked as read |
| `delete` | Message deleted |
| `move` | Message moved to another folder |
| `flag` | Message flagged or unflagged |
| `copy` | Message copied |
| `search` | Search performed |

## POP3 Events

### email.pop3.login

```json
{
  "event": "email.pop3.login",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "pop3",
    "protocol": "pop3",
    "action": "login",
    "user": "user@yourdomain.com",
    "client_info": {
      "ip_address": "192.168.1.100",
      "connection_id": "pop3_xyz789"
    }
  }
}
```

### email.pop3.download

```json
{
  "event": "email.pop3.download",
  "timestamp": "2025-03-25T14:30:00Z",
  "payload": {
    "direction": "pop3",
    "protocol": "pop3",
    "action": "download",
    "user": "user@yourdomain.com",
    "client_info": {
      "ip_address": "192.168.1.100",
      "connection_id": "pop3_xyz789"
    },
    "session_info": {
      "messages_downloaded": 5,
      "bytes_downloaded": 51200,
      "session_duration": 45
    }
  }
}
```

### POP3 Actions

| Action | Trigger |
|--------|---------|
| `login` | User authenticated |
| `logout` | Session ended |
| `download` | Messages downloaded |
| `delete` | Messages deleted from server |

## Event Summary Table

| Event | Direction | When it fires |
|-------|-----------|--------------|
| `email.smtp.inbound` | Inbound | Email received via SMTP |
| `email.smtp.outbound` | Outbound | Email sent via SMTP |
| `email.imap.login` | IMAP | User logs in |
| `email.imap.logout` | IMAP | User logs out |
| `email.imap.read` | IMAP | Message marked as read |
| `email.imap.delete` | IMAP | Message deleted |
| `email.imap.move` | IMAP | Message moved |
| `email.imap.flag` | IMAP | Message flagged/unflagged |
| `email.imap.copy` | IMAP | Message copied |
| `email.imap.search` | IMAP | Search performed |
| `email.pop3.login` | POP3 | User logs in |
| `email.pop3.logout` | POP3 | User logs out |
| `email.pop3.download` | POP3 | Messages downloaded |
| `email.pop3.delete` | POP3 | Messages deleted |

## Delivery Behavior

- Webhooks are sent asynchronously — they don't block email processing
- Failed deliveries are retried with exponential backoff (5s, 10s, 20s, ...)
- After `retry_attempts` failures, the delivery is marked as `abandoned`
- Maximum retry delay is configurable per webhook endpoint (default: 3600s)
- Your endpoint should respond with 2xx within 30 seconds
