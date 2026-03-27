-- =============================================================================
-- Migration 007: Email Migration Service Enhancements
-- =============================================================================
-- Adds: export support, structured error logging, webhook URLs, timing columns
-- Depends on: 005_phase3_scalability.sql (migration_jobs table)
-- =============================================================================

-- Add new columns to migration_jobs for export support and tracking
ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS direction VARCHAR(10) NOT NULL DEFAULT 'import' AFTER status,
    ADD COLUMN IF NOT EXISTS target_host VARCHAR(255) AFTER source_password,
    ADD COLUMN IF NOT EXISTS target_port INT DEFAULT 993 AFTER target_host,
    ADD COLUMN IF NOT EXISTS target_ssl TINYINT(1) DEFAULT 1 AFTER target_port,
    ADD COLUMN IF NOT EXISTS is_retry TINYINT(1) NOT NULL DEFAULT 0 AFTER is_delta,
    ADD COLUMN IF NOT EXISTS parent_job_id VARCHAR(36) AFTER is_retry,
    ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(500) AFTER last_uid,
    ADD COLUMN IF NOT EXISTS started_at DATETIME AFTER webhook_url,
    ADD COLUMN IF NOT EXISTS completed_at DATETIME AFTER started_at;

-- Add indexes for new columns
CREATE INDEX IF NOT EXISTS idx_direction ON migration_jobs (direction);
CREATE INDEX IF NOT EXISTS idx_parent ON migration_jobs (parent_job_id);

-- Structured error log — one row per failed message (replaces text blob)
CREATE TABLE IF NOT EXISTS migration_errors (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(36) NOT NULL,
    folder VARCHAR(255) NOT NULL,
    message_uid VARCHAR(100) DEFAULT '',
    message_id VARCHAR(500) DEFAULT '',
    error_type VARCHAR(50) NOT NULL COMMENT 'Python exception class name',
    error_message TEXT NOT NULL,
    retryable TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'Whether this message can be retried',
    retried TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Whether a retry has been attempted',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_job (job_id),
    INDEX idx_job_retryable (job_id, retryable, retried),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Update alembic version
UPDATE alembic_version SET version_num = '007_migration_enhancements'
WHERE version_num = '006_phase4_security';

INSERT INTO alembic_version (version_num)
SELECT '007_migration_enhancements'
WHERE NOT EXISTS (SELECT 1 FROM alembic_version WHERE version_num = '007_migration_enhancements');
