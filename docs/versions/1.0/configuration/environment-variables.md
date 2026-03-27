
# Environment Variables Reference

This document provides a complete reference for all environment variables used to configure the Mailyte Mail Server. All configuration is managed through environment variables, making deployment flexible across different environments.

## Configuration File Location

Environment variables are typically stored in the `.env` file at the root of your installation:

```bash
/opt/enterprise-mail-server/.env
```

!!! tip "Environment Precedence"
    Environment variables can be set in multiple ways, with the following precedence (highest to lowest):
    
    1. System environment variables
    2. `.env` file in the application root
    3. Default values in the application

## Core Configuration

### Server Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HOSTNAME` | string | **required** | Main hostname for the mail server (e.g., `mail.yourdomain.com`) |
| `ADMIN_EMAIL` | email | **required** | Administrator email address for certificates and notifications |
| `ENVIRONMENT` | enum | `production` | Environment mode: `development`, `staging`, `production` |
| `DEBUG` | boolean | `false` | Enable debug logging and detailed error messages |
| `TIMEZONE` | string | `UTC` | Server timezone (e.g., `America/New_York`, `Europe/London`) |
| `LOCALE` | string | `en_US` | Default locale for the mail server |

!!! example "Example: Core Configuration"
    
    ```bash
    # Core server settings
    HOSTNAME=mail.yourdomain.com
    ADMIN_EMAIL=admin@yourdomain.com
    ENVIRONMENT=production
    DEBUG=false
    TIMEZONE=UTC
    LOCALE=en_US
    ```

## Database Configuration

### MySQL Database

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DB_HOST` | string | **required** | MySQL database hostname |
| `DB_PORT` | integer | `3306` | MySQL database port |
| `DB_NAME` | string | **required** | Database name |
| `DB_USER` | string | **required** | Database username |
| `DB_PASSWORD` | string | **required** | Database password |
| `DB_CHARSET` | string | `utf8mb4` | Database character set |
| `DB_COLLATION` | string | `utf8mb4_unicode_ci` | Database collation |
| `DB_SSL_MODE` | enum | `DISABLED` | SSL mode: `DISABLED`, `REQUIRED`, `VERIFY_CA`, `VERIFY_IDENTITY` |
| `DB_SSL_CERT` | path | - | Path to SSL certificate file |
| `DB_SSL_KEY` | path | - | Path to SSL private key file |
| `DB_SSL_CA` | path | - | Path to SSL CA certificate file |

### Connection Pool Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DB_POOL_MIN_CONNECTIONS` | integer | `5` | Minimum database connections in pool |
| `DB_POOL_MAX_CONNECTIONS` | integer | `50` | Maximum database connections in pool |
| `DB_CONNECTION_TIMEOUT` | integer | `30` | Connection timeout in seconds |
| `DB_POOL_TIMEOUT` | integer | `60` | Pool acquisition timeout in seconds |
| `DB_MAX_IDLE_TIME` | integer | `300` | Maximum idle time for connections in seconds |

!!! example "Example: Database Configuration"
    
    ```bash
    # MySQL Configuration
    DB_HOST=mysql.yourdomain.com
    DB_PORT=3306
    DB_NAME=enterprise_mail
    DB_USER=mailserver_user
    DB_PASSWORD=your_secure_password
    DB_CHARSET=utf8mb4
    DB_COLLATION=utf8mb4_unicode_ci
    
    # SSL Configuration (for production)
    DB_SSL_MODE=REQUIRED
    
    # Connection Pool
    DB_POOL_MIN_CONNECTIONS=10
    DB_POOL_MAX_CONNECTIONS=100
    DB_CONNECTION_TIMEOUT=30
    ```

### Redis Cache

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_HOST` | string | `localhost` | Redis server hostname |
| `REDIS_PORT` | integer | `6379` | Redis server port |
| `REDIS_PASSWORD` | string | - | Redis authentication password |
| `REDIS_DB` | integer | `0` | Redis database number |
| `REDIS_SSL` | boolean | `false` | Enable SSL/TLS for Redis connection |
| `REDIS_POOL_SIZE` | integer | `10` | Redis connection pool size |
| `REDIS_TIMEOUT` | integer | `5` | Redis connection timeout in seconds |

## Cloud Storage Configuration

### Provider Selection

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CLOUD_PROVIDER` | enum | **required** | Cloud provider: `aws`, `azure`, `gcp`, `local` |
| `STORAGE_ENCRYPTION` | boolean | `true` | Enable encryption for stored emails |
| `STORAGE_COMPRESSION` | boolean | `true` | Enable compression for stored emails |

### AWS S3 Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | string | **required** | AWS access key ID |
| `AWS_SECRET_ACCESS_KEY` | string | **required** | AWS secret access key |
| `AWS_SESSION_TOKEN` | string | - | AWS session token (for temporary credentials) |
| `AWS_REGION` | string | `us-east-1` | AWS region |
| `AWS_S3_BUCKET` | string | **required** | S3 bucket name for mail storage |
| `AWS_S3_PREFIX` | string | `mail/` | S3 key prefix for organization |
| `AWS_S3_STORAGE_CLASS` | enum | `STANDARD` | S3 storage class: `STANDARD`, `IA`, `GLACIER` |
| `AWS_S3_ENDPOINT` | string | - | Custom S3 endpoint (for S3-compatible services) |
| `AWS_S3_PATH_STYLE` | boolean | `false` | Use path-style S3 URLs |

### Azure Blob Storage Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AZURE_STORAGE_CONNECTION_STRING` | string | **required** | Azure storage connection string |
| `AZURE_CONTAINER_NAME` | string | **required** | Azure blob container name |
| `AZURE_STORAGE_PREFIX` | string | `mail/` | Blob prefix for organization |
| `AZURE_STORAGE_TIER` | enum | `Hot` | Storage tier: `Hot`, `Cool`, `Archive` |

### Google Cloud Storage Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GCP_PROJECT_ID` | string | **required** | Google Cloud project ID |
| `GCP_SERVICE_ACCOUNT_KEY` | string | **required** | Service account key JSON (base64 encoded) |
| `GCP_STORAGE_BUCKET` | string | **required** | GCS bucket name |
| `GCP_STORAGE_PREFIX` | string | `mail/` | Object prefix for organization |
| `GCP_STORAGE_CLASS` | enum | `STANDARD` | Storage class: `STANDARD`, `NEARLINE`, `COLDLINE` |

!!! example "Example: Cloud Storage Configuration"
    
    === "AWS S3"
        
        ```bash
        # AWS S3 Configuration
        CLOUD_PROVIDER=aws
        AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
        AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
        AWS_REGION=us-east-1
        AWS_S3_BUCKET=my-mail-storage
        AWS_S3_PREFIX=production/mail/
        AWS_S3_STORAGE_CLASS=STANDARD
        
        # Storage options
        STORAGE_ENCRYPTION=true
        STORAGE_COMPRESSION=true
        ```
    
    === "Azure Blob"
        
        ```bash
        # Azure Blob Storage Configuration
        CLOUD_PROVIDER=azure
        AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
        AZURE_CONTAINER_NAME=mail-storage
        AZURE_STORAGE_PREFIX=production/mail/
        AZURE_STORAGE_TIER=Hot
        
        # Storage options
        STORAGE_ENCRYPTION=true
        STORAGE_COMPRESSION=true
        ```

## Email Service Configuration

### SMTP Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SMTP_PORT` | integer | `25` | Standard SMTP port |
| `SUBMISSION_PORT` | integer | `587` | Mail submission port (with STARTTLS) |
| `SMTPS_PORT` | integer | `465` | SMTP over SSL port |
| `SMTP_BANNER` | string | `$myhostname ESMTP` | SMTP greeting banner |
| `SMTP_TIMEOUT` | integer | `300` | SMTP connection timeout in seconds |
| `SMTP_MAX_CONNECTIONS` | integer | `100` | Maximum concurrent SMTP connections |

### IMAP/POP3 Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `IMAP_PORT` | integer | `143` | IMAP port (with STARTTLS) |
| `IMAPS_PORT` | integer | `993` | IMAP over SSL port |
| `POP3_PORT` | integer | `110` | POP3 port (with STARTTLS) |
| `POP3S_PORT` | integer | `995` | POP3 over SSL port |
| `IMAP_IDLE_TIMEOUT` | integer | `1800` | IMAP IDLE timeout in seconds |
| `IMAP_MAX_CONNECTIONS` | integer | `200` | Maximum IMAP connections per user |

### Message Limits

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MESSAGE_SIZE_LIMIT` | size | `50MB` | Maximum message size |
| `ATTACHMENT_SIZE_LIMIT` | size | `25MB` | Maximum attachment size |
| `MAILBOX_SIZE_LIMIT` | size | `10GB` | Default mailbox quota |
| `MAX_RECIPIENTS_PER_MESSAGE` | integer | `100` | Maximum recipients per message |
| `MAX_MESSAGES_PER_HOUR` | integer | `1000` | Default hourly sending limit |

!!! example "Example: Email Service Configuration"
    
    ```bash
    # SMTP Configuration
    SMTP_PORT=25
    SUBMISSION_PORT=587
    SMTPS_PORT=465
    SMTP_BANNER="$myhostname ESMTP Mailyte Mail Server"
    SMTP_MAX_CONNECTIONS=200
    
    # IMAP/POP3 Configuration
    IMAP_PORT=143
    IMAPS_PORT=993
    POP3_PORT=110
    POP3S_PORT=995
    IMAP_MAX_CONNECTIONS=300
    
    # Message Limits
    MESSAGE_SIZE_LIMIT=100MB
    ATTACHMENT_SIZE_LIMIT=50MB
    MAILBOX_SIZE_LIMIT=25GB
    MAX_RECIPIENTS_PER_MESSAGE=500
    MAX_MESSAGES_PER_HOUR=5000
    ```

## Security Configuration

### SSL/TLS Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SSL_ENABLED` | boolean | `true` | Enable SSL/TLS certificates |
| `SSL_PROVIDER` | enum | `letsencrypt` | SSL provider: `letsencrypt`, `custom`, `self-signed` |
| `LETSENCRYPT_EMAIL` | email | - | Email for Let's Encrypt notifications |
| `LETSENCRYPT_STAGING` | boolean | `false` | Use Let's Encrypt staging environment |
| `SSL_CERT_PATH` | path | - | Path to custom SSL certificate |
| `SSL_KEY_PATH` | path | - | Path to custom SSL private key |
| `SSL_CA_PATH` | path | - | Path to SSL CA certificate |
| `TLS_MIN_VERSION` | enum | `1.2` | Minimum TLS version: `1.0`, `1.1`, `1.2`, `1.3` |
| `TLS_CIPHERS` | string | `HIGH:!aNULL:!MD5` | TLS cipher suite configuration |

### Authentication Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AUTH_MECHANISMS` | list | `PLAIN,LOGIN` | Supported SASL mechanisms |
| `AUTH_ALLOW_PLAINTEXT` | boolean | `false` | Allow plaintext authentication over non-TLS |
| `AUTH_MAX_ATTEMPTS` | integer | `3` | Maximum login attempts before lockout |
| `AUTH_LOCKOUT_DURATION` | integer | `900` | Account lockout duration in seconds |
| `PASSWORD_MIN_LENGTH` | integer | `8` | Minimum password length |
| `PASSWORD_REQUIRE_MIXED_CASE` | boolean | `true` | Require upper and lowercase letters |
| `PASSWORD_REQUIRE_NUMBERS` | boolean | `true` | Require numbers in passwords |
| `PASSWORD_REQUIRE_SYMBOLS` | boolean | `false` | Require symbols in passwords |

### Anti-Spam Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RSPAMD_ENABLED` | boolean | `true` | Enable Rspamd spam filtering |
| `RSPAMD_PASSWORD` | string | **required** | Rspamd web interface password |
| `SPAM_THRESHOLD` | float | `5.0` | Spam score threshold |
| `SPAM_QUARANTINE_THRESHOLD` | float | `15.0` | Quarantine threshold |
| `SPAM_REJECT_THRESHOLD` | float | `25.0` | Reject threshold |
| `ANTIVIRUS_ENABLED` | boolean | `true` | Enable ClamAV antivirus scanning |
| `GREYLISTING_ENABLED` | boolean | `true` | Enable greylisting |
| `DNSBL_ENABLED` | boolean | `true` | Enable DNS blacklist checking |

!!! example "Example: Security Configuration"
    
    ```bash
    # SSL/TLS Configuration
    SSL_ENABLED=true
    SSL_PROVIDER=letsencrypt
    LETSENCRYPT_EMAIL=admin@yourdomain.com
    TLS_MIN_VERSION=1.2
    TLS_CIPHERS="HIGH:!aNULL:!MD5:!3DES"
    
    # Authentication
    AUTH_MECHANISMS="PLAIN,LOGIN,CRAM-MD5"
    AUTH_ALLOW_PLAINTEXT=false
    AUTH_MAX_ATTEMPTS=5
    AUTH_LOCKOUT_DURATION=1800
    
    # Password Policy
    PASSWORD_MIN_LENGTH=12
    PASSWORD_REQUIRE_MIXED_CASE=true
    PASSWORD_REQUIRE_NUMBERS=true
    PASSWORD_REQUIRE_SYMBOLS=true
    
    # Anti-Spam
    RSPAMD_ENABLED=true
    RSPAMD_PASSWORD=generate_secure_password
    SPAM_THRESHOLD=4.0
    ANTIVIRUS_ENABLED=true
    GREYLISTING_ENABLED=true
    ```

## Rate Limiting & Abuse Prevention

### Rate Limiting Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RATE_LIMIT_ENABLED` | boolean | `true` | Enable rate limiting |
| `RATE_LIMIT_BACKEND` | enum | `redis` | Backend for rate limiting: `redis`, `database` |
| `RATE_LIMIT_WINDOW` | integer | `3600` | Rate limit window in seconds |
| `RATE_LIMIT_MAX_RECIPIENTS` | integer | `1000` | Max recipients per window |
| `RATE_LIMIT_MAX_MESSAGES` | integer | `500` | Max messages per window |
| `RATE_LIMIT_MAX_CONNECTIONS` | integer | `50` | Max connections per IP |

### Abuse Prevention

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `FAIL2BAN_ENABLED` | boolean | `true` | Enable fail2ban intrusion detection |
| `FAIL2BAN_MAX_RETRY` | integer | `3` | Maximum failed attempts |
| `FAIL2BAN_BAN_TIME` | integer | `3600` | Ban duration in seconds |
| `FAIL2BAN_FIND_TIME` | integer | `600` | Time window for failed attempts |
| `IP_WHITELIST` | list | - | Comma-separated list of whitelisted IPs |
| `IP_BLACKLIST` | list | - | Comma-separated list of blacklisted IPs |

## Feature Configuration

### Email Tracking

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `EMAIL_TRACKING_ENABLED` | boolean | `true` | Enable email tracking features |
| `TRACKING_PIXEL_ENABLED` | boolean | `true` | Enable open tracking |
| `LINK_TRACKING_ENABLED` | boolean | `true` | Enable click tracking |
| `TRACKING_DOMAIN` | string | `$HOSTNAME` | Domain for tracking links |
| `TRACKING_RETENTION_DAYS` | integer | `365` | Tracking data retention period |

### Webhooks

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `WEBHOOK_ENABLED` | boolean | `true` | Enable webhook notifications |
| `WEBHOOK_SECRET` | string | **required** | Secret for webhook authentication |
| `WEBHOOK_TIMEOUT` | integer | `30` | Webhook request timeout in seconds |
| `WEBHOOK_RETRY_ATTEMPTS` | integer | `3` | Number of retry attempts |
| `WEBHOOK_RETRY_DELAY` | integer | `5` | Initial retry delay in seconds |

### Analytics

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANALYTICS_ENABLED` | boolean | `true` | Enable analytics collection |
| `ANALYTICS_RETENTION_DAYS` | integer | `730` | Analytics data retention period |
| `METRICS_ENABLED` | boolean | `true` | Enable Prometheus metrics |
| `METRICS_PORT` | integer | `9090` | Metrics endpoint port |

!!! example "Example: Feature Configuration"
    
    ```bash
    # Rate Limiting
    RATE_LIMIT_ENABLED=true
    RATE_LIMIT_BACKEND=redis
    RATE_LIMIT_MAX_RECIPIENTS=2000
    RATE_LIMIT_MAX_MESSAGES=1000
    
    # Email Tracking
    EMAIL_TRACKING_ENABLED=true
    TRACKING_PIXEL_ENABLED=true
    LINK_TRACKING_ENABLED=true
    TRACKING_DOMAIN=track.yourdomain.com
    
    # Webhooks
    WEBHOOK_ENABLED=true
    WEBHOOK_SECRET=your_webhook_secret_here
    WEBHOOK_TIMEOUT=45
    WEBHOOK_RETRY_ATTEMPTS=5
    
    # Analytics
    ANALYTICS_ENABLED=true
    ANALYTICS_RETENTION_DAYS=1095
    METRICS_ENABLED=true
    ```

## Monitoring & Logging

### Logging Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | enum | `INFO` | Log level: `DEBUG`, `INFO`, `WARN`, `ERROR` |
| `LOG_FORMAT` | enum | `json` | Log format: `json`, `text` |
| `LOG_OUTPUT` | enum | `file` | Log output: `file`, `stdout`, `syslog` |
| `LOG_FILE_PATH` | path | `/var/log/mailserver/` | Log file directory |
| `LOG_MAX_SIZE` | size | `100MB` | Maximum log file size |
| `LOG_MAX_FILES` | integer | `10` | Maximum number of log files |
| `LOG_RETENTION_DAYS` | integer | `30` | Log file retention period |

### Monitoring

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MONITORING_ENABLED` | boolean | `true` | Enable monitoring dashboard |
| `MONITORING_PORT` | integer | `8080` | Monitoring dashboard port |
| `MONITORING_AUTH_ENABLED` | boolean | `true` | Enable dashboard authentication |
| `MONITORING_USERNAME` | string | `admin` | Dashboard username |
| `MONITORING_PASSWORD` | string | **required** | Dashboard password |
| `HEALTH_CHECK_INTERVAL` | integer | `60` | Health check interval in seconds |

### Backup Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BACKUP_ENABLED` | boolean | `true` | Enable automatic backups |
| `BACKUP_SCHEDULE` | cron | `0 2 * * *` | Backup schedule (cron format) |
| `BACKUP_RETENTION_DAYS` | integer | `30` | Backup retention period |
| `BACKUP_DESTINATION` | enum | `cloud` | Backup destination: `local`, `cloud` |
| `BACKUP_ENCRYPTION_ENABLED` | boolean | `true` | Enable backup encryption |
| `BACKUP_ENCRYPTION_KEY` | string | - | Backup encryption key |

!!! example "Example: Monitoring & Logging"
    
    ```bash
    # Logging Configuration
    LOG_LEVEL=INFO
    LOG_FORMAT=json
    LOG_OUTPUT=file
    LOG_FILE_PATH=/var/log/mailserver/
    LOG_RETENTION_DAYS=60
    
    # Monitoring
    MONITORING_ENABLED=true
    MONITORING_PORT=8080
    MONITORING_AUTH_ENABLED=true
    MONITORING_USERNAME=admin
    MONITORING_PASSWORD=secure_dashboard_password
    
    # Backup
    BACKUP_ENABLED=true
    BACKUP_SCHEDULE="0 3 * * *"
    BACKUP_RETENTION_DAYS=90
    BACKUP_ENCRYPTION_ENABLED=true
    ```

## Performance Tuning

### Worker Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `WORKER_PROCESSES` | integer | `4` | Number of worker processes |
| `WORKER_THREADS` | integer | `8` | Threads per worker process |
| `WORKER_TIMEOUT` | integer | `300` | Worker timeout in seconds |
| `QUEUE_MAX_SIZE` | integer | `10000` | Maximum queue size |
| `QUEUE_BATCH_SIZE` | integer | `100` | Queue processing batch size |

### Cache Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CACHE_TTL` | integer | `3600` | Default cache TTL in seconds |
| `CACHE_MAX_MEMORY` | size | `1GB` | Maximum cache memory usage |
| `CACHE_EVICTION_POLICY` | enum | `lru` | Cache eviction policy: `lru`, `lfu`, `random` |

## Development & Testing

### Development Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEV_MODE` | boolean | `false` | Enable development mode |
| `DEV_SMTP_CATCH_ALL` | email | - | Catch all emails to this address |
| `DEV_DISABLE_TLS` | boolean | `false` | Disable TLS verification |
| `DEV_MOCK_CLOUD_STORAGE` | boolean | `false` | Use local mock for cloud storage |

### Testing Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TEST_MODE` | boolean | `false` | Enable test mode |
| `TEST_EMAIL_DOMAIN` | string | `test.local` | Test email domain |
| `TEST_SKIP_DNS_CHECKS` | boolean | `true` | Skip DNS validation in tests |

!!! warning "Security Notice"
    Development and testing variables should **never** be enabled in production environments. They can compromise security and data integrity.

## Validation and Best Practices

### Configuration Validation

The mail server validates all environment variables on startup:

```bash
# Validate configuration
python3 main.py config validate

# Show current configuration
python3 main.py config show

# Test configuration
python3 main.py config test
```

### Security Best Practices

1. **Use Strong Passwords**: All password variables should use randomly generated, strong passwords
2. **Secure Storage**: Store sensitive variables in secure secret management systems
3. **Regular Rotation**: Rotate API keys and passwords regularly
4. **Principle of Least Privilege**: Configure minimal required permissions
5. **Environment Separation**: Use different configurations for different environments

### Performance Optimization

1. **Database Connections**: Tune pool sizes based on expected load
2. **Worker Processes**: Set based on CPU cores available
3. **Cache Settings**: Optimize cache TTL and memory usage
4. **Rate Limits**: Set appropriate limits for your use case

---

!!! success "Configuration Complete"
    Once you've configured all necessary environment variables, restart the mail server to apply changes:
    
    ```bash
    python3 main.py restart
    ```
    
    Verify your configuration with:
    
    ```bash
    python3 main.py status
    python3 main.py config validate
    ```
