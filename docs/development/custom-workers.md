---
title: Custom Workers
description: How to build a new worker module — file structure, FastAPI app, compose registration, health checks, and metrics.
---

# Custom Workers

Workers are the backbone of Mailyte. Each worker handles a specific job — tracking, webhooks, analytics, etc. This guide shows you how to build a new one, following the pattern the existing workers share (`worker/storage_usage/` is a good compact reference).

## Worker Architecture

Every worker is a standalone FastAPI service running in its own Docker container. Workers share:

- A database connection (MySQL) and a cache connection (Redis)
- A health check endpoint (`/health`)
- A metrics endpoint (`/metrics`) backed by `shared/metrics.py`
- The `shared/` Python package (copied into the image at build; bind-mounted in dev)
- The global webhook dispatcher (`shared/webhook_dispatcher.py`) for event notifications

## File Structure

Create a new directory under `worker/`:

```
worker/
  my_new_worker/
    app.py               # FastAPI app: routes, /health, /metrics, middleware
    config.py            # Configuration from env vars
    services/            # Core business logic (optional for small workers)
    Dockerfile           # Container build (repo-root build context)
    requirements.txt     # Pinned Python dependencies for this service
```

## Step 1: Configuration

```python
# worker/my_new_worker/config.py
import os


class Config:
    # Database
    DB_HOST = os.environ.get("DB_HOST", "mysql")
    DB_PORT = int(os.environ.get("DB_PORT", 3306))
    DB_NAME = os.environ.get("DB_NAME", "mailserver")
    DB_USER = os.environ.get("DB_USER", "mailuser")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

    # Redis
    REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
    REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

    # Worker-specific
    SERVICE_PORT = int(os.environ.get("PORT", 8105))
    POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 60))
```

Never give a security-relevant value (password, token, secret) a working default — the `secrets-check` container enforces that at stack startup.

## Step 2: The FastAPI App

The worker's HTTP surface, metrics middleware, and background loop all live in `app.py` — this mirrors what every existing worker does:

```python
# worker/my_new_worker/app.py
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from shared.logging_config import get_service_logger
from shared.metrics import get_metrics

app = FastAPI(title="My New Worker")

metrics = get_metrics("my_new_worker")
logger = get_service_logger("my_new_worker")


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
    """Prometheus exposition endpoint."""
    return PlainTextResponse(metrics.get_prometheus_metrics())


@app.get("/health")
async def health_check():
    """Health check with dependency status."""
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
        content={"status": status, "service": "my_new_worker", "services": services},
        status_code=code,
    )
```

Background work runs as a thread or an asyncio task started from a FastAPI startup hook. Keep handlers non-blocking: an `async def` route doing synchronous I/O freezes the whole process for every caller — use a plain `def` handler (threadpool) or truly async I/O.

## Step 3: Business Logic

Put real work in `services/` (or a single module for small workers), not in route handlers. If your worker emits events, use the global dispatcher — one call handles queuing, signing, delivery, retries, and dead-lettering:

```python
from shared.webhook_dispatcher import dispatch_event

dispatch_event(
    "my_worker.item.processed",
    {"item_id": item_id},
    org_id=org_id,
    source_service="my_new_worker",
)
```

## Step 4: Dockerfile

Workers that import `shared/` (or `database/`) need a **repo-root build context**, because Docker cannot `COPY` from outside the context. Copy the real pattern:

```dockerfile
# worker/my_new_worker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY worker/my_new_worker/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY worker/my_new_worker/ .
COPY shared ./shared
COPY database ./database

RUN groupadd -r mailyte && useradd -r -g mailyte -u 10001 mailyte \
 && chown -R mailyte:mailyte /app
USER mailyte

EXPOSE 8105

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8105"]
```

All paths in `COPY` are relative to the repo root because the compose file sets `build.context: .` (Step 6).

## Step 5: Requirements

Pin exact versions in the worker's own `requirements.txt` (there is no repo-root requirements file). Match the versions the sibling workers already pin — e.g. from `worker/storage_usage/requirements.txt`:

```
fastapi==0.128.8
uvicorn==0.34.0
mysql-connector-python==9.4.0
pymysql==1.2.0
redis==5.0.1
SQLAlchemy==2.0.23
requests==2.32.5
psutil==5.9.6        # required by shared/metrics.py
```

## Step 6: Docker Compose Registration

Add the worker to `docker-compose.yml`. Note the repo-root build context, the bind mounts (which are what make dev hot-reload work), and the `migrate` dependency — no worker may start before the schema is at head:

```yaml
my_new_worker:
  build:
    context: .
    dockerfile: ./worker/my_new_worker/Dockerfile
  container_name: my_new_worker
  ports:
    - "8105:8105"
  environment:
    - PORT=8105
    - DB_HOST=mysql
    - DB_PORT=3306
    - DB_NAME=${DB_NAME:-mailserver}
    - DB_USER=${DB_USER:-mailuser}
    - DB_PASSWORD=${DB_PASSWORD}
    - REDIS_HOST=redis
    - REDIS_PORT=6379
  volumes:
    - ./worker/my_new_worker:/app
    - ./shared:/app/shared
    - ./logs/worker/my_new_worker:/app/logs
  depends_on:
    mysql:
      condition: service_healthy
    migrate:
      condition: service_completed_successfully
    redis:
      condition: service_healthy
  networks:
    - mailserver_network
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8105/health\")'"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 15s
```

Pick an unused host port — 8081–8104 are already claimed by the existing workers and security services (check `docker-compose.yml`; [Getting Started](getting-started.md) maps the common ones).

Then add a hot-reload override to `docker-compose.dev.yml`, matching the other workers:

```yaml
# docker-compose.dev.yml
my_new_worker:
  command: ["python", "-m", "uvicorn", "app:app", "--host=0.0.0.0", "--port=8105", "--reload"]
```

The port in the dev override is the **container** port, not the host half of the mapping — getting this wrong leaves the healthcheck and inter-service calls dialing a port nothing listens on (this exact bug once took the analytics service down; see the comment in `docker-compose.dev.yml`).

## Step 7: CI and Deploy Registration

- Add the service to the build matrix in `.github/workflows/ci.yml` (with `dockerfile:` and `build_context: .` since it uses a repo-root context)
- Add it to the repo-root-context case list in `.github/workflows/deploy.yml` and to `ALL_SERVICES` there

## Step 8: Custom Metrics

Beyond what `record_request` gives you automatically, record worker-specific metrics through the same `shared/metrics.py` instance — see [Metrics Implementation](metrics-implementation.md) for the full API:

```python
metrics.increment_counter("items_processed_total", labels={"status": "success"})
metrics.set_gauge("queue_size", pending_count)
metrics.observe_histogram("processing_seconds", duration)
```

Add the scrape target to the Prometheus config, using the **container** port:

```yaml
# monitoring/prometheus/prometheus.yml
- job_name: 'my_new_worker'
  metrics_path: /metrics
  static_configs:
    - targets: ['my_new_worker:8105']
```

## Checklist

- [x] Worker has a clear, single responsibility
- [x] Config reads from environment variables (no default secrets)
- [x] Health check endpoint at `/health` with dependency status
- [x] Metrics at `/metrics` via `shared.metrics.get_metrics()`
- [x] Dockerfile with repo-root context; pinned `requirements.txt`
- [x] Registered in `docker-compose.yml` (with `migrate` dependency) and `docker-compose.dev.yml`
- [x] Added to the CI build matrix and deploy service list
- [x] Added to the Prometheus scrape config
- [x] Proper error handling with logging; graceful shutdown
- [x] Tests written
