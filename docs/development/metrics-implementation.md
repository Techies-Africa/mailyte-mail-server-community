---
title: Metrics Implementation
description: Implementing new Prometheus metrics — counters, gauges, histograms, labels, and best practices.
---

# Metrics Implementation

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


This is the nuts-and-bolts guide to implementing Prometheus metrics in Mailyte. If you're adding a new metric, start here.

## Metric Types

Prometheus has four metric types. You'll mostly use three:

### Counter

A value that only goes up. Resets to zero when the service restarts.

**Use for:** requests served, emails sent, errors encountered, bytes transferred.

```python
from prometheus_client import Counter

EMAILS_SENT = Counter(
    "mailyte_emails_sent_total",     # metric name
    "Total emails sent",              # help text
    ["organization_id", "domain"],    # labels
)

# Increment by 1
EMAILS_SENT.labels(organization_id="acme", domain="acme.com").inc()

# Increment by N
EMAILS_SENT.labels(organization_id="acme", domain="acme.com").inc(5)
```

### Gauge

A value that can go up and down.

**Use for:** queue depth, memory usage, active connections, temperature.

```python
from prometheus_client import Gauge

QUEUE_SIZE = Gauge(
    "mailyte_mail_queue_size",
    "Current number of messages in the mail queue",
    ["status"],
)

# Set to a specific value
QUEUE_SIZE.labels(status="queued").set(42)

# Increment / decrement
QUEUE_SIZE.labels(status="processing").inc()
QUEUE_SIZE.labels(status="processing").dec()
```

### Histogram

Measures the distribution of values (like request duration). Automatically creates buckets.

**Use for:** latency, response times, sizes, durations.

```python
from prometheus_client import Histogram

REQUEST_DURATION = Histogram(
    "mailyte_api_request_duration_seconds",
    "API request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Observe a value
REQUEST_DURATION.labels(method="POST", endpoint="/add/domain").observe(0.235)

# Or use as a context manager (times automatically)
with REQUEST_DURATION.labels(method="POST", endpoint="/add/domain").time():
    do_work()
```

A histogram creates three time series:

- `_bucket` — count of observations in each bucket
- `_sum` — sum of all observed values
- `_count` — total number of observations

## Labels

Labels add dimensions to your metrics. Use them to slice data by organization, domain, status, etc.

### Good Label Usage

```python
# Good — useful for filtering and aggregation
Counter("mailyte_emails_total", "Emails", ["direction", "status"])
# -> mailyte_emails_total{direction="inbound", status="delivered"}
# -> mailyte_emails_total{direction="outbound", status="bounced"}
```

### Bad Label Usage

```python
# Bad — email address has infinite cardinality, will explode memory
Counter("mailyte_emails_total", "Emails", ["recipient_email"])

# Bad — timestamp labels are unique, creates infinite series
Counter("mailyte_emails_total", "Emails", ["timestamp"])
```

### Label Rules

1. **Low cardinality** — labels should have a small, bounded set of values
2. **Useful for grouping** — every label should enable useful queries
3. **No user-generated content** — don't use email addresses, message IDs, or subject lines as labels
4. **Consistent naming** — use the same label names across related metrics

Good labels: `organization_id`, `domain`, `status`, `method`, `direction`, `event_type`
Bad labels: `email`, `message_id`, `subject`, `ip_address`, `user_agent`

## Naming Conventions

```
mailyte_{subsystem}_{metric}_{unit}
```

| Part | Rules | Examples |
|------|-------|---------|
| Prefix | Always `mailyte_` | |
| Subsystem | Service or component | `api`, `tracking`, `queue`, `webhook` |
| Metric | What's being measured | `requests`, `emails_sent`, `processing_duration` |
| Unit | Standard unit suffix | `_total` (counter), `_seconds` (duration), `_bytes` (size) |

### Unit Suffixes

| Suffix | Meaning | Example |
|--------|---------|---------|
| `_total` | Counter (cumulative) | `mailyte_emails_sent_total` |
| `_seconds` | Duration | `mailyte_delivery_duration_seconds` |
| `_bytes` | Size | `mailyte_storage_used_bytes` |
| `_ratio` | Ratio (0.0-1.0) | `mailyte_storage_usage_ratio` |
| (none) | Gauge of current count | `mailyte_mail_queue_size` |

## Complete Example

Here's a worker with full metrics instrumentation:

```python
from prometheus_client import Counter, Histogram, Gauge, Info
import time

# Service info
SERVICE_INFO = Info("mailyte_queue_manager", "Queue manager service info")
SERVICE_INFO.info({"version": "1.0.0", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")})

# Counters
MESSAGES_PROCESSED = Counter(
    "mailyte_queue_processed_total",
    "Total messages processed from the queue",
    ["status"],  # sent, bounced, rejected, deferred
)

PROCESSING_ERRORS = Counter(
    "mailyte_queue_errors_total",
    "Total processing errors",
    ["error_type"],
)

# Histograms
PROCESSING_DURATION = Histogram(
    "mailyte_queue_processing_seconds",
    "Time to process each message",
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)

MESSAGE_SIZE = Histogram(
    "mailyte_queue_message_size_bytes",
    "Size of messages being processed",
    buckets=[1024, 10240, 102400, 1048576, 10485760, 52428800],
)

# Gauges
QUEUE_DEPTH = Gauge(
    "mailyte_mail_queue_size",
    "Current queue depth",
    ["status"],
)

ACTIVE_WORKERS = Gauge(
    "mailyte_queue_active_workers",
    "Number of currently active worker threads",
)

OLDEST_MESSAGE_AGE = Gauge(
    "mailyte_queue_oldest_message_age_seconds",
    "Age of the oldest message in the queue",
)


class QueueProcessor:
    def process_message(self, message):
        # Track message size
        MESSAGE_SIZE.observe(message.size)

        # Track active workers
        ACTIVE_WORKERS.inc()
        try:
            with PROCESSING_DURATION.time():
                result = self._deliver(message)

            MESSAGES_PROCESSED.labels(status=result.status).inc()
        except Exception as e:
            PROCESSING_ERRORS.labels(error_type=type(e).__name__).inc()
            raise
        finally:
            ACTIVE_WORKERS.dec()

    def update_queue_metrics(self):
        """Called periodically to update gauge metrics."""
        for status in ["queued", "sending", "deferred"]:
            count = self.db.execute(
                "SELECT COUNT(*) FROM mail_queue WHERE status = %s", (status,)
            )
            QUEUE_DEPTH.labels(status=status).set(count)

        oldest = self.db.execute(
            "SELECT MIN(created_at) FROM mail_queue WHERE status = 'queued'"
        )
        if oldest:
            age = (datetime.now() - oldest).total_seconds()
            OLDEST_MESSAGE_AGE.set(age)
```

## PromQL Queries for Your Metrics

After implementing metrics, you'll query them in Prometheus/Grafana:

```promql
# Request rate (per second)
rate(mailyte_queue_processed_total[5m])

# Error rate as percentage
rate(mailyte_queue_errors_total[5m]) / rate(mailyte_queue_processed_total[5m]) * 100

# 95th percentile processing time
histogram_quantile(0.95, rate(mailyte_queue_processing_seconds_bucket[5m]))

# Average message size
rate(mailyte_queue_message_size_bytes_sum[5m]) / rate(mailyte_queue_message_size_bytes_count[5m])
```

## Debugging Metrics

```bash
# Check that metrics are being emitted
curl -s http://localhost:8085/metrics | grep mailyte_queue

# Check specific metric values
curl -s http://localhost:8085/metrics | grep "mailyte_queue_processed_total"

# Check via Prometheus
curl -s "http://localhost:9090/api/v1/query?query=mailyte_queue_processed_total" | python3 -m json.tool
```
