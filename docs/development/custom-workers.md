---
title: Custom Workers
description: How to build a new worker module — file structure, base class, service registration, health checks, and metrics.
---

# Custom Workers

Workers are the backbone of Mailyte. Each worker handles a specific background job — tracking, webhooks, analytics, etc. This guide shows you how to build a new one.

## Worker Architecture

Every worker is a standalone Python service running in its own Docker container. Workers share:

- A database connection (MySQL)
- A cache connection (Redis)
- A health check endpoint (`/health`)
- A metrics endpoint (`/metrics`)
- The `shared/` Python package for common utilities

## File Structure

Create a new directory under `worker/`:

```
worker/
  my_new_worker/
    __init__.py
    main.py              # Entry point
    service.py           # Core business logic
    routes.py            # HTTP routes (health, metrics, any APIs)
    config.py            # Configuration from env vars
    models.py            # Data models
    Dockerfile           # Container build
    requirements.txt     # Python dependencies
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
    SERVICE_PORT = int(os.environ.get("PORT", 8090))
    POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 60))
    WORKER_THREADS = int(os.environ.get("WORKER_THREADS", 2))
```

## Step 2: Core Service Logic

```python
# worker/my_new_worker/service.py
import logging
import threading
import time

logger = logging.getLogger(__name__)

class MyNewWorkerService:
    def __init__(self, config, db, redis_client):
        self.config = config
        self.db = db
        self.redis = redis_client
        self._running = False
        self._thread = None

    def start(self):
        """Start the worker loop in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("MyNewWorker started (poll interval: %ds)", self.config.POLL_INTERVAL)

    def stop(self):
        """Stop the worker loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("MyNewWorker stopped")

    def _run_loop(self):
        """Main worker loop."""
        while self._running:
            try:
                self.process()
            except Exception as e:
                logger.error("Worker loop error: %s", e)
            time.sleep(self.config.POLL_INTERVAL)

    def process(self):
        """Override this with your actual work."""
        logger.debug("Processing...")
        # Your logic here:
        # - Query the database for items to process
        # - Do the work
        # - Update the database with results
        # - Emit metrics

    def health_check(self) -> dict:
        """Return worker health status."""
        return {
            "status": "healthy" if self._running else "unhealthy",
            "service": "my_new_worker",
            "thread_alive": self._thread.is_alive() if self._thread else False,
        }
```

## Step 3: HTTP Routes

```python
# worker/my_new_worker/routes.py
from fastapi import FastAPI
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

def create_app(service) -> FastAPI:
    app = FastAPI(title="My New Worker")

    @app.get("/health")
    async def health():
        return service.health_check()

    @app.get("/metrics")
    async def metrics():
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app
```

## Step 4: Entry Point

```python
# worker/my_new_worker/main.py
import logging
import uvicorn

from config import Config
from service import MyNewWorkerService
from routes import create_app

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

def main():
    config = Config()

    # Initialize connections
    from shared.database import get_connection
    from shared.redis_client import get_redis

    db = get_connection(config)
    redis_client = get_redis(config)

    # Create and start the worker
    service = MyNewWorkerService(config, db, redis_client)
    service.start()

    # Create the HTTP app (health + metrics)
    app = create_app(service)

    # Run the HTTP server
    logger.info("Starting HTTP server on port %d", config.SERVICE_PORT)
    uvicorn.run(app, host="0.0.0.0", port=config.SERVICE_PORT)

if __name__ == "__main__":
    main()
```

## Step 5: Dockerfile

```dockerfile
# worker/my_new_worker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY ../../shared /app/shared

EXPOSE 8090

CMD ["python", "main.py"]
```

## Step 6: Requirements

```
# worker/my_new_worker/requirements.txt
fastapi==0.109.0
uvicorn==0.27.0
mysql-connector-python==8.3.0
redis==5.0.1
prometheus-client==0.19.0
```

## Step 7: Docker Compose Registration

Add the worker to `docker-compose.yml`:

```yaml
my_new_worker:
  build: ./worker/my_new_worker
  container_name: my_new_worker
  ports:
    - "8090:8090"
  environment:
    - PORT=8090
    - DB_HOST=mysql
    - DB_PORT=3306
    - DB_NAME=${DB_NAME:-mailserver}
    - DB_USER=${DB_USER:-mailuser}
    - DB_PASSWORD=${DB_PASSWORD:-mailpassword}
    - REDIS_HOST=redis
    - REDIS_PORT=6379
    - POLL_INTERVAL=60
  volumes:
    - ./worker/my_new_worker:/app
    - ./shared:/app/shared
    - ./logs/worker/my_new_worker:/app/logs
  depends_on:
    mysql:
      condition: service_healthy
    redis:
      condition: service_healthy
  networks:
    - mailserver_network
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8090/health\")'"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 15s
```

## Step 8: Add Prometheus Metrics

```python
# In service.py
from prometheus_client import Counter, Histogram, Gauge

# Define metrics
ITEMS_PROCESSED = Counter(
    "mailyte_my_worker_items_processed_total",
    "Total items processed",
    ["status"],
)
PROCESSING_DURATION = Histogram(
    "mailyte_my_worker_processing_seconds",
    "Time to process each item",
)
QUEUE_SIZE = Gauge(
    "mailyte_my_worker_queue_size",
    "Number of items waiting to be processed",
)

class MyNewWorkerService:
    def process(self):
        # Update queue size
        pending = self.db.execute("SELECT COUNT(*) FROM my_table WHERE status = 'pending'")
        QUEUE_SIZE.set(pending)

        # Process items
        with PROCESSING_DURATION.time():
            result = self.do_work()

        if result.success:
            ITEMS_PROCESSED.labels(status="success").inc()
        else:
            ITEMS_PROCESSED.labels(status="failure").inc()
```

Add the scrape target to Prometheus config:

```yaml
# monitoring/prometheus/prometheus.yml
- job_name: "mailyte-my-new-worker"
  static_configs:
    - targets: ["my_new_worker:8090"]
      labels:
        service: "my-new-worker"
        component: "worker"
```

## Checklist

- [x] Worker has a clear, single responsibility
- [x] Config reads from environment variables
- [x] Health check endpoint at `/health`
- [x] Prometheus metrics at `/metrics`
- [x] Dockerfile and requirements.txt
- [x] Registered in docker-compose.yml
- [x] Proper error handling with logging
- [x] Graceful shutdown support
- [x] Added to Prometheus scrape config
- [x] Tests written
