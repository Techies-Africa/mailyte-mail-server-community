-- =============================================================================
-- 002: Security Hardening Tables
-- Adds IP access control, audit logging, and authentication tracking
-- =============================================================================

-- =============================================================================
-- IP Access Rules — Per-organization IP whitelisting/blacklisting
-- Organizations can restrict SMTP relay to specific IPs (like Mailgun)
-- =============================================================================

CREATE TABLE IF NOT EXISTS ip_access_rules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    rule_type ENUM('whitelist', 'blacklist') NOT NULL DEFAULT 'whitelist',
    ip_address VARCHAR(45) NOT NULL COMMENT 'IPv4/IPv6 address or CIDR notation',
    description VARCHAR(255) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by VARCHAR(255) NULL COMMENT 'Email of admin who created the rule',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_ip_rules_org (organization_id),
    INDEX idx_ip_rules_type (rule_type),
    INDEX idx_ip_rules_active (organization_id, active, rule_type),
    CONSTRAINT fk_ip_rules_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Audit Logs — Track security-relevant events (GDPR/SOC2 compliance)
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL COMMENT 'e.g. auth.login, auth.failed, smtp.send, admin.action',
    event_source VARCHAR(50) NOT NULL COMMENT 'Service: postfix, dovecot, api, rspamd',
    organization_id VARCHAR(100) NULL,
    user_email VARCHAR(255) NULL,
    client_ip VARCHAR(45) NULL,
    user_agent VARCHAR(500) NULL,
    details JSON NULL COMMENT 'Event-specific metadata',
    severity ENUM('info', 'warning', 'critical') NOT NULL DEFAULT 'info',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_type (event_type),
    INDEX idx_audit_source (event_source),
    INDEX idx_audit_org (organization_id),
    INDEX idx_audit_user (user_email),
    INDEX idx_audit_ip (client_ip),
    INDEX idx_audit_severity (severity),
    INDEX idx_audit_created (created_at),
    INDEX idx_audit_org_type (organization_id, event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Partition audit_logs by month for performance (keeps queries fast as table grows)
-- Note: Partitioning is handled at the application level for MySQL 8 compatibility

-- =============================================================================
-- Failed Authentication Attempts — Persistent brute-force tracking
-- Survives container restarts (unlike in-memory tracking)
-- =============================================================================

CREATE TABLE IF NOT EXISTS failed_auth_attempts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    client_ip VARCHAR(45) NOT NULL,
    username VARCHAR(255) NULL,
    service ENUM('smtp', 'imap', 'pop3', 'api', 'sieve') NOT NULL,
    failure_reason VARCHAR(255) NULL,
    blocked_until DATETIME NULL COMMENT 'If set, IP is blocked until this time',
    attempt_count INT NOT NULL DEFAULT 1,
    first_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_failed_auth_ip (client_ip),
    INDEX idx_failed_auth_user (username),
    INDEX idx_failed_auth_service (service),
    INDEX idx_failed_auth_blocked (blocked_until),
    INDEX idx_failed_auth_ip_service (client_ip, service, last_attempt_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- IP Reputation — Track sender IP behavior over time
-- =============================================================================

CREATE TABLE IF NOT EXISTS ip_reputation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ip_address VARCHAR(45) NOT NULL UNIQUE,
    reputation_score DECIMAL(5, 2) NOT NULL DEFAULT 50.00 COMMENT '0=bad, 100=good',
    total_connections INT NOT NULL DEFAULT 0,
    total_messages INT NOT NULL DEFAULT 0,
    spam_count INT NOT NULL DEFAULT 0,
    bounce_count INT NOT NULL DEFAULT 0,
    auth_failure_count INT NOT NULL DEFAULT 0,
    last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_reason VARCHAR(255) NULL,
    blocked_until DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_ip_rep_score (reputation_score),
    INDEX idx_ip_rep_blocked (is_blocked),
    INDEX idx_ip_rep_last_seen (last_seen)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
