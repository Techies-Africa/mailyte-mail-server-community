-- =============================================================================
-- Mailyte Email Server — Database Initialization Schema
-- This file bootstraps the database for Docker MySQL init.
-- It MUST match the Alembic migration: alembic/versions/001_initial_schema.py
-- For schema changes use Alembic: python manage.py migrate:create <name>
-- =============================================================================

SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

-- Create the Alembic version table so Alembic knows this schema is applied
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO alembic_version (version_num) VALUES ('001_initial_schema');

-- =============================================================================
-- System tables (no FK dependencies)
-- =============================================================================

CREATE TABLE IF NOT EXISTS system_config (
    `key` VARCHAR(255) NOT NULL PRIMARY KEY,
    `value` JSON NULL,
    description TEXT NULL,
    category VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_system_config_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS health_checks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    response_time FLOAT NULL,
    error_message TEXT NULL,
    metadata JSON NULL,
    `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_health_service (service_name),
    INDEX idx_health_status (status),
    INDEX idx_health_timestamp (`timestamp`),
    INDEX idx_health_service_time (service_name, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS service_metrics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value FLOAT NOT NULL,
    `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_metrics_service (service_name),
    INDEX idx_metrics_name (metric_name),
    INDEX idx_metrics_timestamp (`timestamp`),
    INDEX idx_metrics_service_metric (service_name, metric_name, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Organizations
-- =============================================================================

CREATE TABLE IF NOT EXISTS organizations (
    id VARCHAR(100) NOT NULL PRIMARY KEY,
    external_id VARCHAR(255) NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    admin_email VARCHAR(255) NULL,
    admin_name VARCHAR(255) NULL,
    settings JSON NULL,
    rate_limits JSON NULL,
    storage_quotas JSON NULL,
    webhook_urls JSON NULL,
    webhook_secret VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_org_active (active),
    INDEX idx_org_name (name),
    INDEX idx_org_external_id (external_id),
    INDEX idx_org_active_created (active, created_at),
    INDEX idx_org_name_active (name, active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Domains
-- =============================================================================

CREATE TABLE IF NOT EXISTS domains (
    id INT AUTO_INCREMENT PRIMARY KEY,
    domain VARCHAR(255) NOT NULL UNIQUE,
    external_id VARCHAR(255) NULL UNIQUE,
    organization_id VARCHAR(100) NOT NULL,
    description TEXT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    max_quota BIGINT NOT NULL DEFAULT 10737418240,
    max_users INT NOT NULL DEFAULT 1000,
    dkim_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    dkim_selector VARCHAR(100) NOT NULL DEFAULT 'default',
    rate_limits JSON NULL,
    storage_quotas JSON NULL,
    total_storage_used BIGINT NOT NULL DEFAULT 0,
    total_attachment_storage BIGINT NOT NULL DEFAULT 0,
    total_email_storage BIGINT NOT NULL DEFAULT 0,
    total_email_accounts INT NOT NULL DEFAULT 0,
    total_emails BIGINT NOT NULL DEFAULT 0,
    total_attachments BIGINT NOT NULL DEFAULT 0,
    rate_usage_data JSON NULL,
    last_storage_calculation DATETIME NULL,
    storage_calculation_time_ms INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_domain_org_active (organization_id, active),
    INDEX idx_domain_name_org (domain, organization_id),
    INDEX idx_domain_active_created (active, created_at),
    INDEX idx_domain_org_updated (organization_id, updated_at),
    INDEX idx_domain_storage_used (total_storage_used),
    INDEX idx_domain_storage_calc (last_storage_calculation),
    INDEX idx_domain_external_id (external_id),
    CONSTRAINT fk_domains_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Email Accounts
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    external_id VARCHAR(255) NULL UNIQUE,
    local_part VARCHAR(255) NOT NULL,
    domain_id INT NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    password VARCHAR(255) NOT NULL,
    name VARCHAR(255) NULL,
    status ENUM('active', 'inactive', 'suspended') NOT NULL DEFAULT 'active',
    storage_quota BIGINT NOT NULL DEFAULT 1073741824,
    storage_used BIGINT NOT NULL DEFAULT 0,
    attachment_storage_used BIGINT NOT NULL DEFAULT 0,
    email_storage_used BIGINT NOT NULL DEFAULT 0,
    total_files INT NOT NULL DEFAULT 0,
    total_attachments INT NOT NULL DEFAULT 0,
    total_emails INT NOT NULL DEFAULT 0,
    rate_usage_data JSON NULL,
    rate_limits JSON NULL,
    storage_quotas JSON NULL,
    forward_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    forward_destination VARCHAR(255) NULL,
    vacation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    vacation_message TEXT NULL,
    last_login DATETIME NULL,
    last_activity DATETIME NULL,
    last_storage_calculation DATETIME NULL,
    storage_calculation_time_ms INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_email_domain_org (domain_id, organization_id),
    INDEX idx_email_status (status),
    INDEX idx_email_storage_used (storage_used),
    INDEX idx_email_last_activity (last_activity),
    INDEX idx_email_org_status (organization_id, status),
    INDEX idx_email_org_created (organization_id, created_at),
    INDEX idx_email_local_domain (local_part, domain_id),
    INDEX idx_email_status_activity (status, last_activity),
    INDEX idx_email_org_activity (organization_id, last_activity),
    INDEX idx_email_external_id (external_id),
    CONSTRAINT fk_email_accounts_domain FOREIGN KEY (domain_id) REFERENCES domains(id),
    CONSTRAINT fk_email_accounts_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Aliases
-- =============================================================================

CREATE TABLE IF NOT EXISTS aliases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    domain_id INT NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    source VARCHAR(255) NOT NULL,
    destination TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_aliases_domain FOREIGN KEY (domain_id) REFERENCES domains(id),
    CONSTRAINT fk_aliases_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- User Sessions
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_sessions (
    id VARCHAR(255) NOT NULL PRIMARY KEY,
    email_account_id INT NOT NULL,
    ip_address VARCHAR(45) NULL,
    user_agent TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_activity DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    INDEX idx_sessions_last_activity (last_activity),
    INDEX idx_sessions_expires (expires_at),
    CONSTRAINT fk_sessions_email_account FOREIGN KEY (email_account_id) REFERENCES email_accounts(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Mail Queue
-- =============================================================================

CREATE TABLE IF NOT EXISTS mail_queue (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sender VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    organization_id VARCHAR(100) NULL,
    subject TEXT NULL,
    body TEXT NOT NULL,
    headers JSON NULL,
    priority INT NOT NULL DEFAULT 5,
    status ENUM('queued', 'sending', 'sent', 'delivered', 'bounced', 'rejected', 'deferred') NOT NULL DEFAULT 'queued',
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    scheduled_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME NULL,
    error_message TEXT NULL,
    worker_id VARCHAR(100) NULL,
    processing_time FLOAT NULL,
    INDEX idx_queue_sender (sender),
    INDEX idx_queue_recipient (recipient),
    INDEX idx_queue_priority (priority),
    INDEX idx_queue_status (status),
    INDEX idx_queue_scheduled (scheduled_at),
    INDEX idx_queue_processing (status, scheduled_at, priority),
    CONSTRAINT fk_mail_queue_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Mail Logs
-- =============================================================================

CREATE TABLE IF NOT EXISTS mail_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sender VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    organization_id VARCHAR(100) NULL,
    subject TEXT NULL,
    status ENUM('queued', 'sending', 'sent', 'delivered', 'bounced', 'rejected', 'deferred') NOT NULL,
    message_id VARCHAR(255) NULL,
    size INT NULL,
    relay VARCHAR(255) NULL,
    delays VARCHAR(100) NULL,
    dsn VARCHAR(10) NULL,
    bounce_reason TEXT NULL,
    spam_score FLOAT NULL,
    INDEX idx_mail_logs_timestamp (`timestamp`),
    INDEX idx_mail_logs_sender (sender),
    INDEX idx_mail_logs_recipient (recipient),
    INDEX idx_mail_logs_status (status),
    INDEX idx_mail_logs_message_id (message_id),
    INDEX idx_mail_logs_time_status (`timestamp`, status),
    INDEX idx_mail_logs_sender_time (sender, `timestamp`),
    CONSTRAINT fk_mail_logs_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Email Tracking
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_tracking (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    email_id VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    domain_id INT NOT NULL,
    event_type ENUM('delivered', 'opened', 'clicked', 'bounced', 'complained', 'unsubscribed') NOT NULL,
    `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_agent TEXT NULL,
    ip_address VARCHAR(45) NULL,
    referer TEXT NULL,
    accept_language VARCHAR(255) NULL,
    device_type VARCHAR(100) NULL,
    browser VARCHAR(100) NULL,
    operating_system VARCHAR(100) NULL,
    country VARCHAR(100) NULL,
    region VARCHAR(100) NULL,
    city VARCHAR(100) NULL,
    additional_data JSON NULL,
    INDEX idx_tracking_email_id (email_id),
    INDEX idx_tracking_recipient (recipient),
    INDEX idx_tracking_event_type (event_type),
    INDEX idx_tracking_timestamp (`timestamp`),
    INDEX idx_tracking_ip (ip_address),
    INDEX idx_tracking_country (country),
    INDEX idx_email_event_time (email_id, event_type, `timestamp`),
    INDEX idx_org_time (organization_id, `timestamp`),
    INDEX idx_recipient_time (recipient, `timestamp`),
    INDEX idx_event_time (event_type, `timestamp`),
    CONSTRAINT fk_email_tracking_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
    CONSTRAINT fk_email_tracking_domain FOREIGN KEY (domain_id) REFERENCES domains(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Tracking Statistics
-- =============================================================================

CREATE TABLE IF NOT EXISTS tracking_statistics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    email_id VARCHAR(255) NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    domain_id INT NOT NULL,
    event_type ENUM('delivered', 'opened', 'clicked', 'bounced', 'complained', 'unsubscribed') NOT NULL,
    total_count INT NOT NULL DEFAULT 0,
    unique_count INT NOT NULL DEFAULT 0,
    unique_ips INT NOT NULL DEFAULT 0,
    first_event DATETIME NULL,
    last_event DATETIME NULL,
    last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_stats_email_id (email_id),
    INDEX idx_stats_event_type (event_type),
    INDEX idx_stats_email_event (email_id, event_type),
    INDEX idx_stats_org_event (organization_id, event_type),
    CONSTRAINT fk_tracking_stats_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
    CONSTRAINT fk_tracking_stats_domain FOREIGN KEY (domain_id) REFERENCES domains(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- API Keys
-- =============================================================================

CREATE TABLE IF NOT EXISTS api_keys (
    id INT AUTO_INCREMENT PRIMARY KEY,
    key_id VARCHAR(100) NOT NULL UNIQUE,
    key_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    permissions JSON NULL,
    organization_id VARCHAR(100) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit INT NULL,
    last_used DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NULL,
    ip_whitelist JSON NULL,
    usage_count BIGINT NOT NULL DEFAULT 0,
    INDEX idx_api_key_expires (expires_at),
    INDEX idx_api_key_active (key_id, active),
    CONSTRAINT fk_api_keys_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Webhook URLs
-- =============================================================================

CREATE TABLE IF NOT EXISTS webhook_urls (
    id INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL,
    name VARCHAR(255) NOT NULL,
    url VARCHAR(1000) NOT NULL,
    event_types JSON NOT NULL,
    service_types JSON NOT NULL,
    encryption_key VARCHAR(255) NOT NULL,
    webhook_secret VARCHAR(255) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    priority INT NOT NULL DEFAULT 1,
    timeout_seconds INT NOT NULL DEFAULT 30,
    retry_attempts INT NOT NULL DEFAULT 3,
    retry_delay_seconds INT NOT NULL DEFAULT 5,
    rate_limit_per_minute INT NULL,
    tenant_filter JSON NULL,
    domain_filter JSON NULL,
    custom_headers JSON NULL,
    auth_type VARCHAR(50) NULL,
    auth_credentials VARCHAR(500) NULL,
    last_success DATETIME NULL,
    last_failure DATETIME NULL,
    success_count BIGINT NOT NULL DEFAULT 0,
    failure_count BIGINT NOT NULL DEFAULT 0,
    description TEXT NULL,
    tags JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by VARCHAR(100) NULL,
    INDEX idx_webhook_urls_active (active),
    INDEX idx_webhook_urls_priority (priority),
    INDEX idx_webhook_urls_last_success (last_success),
    CONSTRAINT fk_webhook_urls_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Webhook Delivery Logs
-- =============================================================================

CREATE TABLE IF NOT EXISTS webhook_delivery_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    webhook_url_id INT NULL,
    event_type VARCHAR(100) NOT NULL,
    event_data JSON NOT NULL,
    webhook_url VARCHAR(1000) NOT NULL,
    webhook_name VARCHAR(255) NULL,
    delivery_status ENUM('pending', 'delivered', 'failed', 'retrying', 'abandoned') NOT NULL DEFAULT 'pending',
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    next_retry_at DATETIME NULL,
    retry_delay_seconds INT NOT NULL DEFAULT 5,
    backoff_multiplier FLOAT NOT NULL DEFAULT 2.0,
    max_retry_delay INT NOT NULL DEFAULT 3600,
    http_status_code INT NULL,
    response_headers JSON NULL,
    response_body TEXT NULL,
    response_size_bytes INT NULL,
    request_duration_ms INT NULL,
    dns_resolution_ms INT NULL,
    connection_time_ms INT NULL,
    error_message TEXT NULL,
    error_code VARCHAR(100) NULL,
    last_error_at DATETIME NULL,
    payload_size_bytes INT NULL,
    payload_encrypted BOOLEAN NOT NULL DEFAULT FALSE,
    signature_verified BOOLEAN NULL,
    organization_id VARCHAR(100) NULL,
    domain_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_attempt_at DATETIME NULL,
    last_attempt_at DATETIME NULL,
    delivered_at DATETIME NULL,
    abandoned_at DATETIME NULL,
    auto_cleanup_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    cleanup_after_hours INT NULL,
    INDEX idx_wdl_event_type (event_type),
    INDEX idx_wdl_webhook_url (webhook_url(255)),
    INDEX idx_wdl_delivery_status (delivery_status),
    INDEX idx_wdl_next_retry (next_retry_at),
    INDEX idx_wdl_http_status (http_status_code),
    INDEX idx_wdl_created_at (created_at),
    INDEX idx_wdl_delivered_at (delivered_at),
    INDEX idx_webhook_delivery_status_retry (delivery_status, next_retry_at),
    INDEX idx_webhook_delivery_cleanup_success (delivery_status, delivered_at, auto_cleanup_enabled),
    INDEX idx_webhook_delivery_cleanup_failed (delivery_status, abandoned_at, auto_cleanup_enabled),
    INDEX idx_webhook_delivery_attempts (attempts, max_attempts),
    INDEX idx_webhook_delivery_org_time (organization_id, created_at),
    INDEX idx_webhook_delivery_url_time (webhook_url(255), created_at),
    INDEX idx_webhook_delivery_event_time (event_type, created_at),
    CONSTRAINT fk_webhook_delivery_url FOREIGN KEY (webhook_url_id) REFERENCES webhook_urls(id),
    CONSTRAINT fk_webhook_delivery_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
    CONSTRAINT fk_webhook_delivery_domain FOREIGN KEY (domain_id) REFERENCES domains(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Alerts
-- =============================================================================

CREATE TABLE IF NOT EXISTS alerts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    alert_type ENUM('rate_limit', 'storage_quota') NOT NULL,
    alert_level ENUM('warning', 'critical', 'exceeded') NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    domain_id INT NULL,
    email_account_id INT NULL,
    current_usage BIGINT NOT NULL,
    limit_value BIGINT NOT NULL,
    usage_percentage FLOAT NOT NULL,
    context_data JSON NULL,
    webhook_sent BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_attempts INT NOT NULL DEFAULT 0,
    webhook_last_attempt DATETIME NULL,
    webhook_success BOOLEAN NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_alerts_type (alert_type),
    INDEX idx_alerts_level (alert_level),
    INDEX idx_alerts_webhook_sent (webhook_sent),
    INDEX idx_alerts_resolved (resolved),
    INDEX idx_alerts_created (created_at),
    INDEX idx_alerts_pending_webhook (webhook_sent, webhook_attempts),
    INDEX idx_alerts_org_type (organization_id, alert_type),
    INDEX idx_alerts_level_created (alert_level, created_at),
    INDEX idx_alerts_resolved_created (resolved, created_at),
    CONSTRAINT fk_alerts_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
    CONSTRAINT fk_alerts_domain FOREIGN KEY (domain_id) REFERENCES domains(id),
    CONSTRAINT fk_alerts_email_account FOREIGN KEY (email_account_id) REFERENCES email_accounts(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Usage History
-- =============================================================================

CREATE TABLE IF NOT EXISTS usage_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    usage_type ENUM('rate_limit', 'storage_quota') NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    domain_id INT NULL,
    email_account_id INT NULL,
    hour_key VARCHAR(13) NOT NULL,
    day_key VARCHAR(10) NOT NULL,
    month_key VARCHAR(7) NOT NULL,
    usage_data JSON NOT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_usage_type (usage_type),
    INDEX idx_usage_hour_key (hour_key),
    INDEX idx_usage_day_key (day_key),
    INDEX idx_usage_month_key (month_key),
    INDEX idx_usage_recorded (recorded_at),
    INDEX idx_usage_history_org_type_day (organization_id, usage_type, day_key),
    INDEX idx_usage_history_cleanup (day_key),
    INDEX idx_usage_history_entity_time (email_account_id, usage_type, hour_key),
    CONSTRAINT fk_usage_history_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
    CONSTRAINT fk_usage_history_domain FOREIGN KEY (domain_id) REFERENCES domains(id),
    CONSTRAINT fk_usage_history_email_account FOREIGN KEY (email_account_id) REFERENCES email_accounts(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- SSL Certificates
-- =============================================================================

CREATE TABLE IF NOT EXISTS ssl_certificates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    domain_id INT NOT NULL,
    certificate_path VARCHAR(500) NOT NULL,
    private_key_path VARCHAR(500) NOT NULL,
    chain_path VARCHAR(500) NULL,
    status ENUM('active', 'expired', 'revoked', 'pending') NOT NULL DEFAULT 'active',
    issuer VARCHAR(255) NULL,
    valid_from DATETIME NULL,
    valid_until DATETIME NULL,
    auto_renew BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_renewed DATETIME NULL,
    fingerprint VARCHAR(255) NULL,
    algorithm VARCHAR(50) NULL,
    key_size INT NULL,
    INDEX idx_ssl_status (status),
    INDEX idx_ssl_valid_until (valid_until),
    INDEX idx_ssl_expiry (valid_until, status),
    CONSTRAINT fk_ssl_certs_domain FOREIGN KEY (domain_id) REFERENCES domains(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- DKIM Keys
-- =============================================================================

CREATE TABLE IF NOT EXISTS dkim_keys (
    id INT AUTO_INCREMENT PRIMARY KEY,
    domain_id INT NOT NULL,
    selector VARCHAR(100) NOT NULL DEFAULT 'default',
    private_key TEXT NOT NULL,
    public_key TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_dkim_domain_selector (domain_id, selector),
    CONSTRAINT fk_dkim_keys_domain FOREIGN KEY (domain_id) REFERENCES domains(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- AI Transactions
-- =============================================================================

CREATE TABLE IF NOT EXISTS ai_transactions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    transaction_id VARCHAR(255) NOT NULL UNIQUE,
    session_id VARCHAR(255) NULL,
    organization_id VARCHAR(100) NOT NULL,
    user_id VARCHAR(100) NULL,
    service_name VARCHAR(100) NOT NULL,
    operation_type VARCHAR(100) NOT NULL,
    model_provider VARCHAR(100) NOT NULL,
    model_name VARCHAR(255) NOT NULL,
    model_version VARCHAR(100) NULL,
    prompt_tokens INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    cost_per_token DECIMAL(10, 8) NULL,
    total_cost DECIMAL(10, 6) NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
    processing_time_ms INT NULL,
    latency_ms INT NULL,
    batch_size INT NOT NULL DEFAULT 1,
    input_text_length INT NULL,
    output_text_length INT NULL,
    request_size_bytes INT NULL,
    response_size_bytes INT NULL,
    api_endpoint VARCHAR(500) NULL,
    api_version VARCHAR(50) NULL,
    request_id VARCHAR(255) NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    error_code VARCHAR(100) NULL,
    error_message TEXT NULL,
    context_type VARCHAR(100) NULL,
    metadata JSON NULL,
    rate_limit_remaining INT NULL,
    quota_consumed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    billing_period VARCHAR(20) NOT NULL DEFAULT 'monthly',
    usage_date DATETIME NOT NULL,
    INDEX idx_ai_session (session_id),
    INDEX idx_ai_user (user_id),
    INDEX idx_ai_service (service_name),
    INDEX idx_ai_operation (operation_type),
    INDEX idx_ai_provider (model_provider),
    INDEX idx_ai_model (model_name),
    INDEX idx_ai_status (status),
    INDEX idx_ai_created (created_at),
    INDEX idx_ai_billing (billing_period),
    INDEX idx_ai_usage_date (usage_date),
    INDEX idx_ai_trans_org_date (organization_id, usage_date),
    INDEX idx_ai_trans_model_date (model_provider, model_name, usage_date),
    INDEX idx_ai_trans_service_date (service_name, operation_type, usage_date),
    INDEX idx_ai_trans_cost (total_cost, usage_date),
    INDEX idx_ai_trans_tokens (total_tokens, usage_date),
    CONSTRAINT fk_ai_transactions_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Email Suppressions
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_suppressions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    suppression_type ENUM('BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL') NOT NULL,
    reason TEXT NULL,
    source VARCHAR(100) NULL,
    expires_at DATETIME NULL,
    bounce_type ENUM('HARD', 'SOFT', 'BLOCK') NULL,
    bounce_count INT NOT NULL DEFAULT 1,
    last_bounce_reason TEXT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_suppression_email (email),
    INDEX idx_suppression_org (organization_id),
    INDEX idx_suppression_type (suppression_type),
    INDEX idx_suppression_active (active),
    INDEX idx_suppression_expires (expires_at),
    UNIQUE INDEX unique_email_suppression (email, organization_id, suppression_type),
    INDEX idx_suppression_expires_active (expires_at, active),
    INDEX idx_suppression_type_org (suppression_type, organization_id),
    CONSTRAINT fk_email_suppressions_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Domain Reputation
-- =============================================================================

CREATE TABLE IF NOT EXISTS domain_reputation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    domain_id INT NOT NULL,
    organization_id VARCHAR(100) NOT NULL,
    overall_score INT NOT NULL DEFAULT 50,
    deliverability_score INT NOT NULL DEFAULT 50,
    engagement_score INT NOT NULL DEFAULT 50,
    emails_sent BIGINT NOT NULL DEFAULT 0,
    emails_delivered BIGINT NOT NULL DEFAULT 0,
    emails_bounced BIGINT NOT NULL DEFAULT 0,
    emails_complained BIGINT NOT NULL DEFAULT 0,
    emails_opened BIGINT NOT NULL DEFAULT 0,
    emails_clicked BIGINT NOT NULL DEFAULT 0,
    period_start DATETIME NOT NULL,
    period_end DATETIME NOT NULL,
    period_type ENUM('HOURLY', 'DAILY', 'WEEKLY', 'MONTHLY') NOT NULL DEFAULT 'DAILY',
    isp_data JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_domain_rep_domain (domain_id),
    INDEX idx_domain_rep_org (organization_id),
    INDEX idx_domain_rep_period_start (period_start),
    INDEX idx_domain_rep_period_end (period_end),
    INDEX idx_domain_reputation_period (domain_id, period_type, period_start),
    INDEX idx_domain_reputation_org (organization_id, period_start),
    CONSTRAINT fk_domain_reputation_domain FOREIGN KEY (domain_id) REFERENCES domains(id),
    CONSTRAINT fk_domain_reputation_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Feedback Loops
-- =============================================================================

CREATE TABLE IF NOT EXISTS feedback_loops (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    original_recipient VARCHAR(255) NOT NULL,
    complaint_recipient VARCHAR(255) NULL,
    organization_id VARCHAR(100) NOT NULL,
    isp_name VARCHAR(100) NOT NULL,
    feedback_type VARCHAR(50) NOT NULL DEFAULT 'abuse',
    email_id VARCHAR(255) NULL,
    subject TEXT NULL,
    sender VARCHAR(255) NULL,
    raw_feedback TEXT NULL,
    headers JSON NULL,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_added BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME NULL,
    INDEX idx_fbl_recipient (original_recipient),
    INDEX idx_fbl_org (organization_id),
    INDEX idx_fbl_isp (isp_name),
    INDEX idx_fbl_email_id (email_id),
    INDEX idx_fbl_sender (sender),
    INDEX idx_fbl_processed (processed),
    INDEX idx_fbl_created (created_at),
    INDEX idx_fbl_isp_date (isp_name, created_at),
    INDEX idx_fbl_processing (processed, created_at),
    INDEX idx_fbl_org_date (organization_id, created_at),
    CONSTRAINT fk_feedback_loops_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Analytics Data
-- =============================================================================

CREATE TABLE IF NOT EXISTS analytics_data (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value DECIMAL(15, 4) NOT NULL,
    dimensions JSON NULL,
    `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    period VARCHAR(20) NOT NULL,
    INDEX idx_analytics_org (organization_id),
    INDEX idx_analytics_metric (metric_name),
    INDEX idx_analytics_timestamp (`timestamp`),
    INDEX idx_analytics_period (period),
    INDEX idx_analytics_org_metric (organization_id, metric_name, `timestamp`),
    CONSTRAINT fk_analytics_data_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
