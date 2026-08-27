-- =============================================================================
-- 006: Phase 4 — Security Hardening
-- DLP policies & violations, TOTP secrets, geo-blocking policies,
-- GDPR consent records, data export requests, data erasure requests,
-- and audit log
-- =============================================================================

-- =============================================================================
-- 1. DLP Policies — per-organization Data Loss Prevention rules
-- =============================================================================

CREATE TABLE IF NOT EXISTS dlp_policies (
    id INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    policy_type ENUM('pii', 'keyword', 'regex', 'file_type') NOT NULL DEFAULT 'keyword',
    patterns JSON NOT NULL COMMENT 'Array of patterns to match',
    action ENUM('block', 'quarantine', 'encrypt', 'notify', 'log_only') NOT NULL DEFAULT 'notify',
    severity ENUM('low', 'medium', 'high', 'critical') NOT NULL DEFAULT 'medium',
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    apply_to ENUM('inbound', 'outbound', 'both') NOT NULL DEFAULT 'outbound',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_org_enabled (organization_id, enabled),
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 2. DLP Violations Log — records every policy match
-- =============================================================================

CREATE TABLE IF NOT EXISTS dlp_violations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    policy_id INT NOT NULL,
    message_id VARCHAR(255),
    sender VARCHAR(255) NOT NULL,
    recipient VARCHAR(255),
    violation_type VARCHAR(100) NOT NULL,
    matched_pattern TEXT,
    action_taken VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_org_date (organization_id, created_at),
    INDEX idx_policy (policy_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (policy_id) REFERENCES dlp_policies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 3. TOTP Secrets — per-user 2FA enrolment
-- =============================================================================

CREATE TABLE IF NOT EXISTS totp_secrets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL UNIQUE,
    secret VARCHAR(64) NOT NULL,
    backup_codes JSON COMMENT 'Array of hashed backup codes',
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    verified TINYINT(1) NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_email (user_email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 4. Geo-Blocking Policies — per-organization country-level access rules
-- =============================================================================

CREATE TABLE IF NOT EXISTS geo_policies (
    id INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    allowed_countries JSON COMMENT 'Array of ISO 3166-1 alpha-2 codes',
    blocked_countries JSON COMMENT 'Array of ISO 3166-1 alpha-2 codes',
    time_restrictions JSON COMMENT 'Time-of-day access rules',
    action ENUM('block', 'challenge', 'log_only') NOT NULL DEFAULT 'block',
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY idx_org (organization_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 5. GDPR Consent Records
-- =============================================================================

CREATE TABLE IF NOT EXISTS consent_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    consent_type ENUM('marketing', 'analytics', 'third_party_sharing', 'data_processing') NOT NULL,
    granted TINYINT(1) NOT NULL DEFAULT 0,
    ip_address VARCHAR(45),
    user_agent TEXT,
    granted_at TIMESTAMP NULL,
    revoked_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_type (user_email, consent_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 6. Data Export Requests
-- =============================================================================

CREATE TABLE IF NOT EXISTS data_export_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    status ENUM('pending', 'processing', 'completed', 'failed', 'expired') NOT NULL DEFAULT 'pending',
    export_type ENUM('full', 'emails', 'contacts', 'settings') NOT NULL DEFAULT 'full',
    file_path VARCHAR(500),
    file_size BIGINT DEFAULT 0,
    requested_by VARCHAR(255) NOT NULL,
    error_message TEXT,
    completed_at TIMESTAMP NULL,
    expires_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_status (user_email, status),
    INDEX idx_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 7. Data Erasure Requests
-- =============================================================================

CREATE TABLE IF NOT EXISTS data_erasure_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    status ENUM('pending', 'soft_deleted', 'hard_deleted', 'cancelled') NOT NULL DEFAULT 'pending',
    requested_by VARCHAR(255) NOT NULL,
    reason TEXT,
    soft_deleted_at TIMESTAMP NULL,
    hard_delete_scheduled_at TIMESTAMP NULL,
    hard_deleted_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_status (user_email, status),
    INDEX idx_hard_delete (hard_delete_scheduled_at, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 8. Audit Log — general-purpose compliance audit trail
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    action VARCHAR(255) NOT NULL,
    user_email VARCHAR(255),
    performed_by VARCHAR(255),
    ip_address VARCHAR(45),
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_action (action),
    INDEX idx_user (user_email),
    INDEX idx_date (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Update Alembic migration version tracker
-- =============================================================================

UPDATE alembic_version SET version_num = '006_phase4_security' WHERE version_num = '005_phase3_scalability';
