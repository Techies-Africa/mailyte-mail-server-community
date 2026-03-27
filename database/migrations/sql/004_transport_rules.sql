-- =============================================================================
-- 004: Transport Rules & Message Quarantine
-- Organization-wide mail flow rules and quarantine management
-- =============================================================================

-- =============================================================================
-- Transport Rules — Organization-level mail flow rules (like Exchange Transport Rules)
-- =============================================================================

CREATE TABLE IF NOT EXISTS transport_rules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    organization_id VARCHAR(100) NOT NULL,
    direction ENUM('inbound', 'outbound', 'both') NOT NULL DEFAULT 'both',
    conditions JSON NOT NULL COMMENT '[{field, operator, value}]',
    condition_logic ENUM('all', 'any') NOT NULL DEFAULT 'all' COMMENT 'AND vs OR for conditions',
    actions JSON NOT NULL COMMENT '[{type, params}]',
    priority INT NOT NULL DEFAULT 100 COMMENT 'Lower = evaluated first',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    hit_count BIGINT NOT NULL DEFAULT 0 COMMENT 'Number of messages matched',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_transport_org (organization_id),
    INDEX idx_transport_enabled (enabled),
    INDEX idx_transport_priority (priority),
    INDEX idx_transport_org_enabled (organization_id, enabled, priority),
    INDEX idx_transport_direction (direction),
    CONSTRAINT fk_transport_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Quarantine — Held messages pending admin review
-- =============================================================================

CREATE TABLE IF NOT EXISTS quarantine (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    message_id VARCHAR(255) NULL COMMENT 'RFC 2822 Message-ID',
    queue_id VARCHAR(100) NULL COMMENT 'Postfix queue ID',
    sender VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    subject VARCHAR(500) NULL,
    reason ENUM('spam', 'virus', 'policy', 'transport_rule', 'admin') NOT NULL DEFAULT 'spam',
    spam_score DECIMAL(6, 2) NULL,
    virus_name VARCHAR(255) NULL,
    rule_id INT NULL COMMENT 'Transport rule that triggered quarantine',
    headers JSON NULL COMMENT 'Original message headers',
    storage_key VARCHAR(500) NULL COMMENT 'S3 key for quarantined message body',
    status ENUM('quarantined', 'released', 'deleted', 'expired') NOT NULL DEFAULT 'quarantined',
    released_by VARCHAR(255) NULL COMMENT 'Admin who released/deleted',
    released_at DATETIME NULL,
    expires_at DATETIME NOT NULL COMMENT 'Auto-delete after this date',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_quarantine_org (organization_id),
    INDEX idx_quarantine_sender (sender),
    INDEX idx_quarantine_recipient (recipient),
    INDEX idx_quarantine_status (status),
    INDEX idx_quarantine_reason (reason),
    INDEX idx_quarantine_expires (expires_at),
    INDEX idx_quarantine_created (created_at),
    INDEX idx_quarantine_org_status (organization_id, status, created_at),
    INDEX idx_quarantine_message_id (message_id),
    CONSTRAINT fk_quarantine_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    CONSTRAINT fk_quarantine_rule FOREIGN KEY (rule_id) REFERENCES transport_rules(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Update Alembic version
UPDATE alembic_version SET version_num = '004_transport_rules' WHERE version_num = '003_shared_mailboxes';
