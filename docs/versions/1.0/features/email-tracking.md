
# Email Tracking

The Mailyte Mail Server provides comprehensive email tracking capabilities that allow you to monitor email opens, link clicks, and delivery status in real-time. This feature is essential for marketing campaigns, transactional emails, and understanding email engagement.

## Overview

Email tracking works by:

1. **Open Tracking**: Injecting invisible tracking pixels into emails
2. **Link Tracking**: Rewriting URLs to track clicks through our service
3. **Delivery Tracking**: Monitoring SMTP delivery status and bounces
4. **Real-time Analytics**: Providing instant feedback on email performance

## Features

### 📊 **Open Tracking**
- Invisible 1x1 pixel tracking
- Multiple open detection
- Time-based open analytics
- Client/device detection

### 🔗 **Link Tracking** 
- Automatic URL rewriting
- Individual link performance
- Geographic tracking
- Click attribution

### 📈 **Delivery Analytics**
- Real-time delivery status
- Bounce categorization
- Spam complaint tracking
- Suppression list management

### 🔔 **Real-time Notifications**
- Webhook events for all tracking
- Instant delivery notifications
- Custom event handlers
- Batch event processing

## Configuration

### Enable Tracking

Email tracking can be enabled globally or per-domain:

=== "Global Configuration"
    
    ```bash
    # In .env file
    EMAIL_TRACKING_ENABLED=true
    TRACKING_PIXEL_ENABLED=true
    LINK_TRACKING_ENABLED=true
    TRACKING_DOMAIN=track.yourdomain.com
    ```

=== "Per-Domain via API"
    
    ```bash
    curl -X PUT "https://mail.yourdomain.com/api/v1/domains/example.com" \
      -H "Authorization: Bearer YOUR_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{
        "tracking_settings": {
          "open_tracking": true,
          "click_tracking": true,
          "delivery_tracking": true
        }
      }'
    ```

=== "Per-Email Header"
    
    ```bash
    # Add tracking headers to individual emails
    X-Tracking-Opens: yes
    X-Tracking-Clicks: yes
    X-Tracking-Delivery: yes
    ```

### Tracking Domain Setup

For optimal deliverability and branding, set up a dedicated tracking domain:

#### 1. DNS Configuration

```dns
# CNAME record for tracking domain
track.yourdomain.com.    3600    IN    CNAME    mail.yourdomain.com.

# SSL certificate for tracking domain
# (automatically handled by cert manager)
```

#### 2. SSL Certificate

The certificate manager automatically generates SSL certificates for tracking domains:

```bash
# Verify SSL certificate
curl -I https://track.yourdomain.com/health
```

#### 3. Configuration Update

```bash
# Update tracking domain in configuration
TRACKING_DOMAIN=track.yourdomain.com
TRACKING_SSL_ENABLED=true
```

## Implementation

### Automatic Tracking Injection

When tracking is enabled, the mail server automatically modifies outgoing emails:

#### Original Email
```html
<!DOCTYPE html>
<html>
<head>
    <title>Welcome Email</title>
</head>
<body>
    <h1>Welcome to Our Service!</h1>
    <p>Thank you for signing up. <a href="https://example.com/dashboard">Visit your dashboard</a></p>
    <p>If you have questions, <a href="mailto:support@example.com">contact support</a></p>
</body>
</html>
```

#### Modified Email (with tracking)
```html
<!DOCTYPE html>
<html>
<head>
    <title>Welcome Email</title>
</head>
<body>
    <h1>Welcome to Our Service!</h1>
    <p>Thank you for signing up. <a href="https://track.yourdomain.com/click/abc123def456?url=https%3A//example.com/dashboard">Visit your dashboard</a></p>
    <p>If you have questions, <a href="mailto:support@example.com">contact support</a></p>
    
    <!-- Tracking pixel (invisible) -->
    <img src="https://track.yourdomain.com/open/abc123def456.gif" width="1" height="1" style="display:none;" alt="">
</body>
</html>
```

### Manual Tracking Integration

For custom implementations, you can manually add tracking:

=== "API Integration"
    
    ```python
    import requests
    
    # Send email with tracking
    response = requests.post(
        'https://mail.yourdomain.com/api/v1/emails',
        headers={
            'Authorization': 'Bearer YOUR_API_KEY',
            'Content-Type': 'application/json'
        },
        json={
            'from': 'sender@yourdomain.com',
            'to': ['recipient@example.com'],
            'subject': 'Test Email',
            'html': '<p>Hello World! <a href="https://example.com">Click here</a></p>',
            'tracking': {
                'opens': True,
                'clicks': True,
                'delivery': True
            }
        }
    )
    
    # Get tracking ID from response
    tracking_id = response.json()['tracking_id']
    print(f"Email sent with tracking ID: {tracking_id}")
    ```

=== "SMTP Headers"
    
    ```python
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    # Create message with tracking headers
    msg = MIMEMultipart('alternative')
    msg['From'] = 'sender@yourdomain.com'
    msg['To'] = 'recipient@example.com'
    msg['Subject'] = 'Test Email'
    
    # Add tracking headers
    msg['X-Tracking-Opens'] = 'yes'
    msg['X-Tracking-Clicks'] = 'yes'
    msg['X-Tracking-ID'] = 'custom-tracking-id-123'
    
    # Email content
    html = """
    <html>
    <body>
        <p>Hello World!</p>
        <a href="https://example.com">Click here</a>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(html, 'html'))
    
    # Send via SMTP
    with smtplib.SMTP('mail.yourdomain.com', 587) as server:
        server.starttls()
        server.login('username', 'password')
        server.send_message(msg)
    ```

## Tracking Data Retrieval

### Real-time Tracking Events

Access tracking data through the API:

```bash
# Get tracking events for a specific email
curl -X GET "https://mail.yourdomain.com/api/v1/tracking/events/MESSAGE_ID" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Response:
```json
{
  "message_id": "msg_abc123def456",
  "tracking_id": "track_xyz789",
  "recipient": "user@example.com",
  "events": [
    {
      "event": "sent",
      "timestamp": "2024-01-15T10:30:00Z",
      "details": {
        "smtp_response": "250 Message accepted"
      }
    },
    {
      "event": "delivered",
      "timestamp": "2024-01-15T10:30:15Z",
      "details": {
        "server": "gmail-smtp-in.l.google.com",
        "response": "250 OK"
      }
    },
    {
      "event": "opened",
      "timestamp": "2024-01-15T10:45:30Z",
      "details": {
        "ip_address": "192.168.1.100",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "location": {
          "country": "US",
          "region": "California",
          "city": "San Francisco"
        }
      }
    },
    {
      "event": "clicked",
      "timestamp": "2024-01-15T10:46:00Z",
      "details": {
        "url": "https://example.com/dashboard",
        "link_id": "link_001",
        "ip_address": "192.168.1.100",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      }
    }
  ]
}
```

### Bulk Tracking Data

Retrieve tracking data in bulk for analytics:

```bash
# Get tracking summary for date range
curl -X GET "https://mail.yourdomain.com/api/v1/tracking/summary?start_date=2024-01-01&end_date=2024-01-31" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Response:
```json
{
  "period": {
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-01-31T23:59:59Z"
  },
  "summary": {
    "emails_sent": 50000,
    "emails_delivered": 48500,
    "emails_opened": 15000,
    "unique_opens": 12000,
    "total_clicks": 3500,
    "unique_clicks": 2800,
    "bounces": 1000,
    "complaints": 50,
    "unsubscribes": 100
  },
  "rates": {
    "delivery_rate": 97.0,
    "open_rate": 30.9,
    "click_rate": 7.2,
    "bounce_rate": 2.0,
    "complaint_rate": 0.1
  }
}
```

## Webhook Integration

Real-time tracking events can be sent to your application via webhooks:

### Webhook Configuration

```bash
# Configure webhook endpoint
curl -X POST "https://mail.yourdomain.com/api/v1/webhooks" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-app.com/webhooks/email-tracking",
    "events": [
      "email.sent",
      "email.delivered", 
      "email.opened",
      "email.clicked",
      "email.bounced",
      "email.complained"
    ],
    "secret": "your_webhook_secret"
  }'
```

### Webhook Event Examples

=== "Email Opened"
    
    ```json
    {
      "event": "email.opened",
      "timestamp": "2024-01-15T10:45:30Z",
      "data": {
        "message_id": "msg_abc123def456",
        "tracking_id": "track_xyz789",
        "recipient": "user@example.com",
        "sender": "newsletter@yourdomain.com",
        "subject": "Weekly Newsletter",
        "open_count": 1,
        "client_info": {
          "ip_address": "192.168.1.100",
          "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)",
          "email_client": "Apple Mail",
          "device_type": "mobile"
        },
        "location": {
          "country": "US",
          "region": "California", 
          "city": "San Francisco",
          "timezone": "America/Los_Angeles"
        }
      }
    }
    ```

=== "Link Clicked"
    
    ```json
    {
      "event": "email.clicked",
      "timestamp": "2024-01-15T10:46:00Z",
      "data": {
        "message_id": "msg_abc123def456",
        "tracking_id": "track_xyz789",
        "recipient": "user@example.com",
        "sender": "newsletter@yourdomain.com",
        "subject": "Weekly Newsletter",
        "link": {
          "url": "https://example.com/special-offer",
          "link_id": "link_003",
          "link_text": "Claim Your Discount",
          "position": 2
        },
        "click_count": 1,
        "client_info": {
          "ip_address": "192.168.1.100",
          "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)",
          "referrer": "webmail.gmail.com"
        }
      }
    }
    ```

=== "Email Bounced"
    
    ```json
    {
      "event": "email.bounced",
      "timestamp": "2024-01-15T10:30:45Z",
      "data": {
        "message_id": "msg_abc123def456",
        "recipient": "invalid@example.com",
        "sender": "notification@yourdomain.com",
        "subject": "Account Verification",
        "bounce_type": "hard",
        "bounce_reason": "mailbox_not_found",
        "smtp_response": "550 5.1.1 User unknown",
        "server_response": "The email account that you tried to reach does not exist"
      }
    }
    ```

### Webhook Security

Verify webhook authenticity using HMAC signatures:

```python
import hmac
import hashlib
import json

def verify_webhook(payload, signature, secret):
    """
    Verify webhook signature
    """
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(
        f"sha256={expected_signature}",
        signature
    )

# Example usage
webhook_secret = "your_webhook_secret"
webhook_payload = request.body
webhook_signature = request.headers.get('X-Webhook-Signature')

if verify_webhook(webhook_payload, webhook_signature, webhook_secret):
    # Process webhook data
    data = json.loads(webhook_payload)
    handle_tracking_event(data)
else:
    # Invalid signature - reject webhook
    return "Invalid signature", 401
```

## Analytics and Reporting

### Dashboard Metrics

The Mailyte Mail Server provides a comprehensive tracking dashboard:

```bash
# Access tracking dashboard
https://mail.yourdomain.com/dashboard/tracking
```

Key metrics include:

- **Delivery Rates**: Successful delivery percentage
- **Open Rates**: Email open percentages
- **Click-through Rates**: Link click percentages
- **Geographic Distribution**: Where emails are being opened
- **Device/Client Analysis**: Which email clients are used
- **Time-based Analytics**: When emails are opened/clicked

### Custom Reports

Generate custom tracking reports via API:

```bash
# Generate custom report
curl -X POST "https://mail.yourdomain.com/api/v1/tracking/reports" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "report_type": "campaign_performance",
    "filters": {
      "date_range": {
        "start": "2024-01-01",
        "end": "2024-01-31"
      },
      "sender": "newsletter@yourdomain.com",
      "domain": "yourdomain.com"
    },
    "metrics": [
      "delivery_rate",
      "open_rate", 
      "click_rate",
      "bounce_rate",
      "geographic_breakdown",
      "device_breakdown"
    ],
    "format": "json"
  }'
```

### Export Data

Export tracking data for external analysis:

```bash
# Export tracking data as CSV
curl -X GET "https://mail.yourdomain.com/api/v1/tracking/export?format=csv&start_date=2024-01-01&end_date=2024-01-31" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -o tracking_data.csv
```

## Privacy and Compliance

### GDPR Compliance

The tracking system is designed with privacy in mind:

#### Data Minimization
- Only collect necessary tracking data
- Automatic data purging based on retention policies
- Opt-out mechanisms for recipients

#### User Rights
```bash
# Delete tracking data for a specific user
curl -X DELETE "https://mail.yourdomain.com/api/v1/tracking/data/user@example.com" \
  -H "Authorization: Bearer YOUR_API_KEY"

# Export user's tracking data
curl -X GET "https://mail.yourdomain.com/api/v1/tracking/data/user@example.com" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Configuration Options
```bash
# Privacy-focused configuration
TRACKING_ANONYMIZE_IP=true
TRACKING_RESPECT_DNT=true
TRACKING_RETENTION_DAYS=365
TRACKING_GDPR_MODE=true
```

### Opt-out Management

Implement tracking opt-out functionality:

=== "API Method"
    
    ```bash
    # Add user to tracking opt-out list
    curl -X POST "https://mail.yourdomain.com/api/v1/tracking/opt-out" \
      -H "Authorization: Bearer YOUR_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{
        "email": "user@example.com",
        "opt_out_types": ["opens", "clicks"]
      }'
    ```

=== "Email Header Method"
    
    ```
    # Recipients can opt out by adding header to their email client
    X-No-Tracking: true
    ```

=== "Unsubscribe Link Method"
    
    ```html
    <!-- Add opt-out link to emails -->
    <p>
      <a href="https://track.yourdomain.com/opt-out/{{tracking_id}}">
        Disable email tracking
      </a>
    </p>
    ```

## Performance Optimization

### Caching Strategy

Optimize tracking performance with intelligent caching:

```bash
# Cache configuration
TRACKING_CACHE_TTL=3600
TRACKING_CACHE_SIZE=1000000
TRACKING_BATCH_SIZE=100
TRACKING_ASYNC_PROCESSING=true
```

### Database Optimization

Efficient tracking data storage:

```sql
-- Optimized tracking table indexes
CREATE INDEX idx_tracking_events_message_id ON tracking_events(message_id);
CREATE INDEX idx_tracking_events_recipient ON tracking_events(recipient);
CREATE INDEX idx_tracking_events_timestamp ON tracking_events(created_at);
CREATE INDEX idx_tracking_events_event_type ON tracking_events(event_type);

-- Partitioning for large datasets
ALTER TABLE tracking_events PARTITION BY RANGE (YEAR(created_at));
```

### CDN Integration

Serve tracking pixels and redirects through CDN:

```bash
# CDN configuration for tracking domain
TRACKING_CDN_ENABLED=true
TRACKING_CDN_URL=https://cdn.yourdomain.com
TRACKING_CACHE_CONTROL="public, max-age=3600"
```

## Troubleshooting

### Common Issues

??? failure "Tracking Pixel Not Loading"
    
    **Symptoms**: Open tracking not working
    
    **Causes**:
    - Image blocking in email client
    - SSL certificate issues
    - DNS resolution problems
    
    **Solutions**:
    ```bash
    # Check SSL certificate
    curl -I https://track.yourdomain.com/health
    
    # Verify DNS resolution
    dig track.yourdomain.com
    
    # Test pixel endpoint
    curl -v "https://track.yourdomain.com/open/test123.gif"
    ```

??? failure "Link Tracking Redirects Failing"
    
    **Symptoms**: Click tracking not working
    
    **Causes**:
    - URL encoding issues
    - Redirect service down
    - Rate limiting
    
    **Solutions**:
    ```bash
    # Test redirect service
    curl -v "https://track.yourdomain.com/click/test123?url=https%3A//example.com"
    
    # Check service logs
    tail -f /var/log/mailserver/tracking.log
    
    # Verify rate limits
    curl -X GET "https://mail.yourdomain.com/api/v1/tracking/limits" \
      -H "Authorization: Bearer YOUR_API_KEY"
    ```

??? failure "High Tracking Latency"
    
    **Symptoms**: Slow tracking response times
    
    **Solutions**:
    ```bash
    # Enable async processing
    TRACKING_ASYNC_PROCESSING=true
    
    # Increase worker processes
    TRACKING_WORKER_PROCESSES=8
    
    # Optimize database
    TRACKING_BATCH_INSERT=true
    TRACKING_BATCH_SIZE=500
    ```

### Monitoring and Alerts

Set up monitoring for tracking services:

```bash
# Health check endpoint
curl -X GET "https://mail.yourdomain.com/api/v1/tracking/health"

# Performance metrics
curl -X GET "https://mail.yourdomain.com/api/v1/tracking/metrics"
```

Configure alerts for:
- High tracking latency
- Failed pixel requests
- Redirect errors
- Database connection issues

---

!!! success "Tracking Enabled"
    Your email tracking system is now configured and ready to provide detailed insights into email engagement. Monitor your tracking dashboard regularly and use the data to optimize your email campaigns for better performance.
