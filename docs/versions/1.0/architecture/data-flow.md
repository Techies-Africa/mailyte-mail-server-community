
# Data Flow Architecture

This document provides a comprehensive overview of how data flows through the Enterprise Mail Server system, covering all major processes and interactions.

## Overview

The Enterprise Mail Server processes data through multiple interconnected flows:
1. **Email Processing Flow**: Handling inbound and outbound emails
2. **Authentication Flow**: User and API authentication
3. **Tracking Flow**: Email open and click tracking
4. **Webhook Flow**: Real-time event notifications
5. **Analytics Flow**: Data collection and reporting
6. **Storage Flow**: Data persistence and cloud synchronization

## Inbound Email Processing Flow

```mermaid
sequenceDiagram
    participant ES as External SMTP
    participant PF as Postfix
    participant RL as Rate Limiter
    participant RS as Rspamd
    participant DC as Dovecot
    participant FS as File System
    participant CS as Cloud Sync
    participant WH as Webhook Service
    participant TR as Tracking Service
    participant RG as RAG Service
    participant DB as MySQL Database

    ES->>PF: SMTP Connection (Port 25)
    PF->>RL: Rate Limit Check
    RL->>DB: Query Usage Limits
    DB-->>RL: Return Limits
    RL-->>PF: PERMIT/REJECT
    
    alt Rate Limit OK
        PF->>RS: Content Filter Check
        RS->>RS: Spam Analysis
        RS-->>PF: Spam Score
        
        alt Not Spam
            PF->>DC: Deliver to Mailbox
            DC->>FS: Store Email File
            FS->>CS: Trigger Cloud Sync
            CS->>CS: Upload to Cloud Storage
            
            PF->>WH: Trigger Webhook
            WH->>WH: Process Email Data
            WH-->>External: HTTP Webhook POST
            
            PF->>TR: Record Tracking Data
            TR->>DB: Store Tracking Info
            
            DC->>RG: Index Email Content
            RG->>RG: Generate Embeddings
            RG->>Vector DB: Store Vectors
        end
    end
```

### Detailed Process Steps

#### 1. SMTP Reception (Postfix)
```python
# Postfix policy daemon interaction
{
    "request": "smtpd_access_policy",
    "protocol_state": "RCPT",
    "protocol_name": "SMTP",
    "client_address": "203.0.113.1",
    "client_name": "mail.external.com",
    "reverse_client_name": "mail.external.com",
    "helo_name": "mail.external.com",
    "sender": "sender@external.com",
    "recipient": "user@yourdomain.com",
    "recipient_count": "1",
    "queue_id": "A1B2C3D4E5",
    "instance": "postfix/smtpd[1234]",
}
```

#### 2. Rate Limiting Check
```sql
-- Rate limiter queries
SELECT 
    hourly_limit, 
    daily_limit, 
    monthly_limit,
    current_hourly_count,
    current_daily_count,
    current_monthly_count
FROM rate_limits 
WHERE identifier = 'sender@external.com' 
  AND type = 'inbound';
```

#### 3. Content Filtering (Rspamd)
```json
{
    "spam_score": 2.1,
    "required_score": 15.0,
    "action": "no action",
    "symbols": {
        "DKIM_SIGNED": -0.1,
        "SPF_PASS": -0.1,
        "RCVD_IN_DNSWL_LOW": -0.1
    }
}
```

#### 4. Email Storage
```bash
# File system structure
/var/mail/vhosts/yourdomain.com/user/
├── cur/           # Current messages
├── new/           # New messages
├── tmp/           # Temporary files
└── dovecot.index* # Index files
```

#### 5. Webhook Notification
```json
{
    "event": "email.smtp.inbound",
    "timestamp": "2024-01-15T10:30:00Z",
    "payload": {
        "direction": "inbound",
        "protocol": "smtp",
        "metadata": {
            "message_id": "<msg@external.com>",
            "subject": "Test Email",
            "from": "sender@external.com",
            "to": "user@yourdomain.com",
            "size": 12345,
            "has_attachments": true
        },
        "delivery_info": {
            "recipient": "user@yourdomain.com",
            "sender": "sender@external.com",
            "client_ip": "203.0.113.1",
            "delivery_status": "received"
        }
    },
    "eml_file": {
        "content_base64": "encoded_content",
        "size": 12345
    }
}
```

## Outbound Email Processing Flow

```mermaid
sequenceDiagram
    participant EC as Email Client
    participant DC as Dovecot
    participant PF as Postfix
    participant RL as Rate Limiter
    participant TI as Tracking Injector
    participant ES as External SMTP
    participant WH as Webhook Service
    participant TR as Tracking Service
    participant DB as MySQL Database

    EC->>DC: IMAP/POP3 Auth (Port 993/995)
    DC->>DB: Validate Credentials
    DB-->>DC: Auth Result
    
    EC->>PF: SMTP Submission (Port 587)
    PF->>DC: SASL Authentication
    DC-->>PF: Auth Success
    
    PF->>RL: Rate Limit Check
    RL->>DB: Query Usage Limits
    DB-->>RL: Return Current Usage
    RL-->>PF: PERMIT/REJECT
    
    alt Rate Limit OK
        PF->>TI: Inject Tracking
        TI->>TI: Rewrite Links
        TI->>TI: Add Tracking Pixel
        TI-->>PF: Modified Email
        
        PF->>ES: Deliver to External SMTP
        ES-->>PF: Delivery Status
        
        PF->>WH: Trigger Webhook
        WH->>WH: Process Send Event
        WH-->>External: HTTP Webhook POST
        
        PF->>TR: Record Tracking Data
        TR->>DB: Store Tracking Info
        
        PF->>RL: Update Usage Counters
        RL->>DB: Increment Counters
    end
```

### Tracking Injection Process

#### Link Rewriting
```python
# Original link
original_url = "https://example.com/page"

# Rewritten tracking link
tracking_url = f"https://track.yourdomain.com/c/{tracking_id}"
```

#### Pixel Injection
```html
<!-- Tracking pixel inserted before </body> tag -->
<img src="https://track.yourdomain.com/o/{{tracking_id}}" 
     width="1" height="1" style="display:none;" alt="" />
```

## IMAP/POP3 Access Flow

```mermaid
sequenceDiagram
    participant EC as Email Client
    participant DC as Dovecot
    participant FS as File System
    participant CS as Cloud Sync
    participant WH as Webhook Service
    participant TR as Tracking Service
    participant DB as MySQL Database

    EC->>DC: IMAP Connect (Port 993)
    DC->>DB: Authenticate User
    DB-->>DC: User Data
    
    EC->>DC: SELECT INBOX
    DC->>FS: Check Local Storage
    
    alt Email Not Local
        DC->>CS: Request from Cloud
        CS->>CS: Download from Cloud Storage
        CS-->>DC: Email Content
        DC->>FS: Cache Locally
    end
    
    DC-->>EC: Mailbox Contents
    
    EC->>DC: FETCH Email
    DC->>FS: Read Email File
    FS-->>DC: Email Data
    DC-->>EC: Email Content
    
    DC->>WH: Trigger Read Event
    WH->>WH: Process IMAP Event
    WH-->>External: HTTP Webhook POST
    
    DC->>TR: Record Read Event
    TR->>DB: Update Read Status
```

## API Request Flow

```mermaid
sequenceDiagram
    participant CL as API Client
    participant AG as API Gateway
    participant AU as Auth Service
    participant MS as Microservice
    participant DB as MySQL Database
    participant WH as Webhook Service

    CL->>AG: API Request + API Key
    AG->>AU: Validate API Key
    AU->>DB: Check API Key
    DB-->>AU: Key Details
    AU-->>AG: Auth Result
    
    alt Auth Success
        AG->>MS: Route Request
        MS->>DB: Execute Query
        DB-->>MS: Query Result
        MS->>WH: Trigger Event Webhook
        MS-->>AG: Response Data
        AG-->>CL: JSON Response
    else Auth Failure
        AG-->>CL: 401 Unauthorized
    end
```

### API Authentication Flow
```python
# API key validation
api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
query = """
    SELECT id, permissions, rate_limit, active 
    FROM api_keys 
    WHERE key_hash = %s AND active = 1
"""
result = db.execute(query, (api_key_hash,))
```

## Tracking Data Flow

### Open Tracking
```mermaid
sequenceDiagram
    participant ER as Email Recipient
    participant EC as Email Client
    participant TR as Tracking Service
    participant DB as MySQL Database
    participant WH as Webhook Service

    EC->>TR: GET /o/{tracking_id}
    TR->>DB: Record Open Event
    TR->>TR: Generate 1x1 Pixel
    TR-->>EC: PNG Image Response
    
    TR->>WH: Trigger Open Webhook
    WH-->>External: Open Event Notification
```

### Click Tracking
```mermaid
sequenceDiagram
    participant ER as Email Recipient
    participant EC as Email Client
    participant TR as Tracking Service
    participant DB as MySQL Database
    participant WH as Webhook Service
    participant TU as Target URL

    EC->>TR: GET /c/{tracking_id}
    TR->>DB: Record Click Event
    TR->>DB: Get Original URL
    DB-->>TR: Original URL
    
    TR->>WH: Trigger Click Webhook
    WH-->>External: Click Event Notification
    
    TR-->>EC: 302 Redirect
    EC->>TU: Follow Redirect
```

## Storage and Backup Flow

```mermaid
sequenceDiagram
    participant FS as File System
    participant CS as Cloud Sync
    participant S3 as AWS S3/Azure
    participant BK as Backup Service
    participant DB as MySQL Database

    FS->>CS: File Change Event
    CS->>CS: Compress & Encrypt
    CS->>S3: Upload to Cloud
    S3-->>CS: Upload Confirmation
    CS->>DB: Update Sync Status
    
    BK->>DB: Backup Database
    BK->>S3: Upload DB Backup
    BK->>DB: Update Backup Log
```

## Error Handling Flow

```mermaid
sequenceDiagram
    participant SV as Service
    participant EH as Error Handler
    participant LG as Logger
    participant AL as Alerting
    participant WH as Webhook Service

    SV->>EH: Exception/Error
    EH->>LG: Log Error Details
    EH->>AL: Check Alert Thresholds
    
    alt Critical Error
        AL->>WH: Send Alert Webhook
        WH-->>External: Alert Notification
    end
    
    EH->>EH: Apply Recovery Strategy
    EH-->>SV: Recovery Action
```

## Performance Monitoring Flow

```mermaid
sequenceDiagram
    participant SV as Services
    participant HM as Health Monitor
    participant DB as Database
    participant AL as Analytics
    participant DS as Dashboard

    loop Every 30 seconds
        HM->>SV: Health Check
        SV-->>HM: Status + Metrics
        HM->>DB: Store Metrics
    end
    
    HM->>AL: Process Metrics
    AL->>AL: Generate Reports
    AL->>DS: Update Dashboard
    
    alt Threshold Breach
        HM->>WH: Send Alert
        WH-->>External: Alert Notification
    end
```

This comprehensive data flow documentation provides developers with a complete understanding of how data moves through the system, enabling them to build upon and extend the existing architecture effectively.
