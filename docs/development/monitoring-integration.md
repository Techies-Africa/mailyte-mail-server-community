---
title: Monitoring Integration
description: How to add health checks and Prometheus metrics to your code using the shared metrics helper.
edition: enterprise
---

# Monitoring Integration

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

A proper health check verifies that the service can actually do its job. The convention the existing workers use (see `worker/storage_usage/app.py`) is a `services` map plus a three-state status — `healthy` (all dependencies up, 200), `degraded` (some up, still 200), `unhealthy` (none up, 503):

```python
from fastapi.responses import JSONResponse


@app.get("/health")
async def health_check():
    services = {
        "database": database_is_available(),
        "cache": redis_is_available(),
    }

    if all(services.values()):
        status, code = "healthy", 200
    elif any(services.values()):
        status, code = "degraded", 200
    else:
        status, code = "unhealthy", 503

    return JSONResponse(
        content={"status": status, "service": "my-service", "services": services},
        status_code=code,
    )
```

### Docker Health Check

Configure Docker to use your health endpoint (stdlib only — the slim images have no curl):

```yaml
healthcheck:
  test: ["CMD-SHELL", "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8080/health\")'"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 15s
```

The `start_period` gives the service time to initialize before health checks begin. Health status matters beyond `docker ps`: the monitoring worker restarts unhealthy containers through the Docker socket proxy, and other services' `depends_on: condition: service_healthy` clauses key off it.

## Prometheus Metrics

### Setup

Use the shared helper — not `prometheus_client` directly. It's already in `shared/` and needs only `psutil` in your requirements:

```python
import time

from fastapi import Request
from fastapi.responses import PlainTextResponse

from shared.metrics import get_metrics

metrics = get_metrics("my_service")


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


@app.get("/metrics")
async def prometheus_metrics():
    return PlainTextResponse(metrics.get_prometheus_metrics())
```

That gives you, with no further code:

- `my_service_http_requests_total{method=...,path=...,status_code=...}`
- `my_service_http_request_duration_seconds` (summary)
- `my_service_http_errors_total` (status ≥ 400)
- `my_service_uptime_seconds`, `my_service_info`
- `my_service_cpu_usage_percent`, `my_service_memory_usage_*`

### Custom Metrics

```python
# Counters
metrics.increment_counter("items_processed_total", labels={"status": "success"})

# Gauges
metrics.set_gauge("queue_size", pending_count)

# Histograms (exposed as summaries with p50/p95/p99)
metrics.observe_histogram("processing_seconds", duration)

# Database + webhook convenience recorders
metrics.record_database_operation(operation="insert", duration=0.01, success=True)
metrics.record_webhook_delivery(url=url, status_code=200, duration=0.4)
```

See [Metrics Implementation](metrics-implementation.md) for naming conventions, label rules, and the full API.

### Registering with Prometheus

Add your service to the scrape config. Targets use **container** ports (the right-hand side of a compose port mapping), not host-mapped ports:

```yaml
# monitoring/prometheus/prometheus.yml
- job_name: 'my_service'
  metrics_path: /metrics
  static_configs:
    - targets: ['my_service:8105']
```

### Verifying Metrics

```bash
# Straight from the service (host-mapped port)
curl -s http://localhost:8105/metrics | head -30

# Confirm Prometheus is scraping it
curl -s "http://localhost:9090/api/v1/targets" | python3 -m json.tool | grep -A3 my_service

# Query a series
curl -s "http://localhost:9090/api/v1/query?query=my_service_http_requests_total" | python3 -m json.tool
```

In a test, hit the endpoint through the FastAPI test client and assert on the exposition text:

```python
def test_metrics_endpoint_exposes_request_counter(client):
    client.get("/health")
    body = client.get("/metrics").text
    assert "my_service_http_requests_total" in body
```

## Database Health in Metrics

Expose database connection health as a gauge, refreshed by your worker loop:

```python
def update_db_metrics():
    try:
        db.execute("SELECT 1")
        metrics.set_gauge("db_healthy", 1)
    except Exception:
        metrics.set_gauge("db_healthy", 0)
```

## Related

- [Metrics Implementation](metrics-implementation.md) — the full metric-authoring guide
- [Monitoring section](../monitoring/index.md) — Prometheus, Grafana, alerting, and auto-healing from the operator's side
