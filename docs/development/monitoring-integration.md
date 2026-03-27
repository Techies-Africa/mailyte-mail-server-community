---
title: Monitoring Integration
description: How to add Prometheus metrics to your code and implement custom health checks.
---

# Monitoring Integration

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Every Mailyte service should expose health checks and Prometheus metrics. This guide shows how to add both to your code.

## Health Checks

### Basic Health Check

Every service needs a `/health` endpoint:

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "my-service",
    }
```

### Health Check with Dependency Verification

A proper health check verifies that the service can actually do its job:

```python
@app.get("/health")
async def health():
    checks = {}
    overall_healthy = True

    # Check database
    try:
        db.execute("SELECT 1")
        checks["database"] = "healthy"
    except Exception as e:
        checks["database"] = f"unhealthy: {e}"
        overall_healthy = False

    # Check Redis
    try:
        redis_client.ping()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {e}"
        overall_healthy = False

    # Check worker thread
    checks["worker_thread"] = "alive" if worker.is_alive() else "dead"
    if not worker.is_alive():
        overall_healthy = False

    return {
        "status": "healthy" if overall_healthy else "unhealthy",
        "service": "my-service",
        "checks": checks,
    }
```

### Docker Health Check

Configure Docker to use your health endpoint:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8080/health\")'"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 15s
```

The `start_period` gives the service time to initialize before health checks begin.

## Prometheus Metrics

### Setup

Install the client library:

```bash
pip install prometheus-client
```

Expose the metrics endpoint:

```python
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

### Naming Conventions

All Mailyte metrics should follow this pattern:

```
mailyte_{service}_{metric_name}_{unit}
```

Examples:

- `mailyte_api_requests_total` (counter)
- `mailyte_api_request_duration_seconds` (histogram)
- `mailyte_queue_size` (gauge)
- `mailyte_webhook_delivery_duration_seconds` (histogram)

### Adding Metrics to Existing Code

Here's a real example — adding metrics to an API route:

```python
from prometheus_client import Counter, Histogram
import time

# Define metrics at module level
REQUEST_COUNT = Counter(
    "mailyte_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"],
)

REQUEST_DURATION = Histogram(
    "mailyte_api_request_duration_seconds",
    "API request duration",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Use them in your route
@app.post("/api/v1/add/domain")
async def add_domain(request: DomainRequest):
    start = time.time()
    try:
        result = await create_domain(request)
        REQUEST_COUNT.labels(
            method="POST",
            endpoint="/api/v1/add/domain",
            status="200",
        ).inc()
        return result
    except HTTPException as e:
        REQUEST_COUNT.labels(
            method="POST",
            endpoint="/api/v1/add/domain",
            status=str(e.status_code),
        ).inc()
        raise
    finally:
        REQUEST_DURATION.labels(
            method="POST",
            endpoint="/api/v1/add/domain",
        ).observe(time.time() - start)
```

### Using Middleware for Automatic Metrics

Instead of instrumenting every route, use middleware:

```python
from starlette.middleware.base import BaseHTTPMiddleware
import time

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status=str(response.status_code),
        ).inc()

        REQUEST_DURATION.labels(
            method=request.method,
            endpoint=request.url.path,
        ).observe(duration)

        return response

app.add_middleware(MetricsMiddleware)
```

### Registering with Prometheus

Add your service to the Prometheus scrape config:

```yaml
# monitoring/prometheus/prometheus.yml
- job_name: "mailyte-my-service"
  static_configs:
    - targets: ["my-service:8080"]
      labels:
        service: "my-service"
        component: "worker"
```

### Testing Metrics

```python
from prometheus_client import REGISTRY

def test_request_counter_increments(client, api_key_header):
    # Get current count
    before = REGISTRY.get_sample_value(
        "mailyte_api_requests_total",
        {"method": "POST", "endpoint": "/api/v1/add/domain", "status": "200"}
    ) or 0

    # Make a request
    client.post("/api/v1/add/domain", headers=api_key_header, json={...})

    # Check count increased
    after = REGISTRY.get_sample_value(
        "mailyte_api_requests_total",
        {"method": "POST", "endpoint": "/api/v1/add/domain", "status": "200"}
    )
    assert after == before + 1
```

## Database Health in Metrics

Expose database connection health as a metric:

```python
from prometheus_client import Gauge

DB_HEALTHY = Gauge("mailyte_db_healthy", "Database connection health (1=up, 0=down)")
DB_CONNECTIONS = Gauge("mailyte_db_connections_active", "Active database connections")

# Update periodically
def update_db_metrics():
    try:
        db.execute("SELECT 1")
        DB_HEALTHY.set(1)
        DB_CONNECTIONS.set(db.pool.size())
    except Exception:
        DB_HEALTHY.set(0)
```
