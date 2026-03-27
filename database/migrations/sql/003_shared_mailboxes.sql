-- =============================================================================
-- 003: Shared Mailboxes & Delegation
-- Adds mailbox_type to email_accounts and shared_mailbox_members table
-- =============================================================================

-- Add mailbox_type column to email_accounts (personal = default, shared = shared mailbox)
ALTER TABLE email_accounts
    ADD COLUMN mailbox_type ENUM('personal', 'shared') NOT NULL DEFAULT 'personal' AFTER status,
    ADD INDEX idx_email_mailbox_type (mailbox_type);

-- =============================================================================
-- Shared Mailbox Members — Maps users to shared mailboxes with permissions
-- =============================================================================

CREATE TABLE IF NOT EXISTS shared_mailbox_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    shared_mailbox_id INT NOT NULL COMMENT 'The shared mailbox (email_accounts.id where mailbox_type=shared)',
    email_account_id INT NOT NULL COMMENT 'The user granted access',
    permission ENUM('full_access', 'send_as', 'send_on_behalf', 'read_only') NOT NULL DEFAULT 'read_only',
    granted_by VARCHAR(255) NULL COMMENT 'Email of admin who granted access',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_shared_member (shared_mailbox_id, email_account_id),
    INDEX idx_shared_mailbox (shared_mailbox_id),
    INDEX idx_shared_member (email_account_id),
    INDEX idx_shared_permission (permission),
    CONSTRAINT fk_shared_mailbox FOREIGN KEY (shared_mailbox_id) REFERENCES email_accounts(id) ON DELETE CASCADE,
    CONSTRAINT fk_shared_member FOREIGN KEY (email_account_id) REFERENCES email_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Distribution Groups — Mailing lists with member management
-- =============================================================================

CREATE TABLE IF NOT EXISTS distribution_groups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    organization_id VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE COMMENT 'Group email address',
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    group_type ENUM('distribution', 'security', 'dynamic') NOT NULL DEFAULT 'distribution',
    moderation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    external_delivery BOOLEAN NOT NULL DEFAULT TRUE COMMENT 'Allow delivery from non-members',
    max_message_size BIGINT NULL COMMENT 'Max message size in bytes (NULL = no limit)',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_group_org (organization_id),
    INDEX idx_group_active (active),
    INDEX idx_group_type (group_type),
    CONSTRAINT fk_group_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS distribution_group_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    group_id INT NOT NULL,
    email_account_id INT NULL COMMENT 'Internal member (NULL if external)',
    external_email VARCHAR(255) NULL COMMENT 'External member address',
    role ENUM('member', 'owner', 'moderator') NOT NULL DEFAULT 'member',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_group_member_group (group_id),
    INDEX idx_group_member_account (email_account_id),
    INDEX idx_group_member_role (role),
    CONSTRAINT fk_group_member_group FOREIGN KEY (group_id) REFERENCES distribution_groups(id) ON DELETE CASCADE,
    CONSTRAINT fk_group_member_account FOREIGN KEY (email_account_id) REFERENCES email_accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Update Alembic version
UPDATE alembic_version SET version_num = '003_shared_mailboxes' WHERE version_num = '002_security_hardening';
