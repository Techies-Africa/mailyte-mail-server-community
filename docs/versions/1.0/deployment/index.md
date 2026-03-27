
# Deployment Guide

This guide covers all aspects of deploying the Mailyte Mail Server, from development environments to production-scale deployments with high availability and monitoring.

## Deployment Overview

The Mailyte Mail Server supports multiple deployment strategies:

1. **Development Deployment**: Single-node setup for development and testing
2. **Production Deployment**: Multi-node setup with load balancing and redundancy
3. **Cloud Deployment**: Kubernetes-based deployment on cloud platforms
4. **Hybrid Deployment**: On-premises with cloud storage and backup

## Prerequisites

### System Requirements

#### Minimum Requirements (Development)
- **CPU**: 2 cores
- **RAM**: 4GB
- **Storage**: 50GB SSD
- **Network**: 100Mbps
- **OS**: Ubuntu 20.04+ or CentOS 8+

#### Recommended Requirements (Production)
- **CPU**: 8 cores per service node
- **RAM**: 16GB per service node
- **Storage**: 500GB NVMe SSD
- **Network**: 1Gbps with redundancy
- **OS**: Ubuntu 22.04 LTS or RHEL 9

#### High-Scale Requirements (Enterprise)
- **CPU**: 16+ cores per node
- **RAM**: 32GB+ per node
- **Storage**: 2TB+ NVMe SSD with RAID 10
- **Network**: 10Gbps with redundancy
- **Load Balancers**: Dedicated hardware or cloud load balancer

### Software Dependencies

#### Required Software
```bash
# Docker and Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Python 3.9+ (for deployment scripts)
sudo apt update
sudo apt install python3 python3-pip python3-venv

# Git (for source code)
sudo apt install git
```

#### Optional Software
```bash
# Kubernetes (for cloud deployment)
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Terraform (for infrastructure as code)
wget https://releases.hashicorp.com/terraform/1.6.0/terraform_1.6.0_linux_amd64.zip
unzip terraform_1.6.0_linux_amd64.zip
sudo mv terraform /usr/local/bin/

# Ansible (for configuration management)
pip3 install ansible
```

## Development Deployment

### Quick Start (Single Node)

1. **Clone Repository**
```bash
git clone https://github.com/your-org/enterprise-mail-server.git
cd enterprise-mail-server
```

2. **Configure Environment**
```bash
cp .env.example .env
# Edit .env with your settings
nano .env
```

3. **Start Services**
```bash
# Start all services
python3 main.py

# Or use Docker Compose directly
docker-compose up -d
```

4. **Verify Deployment**
```bash
# Check service health
curl http://localhost:8080/health

# Check API availability
curl -H "X-API-Key: your-api-key" http://localhost:5000/api/v1/health
```

### Development Configuration

```bash
# .env for development
DEVELOPMENT_MODE=true
DEBUG_MODE=true
LOG_LEVEL=DEBUG

# Use local services
HOSTNAME=localhost
DOMAIN=localhost
WEBHOOK_URLS=http://localhost:5000/webhook

# Simplified SSL (self-signed)
ENABLE_TLS=false
ACME_STAGING=true

# Reduced resource limits
DB_POOL_SIZE=5
WEBHOOK_BATCH_SIZE=10
TRACKING_CACHE_TTL=60
```

## Production Deployment

### Infrastructure Setup

#### Server Architecture
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Load Balancer │    │   Load Balancer │    │   Load Balancer │
│   (Primary)     │    │   (Secondary)   │    │   (Backup)      │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
    ┌────────────────────────────┴────────────────────────────┐
    │                    Application Tier                     │
    ├─────────────────┬─────────────────┬─────────────────────┤
    │   Mail Node 1   │   Mail Node 2   │   Mail Node 3       │
    │   - Postfix     │   - Postfix     │   - Postfix         │
    │   - Dovecot     │   - Dovecot     │   - Dovecot         │
    │   - API Gateway │   - API Gateway │   - API Gateway     │
    └─────────────────┼─────────────────┼─────────────────────┘
                      │                 │
    ┌─────────────────┴─────────────────┴─────────────────────┐
    │                    Worker Tier                          │
    ├─────────────────┬─────────────────┬─────────────────────┤
    │   Worker Node 1 │   Worker Node 2 │   Worker Node 3     │
    │   - Webhooks    │   - Tracking    │   - RAG Service     │
    │   - Rate Limiter│   - Analytics   │   - Storage Service │
    └─────────────────┼─────────────────┼─────────────────────┘
                      │                 │
    ┌─────────────────┴─────────────────┴─────────────────────┐
    │                    Data Tier                            │
    ├─────────────────┬─────────────────┬─────────────────────┤
    │   MySQL Master  │   MySQL Slave   │   Qdrant Cluster   │
    │   (Primary DB)  │   (Read Replica)│   (Vector DB)       │
    └─────────────────┴─────────────────┴─────────────────────┘
```

#### Load Balancer Configuration (Nginx)

```nginx
# /etc/nginx/sites-available/mail-server
upstream mail_api {
    least_conn;
    server mail-node-1:5000 max_fails=3 fail_timeout=30s;
    server mail-node-2:5000 max_fails=3 fail_timeout=30s;
    server mail-node-3:5000 max_fails=3 fail_timeout=30s;
}

upstream mail_smtp {
    least_conn;
    server mail-node-1:25 max_fails=3 fail_timeout=30s;
    server mail-node-2:25 max_fails=3 fail_timeout=30s;
    server mail-node-3:25 max_fails=3 fail_timeout=30s;
}

upstream mail_imap {
    least_conn;
    server mail-node-1:143 max_fails=3 fail_timeout=30s;
    server mail-node-2:143 max_fails=3 fail_timeout=30s;
    server mail-node-3:143 max_fails=3 fail_timeout=30s;
}

# HTTPS API endpoint
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;
    
    ssl_certificate /etc/ssl/certs/yourdomain.com.crt;
    ssl_certificate_key /etc/ssl/private/yourdomain.com.key;
    
    location / {
        proxy_pass http://mail_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# SMTP load balancing (TCP)
stream {
    upstream smtp_backend {
        server mail-node-1:25;
        server mail-node-2:25;
        server mail-node-3:25;
    }
    
    upstream imaps_backend {
        server mail-node-1:993;
        server mail-node-2:993;
        server mail-node-3:993;
    }
    
    server {
        listen 25;
        proxy_pass smtp_backend;
        proxy_timeout 1s;
        proxy_responses 1;
    }
    
    server {
        listen 993;
        proxy_pass imaps_backend;
        proxy_timeout 1s;
        proxy_responses 1;
    }
}
```

### Database Clustering

#### MySQL Master-Slave Setup

**Master Configuration** (`my.cnf`):
```ini
[mysqld]
server-id = 1
log-bin = mysql-bin
binlog-format = ROW
binlog-do-db = mailserver

# Performance settings
innodb_buffer_pool_size = 8G
innodb_log_file_size = 1G
max_connections = 1000
query_cache_size = 256M

# Replication settings
sync_binlog = 1
innodb_flush_log_at_trx_commit = 1
```

**Slave Configuration** (`my.cnf`):
```ini
[mysqld]
server-id = 2
relay-log = relay-log
read_only = 1

# Same performance settings as master
innodb_buffer_pool_size = 8G
innodb_log_file_size = 1G
max_connections = 1000
query_cache_size = 256M
```

**Replication Setup**:
```sql
-- On master server
CREATE USER 'replicator'@'%' IDENTIFIED BY 'strong_password';
GRANT REPLICATION SLAVE ON *.* TO 'replicator'@'%';
FLUSH PRIVILEGES;
SHOW MASTER STATUS;

-- On slave server
CHANGE MASTER TO
  MASTER_HOST='master-ip',
  MASTER_USER='replicator',
  MASTER_PASSWORD='strong_password',
  MASTER_LOG_FILE='mysql-bin.000001',
  MASTER_LOG_POS=107;

START SLAVE;
SHOW SLAVE STATUS\G
```

### Production Configuration

```bash
# .env for production
DEVELOPMENT_MODE=false
DEBUG_MODE=false
LOG_LEVEL=INFO

# Production domains
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
WEBHOOK_URLS=https://your-app.com/webhook

# Production SSL
ENABLE_TLS=true
ACME_STAGING=false
TLS_PROTOCOLS=TLSv1.2,TLSv1.3

# Production performance settings
DB_POOL_SIZE=50
DB_MAX_OVERFLOW=100
WEBHOOK_BATCH_SIZE=100
TRACKING_CACHE_TTL=3600

# Cloud storage
CLOUD_SYNC_ENABLED=true
CLOUD_SYNC_PROVIDER=s3
CLOUD_SYNC_BUCKET=your-production-bucket

# Enhanced monitoring
HEALTH_CHECK_ENABLED=true
PERFORMANCE_LOGGING=true
ALERT_ERROR_RATE_THRESHOLD=0.01
```

## Cloud Deployment (Kubernetes)

### Kubernetes Manifests

#### Namespace and ConfigMap
```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: mail-server

---
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mail-server-config
  namespace: mail-server
data:
  HOSTNAME: "mail.yourdomain.com"
  DOMAIN: "yourdomain.com"
  DB_HOST: "mysql-service"
  DB_NAME: "mailserver"
  WEBHOOK_BATCH_SIZE: "100"
  LOG_LEVEL: "INFO"
```

#### MySQL Deployment
```yaml
# mysql-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql
  namespace: mail-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mysql
  template:
    metadata:
      labels:
        app: mysql
    spec:
      containers:
      - name: mysql
        image: mysql:8.0
        env:
        - name: MYSQL_ROOT_PASSWORD
          valueFrom:
            secretKeyRef:
              name: mysql-secret
              key: root-password
        - name: MYSQL_DATABASE
          value: mailserver
        ports:
        - containerPort: 3306
        volumeMounts:
        - name: mysql-storage
          mountPath: /var/lib/mysql
      volumes:
      - name: mysql-storage
        persistentVolumeClaim:
          claimName: mysql-pvc

---
apiVersion: v1
kind: Service
metadata:
  name: mysql-service
  namespace: mail-server
spec:
  selector:
    app: mysql
  ports:
  - port: 3306
    targetPort: 3306
  type: ClusterIP
```

#### Mail Server Deployment
```yaml
# mail-server-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mail-server
  namespace: mail-server
spec:
  replicas: 3
  selector:
    matchLabels:
      app: mail-server
  template:
    metadata:
      labels:
        app: mail-server
    spec:
      containers:
      - name: postfix
        image: your-registry/mail-server:postfix
        ports:
        - containerPort: 25
        - containerPort: 587
        envFrom:
        - configMapRef:
            name: mail-server-config
        - secretRef:
            name: mail-server-secrets
        volumeMounts:
        - name: mail-storage
          mountPath: /var/mail
        - name: ssl-certs
          mountPath: /etc/ssl/certs
      
      - name: dovecot
        image: your-registry/mail-server:dovecot
        ports:
        - containerPort: 143
        - containerPort: 993
        envFrom:
        - configMapRef:
            name: mail-server-config
        - secretRef:
            name: mail-server-secrets
        volumeMounts:
        - name: mail-storage
          mountPath: /var/mail
        - name: ssl-certs
          mountPath: /etc/ssl/certs
      
      - name: api-gateway
        image: your-registry/mail-server:api
        ports:
        - containerPort: 5000
        envFrom:
        - configMapRef:
            name: mail-server-config
        - secretRef:
            name: mail-server-secrets
      
      volumes:
      - name: mail-storage
        persistentVolumeClaim:
          claimName: mail-storage-pvc
      - name: ssl-certs
        secret:
          secretName: ssl-certificates

---
apiVersion: v1
kind: Service
metadata:
  name: mail-server-service
  namespace: mail-server
spec:
  selector:
    app: mail-server
  ports:
  - name: smtp
    port: 25
    targetPort: 25
  - name: submission
    port: 587
    targetPort: 587
  - name: imap
    port: 143
    targetPort: 143
  - name: imaps
    port: 993
    targetPort: 993
  - name: api
    port: 5000
    targetPort: 5000
  type: LoadBalancer
```

#### Ingress Configuration
```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mail-server-ingress
  namespace: mail-server
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  tls:
  - hosts:
    - api.yourdomain.com
    - track.yourdomain.com
    secretName: mail-server-tls
  rules:
  - host: api.yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: mail-server-service
            port:
              number: 5000
  - host: track.yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: tracking-service
            port:
              number: 8083
```

### Helm Chart Deployment

```yaml
# values.yaml for Helm chart
replicaCount: 3

image:
  repository: your-registry/mail-server
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: LoadBalancer
  
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: mail.yourdomain.com
      paths:
        - path: /
          pathType: ImplementationSpecific

persistence:
  enabled: true
  storageClass: fast-ssd
  size: 100Gi

mysql:
  enabled: true
  auth:
    rootPassword: "secure-password"
    database: mailserver
  primary:
    persistence:
      enabled: true
      size: 50Gi

monitoring:
  enabled: true
  serviceMonitor:
    enabled: true
```

```bash
# Deploy with Helm
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install mail-server ./helm-chart -f values.yaml -n mail-server --create-namespace
```

## Monitoring and Health Checks

### Prometheus Configuration
```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'mail-server'
    static_configs:
      - targets: ['mail-node-1:8080', 'mail-node-2:8080', 'mail-node-3:8080']
    metrics_path: /metrics
    scrape_interval: 30s

  - job_name: 'mail-services'
    static_configs:
      - targets: 
        - 'webhooks:8081'
        - 'tracking:8083'
        - 'rate-limiter:8082'
        - 'storage:8084'
    metrics_path: /metrics
    scrape_interval: 30s
```

### Grafana Dashboard
```json
{
  "dashboard": {
    "title": "Mail Server Monitoring",
    "panels": [
      {
        "title": "Email Throughput",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(emails_processed_total[5m])",
            "legendFormat": "Emails/sec"
          }
        ]
      },
      {
        "title": "Service Health",
        "type": "stat",
        "targets": [
          {
            "expr": "up{job=\"mail-server\"}",
            "legendFormat": "{{instance}}"
          }
        ]
      }
    ]
  }
}
```

## Backup and Disaster Recovery

### Automated Backup Script
```bash
#!/bin/bash
# backup.sh
set -e

BACKUP_DIR="/backups/$(date +%Y%m%d_%H%M%S)"
S3_BUCKET="your-backup-bucket"

mkdir -p $BACKUP_DIR

# Database backup
mysqldump -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME > $BACKUP_DIR/database.sql

# Configuration backup
tar -czf $BACKUP_DIR/config.tar.gz /etc/postfix /etc/dovecot /etc/rspamd

# Mail data backup (incremental)
rsync -av --link-dest=/backups/latest /var/mail/ $BACKUP_DIR/mail/

# Upload to cloud storage
aws s3 sync $BACKUP_DIR s3://$S3_BUCKET/backups/$(basename $BACKUP_DIR)/

# Update latest symlink
ln -sfn $BACKUP_DIR /backups/latest

# Cleanup old backups (keep 30 days)
find /backups -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "Backup completed: $BACKUP_DIR"
```

### Disaster Recovery Procedures

1. **Service Recovery**
```bash
# Stop all services
docker-compose down

# Restore from backup
aws s3 sync s3://your-backup-bucket/backups/latest/ /restore/

# Restore database
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME < /restore/database.sql

# Restore configuration
tar -xzf /restore/config.tar.gz -C /

# Start services
docker-compose up -d
```

2. **Data Validation**
```bash
# Verify database integrity
mysqlcheck -h $DB_HOST -u $DB_USER -p$DB_PASSWORD --all-databases

# Verify email data
find /var/mail -name "*.eml" -exec file {} \; | grep -v "text" | wc -l

# Verify service health
curl http://localhost:8080/health
```

This comprehensive deployment guide provides all the information needed to successfully deploy the Mailyte Mail Server in various environments, from development to enterprise-scale production deployments.
