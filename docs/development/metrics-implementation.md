---
title: Metrics Implementation
description: Implementing new metrics with shared/metrics.py — counters, gauges, histograms, labels, and best practices.
---

# Metrics Implementation

This is the nuts-and-bolts guide to implementing metrics in Mailyte. If you're adding a new metric, start here.

## The Shared Helper, Not prometheus_client

Workers do **not** use the `prometheus_client` library directly (the one exception is `worker/queue_manager/metrics.py`). The standard path is `shared/metrics.py`, which every worker's `/metrics` endpoint is built on:

```python
from shared.metrics import get_metrics

metrics = get_metrics("my_service")  # one instance per service name, cached
```

`get_metrics()` returns a `PrometheusMetrics` instance that collects counters, gauges, and histograms in-process and renders them in Prometheus exposition format via `get_prometheus_metrics()`. It also emits some series for free:

- `{service}_info`, `{service}_uptime_seconds`
- `{service}_cpu_usage_percent`, `{service}_memory_usage_percent`, `{service}_memory_usage_bytes` (via `psutil` — which is why every worker's requirements pin it)

## Metric Types

### Counter

A value that only goes up. Resets to zero when the service restarts.

**Use for:** requests served, emails sent, errors encountered, bytes transferred.

```python
# Increment by 1
metrics.increment_counter("emails_sent_total", labels={"domain": "acme.com"})

# Increment by N
metrics.increment_counter("emails_sent_total", value=5, labels={"domain": "acme.com"})
```

### Gauge

A value that can go up and down.

**Use for:** queue depth, active connections, current usage.

```python
metrics.set_gauge("mail_queue_size", 42, labels={"status": "queued"})
```

### Histogram

Measures the distribution of values (like request duration). The shared helper keeps the last 1000 observations per series and exposes them as a Prometheus **summary**: `_count`, `_sum`, and `0.5`/`0.95`/`0.99` quantiles.

**Use for:** latency, response times, sizes, durations.

```python
metrics.observe_histogram("processing_seconds", duration, labels={"operation": "deliver"})
```

## Built-in Recorders

For the three most common patterns, use the purpose-built methods instead of raw counters — they keep label names consistent across every service:

```python
# HTTP requests — usually wired up once as FastAPI middleware (see below)
metrics.record_request(method="POST", path="/api/v1/domains", status_code=200, duration=0.235)
# -> {service}_http_requests_total, {service}_http_request_duration_seconds,
#    {service}_http_errors_total (for status >= 400)

# Database operations
metrics.record_database_operation(operation="insert_domain", duration=0.012, success=True)
# -> {service}_database_operations_total, {service}_database_operation_duration_seconds

# Webhook deliveries
metrics.record_webhook_delivery(url=url, status_code=200, duration=0.4)
# -> {service}_webhook_deliveries_total, {service}_webhook_delivery_duration_seconds
```

The standard middleware every worker carries:

```python
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    metrics.record_request(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration=time.time() - start_time,
    )
    return response
```

## Exposing /metrics

```python
from fastapi.responses import PlainTextResponse


@app.get("/metrics")
async def prometheus_metrics():
    return PlainTextResponse(metrics.get_prometheus_metrics())
```

Then register the service in `monitoring/prometheus/prometheus.yml` (targets use **container** ports, not host-mapped ports):

```yaml
- job_name: 'my_service'
  metrics_path: /metrics
  static_configs:
    - targets: ['my_service:8094']
```

## Naming Conventions

The helper prefixes every metric with the service name you passed to `get_metrics()`:

```
{service}_{metric}_{unit}
```

| Part | Rules | Examples |
|------|-------|---------|
| Service | Set once via `get_metrics("...")` | `api`, `storage`, `tracking`, `webhook` |
| Metric | What's being measured | `http_requests`, `emails_sent`, `processing_duration` |
| Unit | Standard unit suffix | `_total` (counter), `_seconds` (duration), `_bytes` (size) |

### Unit Suffixes

| Suffix | Meaning | Example |
|--------|---------|---------|
| `_total` | Counter (cumulative) | `storage_http_requests_total` |
| `_seconds` | Duration | `api_http_request_duration_seconds` |
| `_bytes` | Size | `storage_usage_bytes` |
| `_percent` | Percentage (0-100) | `api_cpu_usage_percent` |
| (none) | Gauge of current count | `queue_manager_mail_queue_size` |

## Labels

Labels add dimensions to your metrics. Pass them as a dict; the helper renders them into the series name.

### Good Label Usage

```python
# Good — useful for filtering and aggregation
metrics.increment_counter("emails_total", labels={"direction": "inbound", "status": "delivered"})
```

### Bad Label Usage

```python
# Bad — email address has unbounded cardinality; every unique value is a new
# in-memory series AND a new Prometheus series
metrics.increment_counter("emails_total", labels={"recipient_email": email})

# Bad — timestamps are unique, creates infinite series
metrics.increment_counter("emails_total", labels={"timestamp": now_iso})
```

### Label Rules

1. **Low cardinality** — labels should have a small, bounded set of values
2. **Useful for grouping** — every label should enable useful queries
3. **No user-generated content** — don't use email addresses, message IDs, or subject lines as labels
4. **Consistent naming** — use the same label names across related metrics (the built-in recorders exist for exactly this reason)

Good labels: `organization_id`, `domain`, `status`, `method`, `direction`, `event_type`
Bad labels: `email`, `message_id`, `subject`, `ip_address`, `user_agent`

## Complete Example

A worker instrumenting its processing loop:

```python
from shared.metrics import get_metrics

metrics = get_metrics("my_worker")


class QueueProcessor:
    def process_message(self, message):
        metrics.observe_histogram("message_size_bytes", message.size)

        start = time.time()
        try:
            result = self._deliver(message)
            metrics.increment_counter("processed_total", labels={"status": result.status})
        except Exception as e:
            metrics.increment_counter("errors_total", labels={"error_type": type(e).__name__})
            raise
        finally:
            metrics.observe_histogram("processing_seconds", time.time() - start)

    def update_queue_metrics(self):
        """Called periodically to refresh gauges."""
        for status in ["queued", "sending", "deferred"]:
            count = self.count_by_status(status)
            metrics.set_gauge("mail_queue_size", count, labels={"status": status})
```

## PromQL Queries for Your Metrics

After implementing metrics, you'll query them in Prometheus/Grafana:

```promql
# Request rate (per second)
rate(my_worker_processed_total[5m])

# Error rate as percentage
rate(my_worker_errors_total[5m]) / rate(my_worker_processed_total[5m]) * 100

# p95 processing time (the helper exposes summary quantiles directly)
my_worker_processing_seconds{quantile="0.95"}

# Average message size
rate(my_worker_message_size_bytes_sum[5m]) / rate(my_worker_message_size_bytes_count[5m])
```

Note: because the helper exposes **summaries** (pre-computed quantiles), `histogram_quantile()` does not apply — query the `quantile` label directly.

## Debugging Metrics

```bash
# Check the raw exposition output (host-mapped port; e.g. monitoring on 8085)
curl -s http://localhost:8085/metrics | grep http_requests

# Check via Prometheus
curl -s "http://localhost:9090/api/v1/query?query=api_http_requests_total" | python3 -m json.tool
```

!!! info "Malformed exposition kills the whole scrape"
    Prometheus rejects an entire scrape on a single malformed line (a `TYPE` declared twice, or with labels in the name). `shared/metrics.py`'s emitter handles family grouping correctly — one more reason to go through it instead of hand-rolling exposition text.
