# Docker Deployment

The complete Docker Compose setup — every service, volume, network, and port explained.

## Overview

Mailyte runs as a set of Docker containers orchestrated by Docker Compose. One `docker compose up -d` brings up the entire stack.

```mermaid
graph TB
    subgraph docker-network[mailyte-network]
        PF[Postfix<br/>:25, :587]
        DV[Dovecot<br/>:993, :143]
        RS[Rspamd<br/>:11334]
        API[FastAPI<br/>:5000]
        DB[(MySQL<br/>:3306)]
        RD[(Redis<br/>:6379)]
        WK[Workers]
        HM[Health Monitor<br/>:8080]
    end

    subgraph monitoring-network[monitoring-network]
        PR[Prometheus<br/>:9090]
        GR[Grafana<br/>:3000]
        AM[Alertmanager<br/>:9093]
    end

    PF --- RS
    PF --- DV
    API --- DB
    API --- RD
    WK --- RD
    WK --- PF
    HM --- PF
    HM --- DV
    HM --- RS
    HM --- DB
    HM --- RD
    HM --- API

    PR --- API
    PR --- DB
    PR --- RD
    PR --- HM
    GR --- PR
    AM --- PR
```

## Docker Compose File

Here's the full service definition with explanations:

```yaml
# docker-compose.yml
version: "3.8"

services:
  # ─────────────────────────────────────────
  # Postfix — SMTP server
  # ─────────────────────────────────────────
  postfix:
    image: mailyte/postfix:latest
    container_name: mailyte-postfix
    restart: unless-stopped
    ports:
      - "25:25"       # SMTP (server-to-server delivery)
      - "587:587"     # Submission (client sends email)
      - "465:465"     # SMTPS (implicit TLS, optional)
    volumes:
      - mail-data:/var/mail                     # Mailbox storage
      - mail-state:/var/mail-state              # Postfix state (queue, etc.)
      - ./config/postfix:/etc/postfix/custom    # Custom Postfix config
      - ${TLS_CERT_PATH}:/etc/ssl/certs/mail.crt:ro
      - ${TLS_KEY_PATH}:/etc/ssl/private/mail.key:ro
    environment:
      - DOMAIN=${DOMAIN}
      - HOSTNAME=${HOSTNAME}
      - MYSQL_HOST=mysql
      - MYSQL_DATABASE=${MYSQL_DATABASE}
      - MYSQL_USER=${MYSQL_USER}
      - MYSQL_PASSWORD=${MYSQL_PASSWORD}
    depends_on:
      mysql:
        condition: service_healthy
      rspamd:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "nc", "-z", "localhost", "25"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G

  # ─────────────────────────────────────────
  # Dovecot — IMAP/POP3 server
  # ─────────────────────────────────────────
  dovecot:
    image: mailyte/dovecot:latest
    container_name: mailyte-dovecot
    restart: unless-stopped
    ports:
      - "993:993"     # IMAPS
      - "143:143"     # IMAP (STARTTLS)
      - "995:995"     # POP3S (optional)
    volumes:
      - mail-data:/var/mail
      - ./config/dovecot:/etc/dovecot/custom
      - ${TLS_CERT_PATH}:/etc/ssl/certs/mail.crt:ro
      - ${TLS_KEY_PATH}:/etc/ssl/private/mail.key:ro
    environment:
      - MYSQL_HOST=mysql
      - MYSQL_DATABASE=${MYSQL_DATABASE}
      - MYSQL_USER=${MYSQL_USER}
      - MYSQL_PASSWORD=${MYSQL_PASSWORD}
    depends_on:
      mysql:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "nc", "-z", "localhost", "143"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G

  # ─────────────────────────────────────────
  # Rspamd — Spam filter
  # ─────────────────────────────────────────
  rspamd:
    image: mailyte/rspamd:latest
    container_name: mailyte-rspamd
    restart: unless-stopped
    volumes:
      - rspamd-data:/var/lib/rspamd
      - ./config/rspamd:/etc/rspamd/local.d
    environment:
      - REDIS_HOST=redis
      - REDIS_PASSWORD=${REDIS_PASSWORD}
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11334/ping"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G

  # ─────────────────────────────────────────
  # MySQL — Database
  # ─────────────────────────────────────────
  mysql:
    image: mysql:8.0
    container_name: mailyte-mysql
    restart: unless-stopped
    volumes:
      - mysql-data:/var/lib/mysql
      - ./config/mysql/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
      - ./config/mysql/my.cnf:/etc/mysql/conf.d/mailyte.cnf:ro
    environment:
      - MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
      - MYSQL_DATABASE=${MYSQL_DATABASE}
      - MYSQL_USER=${MYSQL_USER}
      - MYSQL_PASSWORD=${MYSQL_PASSWORD}
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G

  # ─────────────────────────────────────────
  # Redis — Cache and queue broker
  # ─────────────────────────────────────────
  redis:
    image: redis:7-alpine
    container_name: mailyte-redis
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 15s
      timeout: 3s
      retries: 3
      start_period: 10s
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M

  # ─────────────────────────────────────────
  # FastAPI — API server
  # ─────────────────────────────────────────
  api:
    image: mailyte/api:latest
    container_name: mailyte-api
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./config/api:/app/config
    environment:
      - DATABASE_URL=mysql+aiomysql://${MYSQL_USER}:${MYSQL_PASSWORD}@mysql:3306/${MYSQL_DATABASE}
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SECRET_KEY=${API_SECRET_KEY}
      - DOMAIN=${DOMAIN}
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M

  # ─────────────────────────────────────────
  # Worker — Background job processor
  # ─────────────────────────────────────────
  worker:
    image: mailyte/api:latest
    container_name: mailyte-worker
    restart: unless-stopped
    command: python3 -m mailyte.worker
    volumes:
      - ./config/api:/app/config
    environment:
      - DATABASE_URL=mysql+aiomysql://${MYSQL_USER}:${MYSQL_PASSWORD}@mysql:3306/${MYSQL_DATABASE}
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - POSTFIX_HOST=postfix
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
      postfix:
        condition: service_healthy
    networks:
      - mailyte-network
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M

  # ─────────────────────────────────────────
  # Health Monitor — Service health + auto-healing
  # ─────────────────────────────────────────
  health-monitor:
    image: mailyte/health-monitor:latest
    container_name: mailyte-health-monitor
    restart: always
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - HEALTH_CHECK_INTERVAL=30
      - ENABLE_AUTO_HEALING=true
      - UNHEALTHY_THRESHOLD=3
    depends_on:
      - postfix
      - dovecot
      - rspamd
      - mysql
      - redis
      - api
    networks:
      - mailyte-network

# ─────────────────────────────────────────
# Volumes
# ─────────────────────────────────────────
volumes:
  mail-data:
    driver: local
    # Stores actual email messages (Maildir format)
  mail-state:
    driver: local
    # Stores Postfix state: queue, transport maps
  mysql-data:
    driver: local
    # MySQL data directory
  redis-data:
    driver: local
    # Redis persistence (RDB/AOF)
  rspamd-data:
    driver: local
    # Rspamd learned data and statistics

# ─────────────────────────────────────────
# Networks
# ─────────────────────────────────────────
networks:
  mailyte-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/24
```

## Volumes Explained

| Volume | Purpose | Backup Priority |
|--------|---------|----------------|
| `mail-data` | Email messages in Maildir format | Critical |
| `mail-state` | Postfix queue and state files | Medium |
| `mysql-data` | All database tables | Critical |
| `redis-data` | Cache and job queues | Low (rebuilt on restart) |
| `rspamd-data` | Learned spam/ham data | Medium |

```bash
# See volume sizes
docker system df -v | grep -A 20 "VOLUME NAME"

# Inspect a specific volume
docker volume inspect mailyte_mail-data
```

## Port Mappings

| Host Port | Container | Service | Public? |
|-----------|-----------|---------|---------|
| 25 | postfix:25 | SMTP | Yes |
| 587 | postfix:587 | Submission | Yes |
| 465 | postfix:465 | SMTPS | Optional |
| 993 | dovecot:993 | IMAPS | Yes |
| 143 | dovecot:143 | IMAP | Optional |
| 5000 | api:5000 | REST API | Behind proxy |
| 8080 | health-monitor:8080 | Health | Internal only |

To bind a port to localhost only (not accessible from outside):

```yaml
ports:
  - "127.0.0.1:5000:5000"  # Only accessible from the server itself
```

## Common Operations

```bash
# Start everything
docker compose up -d

# Stop everything (keeps data)
docker compose down

# Stop and remove volumes (DESTROYS DATA)
docker compose down -v

# Restart a single service
docker compose restart postfix

# View logs
docker compose logs -f api

# Update images
docker compose pull
docker compose up -d

# Scale workers
docker compose up -d --scale worker=3

# Enter a container
docker compose exec postfix bash
docker compose exec mysql mysql -u root -p
```

## Environment Variable Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DOMAIN` | Yes | Your email domain |
| `HOSTNAME` | Yes | Mail server FQDN |
| `MYSQL_ROOT_PASSWORD` | Yes | MySQL root password |
| `MYSQL_DATABASE` | Yes | Database name |
| `MYSQL_USER` | Yes | Database user |
| `MYSQL_PASSWORD` | Yes | Database password |
| `REDIS_PASSWORD` | Yes | Redis password |
| `API_SECRET_KEY` | Yes | JWT signing key |
| `TLS_CERT_PATH` | Yes | Path to TLS certificate |
| `TLS_KEY_PATH` | Yes | Path to TLS private key |
| `GRAFANA_PASSWORD` | No | Grafana admin password |
| `LETSENCRYPT_EMAIL` | No | Email for cert notifications |
