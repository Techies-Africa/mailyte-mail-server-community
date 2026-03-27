-- =============================================================================
-- Migration 008: Missing Tables
-- =============================================================================
-- Creates all tables referenced in service code but missing from previous
-- migrations. Also fixes foreign key type mismatches in migration 006.
-- Depends on: 007_migration_enhancements.sql
-- =============================================================================

-- =============================================================================
-- STEP 1: Fix FK type mismatches from migration 006
-- organization_id was incorrectly typed as INT; organizations.id is VARCHAR(100)
-- =============================================================================

ALTER TABLE dlp_policies
    MODIFY COLUMN organization_id VARCHAR(100) NOT NULL;

ALTER TABLE dlp_violations
    MODIFY COLUMN organization_id VARCHAR(100) NOT NULL;

ALTER TABLE geo_policies
    MODIFY COLUMN organization_id VARCHAR(100) NOT NULL;

-- =============================================================================
-- STEP 2: Alias duplicate audit table — audit_log (006) vs audit_logs (002)
-- Keep audit_logs as the canonical table; create a view for audit_log.
-- =============================================================================

-- Drop the smaller audit_log created in 006 (it has fewer columns than
-- audit_logs from 002, and may be empty at migration time).
DROP TABLE IF EXISTS audit_log;

-- Create a compatibility view so any code referencing audit_log still works
CREATE OR REPLACE VIEW audit_log AS
    SELECT
        id,
        event_type  AS action,
        user_email,
        performed_by,
        client_ip   AS ip_address,
        details,
        created_at
    FROM audit_logs;

-- =============================================================================
-- STEP 3: PGP/OpenPGP key storage (worker/encryption/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS pgp_keys (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    fingerprint VARCHAR(64)  NOT NULL UNIQUE,
    public_key  LONGTEXT     NOT NULL,
    private_key LONGTEXT     NULL COMMENT 'Encrypted private key (null = public only)',
    key_type    ENUM('rsa', 'dsa', 'ecdsa', 'ed25519') NOT NULL DEFAULT 'rsa',
    key_length  INT          NULL COMMENT 'Key size in bits (RSA/DSA only)',
    has_private TINYINT(1)   NOT NULL DEFAULT 0,
    expires_at  DATETIME     NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_pgp_email (email),
    INDEX idx_pgp_fingerprint (fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 4: S/MIME certificate storage (worker/encryption/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS smime_certs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    certificate LONGTEXT     NOT NULL COMMENT 'PEM-encoded certificate',
    private_key LONGTEXT     NULL  COMMENT 'Encrypted private key',
    issuer      VARCHAR(500) NULL,
    subject     VARCHAR(500) NULL,
    serial      VARCHAR(100) NULL,
    not_before  DATETIME     NULL,
    not_after   DATETIME     NULL,
    has_private TINYINT(1)   NOT NULL DEFAULT 0,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_smime_email (email),
    INDEX idx_smime_expires (not_after)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 5: OAuth 2.0 clients and grants (worker/oauth/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS oauth_clients (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    client_id       VARCHAR(100) NOT NULL UNIQUE,
    client_secret   VARCHAR(255) NOT NULL COMMENT 'bcrypt-hashed',
    name            VARCHAR(255) NOT NULL,
    description     TEXT         NULL,
    organization_id VARCHAR(100) NULL,
    client_type     ENUM('confidential', 'public') NOT NULL DEFAULT 'confidential',
    redirect_uris   JSON         NULL,
    scopes          JSON         NULL COMMENT 'Array of allowed scopes',
    grant_types     JSON         NULL COMMENT 'allowed: authorization_code, refresh_token, client_credentials',
    active          TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_oauth_client_org (organization_id),
    INDEX idx_oauth_client_active (active),
    CONSTRAINT fk_oauth_client_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS oauth_grants (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    client_id           VARCHAR(100) NOT NULL,
    user_email          VARCHAR(255) NULL,
    access_token_hash   VARCHAR(255) NOT NULL UNIQUE,
    refresh_token_hash  VARCHAR(255) NULL UNIQUE,
    scopes              JSON         NULL,
    access_expires_at   DATETIME     NOT NULL,
    refresh_expires_at  DATETIME     NULL,
    revoked             TINYINT(1)   NOT NULL DEFAULT 0,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_oauth_grant_client (client_id),
    INDEX idx_oauth_grant_user (user_email),
    INDEX idx_oauth_grant_revoked (revoked),
    INDEX idx_oauth_grant_expires (access_expires_at),
    CONSTRAINT fk_oauth_grant_client FOREIGN KEY (client_id)
        REFERENCES oauth_clients(client_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 6: URL protection tables (worker/url_protection/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS url_blocklist (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL COMMENT 'NULL = global block',
    entry           VARCHAR(2048) NOT NULL COMMENT 'URL, domain, or pattern',
    entry_type      ENUM('url', 'domain', 'pattern', 'allowlist') NOT NULL DEFAULT 'domain',
    reason          TEXT         NULL,
    added_by        VARCHAR(255) NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_url_block_org (organization_id),
    INDEX idx_url_block_type (entry_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS url_clicks (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL,
    message_id      VARCHAR(255) NULL,
    original_url    TEXT         NOT NULL,
    url_domain      VARCHAR(255) NULL,
    safe_url        TEXT         NULL,
    scan_result     ENUM('safe', 'suspicious', 'malicious', 'error', 'pending') NOT NULL DEFAULT 'pending',
    scan_details    JSON         NULL,
    clicked_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    client_ip       VARCHAR(45)  NULL,
    user_agent      TEXT         NULL,
    INDEX idx_url_click_org (organization_id),
    INDEX idx_url_click_message (message_id),
    INDEX idx_url_click_result (scan_result),
    INDEX idx_url_click_domain (url_domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 7: Email templates (worker/templates/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_templates (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT         NULL,
    subject         VARCHAR(500) NOT NULL,
    body_html       LONGTEXT     NULL,
    body_text       LONGTEXT     NULL,
    variables       JSON         NULL COMMENT 'Expected variable names and descriptions',
    category        VARCHAR(100) NULL,
    active          TINYINT(1)   NOT NULL DEFAULT 1,
    created_by      VARCHAR(255) NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_template_org_name (organization_id, name),
    INDEX idx_template_org (organization_id),
    INDEX idx_template_active (active),
    CONSTRAINT fk_template_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS email_template_versions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    template_id INT          NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    subject     VARCHAR(500) NOT NULL,
    body_html   LONGTEXT     NULL,
    body_text   LONGTEXT     NULL,
    variables   JSON         NULL,
    created_by  VARCHAR(255) NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tmpl_ver_template (template_id),
    CONSTRAINT fk_tmpl_ver_template FOREIGN KEY (template_id)
        REFERENCES email_templates(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS template_render_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    template_id     INT          NULL,
    organization_id VARCHAR(100) NULL,
    recipient       VARCHAR(255) NULL,
    variables_used  JSON         NULL,
    rendered_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status          ENUM('success', 'error') NOT NULL DEFAULT 'success',
    error_message   TEXT         NULL,
    INDEX idx_render_log_template (template_id),
    INDEX idx_render_log_org (organization_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 8: Email archival tables (worker/archiver/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_archive (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    message_id      VARCHAR(255) NULL,
    sender          VARCHAR(255) NULL,
    recipient       VARCHAR(255) NULL,
    subject         VARCHAR(500) NULL,
    storage_key     VARCHAR(1000) NOT NULL COMMENT 'S3 object key',
    storage_type    ENUM('s3', 'azure', 'gcs', 'local') NOT NULL DEFAULT 's3',
    original_size   BIGINT       NULL,
    compressed_size BIGINT       NULL,
    legal_hold      TINYINT(1)   NOT NULL DEFAULT 0,
    archived_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      DATETIME     NULL,
    INDEX idx_archive_org (organization_id),
    INDEX idx_archive_message (message_id),
    INDEX idx_archive_legal_hold (legal_hold),
    INDEX idx_archive_expires (expires_at),
    CONSTRAINT fk_archive_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retention_policies (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL UNIQUE,
    retention_days  INT          NOT NULL DEFAULT 365,
    legal_hold      TINYINT(1)   NOT NULL DEFAULT 0 COMMENT 'Override: never delete',
    auto_archive    TINYINT(1)   NOT NULL DEFAULT 1,
    archive_after_days INT       NOT NULL DEFAULT 90,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_retention_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS legal_holds (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT         NULL,
    custodians      JSON         NULL COMMENT 'List of email addresses under hold',
    start_date      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_date        DATETIME     NULL,
    active          TINYINT(1)   NOT NULL DEFAULT 1,
    created_by      VARCHAR(255) NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_legal_hold_org (organization_id),
    INDEX idx_legal_hold_active (active),
    CONSTRAINT fk_legal_hold_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 9: Storage tracking (worker/storage_usage/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS storage_usage (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    domain          VARCHAR(255) NULL,
    email_account   VARCHAR(255) NULL,
    storage_type    ENUM('mailbox', 'attachments', 'archive', 'total') NOT NULL DEFAULT 'total',
    used_bytes      BIGINT       NOT NULL DEFAULT 0,
    quota_bytes     BIGINT       NULL,
    calculated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_storage_org (organization_id),
    INDEX idx_storage_account (email_account),
    INDEX idx_storage_calculated (calculated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS storage_alerts (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    entity_type     ENUM('organization', 'domain', 'mailbox') NOT NULL,
    identifier      VARCHAR(255) NOT NULL,
    alert_level     ENUM('warning', 'critical', 'exceeded') NOT NULL,
    usage_percentage DECIMAL(5,2) NOT NULL,
    used_bytes      BIGINT       NOT NULL,
    quota_bytes     BIGINT       NOT NULL,
    acknowledged    TINYINT(1)   NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_storage_alert_org (organization_id),
    INDEX idx_storage_alert_level (alert_level),
    INDEX idx_storage_alert_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 10: Rate limit tracking (worker/rate_limiter/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS rate_limit_configs (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL COMMENT 'NULL = global default',
    entity_type     ENUM('organization', 'domain', 'mailbox', 'api_key') NOT NULL,
    identifier      VARCHAR(255) NULL COMMENT 'NULL = applies to all of entity_type',
    window          ENUM('second', 'minute', 'hour', 'day', 'month') NOT NULL DEFAULT 'hour',
    max_requests    INT          NOT NULL,
    warning_pct     INT          NOT NULL DEFAULT 80,
    critical_pct    INT          NOT NULL DEFAULT 95,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rl_config_org (organization_id),
    INDEX idx_rl_config_entity (entity_type, identifier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rate_limit_alerts (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL,
    entity_type     VARCHAR(50)  NOT NULL,
    identifier      VARCHAR(255) NOT NULL,
    window          VARCHAR(20)  NOT NULL,
    usage_pct       DECIMAL(5,2) NOT NULL,
    alert_level     ENUM('warning', 'critical', 'exceeded') NOT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_rl_alert_org (organization_id),
    INDEX idx_rl_alert_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 11: Queue statistics (worker/queue_manager/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS queue_statistics (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    queue_name      VARCHAR(100) NOT NULL,
    message_count   INT          NOT NULL DEFAULT 0,
    size_bytes      BIGINT       NOT NULL DEFAULT 0,
    oldest_message_age INT       NULL COMMENT 'Seconds',
    processed_total BIGINT       NOT NULL DEFAULT 0,
    failed_total    BIGINT       NOT NULL DEFAULT 0,
    recorded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_queue_stats_name (queue_name),
    INDEX idx_queue_stats_time (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 12: Bounce events (worker/delivery_optimizer/app.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS bounce_events (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL,
    message_id      VARCHAR(255) NULL,
    sender          VARCHAR(255) NULL,
    recipient       VARCHAR(255) NOT NULL,
    bounce_type     ENUM('hard', 'soft', 'complaint', 'unsubscribe') NOT NULL DEFAULT 'hard',
    bounce_code     VARCHAR(10)  NULL COMMENT 'SMTP response code',
    bounce_reason   TEXT         NULL,
    domain          VARCHAR(255) NULL,
    suppressed      TINYINT(1)   NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_bounce_org (organization_id),
    INDEX idx_bounce_recipient (recipient),
    INDEX idx_bounce_type (bounce_type),
    INDEX idx_bounce_domain (domain),
    INDEX idx_bounce_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 13: User login audit (worker/api/routes/mailboxes.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_logins (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_email  VARCHAR(255) NOT NULL,
    client_ip   VARCHAR(45)  NULL,
    user_agent  TEXT         NULL,
    protocol    ENUM('imap', 'pop3', 'smtp', 'webmail', 'api') NOT NULL DEFAULT 'imap',
    success     TINYINT(1)   NOT NULL DEFAULT 1,
    failure_reason VARCHAR(255) NULL,
    logged_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_login_email (user_email),
    INDEX idx_user_login_ip (client_ip),
    INDEX idx_user_login_time (logged_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 14: Webhook dead letter queue (shared/webhook_dispatcher.py)
-- =============================================================================

CREATE TABLE IF NOT EXISTS webhook_dead_letters (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL,
    event_type      VARCHAR(255) NOT NULL,
    endpoint_url    VARCHAR(2048) NOT NULL,
    payload         JSON         NOT NULL,
    last_error      TEXT         NULL,
    attempt_count   INT          NOT NULL DEFAULT 0,
    status          ENUM('pending', 'retrying', 'resolved', 'abandoned') NOT NULL DEFAULT 'pending',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempted_at DATETIME   NULL,
    resolved_at     DATETIME     NULL,
    INDEX idx_wdl_org (organization_id),
    INDEX idx_wdl_status (status),
    INDEX idx_wdl_event (event_type),
    INDEX idx_wdl_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 15: Backup history (scripts/backup.sh)
-- =============================================================================

CREATE TABLE IF NOT EXISTS backup_history (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    backup_type     ENUM('full', 'incremental', 'differential') NOT NULL DEFAULT 'full',
    target          ENUM('database', 'mail', 'config', 'all') NOT NULL DEFAULT 'all',
    storage_path    VARCHAR(1000) NULL COMMENT 'S3 key or local path',
    size_bytes      BIGINT       NULL,
    status          ENUM('running', 'completed', 'failed') NOT NULL DEFAULT 'running',
    error_message   TEXT         NULL,
    started_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at    DATETIME     NULL,
    INDEX idx_backup_status (status),
    INDEX idx_backup_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- STEP 16: API rate limits (per-endpoint, used by rate_limiter service)
-- =============================================================================

CREATE TABLE IF NOT EXISTS api_rate_limits (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NULL,
    api_key_id      INT          NULL,
    endpoint_pattern VARCHAR(255) NOT NULL,
    max_requests    INT          NOT NULL DEFAULT 1000,
    window_seconds  INT          NOT NULL DEFAULT 3600,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_api_rl_org (organization_id),
    INDEX idx_api_rl_pattern (endpoint_pattern)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================================
-- Update Alembic version
-- =============================================================================

UPDATE alembic_version SET version_num = '008_missing_tables'
WHERE version_num = '007_migration_enhancements';

INSERT INTO alembic_version (version_num)
SELECT '008_missing_tables'
WHERE NOT EXISTS (
    SELECT 1 FROM alembic_version WHERE version_num = '008_missing_tables'
);
