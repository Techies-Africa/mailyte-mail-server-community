-- =============================================================================
-- 005: Phase 3 — Scalability & Advanced Features
-- White-label / Reseller support, JMAP state tracking, migration jobs,
-- Kafka dead-letter queue, and table partitioning for mail_logs & mail_queue
-- =============================================================================

-- =============================================================================
-- 1. White-label / Reseller — self-referential org hierarchy
-- =============================================================================

ALTER TABLE organizations
    ADD COLUMN parent_organization_id VARCHAR(100) NULL AFTER id,
    ADD COLUMN whitelabel_domain VARCHAR(255) NULL AFTER webhook_secret,
    ADD INDEX idx_org_parent (parent_organization_id),
    ADD CONSTRAINT fk_org_parent FOREIGN KEY (parent_organization_id) REFERENCES organizations(id) ON DELETE SET NULL;

-- =============================================================================
-- 2. JMAP State Tracking — per-account state vectors for JMAP push / delta sync
-- =============================================================================

CREATE TABLE IF NOT EXISTS jmap_states (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id VARCHAR(255) NOT NULL COMMENT 'Email address acting as the JMAP account identifier',
    data_type VARCHAR(50) NOT NULL COMMENT 'JMAP data type: Mailbox, Email, Thread, etc.',
    state VARCHAR(100) NOT NULL COMMENT 'Opaque state string returned to JMAP clients',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE INDEX uk_jmap_account_type (account_id, data_type),
    INDEX idx_jmap_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 3. Migration Jobs — IMAP-to-IMAP mailbox migration tracking
-- =============================================================================

CREATE TABLE IF NOT EXISTS migration_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(100) NOT NULL UNIQUE,
    org_id VARCHAR(100) NULL,
    source_host VARCHAR(255) NULL,
    source_port INT NULL,
    source_user VARCHAR(255) NULL,
    source_email VARCHAR(255) NULL,
    target_email VARCHAR(255) NOT NULL,
    status ENUM('pending','running','paused','completed','failed','cancelled') NOT NULL DEFAULT 'pending',
    total_messages INT NOT NULL DEFAULT 0,
    migrated_messages INT NOT NULL DEFAULT 0,
    failed_messages INT NOT NULL DEFAULT 0,
    current_folder VARCHAR(255) NULL,
    speed FLOAT DEFAULT 0 COMMENT 'Messages per second',
    folder_mapping JSON NULL COMMENT 'Source->target folder name mapping',
    exclude_folders JSON NULL COMMENT 'List of folders to skip',
    last_uid JSON NULL COMMENT 'Per-folder last-synced UID for resume',
    error_log TEXT NULL,
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_migration_status (status),
    INDEX idx_migration_org (org_id),
    CONSTRAINT fk_migration_org FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 4. Kafka Dead-Letter Queue — stores unprocessable messages for inspection
-- =============================================================================

CREATE TABLE IF NOT EXISTS kafka_dead_letters (
    id INT AUTO_INCREMENT PRIMARY KEY,
    topic VARCHAR(255) NOT NULL,
    partition_num INT NULL,
    offset_num BIGINT NULL,
    key_data VARCHAR(255) NULL,
    value_data LONGTEXT NULL,
    error_message TEXT NULL,
    retry_count INT NOT NULL DEFAULT 0,
    status ENUM('pending','retrying','resolved','abandoned') NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_kafka_dl_topic_status (topic, status),
    INDEX idx_kafka_dl_status (status),
    INDEX idx_kafka_dl_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- 5. Table Partitioning — monthly RANGE partitions on mail_logs and mail_queue
--    Wrapped in a procedure so we can skip gracefully if already partitioned.
-- =============================================================================

DELIMITER $$

DROP PROCEDURE IF EXISTS partition_mail_logs$$
CREATE PROCEDURE partition_mail_logs()
BEGIN
    DECLARE already_partitioned INT DEFAULT 0;

    -- Check whether mail_logs is already partitioned
    SELECT COUNT(*) INTO already_partitioned
      FROM information_schema.PARTITIONS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME   = 'mail_logs'
       AND PARTITION_NAME IS NOT NULL;

    IF already_partitioned = 0 THEN
        ALTER TABLE mail_logs
            PARTITION BY RANGE (TO_DAYS(`timestamp`)) (
                PARTITION p2026_01 VALUES LESS THAN (TO_DAYS('2026-02-01')),
                PARTITION p2026_02 VALUES LESS THAN (TO_DAYS('2026-03-01')),
                PARTITION p2026_03 VALUES LESS THAN (TO_DAYS('2026-04-01')),
                PARTITION p2026_04 VALUES LESS THAN (TO_DAYS('2026-05-01')),
                PARTITION p2026_05 VALUES LESS THAN (TO_DAYS('2026-06-01')),
                PARTITION p2026_06 VALUES LESS THAN (TO_DAYS('2026-07-01')),
                PARTITION p2026_07 VALUES LESS THAN (TO_DAYS('2026-08-01')),
                PARTITION p2026_08 VALUES LESS THAN (TO_DAYS('2026-09-01')),
                PARTITION p2026_09 VALUES LESS THAN (TO_DAYS('2026-10-01')),
                PARTITION p2026_10 VALUES LESS THAN (TO_DAYS('2026-11-01')),
                PARTITION p2026_11 VALUES LESS THAN (TO_DAYS('2026-12-01')),
                PARTITION p2027_01 VALUES LESS THAN (TO_DAYS('2027-01-01')),
                PARTITION p_future VALUES LESS THAN MAXVALUE
            );
    END IF;
END$$

DROP PROCEDURE IF EXISTS partition_mail_queue$$
CREATE PROCEDURE partition_mail_queue()
BEGIN
    DECLARE already_partitioned INT DEFAULT 0;

    -- Check whether mail_queue is already partitioned
    SELECT COUNT(*) INTO already_partitioned
      FROM information_schema.PARTITIONS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME   = 'mail_queue'
       AND PARTITION_NAME IS NOT NULL;

    IF already_partitioned = 0 THEN
        ALTER TABLE mail_queue
            PARTITION BY RANGE (TO_DAYS(created_at)) (
                PARTITION p2026_01 VALUES LESS THAN (TO_DAYS('2026-02-01')),
                PARTITION p2026_02 VALUES LESS THAN (TO_DAYS('2026-03-01')),
                PARTITION p2026_03 VALUES LESS THAN (TO_DAYS('2026-04-01')),
                PARTITION p2026_04 VALUES LESS THAN (TO_DAYS('2026-05-01')),
                PARTITION p2026_05 VALUES LESS THAN (TO_DAYS('2026-06-01')),
                PARTITION p2026_06 VALUES LESS THAN (TO_DAYS('2026-07-01')),
                PARTITION p2026_07 VALUES LESS THAN (TO_DAYS('2026-08-01')),
                PARTITION p2026_08 VALUES LESS THAN (TO_DAYS('2026-09-01')),
                PARTITION p2026_09 VALUES LESS THAN (TO_DAYS('2026-10-01')),
                PARTITION p2026_10 VALUES LESS THAN (TO_DAYS('2026-11-01')),
                PARTITION p2026_11 VALUES LESS THAN (TO_DAYS('2026-12-01')),
                PARTITION p2027_01 VALUES LESS THAN (TO_DAYS('2027-01-01')),
                PARTITION p_future VALUES LESS THAN MAXVALUE
            );
    END IF;
END$$

DELIMITER ;

-- Execute the partitioning procedures, then clean up
CALL partition_mail_logs();
CALL partition_mail_queue();
DROP PROCEDURE IF EXISTS partition_mail_logs;
DROP PROCEDURE IF EXISTS partition_mail_queue;

-- Update Alembic version
UPDATE alembic_version SET version_num = '005_phase3_scalability' WHERE version_num = '004_transport_rules';
