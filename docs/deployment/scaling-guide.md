# Scaling Guide

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


When to scale, what to scale, and how — so your email server grows with your needs.

## Signs You Need to Scale

Before scaling anything, confirm you actually have a bottleneck:

| Symptom | Likely Bottleneck | First Action |
|---------|------------------|--------------|
| API responses slow | API or database | Check query times, add API replicas |
| Mail queue growing | Postfix or workers | Add workers, check delivery errors |
| High CPU | Rspamd or MySQL | Profile and optimize first |
| High memory | MySQL buffer pool or Rspamd | Tune settings, then add RAM |
| Disk filling up | Mail storage or logs | Clean up first, expand disk |
| IMAP slow for users | Dovecot or disk I/O | Faster storage, optimize mailboxes |

> **Note:** Always optimize before you scale. Throwing hardware at a bad query or misconfiguration is expensive and doesn't fix the root cause.

## Scaling Workers

Workers are the easiest thing to scale. They're stateless and share a Redis queue.

### Docker Compose

```bash
# Scale to 3 workers
docker compose up -d --scale worker=3

# Check they're all running
docker compose ps | grep worker
```

### Docker Compose File (Permanent)

```yaml
services:
  worker:
    image: mailyte/api:latest
    command: python3 -m mailyte.worker
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
```

**When to add workers:**

- Email queue depth consistently > 100
- Job processing time increasing
- Worker CPU at > 80%

**How many workers?**

| Email Volume | Workers |
|-------------|---------|
| < 10k/day | 1 |
| 10k-50k/day | 2-3 |
| 50k-200k/day | 3-5 |
| 200k+/day | 5-10 |

## Scaling the API

The FastAPI server is stateless. Scale it behind a load balancer.

### Docker Compose

```yaml
services:
  api:
    image: mailyte/api:latest
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
    # Remove the fixed port mapping
    # ports:
    #   - "5000:5000"
    expose:
      - "5000"

  # Add an nginx load balancer
  nginx:
    image: nginx:alpine
    ports:
      - "5000:80"
    volumes:
      - ./config/nginx/api-lb.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - api
```

```nginx
# config/nginx/api-lb.conf
upstream api_backend {
    least_conn;
    server api:5000;
}

server {
    listen 80;

    location / {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## Database Scaling

MySQL is usually the hardest bottleneck to fix because it's stateful.

### Step 1: Optimize First

```sql
-- Check for missing indexes
SELECT * FROM sys.schema_unused_indexes;

-- Find slow queries
SELECT * FROM sys.statements_with_full_table_scans
ORDER BY exec_count DESC LIMIT 10;

-- Tune buffer pool (should be ~70% of available RAM for dedicated DB)
SET GLOBAL innodb_buffer_pool_size = 4294967296;  -- 4GB
```

### Step 2: Read Replicas

For read-heavy workloads, add MySQL read replicas:

```yaml
# docker-compose.yml addition
mysql-replica:
  image: mysql:8.0
  environment:
    - MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
  volumes:
    - mysql-replica-data:/var/lib/mysql
  command: >
    --server-id=2
    --read-only=1
    --relay-log=relay-log
```

Configure the API to use read replicas:

```python
# In the API configuration
DATABASE_READ_URL=mysql+aiomysql://mailyte:pass@mysql-replica:3306/mailyte
DATABASE_WRITE_URL=mysql+aiomysql://mailyte:pass@mysql:3306/mailyte
```

### Step 3: Dedicated Database Server

When your database outgrows the mail server:

1. Set up MySQL on a dedicated server (or use managed MySQL like RDS)
2. Update `DATABASE_URL` in `.env`
3. Ensure network connectivity between servers
4. Set up replication for high availability

## Redis Scaling

### Redis Memory Optimization

```bash
# Check memory usage
docker compose exec redis redis-cli -a $REDIS_PASSWORD info memory

# Set a memory limit
docker compose exec redis redis-cli -a $REDIS_PASSWORD config set maxmemory 1gb
docker compose exec redis redis-cli -a $REDIS_PASSWORD config set maxmemory-policy allkeys-lru
```

### Redis Cluster (for large deployments)

If a single Redis instance isn't enough:

```yaml
services:
  redis-node-1:
    image: redis:7-alpine
    command: redis-server --cluster-enabled yes --cluster-config-file nodes.conf
    volumes:
      - redis-node-1-data:/data

  redis-node-2:
    image: redis:7-alpine
    command: redis-server --cluster-enabled yes --cluster-config-file nodes.conf
    volumes:
      - redis-node-2-data:/data

  redis-node-3:
    image: redis:7-alpine
    command: redis-server --cluster-enabled yes --cluster-config-file nodes.conf
    volumes:
      - redis-node-3-data:/data
```

> **Note:** Most Mailyte deployments don't need Redis clustering. A single Redis instance handles hundreds of thousands of operations per second.

## Multiple Postfix Instances

For very high email volume, run multiple Postfix instances behind a load balancer.

```mermaid
graph LR
    LB[Load Balancer :25] --> PF1[Postfix 1]
    LB --> PF2[Postfix 2]
    LB --> PF3[Postfix 3]
    PF1 --> DB[(Shared MySQL)]
    PF2 --> DB
    PF3 --> DB
```

### Setup

```yaml
services:
  postfix-1:
    image: mailyte/postfix:latest
    hostname: mx1.yourdomain.com
    environment:
      - INSTANCE_ID=1
    volumes:
      - postfix-1-queue:/var/spool/postfix
      - mail-data:/var/mail    # Shared via NFS or similar

  postfix-2:
    image: mailyte/postfix:latest
    hostname: mx2.yourdomain.com
    environment:
      - INSTANCE_ID=2
    volumes:
      - postfix-2-queue:/var/spool/postfix
      - mail-data:/var/mail

  postfix-lb:
    image: haproxy:latest
    ports:
      - "25:25"
      - "587:587"
    volumes:
      - ./config/haproxy/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro
```

Add multiple MX records:

```
yourdomain.com  MX  10 mx1.yourdomain.com
yourdomain.com  MX  20 mx2.yourdomain.com
```

## Scaling Roadmap

```mermaid
graph TD
    A[Single Server<br/>Docker Compose] -->|Growing pains| B[Optimize<br/>Tune configs, indexes]
    B -->|Still not enough| C[Scale Workers<br/>Add 2-3 more workers]
    C -->|API bottleneck| D[Scale API<br/>Multiple replicas + LB]
    D -->|DB bottleneck| E[DB Read Replicas<br/>Split reads/writes]
    E -->|Mail throughput| F[Multiple Postfix<br/>Load-balanced SMTP]
    F -->|Need HA| G[Kubernetes<br/>Full orchestration]
```

| Stage | Email Volume | Users | Server Specs |
|-------|-------------|-------|-------------|
| Single server | < 50k/day | < 5k | 4 CPU, 8 GB RAM |
| Scaled workers + API | 50k-200k/day | 5k-20k | 8 CPU, 16 GB RAM |
| DB replicas + multiple MX | 200k-1M/day | 20k-100k | Multiple servers |
| Kubernetes | 1M+/day | 100k+ | Cluster |

## Capacity Planning

Track these metrics to predict when you'll need to scale:

```promql
# Growth rate: emails per day trend
increase(postfix_delivery_total[24h])

# User growth rate
increase(dovecot_auth_success_total[7d]) / 7

# Storage growth rate (GB per month)
deriv(sum(dovecot_storage_bytes)[30d:1d]) * 86400 * 30 / 1073741824

# CPU headroom
100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)
```

> **Tip:** Set up a Grafana dashboard with these metrics and review it monthly. Scale proactively when you see sustained growth approaching your current capacity.
