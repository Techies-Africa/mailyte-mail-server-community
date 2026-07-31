
# Analytics Service

The Analytics Service provides comprehensive email analytics, performance monitoring, and business intelligence for email operations with real-time dashboards and custom reporting.

## Overview

**Port**: 8085  
**Status**: ✅ Production Ready  
**Data Processing**: Real-time and batch analytics  

## Key Features

### Email Analytics
- **Delivery Metrics**: Success/failure rates, bounce analysis
- **Engagement Analytics**: Open rates, click-through rates
- **Geographic Insights**: Location-based email performance
- **Device Analytics**: Email client and device statistics

### Performance Monitoring
- **System Metrics**: Service health and performance
- **Throughput Analysis**: Email volume and processing rates
- **Error Tracking**: Detailed error analysis and trends
- **Resource Usage**: CPU, memory, and storage metrics

### Business Intelligence
- **Custom Dashboards**: Configurable analytics views
- **Automated Reports**: Scheduled report generation
- **Trend Analysis**: Historical data analysis
- **Comparative Analytics**: Period-over-period comparisons

## API Endpoints

### Dashboard Analytics
```
GET /analytics/dashboard/{domain}
GET /analytics/overview
GET /analytics/real-time
```

### Email Metrics
```
GET /analytics/email-volume/{domain}
GET /analytics/engagement/{domain}
GET /analytics/deliverability/{domain}
GET /analytics/metrics/{domain}
```

### Reporting
```
POST /reports/generate
GET /reports/{report_id}
GET /reports/scheduled
POST /reports/scheduled
```

### System Analytics
```
GET /analytics/system/performance
GET /analytics/system/errors
GET /analytics/system/usage
```

## Data Collection

### Email Event Tracking
```python
# Collected metrics
{
    "event_type": "email_sent",
    "timestamp": "2024-01-01T12:00:00Z",
    "domain": "example.com",
    "recipient": "user@domain.com",
    "message_id": "abc123",
    "status": "delivered",
    "delivery_time": 1.2,
    "size_bytes": 2048,
    "tracking_enabled": true,
}
```

### Engagement Metrics
```python
{
    "event_type": "email_opened",
    "tracking_id": "track123",
    "timestamp": "2024-01-01T12:00:00Z",
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "location": {"country": "US", "region": "CA", "city": "San Francisco"},
    "device_type": "mobile",
}
```

## Analytics Dashboard

### Key Performance Indicators
- **Delivery Rate**: Percentage of successfully delivered emails
- **Open Rate**: Email open percentage
- **Click Rate**: Link click percentage
- **Bounce Rate**: Email bounce percentage
- **Spam Rate**: Spam complaint percentage

### Real-time Metrics
```json
{
    "current_hour": {
        "emails_sent": 1250,
        "emails_delivered": 1230,
        "emails_opened": 245,
        "links_clicked": 89,
        "bounces": 15,
        "complaints": 2
    },
    "trends": {
        "delivery_rate": "+2.3%",
        "open_rate": "-0.5%",
        "click_rate": "+1.8%"
    }
}
```

## Report Generation

### Automated Reports
```json
{
    "report_type": "daily_summary",
    "recipients": ["admin@company.com"],
    "format": "pdf",
    "schedule": "0 9 * * *",
    "include_metrics": [
        "delivery_stats",
        "engagement_metrics",
        "top_performing_domains"
    ]
}
```

### Custom Reports
- **Date Range Selection**: Flexible time period analysis
- **Domain Filtering**: Specific domain performance
- **Metric Selection**: Choose relevant metrics
- **Export Formats**: PDF, CSV, JSON

## Data Processing

### Real-time Processing
```python
# Stream processing pipeline
email_events → data_processor → metrics_aggregator → dashboard_updates
```

### Batch Processing
```python
# Daily batch jobs
raw_events → data_cleaner → aggregator → report_generator → storage
```

### Data Retention
- **Real-time Data**: 7 days
- **Hourly Aggregates**: 90 days
- **Daily Aggregates**: 2 years
- **Monthly Aggregates**: 5 years

## Database Schema

### Email Metrics
```sql
CREATE TABLE email_metrics (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    timestamp DATETIME,
    domain VARCHAR(255),
    event_type ENUM('sent', 'delivered', 'opened', 'clicked', 'bounced'),
    count INT,
    INDEX idx_timestamp_domain (timestamp, domain),
    INDEX idx_event_type (event_type)
);
```

### Engagement Analytics
```sql
CREATE TABLE engagement_analytics (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    date DATE,
    domain VARCHAR(255),
    emails_sent INT,
    emails_delivered INT,
    emails_opened INT,
    unique_opens INT,
    links_clicked INT,
    unique_clicks INT,
    UNIQUE KEY unique_date_domain (date, domain)
);
```

## Configuration

### Environment Variables
```bash
ANALYTICS_API_PORT=8085
DATA_RETENTION_DAYS=90
BATCH_PROCESSING_HOUR=2
REAL_TIME_ENABLED=true
REPORT_STORAGE_PATH=/storage/reports
```

### Performance Settings
```bash
BATCH_SIZE=10000
WORKER_THREADS=4
CACHE_SIZE=1000
AGGREGATION_INTERVAL=60
```

## Monitoring

### Service Health
```
GET /analytics/health
GET /analytics/metrics/system
```

### Performance Metrics
- Data processing latency
- Report generation time
- Database query performance
- Cache hit ratio

## Integration

### Webhook Integration
Receives real-time events from all services:

```python
@app.route("/webhook/email-event", methods=["POST"])
def process_email_event():
    event = request.get_json()
    analytics_processor.process_event(event)
    return jsonify({"status": "processed"})
```

### API Integration
External systems can query analytics:

```python
# Get domain performance
response = requests.get(
    f"{ANALYTICS_API}/analytics/dashboard/example.com", headers={"X-API-Key": api_key}
)
```

## Development

### Adding New Metrics
1. Define metric schema
2. Update data collection
3. Create aggregation logic
4. Add to dashboard/reports
5. Update API endpoints

### Testing
```bash
# Run analytics tests
python -m pytest tests/test_analytics.py

# Check real-time metrics
curl http://0.0.0.0:8085/analytics/real-time
```
