"""Baseline -- schema produced by the frozen SQL chain (001-013)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-30 00:00:00.000000

Phase-08 (schema-migrations): this revision replaces the old
001_initial_schema.py stub, which represented 24 tables from an early ORM
pass and had drifted hard from reality -- the live schema already produced
by database/migrations/sql/001..013 has 70 tables (verified via
`mysqldump --no-data` against a database built fresh from that exact SQL
chain, diffed against a live instance -- see phase-08 build notes).

This is *the* baseline going forward. database/migrations/sql/*.sql is now
frozen (conventions.md SS4): no new file is ever added there again. Every
schema change from this point on is a new Alembic revision, applied by the
`migrate` service (docker-compose.yml) before `api` is allowed to start.

Existing databases that already have this exact schema (every EE/CE
instance up to and including this phase, since it is the only schema chain
that has ever existed) are stamped to this revision rather than replayed --
see scripts/run_migrations.py, which detects "schema present, revision
untracked" and stamps instead of executing CREATE TABLE against tables
that already exist.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Verbatim `mysqldump --no-data` output (statements only, mysqldump's
# session-variable boilerplate stripped) against a database initialized
# from database/migrations/sql/001..013 in order. Do not hand-edit -- if
# the frozen SQL chain is ever wrong, fix it there for CE's benefit (it's
# still CE's first-boot path) and regenerate this file the same way:
#
#   docker exec mysql mysql -u root -e "DROP DATABASE IF EXISTS mig_baseline; CREATE DATABASE mig_baseline;"
#   for f in database/migrations/sql/*.sql; do docker exec -i mysql mysql -u root -D mig_baseline < "$f"; done
#   docker exec mysql mysqldump -u root --no-data --skip-comments --skip-add-locks --skip-set-charset --routines --triggers mig_baseline
_TABLES_SQL = r"""
CREATE TABLE `ai_transactions` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `transaction_id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `session_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `service_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `operation_type` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_provider` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_version` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `prompt_tokens` int NOT NULL DEFAULT '0',
  `completion_tokens` int NOT NULL DEFAULT '0',
  `total_tokens` int NOT NULL DEFAULT '0',
  `cost_per_token` decimal(10,8) DEFAULT NULL,
  `total_cost` decimal(10,6) DEFAULT NULL,
  `currency` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'USD',
  `processing_time_ms` int DEFAULT NULL,
  `latency_ms` int DEFAULT NULL,
  `batch_size` int NOT NULL DEFAULT '1',
  `input_text_length` int DEFAULT NULL,
  `output_text_length` int DEFAULT NULL,
  `request_size_bytes` int DEFAULT NULL,
  `response_size_bytes` int DEFAULT NULL,
  `api_endpoint` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `api_version` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `request_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'success',
  `error_code` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `context_type` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `metadata` json DEFAULT NULL,
  `rate_limit_remaining` int DEFAULT NULL,
  `quota_consumed` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `completed_at` datetime DEFAULT NULL,
  `billing_period` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'monthly',
  `usage_date` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `transaction_id` (`transaction_id`),
  KEY `idx_ai_session` (`session_id`),
  KEY `idx_ai_user` (`user_id`),
  KEY `idx_ai_service` (`service_name`),
  KEY `idx_ai_operation` (`operation_type`),
  KEY `idx_ai_provider` (`model_provider`),
  KEY `idx_ai_model` (`model_name`),
  KEY `idx_ai_status` (`status`),
  KEY `idx_ai_created` (`created_at`),
  KEY `idx_ai_billing` (`billing_period`),
  KEY `idx_ai_usage_date` (`usage_date`),
  KEY `idx_ai_trans_org_date` (`organization_id`,`usage_date`),
  KEY `idx_ai_trans_model_date` (`model_provider`,`model_name`,`usage_date`),
  KEY `idx_ai_trans_service_date` (`service_name`,`operation_type`,`usage_date`),
  KEY `idx_ai_trans_cost` (`total_cost`,`usage_date`),
  KEY `idx_ai_trans_tokens` (`total_tokens`,`usage_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `alerts` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `alert_type` enum('rate_limit','storage_quota') COLLATE utf8mb4_unicode_ci NOT NULL,
  `alert_level` enum('warning','critical','exceeded') COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `domain_id` int DEFAULT NULL,
  `email_account_id` int DEFAULT NULL,
  `current_usage` bigint NOT NULL,
  `limit_value` bigint NOT NULL,
  `usage_percentage` float NOT NULL,
  `context_data` json DEFAULT NULL,
  `webhook_sent` tinyint(1) NOT NULL DEFAULT '0',
  `webhook_attempts` int NOT NULL DEFAULT '0',
  `webhook_last_attempt` datetime DEFAULT NULL,
  `webhook_success` tinyint(1) DEFAULT NULL,
  `resolved` tinyint(1) NOT NULL DEFAULT '0',
  `resolved_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_alerts_type` (`alert_type`),
  KEY `idx_alerts_level` (`alert_level`),
  KEY `idx_alerts_webhook_sent` (`webhook_sent`),
  KEY `idx_alerts_resolved` (`resolved`),
  KEY `idx_alerts_created` (`created_at`),
  KEY `idx_alerts_pending_webhook` (`webhook_sent`,`webhook_attempts`),
  KEY `idx_alerts_org_type` (`organization_id`,`alert_type`),
  KEY `idx_alerts_level_created` (`alert_level`,`created_at`),
  KEY `idx_alerts_resolved_created` (`resolved`,`created_at`),
  KEY `fk_alerts_domain` (`domain_id`),
  KEY `fk_alerts_email_account` (`email_account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `aliases` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `source` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `destination` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `fk_aliases_org` (`organization_id`),
  KEY `fk_aliases_domain` (`domain_id`),
  CONSTRAINT `fk_aliases_domain` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `analytics_data` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `metric_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_value` decimal(15,4) NOT NULL,
  `dimensions` json DEFAULT NULL,
  `timestamp` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `period` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_analytics_org` (`organization_id`),
  KEY `idx_analytics_metric` (`metric_name`),
  KEY `idx_analytics_timestamp` (`timestamp`),
  KEY `idx_analytics_period` (`period`),
  KEY `idx_analytics_org_metric` (`organization_id`,`metric_name`,`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `api_keys` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `key_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `key_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `permissions` json DEFAULT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `rate_limit` int DEFAULT NULL,
  `last_used` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` datetime DEFAULT NULL,
  `ip_whitelist` json DEFAULT NULL,
  `usage_count` bigint NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `key_id` (`key_id`),
  KEY `idx_api_key_expires` (`expires_at`),
  KEY `idx_api_key_active` (`key_id`,`active`),
  KEY `fk_api_keys_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `api_rate_limits` (
  `id` int NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `api_key_id` int DEFAULT NULL,
  `endpoint_pattern` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `max_requests` int NOT NULL DEFAULT '1000',
  `window_seconds` int NOT NULL DEFAULT '3600',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_api_rl_org` (`organization_id`),
  KEY `idx_api_rl_pattern` (`endpoint_pattern`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `audit_logs` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `event_type` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'e.g. auth.login, auth.failed, smtp.send, admin.action',
  `event_source` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Service: postfix, dovecot, api, rspamd',
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `client_ip` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `details` json DEFAULT NULL COMMENT 'Event-specific metadata',
  `severity` enum('info','warning','critical') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'info',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_audit_type` (`event_type`),
  KEY `idx_audit_source` (`event_source`),
  KEY `idx_audit_org` (`organization_id`),
  KEY `idx_audit_user` (`user_email`),
  KEY `idx_audit_ip` (`client_ip`),
  KEY `idx_audit_severity` (`severity`),
  KEY `idx_audit_created` (`created_at`),
  KEY `idx_audit_org_type` (`organization_id`,`event_type`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `backup_history` (
  `id` int NOT NULL AUTO_INCREMENT,
  `backup_type` enum('full','incremental','differential') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'full',
  `target` enum('database','mail','config','all') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'all',
  `storage_path` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'S3 key or local path',
  `size_bytes` bigint DEFAULT NULL,
  `status` enum('running','completed','failed') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'running',
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `completed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_backup_status` (`status`),
  KEY `idx_backup_started` (`started_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `bounce_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `message_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `bounce_type` enum('hard','soft','complaint','unsubscribe') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'hard',
  `bounce_code` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SMTP response code',
  `bounce_reason` text COLLATE utf8mb4_unicode_ci,
  `domain` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `suppressed` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_bounce_org` (`organization_id`),
  KEY `idx_bounce_recipient` (`recipient`),
  KEY `idx_bounce_type` (`bounce_type`),
  KEY `idx_bounce_domain` (`domain`),
  KEY `idx_bounce_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `consent_records` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `consent_type` enum('marketing','analytics','third_party_sharing','data_processing') COLLATE utf8mb4_unicode_ci NOT NULL,
  `granted` tinyint(1) NOT NULL DEFAULT '0',
  `ip_address` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent` text COLLATE utf8mb4_unicode_ci,
  `granted_at` timestamp NULL DEFAULT NULL,
  `revoked_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_type` (`user_email`,`consent_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `data_erasure_requests` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('pending','soft_deleted','hard_deleted','cancelled') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `requested_by` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `reason` text COLLATE utf8mb4_unicode_ci,
  `soft_deleted_at` timestamp NULL DEFAULT NULL,
  `hard_delete_scheduled_at` timestamp NULL DEFAULT NULL,
  `hard_deleted_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_status` (`user_email`,`status`),
  KEY `idx_hard_delete` (`hard_delete_scheduled_at`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `data_export_requests` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('pending','processing','completed','failed','expired') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `export_type` enum('full','emails','contacts','settings') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'full',
  `file_path` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `file_size` bigint DEFAULT '0',
  `requested_by` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `completed_at` timestamp NULL DEFAULT NULL,
  `expires_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_status` (`user_email`,`status`),
  KEY `idx_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `distribution_group_members` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `group_id` int NOT NULL,
  `email_account_id` int DEFAULT NULL COMMENT 'Internal member (NULL if external)',
  `external_email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'External member address',
  `role` enum('member','owner','moderator') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'member',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_group_member_group` (`group_id`),
  KEY `idx_group_member_account` (`email_account_id`),
  KEY `idx_group_member_role` (`role`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `distribution_groups` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Group email address',
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `group_type` enum('distribution','security','dynamic') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'distribution',
  `moderation_enabled` tinyint(1) NOT NULL DEFAULT '0',
  `external_delivery` tinyint(1) NOT NULL DEFAULT '1' COMMENT 'Allow delivery from non-members',
  `max_message_size` bigint DEFAULT NULL COMMENT 'Max message size in bytes (NULL = no limit)',
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`),
  KEY `idx_group_org` (`organization_id`),
  KEY `idx_group_active` (`active`),
  KEY `idx_group_type` (`group_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `dkim_keys` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `selector` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'default',
  `private_key` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `public_key` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_dkim_domain_selector` (`domain_id`,`selector`),
  CONSTRAINT `fk_dkim_domain` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `dlp_policies` (
  `id` int NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `policy_type` enum('pii','keyword','regex','file_type') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'keyword',
  `patterns` json NOT NULL COMMENT 'Array of patterns to match',
  `action` enum('block','quarantine','encrypt','notify','log_only') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'notify',
  `severity` enum('low','medium','high','critical') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'medium',
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `apply_to` enum('inbound','outbound','both') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'outbound',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_org_enabled` (`organization_id`,`enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `dlp_violations` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `policy_id` int NOT NULL,
  `message_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `violation_type` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `matched_pattern` text COLLATE utf8mb4_unicode_ci,
  `action_taken` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `severity` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `details` json DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_org_date` (`organization_id`,`created_at`),
  KEY `idx_policy` (`policy_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `domain_reputation` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `overall_score` int NOT NULL DEFAULT '50',
  `deliverability_score` int NOT NULL DEFAULT '50',
  `engagement_score` int NOT NULL DEFAULT '50',
  `emails_sent` bigint NOT NULL DEFAULT '0',
  `emails_delivered` bigint NOT NULL DEFAULT '0',
  `emails_bounced` bigint NOT NULL DEFAULT '0',
  `emails_complained` bigint NOT NULL DEFAULT '0',
  `emails_opened` bigint NOT NULL DEFAULT '0',
  `emails_clicked` bigint NOT NULL DEFAULT '0',
  `period_start` datetime NOT NULL,
  `period_end` datetime NOT NULL,
  `period_type` enum('HOURLY','DAILY','WEEKLY','MONTHLY') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'DAILY',
  `isp_data` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_domain_rep_domain` (`domain_id`),
  KEY `idx_domain_rep_org` (`organization_id`),
  KEY `idx_domain_rep_period_start` (`period_start`),
  KEY `idx_domain_rep_period_end` (`period_end`),
  KEY `idx_domain_reputation_period` (`domain_id`,`period_type`,`period_start`),
  KEY `idx_domain_reputation_org` (`organization_id`,`period_start`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `domains` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `external_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `max_quota` bigint NOT NULL DEFAULT '10737418240',
  `max_users` int NOT NULL DEFAULT '1000',
  `dkim_enabled` tinyint(1) NOT NULL DEFAULT '1',
  `dkim_selector` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'default',
  `rate_limits` json DEFAULT NULL,
  `storage_quotas` json DEFAULT NULL,
  `total_storage_used` bigint NOT NULL DEFAULT '0',
  `total_attachment_storage` bigint NOT NULL DEFAULT '0',
  `total_email_storage` bigint NOT NULL DEFAULT '0',
  `total_email_accounts` int NOT NULL DEFAULT '0',
  `total_emails` bigint NOT NULL DEFAULT '0',
  `total_attachments` bigint NOT NULL DEFAULT '0',
  `rate_usage_data` json DEFAULT NULL,
  `last_storage_calculation` datetime DEFAULT NULL,
  `storage_calculation_time_ms` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `domain` (`domain`),
  UNIQUE KEY `external_id` (`external_id`),
  KEY `idx_domain_org_active` (`organization_id`,`active`),
  KEY `idx_domain_name_org` (`domain`,`organization_id`),
  KEY `idx_domain_active_created` (`active`,`created_at`),
  KEY `idx_domain_org_updated` (`organization_id`,`updated_at`),
  KEY `idx_domain_storage_used` (`total_storage_used`),
  KEY `idx_domain_storage_calc` (`last_storage_calculation`),
  KEY `idx_domain_external_id` (`external_id`),
  CONSTRAINT `fk_domains_org` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `email_accounts` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `external_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `local_part` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `password` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` enum('active','inactive','suspended') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `mailbox_type` enum('personal','shared') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'personal',
  `storage_quota` bigint NOT NULL DEFAULT '1073741824',
  `storage_used` bigint NOT NULL DEFAULT '0',
  `attachment_storage_used` bigint NOT NULL DEFAULT '0',
  `email_storage_used` bigint NOT NULL DEFAULT '0',
  `total_files` int NOT NULL DEFAULT '0',
  `total_attachments` int NOT NULL DEFAULT '0',
  `total_emails` int NOT NULL DEFAULT '0',
  `rate_usage_data` json DEFAULT NULL,
  `rate_limits` json DEFAULT NULL,
  `storage_quotas` json DEFAULT NULL,
  `forward_enabled` tinyint(1) NOT NULL DEFAULT '0',
  `forward_destination` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `vacation_enabled` tinyint(1) NOT NULL DEFAULT '0',
  `vacation_message` text COLLATE utf8mb4_unicode_ci,
  `last_login` datetime DEFAULT NULL,
  `last_activity` datetime DEFAULT NULL,
  `last_storage_calculation` datetime DEFAULT NULL,
  `storage_calculation_time_ms` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`),
  UNIQUE KEY `external_id` (`external_id`),
  KEY `idx_email_domain_org` (`domain_id`,`organization_id`),
  KEY `idx_email_status` (`status`),
  KEY `idx_email_storage_used` (`storage_used`),
  KEY `idx_email_last_activity` (`last_activity`),
  KEY `idx_email_org_status` (`organization_id`,`status`),
  KEY `idx_email_org_created` (`organization_id`,`created_at`),
  KEY `idx_email_local_domain` (`local_part`,`domain_id`),
  KEY `idx_email_status_activity` (`status`,`last_activity`),
  KEY `idx_email_org_activity` (`organization_id`,`last_activity`),
  KEY `idx_email_external_id` (`external_id`),
  KEY `idx_email_mailbox_type` (`mailbox_type`),
  CONSTRAINT `fk_accounts_domain` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_accounts_org` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `email_archive` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `message_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `subject` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `storage_key` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'S3 object key',
  `storage_type` enum('s3','azure','gcs','local') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 's3',
  `original_size` bigint DEFAULT NULL,
  `compressed_size` bigint DEFAULT NULL,
  `legal_hold` tinyint(1) NOT NULL DEFAULT '0',
  `archived_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_archive_org` (`organization_id`),
  KEY `idx_archive_message` (`message_id`),
  KEY `idx_archive_legal_hold` (`legal_hold`),
  KEY `idx_archive_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `email_suppressions` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `suppression_type` enum('BOUNCE','COMPLAINT','UNSUBSCRIBE','MANUAL') COLLATE utf8mb4_unicode_ci NOT NULL,
  `reason` text COLLATE utf8mb4_unicode_ci,
  `source` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `expires_at` datetime DEFAULT NULL,
  `bounce_type` enum('HARD','SOFT','BLOCK') COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bounce_count` int NOT NULL DEFAULT '1',
  `last_bounce_reason` text COLLATE utf8mb4_unicode_ci,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_email_suppression` (`email`,`organization_id`,`suppression_type`),
  KEY `idx_suppression_email` (`email`),
  KEY `idx_suppression_org` (`organization_id`),
  KEY `idx_suppression_type` (`suppression_type`),
  KEY `idx_suppression_active` (`active`),
  KEY `idx_suppression_expires` (`expires_at`),
  KEY `idx_suppression_expires_active` (`expires_at`,`active`),
  KEY `idx_suppression_type_org` (`suppression_type`,`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `email_template_versions` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `template_id` int NOT NULL,
  `version` int NOT NULL DEFAULT '1',
  `subject` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `body_html` longtext COLLATE utf8mb4_unicode_ci,
  `body_text` longtext COLLATE utf8mb4_unicode_ci,
  `variables` json DEFAULT NULL,
  `created_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_tmpl_ver_template` (`template_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `email_templates` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `subject` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `body_html` longtext COLLATE utf8mb4_unicode_ci,
  `body_text` longtext COLLATE utf8mb4_unicode_ci,
  `variables` json DEFAULT NULL COMMENT 'Expected variable names and descriptions',
  `category` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_template_org_name` (`organization_id`,`name`),
  KEY `idx_template_org` (`organization_id`),
  KEY `idx_template_active` (`active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `email_tracking` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email_id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `event_type` enum('delivered','opened','clicked','bounced','complained','unsubscribed') COLLATE utf8mb4_unicode_ci NOT NULL,
  `timestamp` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `user_agent` text COLLATE utf8mb4_unicode_ci,
  `ip_address` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `referer` text COLLATE utf8mb4_unicode_ci,
  `accept_language` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `device_type` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `browser` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `operating_system` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `country` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `region` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `city` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `additional_data` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_tracking_email_id` (`email_id`),
  KEY `idx_tracking_recipient` (`recipient`),
  KEY `idx_tracking_event_type` (`event_type`),
  KEY `idx_tracking_timestamp` (`timestamp`),
  KEY `idx_tracking_ip` (`ip_address`),
  KEY `idx_tracking_country` (`country`),
  KEY `idx_email_event_time` (`email_id`,`event_type`,`timestamp`),
  KEY `idx_org_time` (`organization_id`,`timestamp`),
  KEY `idx_recipient_time` (`recipient`,`timestamp`),
  KEY `idx_event_time` (`event_type`,`timestamp`),
  KEY `fk_email_tracking_domain` (`domain_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `failed_auth_attempts` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_ip` varchar(45) COLLATE utf8mb4_unicode_ci NOT NULL,
  `username` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `service` enum('smtp','imap','pop3','api','sieve') COLLATE utf8mb4_unicode_ci NOT NULL,
  `failure_reason` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `blocked_until` datetime DEFAULT NULL COMMENT 'If set, IP is blocked until this time',
  `attempt_count` int NOT NULL DEFAULT '1',
  `first_attempt_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_attempt_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_failed_auth_ip` (`client_ip`),
  KEY `idx_failed_auth_user` (`username`),
  KEY `idx_failed_auth_service` (`service`),
  KEY `idx_failed_auth_blocked` (`blocked_until`),
  KEY `idx_failed_auth_ip_service` (`client_ip`,`service`,`last_attempt_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `feedback_loops` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `original_recipient` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `complaint_recipient` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `isp_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `feedback_type` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'abuse',
  `email_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `subject` text COLLATE utf8mb4_unicode_ci,
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `raw_feedback` text COLLATE utf8mb4_unicode_ci,
  `headers` json DEFAULT NULL,
  `processed` tinyint(1) NOT NULL DEFAULT '0',
  `suppression_added` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `processed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_fbl_recipient` (`original_recipient`),
  KEY `idx_fbl_org` (`organization_id`),
  KEY `idx_fbl_isp` (`isp_name`),
  KEY `idx_fbl_email_id` (`email_id`),
  KEY `idx_fbl_sender` (`sender`),
  KEY `idx_fbl_processed` (`processed`),
  KEY `idx_fbl_created` (`created_at`),
  KEY `idx_fbl_isp_date` (`isp_name`,`created_at`),
  KEY `idx_fbl_processing` (`processed`,`created_at`),
  KEY `idx_fbl_org_date` (`organization_id`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `geo_policies` (
  `id` int NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `allowed_countries` json DEFAULT NULL COMMENT 'Array of ISO 3166-1 alpha-2 codes',
  `blocked_countries` json DEFAULT NULL COMMENT 'Array of ISO 3166-1 alpha-2 codes',
  `time_restrictions` json DEFAULT NULL COMMENT 'Time-of-day access rules',
  `action` enum('block','challenge','log_only') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'block',
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `health_checks` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `service_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `response_time` float DEFAULT NULL,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `metadata` json DEFAULT NULL,
  `timestamp` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_health_service` (`service_name`),
  KEY `idx_health_status` (`status`),
  KEY `idx_health_timestamp` (`timestamp`),
  KEY `idx_health_service_time` (`service_name`,`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `idempotency_keys` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `idempotency_key` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_method` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_path` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `response_status` int DEFAULT NULL,
  `response_body` mediumtext COLLATE utf8mb4_unicode_ci,
  `state` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'in_progress',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_idem` (`organization_id`,`idempotency_key`),
  KEY `idx_idem_expiry` (`expires_at`),
  CONSTRAINT `fk_idem_org` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `ip_access_rules` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `rule_type` enum('whitelist','blacklist') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'whitelist',
  `ip_address` varchar(45) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'IPv4/IPv6 address or CIDR notation',
  `description` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Email of admin who created the rule',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_ip_rules_org` (`organization_id`),
  KEY `idx_ip_rules_type` (`rule_type`),
  KEY `idx_ip_rules_active` (`organization_id`,`active`,`rule_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `ip_reputation` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ip_address` varchar(45) COLLATE utf8mb4_unicode_ci NOT NULL,
  `reputation_score` decimal(5,2) NOT NULL DEFAULT '50.00' COMMENT '0=bad, 100=good',
  `total_connections` int NOT NULL DEFAULT '0',
  `total_messages` int NOT NULL DEFAULT '0',
  `spam_count` int NOT NULL DEFAULT '0',
  `bounce_count` int NOT NULL DEFAULT '0',
  `auth_failure_count` int NOT NULL DEFAULT '0',
  `last_seen` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `first_seen` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_blocked` tinyint(1) NOT NULL DEFAULT '0',
  `blocked_reason` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `blocked_until` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ip_address` (`ip_address`),
  KEY `idx_ip_rep_score` (`reputation_score`),
  KEY `idx_ip_rep_blocked` (`is_blocked`),
  KEY `idx_ip_rep_last_seen` (`last_seen`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `jmap_states` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `account_id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Email address acting as the JMAP account identifier',
  `data_type` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'JMAP data type: Mailbox, Email, Thread, etc.',
  `state` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Opaque state string returned to JMAP clients',
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_jmap_account_type` (`account_id`,`data_type`),
  KEY `idx_jmap_account` (`account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `kafka_dead_letters` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `topic` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `partition_num` int DEFAULT NULL,
  `offset_num` bigint DEFAULT NULL,
  `key_data` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `value_data` longtext COLLATE utf8mb4_unicode_ci,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `retry_count` int NOT NULL DEFAULT '0',
  `status` enum('pending','retrying','resolved','abandoned') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_kafka_dl_topic_status` (`topic`,`status`),
  KEY `idx_kafka_dl_status` (`status`),
  KEY `idx_kafka_dl_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `legal_holds` (
  `id` int NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `custodians` json DEFAULT NULL COMMENT 'List of email addresses under hold',
  `start_date` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `end_date` datetime DEFAULT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_legal_hold_org` (`organization_id`),
  KEY `idx_legal_hold_active` (`active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `mail_logs` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `timestamp` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `subject` text COLLATE utf8mb4_unicode_ci,
  `status` enum('queued','sending','sent','delivered','bounced','rejected','deferred') COLLATE utf8mb4_unicode_ci NOT NULL,
  `message_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `size` int DEFAULT NULL,
  `relay` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `delays` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `dsn` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bounce_reason` text COLLATE utf8mb4_unicode_ci,
  `spam_score` float DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_mail_logs_timestamp` (`timestamp`),
  KEY `idx_mail_logs_sender` (`sender`),
  KEY `idx_mail_logs_recipient` (`recipient`),
  KEY `idx_mail_logs_status` (`status`),
  KEY `idx_mail_logs_message_id` (`message_id`),
  KEY `idx_mail_logs_time_status` (`timestamp`,`status`),
  KEY `idx_mail_logs_sender_time` (`sender`,`timestamp`),
  KEY `fk_mail_logs_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `mail_queue` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `subject` text COLLATE utf8mb4_unicode_ci,
  `body` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `headers` json DEFAULT NULL,
  `priority` int NOT NULL DEFAULT '5',
  `status` enum('queued','sending','sent','delivered','bounced','rejected','deferred') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'queued',
  `attempts` int NOT NULL DEFAULT '0',
  `max_attempts` int NOT NULL DEFAULT '3',
  `scheduled_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `processed_at` datetime DEFAULT NULL,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `worker_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `processing_time` float DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_queue_sender` (`sender`),
  KEY `idx_queue_recipient` (`recipient`),
  KEY `idx_queue_priority` (`priority`),
  KEY `idx_queue_status` (`status`),
  KEY `idx_queue_scheduled` (`scheduled_at`),
  KEY `idx_queue_processing` (`status`,`scheduled_at`,`priority`),
  KEY `fk_mail_queue_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `migration_errors` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_id` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `folder` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `message_uid` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT '',
  `message_id` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT '',
  `error_type` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Python exception class name',
  `error_message` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `retryable` tinyint(1) NOT NULL DEFAULT '1' COMMENT 'Whether this message can be retried',
  `retried` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'Whether a retry has been attempted',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_job` (`job_id`),
  KEY `idx_job_retryable` (`job_id`,`retryable`,`retried`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `migration_jobs` (
  `job_id` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `direction` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'import',
  `source_host` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_port` int NOT NULL DEFAULT '993',
  `source_ssl` tinyint(1) NOT NULL DEFAULT '1',
  `source_username` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_password` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `target_host` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `target_port` int DEFAULT '993',
  `target_ssl` tinyint(1) DEFAULT '1',
  `target_email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `target_password` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `org_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `folder_mapping` json DEFAULT NULL,
  `exclude_folders` json DEFAULT NULL,
  `total_messages` int NOT NULL DEFAULT '0',
  `migrated_messages` int NOT NULL DEFAULT '0',
  `failed_messages` int NOT NULL DEFAULT '0',
  `current_folder` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `speed` double NOT NULL DEFAULT '0',
  `error_log` longtext COLLATE utf8mb4_unicode_ci,
  `is_delta` tinyint(1) NOT NULL DEFAULT '0',
  `is_retry` tinyint(1) NOT NULL DEFAULT '0',
  `parent_job_id` varchar(36) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `last_uid` json DEFAULT NULL,
  `webhook_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`job_id`),
  KEY `idx_status` (`status`),
  KEY `idx_org_id` (`org_id`),
  KEY `idx_direction` (`direction`),
  KEY `idx_parent` (`parent_job_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `oauth_clients` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_secret` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'bcrypt-hashed',
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `client_type` enum('confidential','public') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'confidential',
  `redirect_uris` json DEFAULT NULL,
  `scopes` json DEFAULT NULL COMMENT 'Array of allowed scopes',
  `grant_types` json DEFAULT NULL COMMENT 'allowed: authorization_code, refresh_token, client_credentials',
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `client_id` (`client_id`),
  KEY `idx_oauth_client_org` (`organization_id`),
  KEY `idx_oauth_client_active` (`active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `oauth_grants` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `access_token_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `refresh_token_hash` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `scopes` json DEFAULT NULL,
  `access_expires_at` datetime NOT NULL,
  `refresh_expires_at` datetime DEFAULT NULL,
  `revoked` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `access_token_hash` (`access_token_hash`),
  UNIQUE KEY `refresh_token_hash` (`refresh_token_hash`),
  KEY `idx_oauth_grant_client` (`client_id`),
  KEY `idx_oauth_grant_user` (`user_email`),
  KEY `idx_oauth_grant_revoked` (`revoked`),
  KEY `idx_oauth_grant_expires` (`access_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `organizations` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `parent_organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `external_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `admin_email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `admin_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `settings` json DEFAULT NULL,
  `rate_limits` json DEFAULT NULL,
  `storage_quotas` json DEFAULT NULL,
  `webhook_urls` json DEFAULT NULL,
  `webhook_secret` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `whitelabel_domain` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `external_id` (`external_id`),
  KEY `idx_org_active` (`active`),
  KEY `idx_org_name` (`name`),
  KEY `idx_org_external_id` (`external_id`),
  KEY `idx_org_active_created` (`active`,`created_at`),
  KEY `idx_org_name_active` (`name`,`active`),
  KEY `idx_org_parent` (`parent_organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pgp_keys` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `fingerprint` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `public_key` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `private_key` longtext COLLATE utf8mb4_unicode_ci COMMENT 'Encrypted private key (null = public only)',
  `key_type` enum('rsa','dsa','ecdsa','ed25519') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'rsa',
  `key_length` int DEFAULT NULL COMMENT 'Key size in bits (RSA/DSA only)',
  `has_private` tinyint(1) NOT NULL DEFAULT '0',
  `expires_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `fingerprint` (`fingerprint`),
  KEY `idx_pgp_email` (`email`),
  KEY `idx_pgp_fingerprint` (`fingerprint`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `quarantine` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `message_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'RFC 2822 Message-ID',
  `queue_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Postfix queue ID',
  `sender` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `subject` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `reason` enum('spam','virus','policy','transport_rule','admin') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'spam',
  `spam_score` decimal(6,2) DEFAULT NULL,
  `virus_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `rule_id` int DEFAULT NULL COMMENT 'Transport rule that triggered quarantine',
  `headers` json DEFAULT NULL COMMENT 'Original message headers',
  `storage_key` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'S3 key for quarantined message body',
  `status` enum('quarantined','released','deleted','expired') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'quarantined',
  `released_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Admin who released/deleted',
  `released_at` datetime DEFAULT NULL,
  `expires_at` datetime NOT NULL COMMENT 'Auto-delete after this date',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_quarantine_org` (`organization_id`),
  KEY `idx_quarantine_sender` (`sender`),
  KEY `idx_quarantine_recipient` (`recipient`),
  KEY `idx_quarantine_status` (`status`),
  KEY `idx_quarantine_reason` (`reason`),
  KEY `idx_quarantine_expires` (`expires_at`),
  KEY `idx_quarantine_created` (`created_at`),
  KEY `idx_quarantine_org_status` (`organization_id`,`status`,`created_at`),
  KEY `idx_quarantine_message_id` (`message_id`),
  KEY `fk_quarantine_rule` (`rule_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `queue_statistics` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `queue_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `message_count` int NOT NULL DEFAULT '0',
  `size_bytes` bigint NOT NULL DEFAULT '0',
  `oldest_message_age` int DEFAULT NULL COMMENT 'Seconds',
  `processed_total` bigint NOT NULL DEFAULT '0',
  `failed_total` bigint NOT NULL DEFAULT '0',
  `recorded_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_queue_stats_name` (`queue_name`),
  KEY `idx_queue_stats_time` (`recorded_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `rate_limit_alerts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `entity_type` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `identifier` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `window` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `usage_pct` decimal(5,2) NOT NULL,
  `alert_level` enum('warning','critical','exceeded') COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_rl_alert_org` (`organization_id`),
  KEY `idx_rl_alert_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `rate_limit_configs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'NULL = global default',
  `entity_type` enum('organization','domain','mailbox','api_key') COLLATE utf8mb4_unicode_ci NOT NULL,
  `identifier` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'NULL = applies to all of entity_type',
  `window` enum('second','minute','hour','day','month') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'hour',
  `max_requests` int NOT NULL,
  `warning_pct` int NOT NULL DEFAULT '80',
  `critical_pct` int NOT NULL DEFAULT '95',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_rl_config_org` (`organization_id`),
  KEY `idx_rl_config_entity` (`entity_type`,`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `retention_policies` (
  `id` int NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `retention_days` int NOT NULL DEFAULT '365',
  `legal_hold` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'Override: never delete',
  `auto_archive` tinyint(1) NOT NULL DEFAULT '1',
  `archive_after_days` int NOT NULL DEFAULT '90',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `service_metrics` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `service_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_value` float NOT NULL,
  `timestamp` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_metrics_service` (`service_name`),
  KEY `idx_metrics_name` (`metric_name`),
  KEY `idx_metrics_timestamp` (`timestamp`),
  KEY `idx_metrics_service_metric` (`service_name`,`metric_name`,`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `shared_mailbox_members` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `shared_mailbox_id` int NOT NULL COMMENT 'The shared mailbox (email_accounts.id where mailbox_type=shared)',
  `email_account_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `permission` enum('full_access','send_as','send_on_behalf','read_only') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'read_only',
  `granted_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Email of admin who granted access',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_shared_member` (`shared_mailbox_id`,`email_account_id`),
  KEY `idx_shared_mailbox` (`shared_mailbox_id`),
  KEY `idx_shared_member` (`email_account_id`),
  KEY `idx_shared_permission` (`permission`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `smime_certs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `certificate` longtext COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'PEM-encoded certificate',
  `private_key` longtext COLLATE utf8mb4_unicode_ci COMMENT 'Encrypted private key',
  `issuer` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `subject` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `serial` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `not_before` datetime DEFAULT NULL,
  `not_after` datetime DEFAULT NULL,
  `has_private` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_smime_email` (`email`),
  KEY `idx_smime_expires` (`not_after`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `ssl_certificates` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `certificate_path` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `private_key_path` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `chain_path` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` enum('active','expired','revoked','pending') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `issuer` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `valid_from` datetime DEFAULT NULL,
  `valid_until` datetime DEFAULT NULL,
  `auto_renew` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_renewed` datetime DEFAULT NULL,
  `fingerprint` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `algorithm` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `key_size` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ssl_status` (`status`),
  KEY `idx_ssl_valid_until` (`valid_until`),
  KEY `idx_ssl_expiry` (`valid_until`,`status`),
  KEY `fk_ssl_certs_domain` (`domain_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `storage_alerts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `entity_type` enum('organization','domain','mailbox') COLLATE utf8mb4_unicode_ci NOT NULL,
  `identifier` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `alert_level` enum('warning','critical','exceeded') COLLATE utf8mb4_unicode_ci NOT NULL,
  `usage_percentage` decimal(5,2) NOT NULL,
  `used_bytes` bigint NOT NULL,
  `quota_bytes` bigint NOT NULL,
  `acknowledged` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_storage_alert_org` (`organization_id`),
  KEY `idx_storage_alert_level` (`alert_level`),
  KEY `idx_storage_alert_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `storage_usage` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email_account` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `storage_type` enum('mailbox','attachments','archive','total') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'total',
  `used_bytes` bigint NOT NULL DEFAULT '0',
  `quota_bytes` bigint DEFAULT NULL,
  `calculated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_storage_org` (`organization_id`),
  KEY `idx_storage_account` (`email_account`),
  KEY `idx_storage_calculated` (`calculated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `system_config` (
  `key` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `value` json DEFAULT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `category` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`key`),
  KEY `idx_system_config_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `template_render_log` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `template_id` int DEFAULT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `recipient` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `variables_used` json DEFAULT NULL,
  `rendered_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `status` enum('success','error') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'success',
  `error_message` text COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`),
  KEY `idx_render_log_template` (`template_id`),
  KEY `idx_render_log_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `totp_secrets` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `secret` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `backup_codes` json DEFAULT NULL COMMENT 'Array of hashed backup codes',
  `enabled` tinyint(1) NOT NULL DEFAULT '0',
  `verified` tinyint(1) NOT NULL DEFAULT '0',
  `last_used_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `user_email` (`user_email`),
  KEY `idx_email` (`user_email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `tracking_statistics` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email_id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `domain_id` int NOT NULL,
  `event_type` enum('delivered','opened','clicked','bounced','complained','unsubscribed') COLLATE utf8mb4_unicode_ci NOT NULL,
  `total_count` int NOT NULL DEFAULT '0',
  `unique_count` int NOT NULL DEFAULT '0',
  `unique_ips` int NOT NULL DEFAULT '0',
  `first_event` datetime DEFAULT NULL,
  `last_event` datetime DEFAULT NULL,
  `last_updated` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_stats_email_id` (`email_id`),
  KEY `idx_stats_event_type` (`event_type`),
  KEY `idx_stats_email_event` (`email_id`,`event_type`),
  KEY `idx_stats_org_event` (`organization_id`,`event_type`),
  KEY `fk_tracking_stats_domain` (`domain_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `transport_rules` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `direction` enum('inbound','outbound','both') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'both',
  `conditions` json NOT NULL COMMENT '[{field, operator, value}]',
  `condition_logic` enum('all','any') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'all' COMMENT 'AND vs OR for conditions',
  `actions` json NOT NULL COMMENT '[{type, params}]',
  `priority` int NOT NULL DEFAULT '100' COMMENT 'Lower = evaluated first',
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `hit_count` bigint NOT NULL DEFAULT '0' COMMENT 'Number of messages matched',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_transport_org` (`organization_id`),
  KEY `idx_transport_enabled` (`enabled`),
  KEY `idx_transport_priority` (`priority`),
  KEY `idx_transport_org_enabled` (`organization_id`,`enabled`,`priority`),
  KEY `idx_transport_direction` (`direction`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `url_blocklist` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'NULL = global block',
  `entry` varchar(2048) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'URL, domain, or pattern',
  `entry_type` enum('url','domain','pattern','allowlist') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'domain',
  `reason` text COLLATE utf8mb4_unicode_ci,
  `added_by` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_url_block_org` (`organization_id`),
  KEY `idx_url_block_type` (`entry_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `url_clicks` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `message_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `original_url` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `url_domain` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `safe_url` text COLLATE utf8mb4_unicode_ci,
  `scan_result` enum('safe','suspicious','malicious','error','pending') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `scan_details` json DEFAULT NULL,
  `clicked_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `client_ip` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent` text COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`),
  KEY `idx_url_click_org` (`organization_id`),
  KEY `idx_url_click_message` (`message_id`),
  KEY `idx_url_click_result` (`scan_result`),
  KEY `idx_url_click_domain` (`url_domain`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `usage_history` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `usage_type` enum('rate_limit','storage_quota') COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `domain_id` int DEFAULT NULL,
  `email_account_id` int DEFAULT NULL,
  `hour_key` varchar(13) COLLATE utf8mb4_unicode_ci NOT NULL,
  `day_key` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `month_key` varchar(7) COLLATE utf8mb4_unicode_ci NOT NULL,
  `usage_data` json NOT NULL,
  `recorded_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_usage_type` (`usage_type`),
  KEY `idx_usage_hour_key` (`hour_key`),
  KEY `idx_usage_day_key` (`day_key`),
  KEY `idx_usage_month_key` (`month_key`),
  KEY `idx_usage_recorded` (`recorded_at`),
  KEY `idx_usage_history_org_type_day` (`organization_id`,`usage_type`,`day_key`),
  KEY `idx_usage_history_cleanup` (`day_key`),
  KEY `idx_usage_history_entity_time` (`email_account_id`,`usage_type`,`hour_key`),
  KEY `fk_usage_history_domain` (`domain_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `user_logins` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_ip` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent` text COLLATE utf8mb4_unicode_ci,
  `protocol` enum('imap','pop3','smtp','webmail','api') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'imap',
  `success` tinyint(1) NOT NULL DEFAULT '1',
  `failure_reason` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `logged_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_login_email` (`user_email`),
  KEY `idx_user_login_ip` (`client_ip`),
  KEY `idx_user_login_time` (`logged_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `user_sessions` (
  `id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email_account_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ip_address` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent` text COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_activity` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` datetime NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`),
  KEY `idx_sessions_last_activity` (`last_activity`),
  KEY `idx_sessions_expires` (`expires_at`),
  KEY `fk_sessions_email_account` (`email_account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `users` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `role` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'admin',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `last_login_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_org_email` (`organization_id`,`email`),
  KEY `idx_users_org` (`organization_id`),
  CONSTRAINT `fk_users_org` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `web_sessions` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `token_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ip_address` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `expires_at` datetime NOT NULL,
  `absolute_expiry` datetime NOT NULL,
  `revoked_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_web_sessions_token` (`token_hash`),
  KEY `idx_web_sessions_user` (`user_id`),
  KEY `idx_web_sessions_expiry` (`expires_at`),
  CONSTRAINT `fk_web_sessions_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `webhook_dead_letters` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `event_type` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `endpoint_url` varchar(2048) COLLATE utf8mb4_unicode_ci NOT NULL,
  `payload` json NOT NULL,
  `last_error` text COLLATE utf8mb4_unicode_ci,
  `attempt_count` int NOT NULL DEFAULT '0',
  `status` enum('pending','retrying','resolved','abandoned') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_attempted_at` datetime DEFAULT NULL,
  `resolved_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_wdl_org` (`organization_id`),
  KEY `idx_wdl_status` (`status`),
  KEY `idx_wdl_event` (`event_type`),
  KEY `idx_wdl_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `webhook_delivery_logs` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `webhook_url_id` int DEFAULT NULL,
  `event_type` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `event_data` json NOT NULL,
  `webhook_url` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL,
  `webhook_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `delivery_status` enum('pending','delivered','failed','retrying','abandoned') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `attempts` int NOT NULL DEFAULT '0',
  `max_attempts` int NOT NULL DEFAULT '3',
  `next_retry_at` datetime DEFAULT NULL,
  `retry_delay_seconds` int NOT NULL DEFAULT '5',
  `backoff_multiplier` float NOT NULL DEFAULT '2',
  `max_retry_delay` int NOT NULL DEFAULT '3600',
  `http_status_code` int DEFAULT NULL,
  `response_headers` json DEFAULT NULL,
  `response_body` text COLLATE utf8mb4_unicode_ci,
  `response_size_bytes` int DEFAULT NULL,
  `request_duration_ms` int DEFAULT NULL,
  `dns_resolution_ms` int DEFAULT NULL,
  `connection_time_ms` int DEFAULT NULL,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `error_code` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `last_error_at` datetime DEFAULT NULL,
  `payload_size_bytes` int DEFAULT NULL,
  `payload_encrypted` tinyint(1) NOT NULL DEFAULT '0',
  `signature_verified` tinyint(1) DEFAULT NULL,
  `organization_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `domain_id` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `first_attempt_at` datetime DEFAULT NULL,
  `last_attempt_at` datetime DEFAULT NULL,
  `delivered_at` datetime DEFAULT NULL,
  `abandoned_at` datetime DEFAULT NULL,
  `auto_cleanup_enabled` tinyint(1) NOT NULL DEFAULT '1',
  `cleanup_after_hours` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_wdl_event_type` (`event_type`),
  KEY `idx_wdl_webhook_url` (`webhook_url`(255)),
  KEY `idx_wdl_delivery_status` (`delivery_status`),
  KEY `idx_wdl_next_retry` (`next_retry_at`),
  KEY `idx_wdl_http_status` (`http_status_code`),
  KEY `idx_wdl_created_at` (`created_at`),
  KEY `idx_wdl_delivered_at` (`delivered_at`),
  KEY `idx_webhook_delivery_status_retry` (`delivery_status`,`next_retry_at`),
  KEY `idx_webhook_delivery_cleanup_success` (`delivery_status`,`delivered_at`,`auto_cleanup_enabled`),
  KEY `idx_webhook_delivery_cleanup_failed` (`delivery_status`,`abandoned_at`,`auto_cleanup_enabled`),
  KEY `idx_webhook_delivery_attempts` (`attempts`,`max_attempts`),
  KEY `idx_webhook_delivery_org_time` (`organization_id`,`created_at`),
  KEY `idx_webhook_delivery_url_time` (`webhook_url`(255),`created_at`),
  KEY `idx_webhook_delivery_event_time` (`event_type`,`created_at`),
  KEY `fk_webhook_delivery_url` (`webhook_url_id`),
  KEY `fk_webhook_delivery_domain` (`domain_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `webhook_urls` (
  `id` char(26) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(26) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `url` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL,
  `event_types` json NOT NULL,
  `service_types` json NOT NULL,
  `encryption_key` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `webhook_secret` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `priority` int NOT NULL DEFAULT '1',
  `timeout_seconds` int NOT NULL DEFAULT '30',
  `retry_attempts` int NOT NULL DEFAULT '3',
  `retry_delay_seconds` int NOT NULL DEFAULT '5',
  `rate_limit_per_minute` int DEFAULT NULL,
  `tenant_filter` json DEFAULT NULL,
  `domain_filter` json DEFAULT NULL,
  `custom_headers` json DEFAULT NULL,
  `auth_type` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `auth_credentials` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `last_success` datetime DEFAULT NULL,
  `last_failure` datetime DEFAULT NULL,
  `success_count` bigint NOT NULL DEFAULT '0',
  `failure_count` bigint NOT NULL DEFAULT '0',
  `description` text COLLATE utf8mb4_unicode_ci,
  `tags` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_webhook_urls_active` (`active`),
  KEY `idx_webhook_urls_priority` (`priority`),
  KEY `idx_webhook_urls_last_success` (`last_success`),
  KEY `fk_webhook_urls_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

_VIEW_SQL = r"""
CREATE VIEW `audit_log` AS select `audit_logs`.`id` AS `id`,`audit_logs`.`event_type` AS `action`,`audit_logs`.`user_email` AS `user_email`,NULL AS `performed_by`,`audit_logs`.`client_ip` AS `ip_address`,`audit_logs`.`details` AS `details`,`audit_logs`.`created_at` AS `created_at` from `audit_logs`
"""


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
    for stmt in _TABLES_SQL.strip().split(";\n\n"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(sa.text(stmt))
    conn.execute(sa.text(_VIEW_SQL.strip()))
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))


def downgrade() -> None:
    """Irreversible by replay -- this is the beginning of the migration
    chain, so "downgrading" it means destroying the schema entirely rather
    than reconstructing a prior state (there is none). Drops every table
    in the target database regardless of what created it, which is the
    only meaningful interpretation of "undo the baseline" -- except
    alembic_version itself, which Alembic owns: it writes to that table
    immediately after this function returns (to record the new current
    revision, or clear it entirely when downgrading past the first
    revision), and needs it to still exist to do so."""
    conn = op.get_bind()
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
    tables = [row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
    ))]
    for t in tables:
        if t != 'alembic_version':
            conn.execute(sa.text(f"DROP TABLE IF EXISTS `{t}`"))
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))
