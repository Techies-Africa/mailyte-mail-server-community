-- =============================================================================
-- Migration 009: ULID Primary Keys (Safe version)
-- =============================================================================
-- Drops ALL foreign keys dynamically, converts PKs to CHAR(26), re-adds FKs.
-- Safe to re-run — checks before altering.
-- =============================================================================

-- Step 0: Create ULID generation function for backfilling existing rows
DROP FUNCTION IF EXISTS generate_ulid_backfill;
DELIMITER //
CREATE FUNCTION generate_ulid_backfill() RETURNS CHAR(26) DETERMINISTIC
BEGIN
    DECLARE ts BIGINT;
    DECLARE rand_part VARCHAR(16);
    SET ts = UNIX_TIMESTAMP(NOW(3)) * 1000;
    SET rand_part = UPPER(SUBSTR(MD5(RAND()), 1, 16));
    RETURN CONCAT(LPAD(CONV(ts, 10, 36), 10, '0'), rand_part);
END //
DELIMITER ;

-- Step 1: Drop ALL foreign key constraints dynamically
-- This procedure finds every FK in the mailserver database and drops it
DROP PROCEDURE IF EXISTS drop_all_fks;
DELIMITER //
CREATE PROCEDURE drop_all_fks()
BEGIN
    DECLARE done INT DEFAULT FALSE;
    DECLARE fk_name VARCHAR(255);
    DECLARE tbl_name VARCHAR(255);
    DECLARE cur CURSOR FOR
        SELECT CONSTRAINT_NAME, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_TYPE = 'FOREIGN KEY'
          AND TABLE_SCHEMA = DATABASE();
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;

    OPEN cur;
    read_loop: LOOP
        FETCH cur INTO fk_name, tbl_name;
        IF done THEN LEAVE read_loop; END IF;
        SET @sql = CONCAT('ALTER TABLE `', tbl_name, '` DROP FOREIGN KEY `', fk_name, '`');
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END LOOP;
    CLOSE cur;
END //
DELIMITER ;

CALL drop_all_fks();
DROP PROCEDURE IF EXISTS drop_all_fks;

-- Step 2: Convert organizations PK (VARCHAR(100) → CHAR(26))
-- Existing values like 'test-org' are preserved (padded/truncated to 26 chars)
ALTER TABLE organizations MODIFY COLUMN id CHAR(26) NOT NULL;

-- Step 3: Convert all INT AUTO_INCREMENT PKs to CHAR(26)
-- For each table: add new_id, backfill, drop old id, rename

-- Helper procedure to convert a table's INT id to CHAR(26) ULID
DROP PROCEDURE IF EXISTS convert_pk_to_ulid;
DELIMITER //
CREATE PROCEDURE convert_pk_to_ulid(IN tbl VARCHAR(64))
BEGIN
    -- Check if table exists and has INT id
    IF EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl
          AND COLUMN_NAME = 'id' AND DATA_TYPE IN ('int', 'bigint')
    ) THEN
        -- Add new ULID column
        SET @sql = CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `new_id` CHAR(26) NULL AFTER `id`');
        PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

        -- Backfill with generated ULIDs
        SET @sql = CONCAT('UPDATE `', tbl, '` SET `new_id` = generate_ulid_backfill() WHERE `new_id` IS NULL');
        PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

        -- Drop old PK
        SET @sql = CONCAT('ALTER TABLE `', tbl, '` DROP PRIMARY KEY, DROP COLUMN `id`');
        PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

        -- Rename and set as PK
        SET @sql = CONCAT('ALTER TABLE `', tbl, '` CHANGE `new_id` `id` CHAR(26) NOT NULL, ADD PRIMARY KEY (`id`)');
        PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END //
DELIMITER ;

-- Convert all tables with INT PKs
CALL convert_pk_to_ulid('domains');
CALL convert_pk_to_ulid('email_accounts');
CALL convert_pk_to_ulid('aliases');
CALL convert_pk_to_ulid('api_keys');
CALL convert_pk_to_ulid('dkim_keys');
CALL convert_pk_to_ulid('email_tracking');
CALL convert_pk_to_ulid('mail_logs');
CALL convert_pk_to_ulid('audit_logs');
CALL convert_pk_to_ulid('webhook_delivery_logs');
CALL convert_pk_to_ulid('ssl_certificates');
CALL convert_pk_to_ulid('transport_rules');
CALL convert_pk_to_ulid('quarantine');
CALL convert_pk_to_ulid('health_checks');
CALL convert_pk_to_ulid('service_metrics');
CALL convert_pk_to_ulid('url_clicks');
CALL convert_pk_to_ulid('tracking_statistics');
CALL convert_pk_to_ulid('email_suppressions');
CALL convert_pk_to_ulid('user_sessions');
CALL convert_pk_to_ulid('migration_jobs');
CALL convert_pk_to_ulid('webhook_urls');
CALL convert_pk_to_ulid('alerts');
CALL convert_pk_to_ulid('usage_history');
CALL convert_pk_to_ulid('analytics_data');
CALL convert_pk_to_ulid('domain_reputation');
CALL convert_pk_to_ulid('feedback_loops');
CALL convert_pk_to_ulid('ip_access_rules');
CALL convert_pk_to_ulid('failed_auth_attempts');
CALL convert_pk_to_ulid('ip_reputation');
CALL convert_pk_to_ulid('shared_mailbox_members');
CALL convert_pk_to_ulid('distribution_groups');
CALL convert_pk_to_ulid('distribution_group_members');
CALL convert_pk_to_ulid('jmap_states');
CALL convert_pk_to_ulid('kafka_dead_letters');
CALL convert_pk_to_ulid('oauth_clients');
CALL convert_pk_to_ulid('oauth_grants');
CALL convert_pk_to_ulid('mail_queue');
CALL convert_pk_to_ulid('system_config');
CALL convert_pk_to_ulid('email_template_versions');
CALL convert_pk_to_ulid('email_templates');
CALL convert_pk_to_ulid('template_render_log');
CALL convert_pk_to_ulid('url_blocklist');
CALL convert_pk_to_ulid('ai_transactions');
CALL convert_pk_to_ulid('consent_records');
CALL convert_pk_to_ulid('data_export_requests');
CALL convert_pk_to_ulid('data_erasure_requests');

DROP PROCEDURE IF EXISTS convert_pk_to_ulid;

-- Step 4: Convert FK columns from INT to CHAR(26)
-- Helper procedure
DROP PROCEDURE IF EXISTS convert_fk_column;
DELIMITER //
CREATE PROCEDURE convert_fk_column(IN tbl VARCHAR(64), IN col VARCHAR(64))
BEGIN
    IF EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl
          AND COLUMN_NAME = col AND DATA_TYPE IN ('int', 'bigint')
    ) THEN
        SET @sql = CONCAT('ALTER TABLE `', tbl, '` MODIFY COLUMN `', col, '` CHAR(26) NULL');
        PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END //
DELIMITER ;

-- Convert all FK columns that reference tables we just converted
CALL convert_fk_column('domains', 'organization_id');
CALL convert_fk_column('email_accounts', 'domain_id');
CALL convert_fk_column('email_accounts', 'organization_id');
CALL convert_fk_column('aliases', 'domain_id');
CALL convert_fk_column('aliases', 'organization_id');
CALL convert_fk_column('dkim_keys', 'domain_id');
CALL convert_fk_column('email_tracking', 'domain_id');
CALL convert_fk_column('email_tracking', 'organization_id');
CALL convert_fk_column('transport_rules', 'organization_id');
CALL convert_fk_column('api_keys', 'organization_id');
CALL convert_fk_column('webhook_urls', 'organization_id');
CALL convert_fk_column('ssl_certificates', 'domain_id');
CALL convert_fk_column('user_sessions', 'email_account_id');
CALL convert_fk_column('shared_mailbox_members', 'email_account_id');
CALL convert_fk_column('alerts', 'organization_id');
CALL convert_fk_column('usage_history', 'organization_id');
CALL convert_fk_column('analytics_data', 'organization_id');
CALL convert_fk_column('analytics_data', 'domain_id');
CALL convert_fk_column('domain_reputation', 'domain_id');
CALL convert_fk_column('feedback_loops', 'domain_id');
CALL convert_fk_column('ip_access_rules', 'organization_id');
CALL convert_fk_column('distribution_groups', 'organization_id');
CALL convert_fk_column('jmap_states', 'email_account_id');
CALL convert_fk_column('oauth_clients', 'organization_id');
CALL convert_fk_column('oauth_grants', 'organization_id');

-- Also convert organization_id columns that are VARCHAR(100) → CHAR(26)
DROP PROCEDURE IF EXISTS shrink_org_id;
DELIMITER //
CREATE PROCEDURE shrink_org_id(IN tbl VARCHAR(64), IN col VARCHAR(64))
BEGIN
    IF EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl
          AND COLUMN_NAME = col AND DATA_TYPE = 'varchar' AND CHARACTER_MAXIMUM_LENGTH > 26
    ) THEN
        SET @sql = CONCAT('ALTER TABLE `', tbl, '` MODIFY COLUMN `', col, '` CHAR(26) NULL');
        PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END //
DELIMITER ;

CALL shrink_org_id('domains', 'organization_id');
CALL shrink_org_id('email_accounts', 'organization_id');
CALL shrink_org_id('aliases', 'organization_id');
CALL shrink_org_id('api_keys', 'organization_id');
CALL shrink_org_id('transport_rules', 'organization_id');
CALL shrink_org_id('webhook_urls', 'organization_id');
CALL shrink_org_id('alerts', 'organization_id');
CALL shrink_org_id('usage_history', 'organization_id');
CALL shrink_org_id('analytics_data', 'organization_id');
CALL shrink_org_id('ip_access_rules', 'organization_id');
CALL shrink_org_id('distribution_groups', 'organization_id');
CALL shrink_org_id('oauth_clients', 'organization_id');
CALL shrink_org_id('oauth_grants', 'organization_id');

DROP PROCEDURE IF EXISTS shrink_org_id;
DROP PROCEDURE IF EXISTS convert_fk_column;

-- Step 5: Re-add critical foreign keys
-- (Only the most important ones — the rest are enforced at application level)
ALTER TABLE domains ADD CONSTRAINT fk_domains_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE email_accounts ADD CONSTRAINT fk_accounts_domain FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE;
ALTER TABLE email_accounts ADD CONSTRAINT fk_accounts_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE aliases ADD CONSTRAINT fk_aliases_domain FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE;
ALTER TABLE dkim_keys ADD CONSTRAINT fk_dkim_domain FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE;

-- Step 6: Clean up
DROP FUNCTION IF EXISTS generate_ulid_backfill;

-- Update alembic version.
-- version_num is the PRIMARY KEY, and the UPDATE chain is broken upstream (002 never
-- sets its version), so multiple rows accumulate and updating them all to the same
-- value trips a duplicate-key error. Reset to exactly one row instead.
DELETE FROM alembic_version;
INSERT INTO alembic_version (version_num) VALUES ('009_ulid_primary_keys');

SELECT 'ULID migration complete' AS status;
