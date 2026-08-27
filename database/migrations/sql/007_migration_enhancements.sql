-- =============================================================================
-- Migration 007: Email Migration Service Enhancements
-- =============================================================================
-- Adds: export support, structured error logging, webhook URLs, timing columns
-- Depends on: 005_phase3_scalability.sql (migration_jobs table)
-- =============================================================================

-- Add new columns to migration_jobs for export support and tracking.
--
-- Three deviations from the original, all forced by 005's actual table shape:
--   * No `IF NOT EXISTS` — that is MariaDB syntax; MySQL rejects it (ERROR 1064).
--   * No `AFTER <col>` clauses — column order is cosmetic, and two of the original
--     anchors (source_password, is_delta) do not exist in 005's migration_jobs.
--   * started_at / completed_at are not added here — 005 already defines them, so
--     re-adding them raises a duplicate-column error.
ALTER TABLE migration_jobs
    ADD COLUMN direction VARCHAR(10) NOT NULL DEFAULT 'import',
    ADD COLUMN target_host VARCHAR(255),
    ADD COLUMN target_port INT DEFAULT 993,
    ADD COLUMN target_ssl TINYINT(1) DEFAULT 1,
    ADD COLUMN is_retry TINYINT(1) NOT NULL DEFAULT 0,
    ADD COLUMN parent_job_id VARCHAR(36),
    ADD COLUMN webhook_url VARCHAR(500);

-- Add indexes for new columns
CREATE INDEX idx_direction ON migration_jobs (direction);
CREATE INDEX idx_parent ON migration_jobs (parent_job_id);

-- Structured error log — one row per failed message (replaces text blob)
CREATE TABLE IF NOT EXISTS migration_errors (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(100) NOT NULL,  -- must match migration_jobs.job_id VARCHAR(100) in 005
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Update alembic version
UPDATE alembic_version SET version_num = '007_migration_enhancements'
WHERE version_num = '006_phase4_security';

INSERT INTO alembic_version (version_num)
SELECT '007_migration_enhancements'
WHERE NOT EXISTS (SELECT 1 FROM alembic_version WHERE version_num = '007_migration_enhancements');
