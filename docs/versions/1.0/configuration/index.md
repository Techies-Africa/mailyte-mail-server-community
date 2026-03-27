
# Configuration Overview

The Mailyte Mail Server uses a comprehensive configuration system based on environment variables, service-specific configuration files, and runtime parameters. This section provides complete guidance on configuring all aspects of the system.

## Configuration Hierarchy

The system follows a hierarchical configuration approach:

1. **Environment Variables** (`.env` file) - Primary configuration
2. **Service Configuration Files** - Service-specific settings
3. **Database Configuration** - Runtime configurable settings
4. **Runtime Parameters** - API and webhook configurations

## Environment Variable Structure

The `.env` file is organized into logical sections:

```bash
# =============================================================================
# ENTERPRISE EMAIL SERVER ENVIRONMENT CONFIGURATION
# =============================================================================

# Database Configuration
DB_HOST=mysql
DB_PORT=3306
DB_NAME=mailserver
# ... additional database settings

# General Email Server Settings
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
# ... additional server settings

# Service-Specific Configurations
POSTFIX_MYHOSTNAME=mail.yourdomain.com
DOVECOT_MAIL_LOCATION=maildir:/var/mail/vhosts/%d/%n
WEBHOOK_URLS=https://your-webhook-endpoint.com/webhook
# ... additional service settings
```

## Configuration Categories

### 1. Database Configuration
```bash
# Primary database connection
DB_HOST=mysql
DB_PORT=3306
DB_NAME=mailserver
DB_USER=root
DB_PASSWORD=secure_password

# Connection pooling for high performance
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=3600
```

### 2. Mail Server Settings
```bash
# Core server identification
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
ADMIN_EMAIL=admin@yourdomain.com

# SSL/TLS configuration
SSL_CERT_PATH=/etc/ssl/certs/mail.crt
SSL_KEY_PATH=/etc/ssl/private/mail.key
ENABLE_TLS=true
TLS_PROTOCOLS=TLSv1.2,TLSv1.3
```

### 3. Postfix Configuration
```bash
# Server identity
POSTFIX_MYHOSTNAME=mail.yourdomain.com
POSTFIX_MYDOMAIN=yourdomain.com
POSTFIX_MYORIGIN=$mydomain

# Virtual mailbox configuration
POSTFIX_VIRTUAL_MAILBOX_DOMAINS=mysql:/etc/postfix/mysql-virtual-mailbox-domains.cf
POSTFIX_VIRTUAL_MAILBOX_MAPS=mysql:/etc/postfix/mysql-virtual-mailbox-maps.cf
POSTFIX_VIRTUAL_ALIAS_MAPS=mysql:/etc/postfix/mysql-virtual-alias-maps.cf

# Message size and security limits
POSTFIX_MESSAGE_SIZE_LIMIT=52428800  # 50MB
POSTFIX_MAILBOX_SIZE_LIMIT=1073741824  # 1GB

# Rate limiting configuration
POSTFIX_ANVIL_RATE_TIME_UNIT=60s
POSTFIX_ANVIL_RATE_COUNT_MAX=20

# Security settings
POSTFIX_SMTPD_HELO_REQUIRED=yes
POSTFIX_SMTPD_DELAY_REJECT=yes
POSTFIX_DISABLE_VRFY_COMMAND=yes
```

### 4. Dovecot Configuration
```bash
# Mail storage location
DOVECOT_MAIL_LOCATION=maildir:/var/mail/vhosts/%d/%n
DOVECOT_MAIL_PRIVILEGED_GROUP=mail
DOVECOT_FIRST_VALID_UID=1000
DOVECOT_LAST_VALID_UID=1000

# Authentication settings
DOVECOT_AUTH_MECHANISMS=plain login
DOVECOT_DISABLE_PLAINTEXT_AUTH=yes
DOVECOT_AUTH_SOCKET_PATH=/var/run/dovecot/auth-postfix

# SSL configuration
DOVECOT_SSL=required
DOVECOT_SSL_CERT_PATH=/etc/ssl/certs/mail.crt
DOVECOT_SSL_KEY_PATH=/etc/ssl/private/mail.key
DOVECOT_SSL_PROTOCOLS=!SSLv2 !SSLv3
```

### 5. Webhook Configuration
```bash
# Webhook endpoints (comma-separated)
WEBHOOK_URLS=https://your-webhook-endpoint.com/webhook,https://backup-webhook.com/webhook

# Security settings
WEBHOOK_SECRET=your-webhook-secret-key-here
WEBHOOK_ENCRYPTION_KEY=your-32-character-encryption-key

# Behavior settings
WEBHOOK_TIMEOUT=30
WEBHOOK_RETRY_ATTEMPTS=3
WEBHOOK_RETRY_DELAY=5
WEBHOOK_BATCH_SIZE=50

# Event filtering
WEBHOOK_ENABLED_EVENTS=email.smtp.inbound,email.smtp.outbound,email.imap.read,dovecot.auth.success
```

### 6. Email Tracking Configuration
```bash
# Core tracking settings
TRACKING_ENABLED=true
OPEN_TRACKING_ENABLED=true
CLICK_TRACKING_ENABLED=true

# Tracking domain
TRACKING_DOMAIN=yourdomain.com
TRACKING_SUBDOMAIN=track
TRACKING_PROTOCOL=https
TRACKING_REQUIRE_SSL=true

# Privacy and analytics
TRACK_USER_AGENT=true
TRACK_IP_ADDRESS=true
TRACK_GEOLOCATION=true
TRACKING_ANONYMIZE_IP=false
TRACKING_IP_RETENTION_DAYS=90
TRACKING_DATA_RETENTION_DAYS=365
```

### 7. RAG (AI) Configuration
```bash
# Core RAG settings
RAG_MODE=local
RAG_TENANT_ID=default
RAG_HOST=0.0.0.0
RAG_PORT=8090
RAG_ENABLE_MULTI_TENANCY=true

# Vector database (Qdrant)
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION_PREFIX=mailrag
QDRANT_VECTOR_SIZE=1536
QDRANT_DISTANCE_METRIC=cosine

# Embedding configuration
EMBEDDING_PROVIDER=sentence_transformers
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2
EMBEDDING_PRIVACY_MODE=true
EMBEDDING_BATCH_SIZE=100
```

### 8. Rate Limiting Configuration
```bash
# Service settings
RATE_LIMITER_HOST=0.0.0.0
RATE_LIMITER_PORT=8082
RATE_LIMITER_DEBUG=false

# Default limits - Organization level
ORG_INBOUND_HOURLY_DEFAULT=5000
ORG_INBOUND_DAILY_DEFAULT=50000
ORG_OUTBOUND_HOURLY_DEFAULT=10000
ORG_OUTBOUND_DAILY_DEFAULT=100000

# Default limits - Domain level
DOMAIN_INBOUND_HOURLY_DEFAULT=1000
DOMAIN_INBOUND_DAILY_DEFAULT=10000
DOMAIN_OUTBOUND_HOURLY_DEFAULT=2000
DOMAIN_OUTBOUND_DAILY_DEFAULT=20000

# Alert thresholds
RATE_LIMIT_WARNING_THRESHOLD=80
RATE_LIMIT_CRITICAL_THRESHOLD=95
```

### 9. Storage and Backup Configuration
```bash
# Storage paths
STORAGE_DATA_PATH=/storage/mail_data
STORAGE_ATTACHMENT_PATH=/storage/attachments
STORAGE_TEMP_PATH=/tmp/storage_calculations

# Cloud storage
CLOUD_SYNC_ENABLED=true
CLOUD_SYNC_PROVIDER=s3
CLOUD_SYNC_BUCKET=your-backup-bucket
CLOUD_SYNC_ACCESS_KEY=your-access-key
CLOUD_SYNC_SECRET_KEY=your-secret-key

# Backup settings
BACKUP_ENABLED=true
BACKUP_SCHEDULE=0 2 * * *  # Daily at 2 AM
BACKUP_RETENTION_DAYS=30
BACKUP_COMPRESSION=gzip
```

### 10. Security Configuration
```bash
# SSL Certificate management
ACME_EMAIL=admin@yourdomain.com
ACME_STAGING=false
CERT_RENEWAL_DAYS=30
CERT_CHECK_INTERVAL=21600  # 6 hours

# Anti-spam settings
RSPAMD_ENABLED=true
SPAM_THRESHOLD_REJECT=15.0
SPAM_THRESHOLD_ADD_HEADER=6.0
SPAM_THRESHOLD_GREYLIST=4.0
ENABLE_GREYLISTING=true
ENABLE_DKIM_SIGNING=true
ENABLE_SPF_CHECK=true
ENABLE_DMARC_CHECK=true
```

## Service-Specific Configuration Files

### Postfix Configuration (`mailer/postfix/config/`)
- `main.cf` - Main Postfix configuration
- `master.cf` - Service definitions
- `mysql-virtual-*.cf` - Database lookup configurations
- `header_checks`, `body_checks` - Content filtering rules

### Dovecot Configuration (`mailer/dovecot/config/`)
- `dovecot.conf` - Main Dovecot configuration
- `dovecot-sql.conf.ext` - Database authentication

### Rspamd Configuration (`mailer/rspamd/config/`)
- `antivirus.conf` - Antivirus integration
- `classifier-bayes.conf` - Bayesian filtering
- `greylisting.conf` - Greylisting settings

## Configuration Validation

### Environment Variable Validation
```python
# Example validation in Python services
import os
from typing import Optional

def validate_config():
    required_vars = [
        'DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
        'HOSTNAME', 'DOMAIN', 'WEBHOOK_SECRET'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")
```

### Database Configuration Validation
```sql
-- Test database connectivity
SELECT 1 as connection_test;

-- Validate table structure
SHOW TABLES LIKE 'domains';
SHOW TABLES LIKE 'users';
SHOW TABLES LIKE 'aliases';
```

## Configuration Templates

### Development Configuration
```bash
# Development settings
DEVELOPMENT_MODE=true
DEBUG_MODE=true
LOG_LEVEL=DEBUG

# Use local services
WEBHOOK_URLS=http://localhost:5000/webhook
TRACKING_DOMAIN=localhost
CLOUD_SYNC_ENABLED=false
```

### Production Configuration
```bash
# Production settings
DEVELOPMENT_MODE=false
DEBUG_MODE=false
LOG_LEVEL=INFO

# Production services
WEBHOOK_URLS=https://production-webhook.com/webhook
TRACKING_DOMAIN=yourdomain.com
CLOUD_SYNC_ENABLED=true

# Enhanced security
TLS_PROTOCOLS=TLSv1.3
TRACKING_REQUIRE_SSL=true
DOVECOT_SSL=required
```

### Testing Configuration
```bash
# Testing environment
TESTING_MODE=true
DB_NAME=mailserver_test
WEBHOOK_URLS=http://webhook-test.local/webhook

# Mock services
MOCK_WEBHOOK_ENABLED=true
MOCK_QDRANT_ENABLED=true
MOCK_EMBEDDING_ENABLED=true
```

## Configuration Management Best Practices

### 1. Environment-Specific Files
- Use `.env.development`, `.env.production`, `.env.testing`
- Never commit production secrets to version control
- Use environment variable injection in deployment

### 2. Secret Management
```bash
# Use strong, randomly generated secrets
WEBHOOK_SECRET=$(openssl rand -hex 32)
WEBHOOK_ENCRYPTION_KEY=$(openssl rand -hex 16)
DB_PASSWORD=$(openssl rand -base64 32)
```

### 3. Configuration Validation
- Validate configuration on service startup
- Provide clear error messages for invalid configurations
- Use configuration schemas where possible

### 4. Dynamic Configuration
```python
# Runtime configuration updates via API
PUT /api/v1/config/webhook
{
    "webhook_urls": ["https://new-webhook.com/endpoint"],
    "retry_attempts": 5,
    "timeout": 45
}
```

## Troubleshooting Configuration Issues

### Common Issues and Solutions

#### 1. Database Connection Failures
```bash
# Test database connectivity
mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p$DB_PASSWORD $DB_NAME -e "SELECT 1;"
```

#### 2. SSL Certificate Issues
```bash
# Verify certificate validity
openssl x509 -in $SSL_CERT_PATH -text -noout
openssl rsa -in $SSL_KEY_PATH -check
```

#### 3. Service Communication Issues
```bash
# Test inter-service connectivity
curl -f http://webhooks:8081/health
curl -f http://tracking:8083/health
curl -f http://rate-limiter:8082/health
```

#### 4. Configuration File Syntax
```bash
# Validate Postfix configuration
postfix check

# Test Dovecot configuration
dovecot -n
```

This comprehensive configuration overview provides developers with everything needed to understand, modify, and extend the system's configuration capabilities.
