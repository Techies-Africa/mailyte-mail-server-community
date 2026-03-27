"""Initial schema — all tables matching current ORM models

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-02-20 00:00:00.000000

This is the baseline migration for Mailyte Email Server.
It creates every table in the correct dependency order so that
foreign key constraints are satisfied on a fresh database.

Tables created (24):
  - system_config, health_checks, service_metrics
  - organizations
  - domains, aliases
  - email_accounts
  - user_sessions
  - mail_queue, mail_logs
  - email_tracking, tracking_statistics
  - api_keys
  - webhook_urls, webhook_delivery_logs
  - alerts, usage_history
  - ssl_certificates, dkim_keys
  - ai_transactions
  - email_suppressions, domain_reputation, feedback_loops
  - analytics_data
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""

    # ── System tables (no FK dependencies) ──────────────────────────

    op.create_table(
        'system_config',
        sa.Column('key', sa.String(255), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(100), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('key'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )

    op.create_table(
        'health_checks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('service_name', sa.String(100), nullable=False, index=True),
        sa.Column('status', sa.String(50), nullable=False, index=True),
        sa.Column('response_time', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_health_service_time', 'health_checks', ['service_name', 'timestamp'])

    op.create_table(
        'service_metrics',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('service_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_value', sa.Float(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_metrics_service_metric', 'service_metrics', ['service_name', 'metric_name', 'timestamp'])

    # ── Organizations ───────────────────────────────────────────────

    op.create_table(
        'organizations',
        sa.Column('id', sa.String(100), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=True, unique=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('admin_email', sa.String(255), nullable=True),
        sa.Column('admin_name', sa.String(255), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('rate_limits', sa.JSON(), nullable=True),
        sa.Column('storage_quotas', sa.JSON(), nullable=True),
        sa.Column('webhook_urls', sa.JSON(), nullable=True),
        sa.Column('webhook_secret', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_org_active', 'organizations', ['active'])
    op.create_index('idx_org_name', 'organizations', ['name'])
    op.create_index('idx_org_external_id', 'organizations', ['external_id'])
    op.create_index('idx_org_active_created', 'organizations', ['active', 'created_at'])
    op.create_index('idx_org_name_active', 'organizations', ['name', 'active'])

    # ── Domains ─────────────────────────────────────────────────────

    op.create_table(
        'domains',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('domain', sa.String(255), nullable=False, unique=True),
        sa.Column('external_id', sa.String(255), nullable=True, unique=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        # Mail server config
        sa.Column('max_quota', sa.BigInteger(), nullable=False, server_default=sa.text('10737418240')),
        sa.Column('max_users', sa.Integer(), nullable=False, server_default=sa.text('1000')),
        # DKIM
        sa.Column('dkim_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('dkim_selector', sa.String(100), nullable=False, server_default=sa.text("'default'")),
        # Rate limiting / storage
        sa.Column('rate_limits', sa.JSON(), nullable=True),
        sa.Column('storage_quotas', sa.JSON(), nullable=True),
        sa.Column('total_storage_used', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_attachment_storage', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_email_storage', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_email_accounts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_emails', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_attachments', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('rate_usage_data', sa.JSON(), nullable=True),
        sa.Column('last_storage_calculation', sa.DateTime(), nullable=True),
        sa.Column('storage_calculation_time_ms', sa.Integer(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_domains_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_domain_org_active', 'domains', ['organization_id', 'active'])
    op.create_index('idx_domain_name_org', 'domains', ['domain', 'organization_id'])
    op.create_index('idx_domain_active_created', 'domains', ['active', 'created_at'])
    op.create_index('idx_domain_org_updated', 'domains', ['organization_id', 'updated_at'])
    op.create_index('idx_domain_storage_used', 'domains', ['total_storage_used'])
    op.create_index('idx_domain_storage_calc', 'domains', ['last_storage_calculation'])
    op.create_index('idx_domain_external_id', 'domains', ['external_id'])

    # ── Email Accounts ──────────────────────────────────────────────

    op.create_table(
        'email_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('external_id', sa.String(255), nullable=True, unique=True),
        sa.Column('local_part', sa.String(255), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('password', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('status', sa.Enum('active', 'inactive', 'suspended', name='accountstatus'), nullable=False, server_default=sa.text("'active'")),
        # Storage
        sa.Column('storage_quota', sa.BigInteger(), nullable=False, server_default=sa.text('1073741824')),
        sa.Column('storage_used', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('attachment_storage_used', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('email_storage_used', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_files', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_attachments', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_emails', sa.Integer(), nullable=False, server_default=sa.text('0')),
        # Rate limiting
        sa.Column('rate_usage_data', sa.JSON(), nullable=True),
        sa.Column('rate_limits', sa.JSON(), nullable=True),
        sa.Column('storage_quotas', sa.JSON(), nullable=True),
        # Mail settings
        sa.Column('forward_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('forward_destination', sa.String(255), nullable=True),
        sa.Column('vacation_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('vacation_message', sa.Text(), nullable=True),
        # Activity
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('last_activity', sa.DateTime(), nullable=True),
        sa.Column('last_storage_calculation', sa.DateTime(), nullable=True),
        sa.Column('storage_calculation_time_ms', sa.Integer(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_email_accounts_domain'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_email_accounts_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_email_domain_org', 'email_accounts', ['domain_id', 'organization_id'])
    op.create_index('idx_email_status', 'email_accounts', ['status'])
    op.create_index('idx_email_storage_used', 'email_accounts', ['storage_used'])
    op.create_index('idx_email_last_activity', 'email_accounts', ['last_activity'])
    op.create_index('idx_email_org_status', 'email_accounts', ['organization_id', 'status'])
    op.create_index('idx_email_org_created', 'email_accounts', ['organization_id', 'created_at'])
    op.create_index('idx_email_local_domain', 'email_accounts', ['local_part', 'domain_id'])
    op.create_index('idx_email_status_activity', 'email_accounts', ['status', 'last_activity'])
    op.create_index('idx_email_org_activity', 'email_accounts', ['organization_id', 'last_activity'])
    op.create_index('idx_email_external_id', 'email_accounts', ['external_id'])

    # ── Aliases ─────────────────────────────────────────────────────

    op.create_table(
        'aliases',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('source', sa.String(255), nullable=False),
        sa.Column('destination', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_aliases_domain'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_aliases_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )

    # ── User Sessions ───────────────────────────────────────────────

    op.create_table(
        'user_sessions',
        sa.Column('id', sa.String(255), nullable=False),
        sa.Column('email_account_id', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('last_activity', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'], name='fk_sessions_email_account'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )

    # ── Mail Queue ──────────────────────────────────────────────────

    op.create_table(
        'mail_queue',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('sender', sa.String(255), nullable=False, index=True),
        sa.Column('recipient', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text('5'), index=True),
        sa.Column('status', sa.Enum('queued', 'sending', 'sent', 'delivered', 'bounced', 'rejected', 'deferred', name='mailstatus'), nullable=False, server_default=sa.text("'queued'"), index=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default=sa.text('3')),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('worker_id', sa.String(100), nullable=True),
        sa.Column('processing_time', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_mail_queue_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_queue_processing', 'mail_queue', ['status', 'scheduled_at', 'priority'])

    # ── Mail Logs ───────────────────────────────────────────────────

    op.create_table(
        'mail_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('sender', sa.String(255), nullable=False, index=True),
        sa.Column('recipient', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('queued', 'sending', 'sent', 'delivered', 'bounced', 'rejected', 'deferred', name='mailstatus'), nullable=False, index=True),
        sa.Column('message_id', sa.String(255), nullable=True, index=True),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('relay', sa.String(255), nullable=True),
        sa.Column('delays', sa.String(100), nullable=True),
        sa.Column('dsn', sa.String(10), nullable=True),
        sa.Column('bounce_reason', sa.Text(), nullable=True),
        sa.Column('spam_score', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_mail_logs_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_mail_logs_time_status', 'mail_logs', ['timestamp', 'status'])
    op.create_index('idx_mail_logs_sender_time', 'mail_logs', ['sender', 'timestamp'])

    # ── Email Tracking ──────────────────────────────────────────────

    op.create_table(
        'email_tracking',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email_id', sa.String(255), nullable=False, index=True),
        sa.Column('recipient', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.Enum('delivered', 'opened', 'clicked', 'bounced', 'complained', 'unsubscribed', name='eventtype'), nullable=False, index=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True, index=True),
        sa.Column('referer', sa.Text(), nullable=True),
        sa.Column('accept_language', sa.String(255), nullable=True),
        sa.Column('device_type', sa.String(100), nullable=True),
        sa.Column('browser', sa.String(100), nullable=True),
        sa.Column('operating_system', sa.String(100), nullable=True),
        sa.Column('country', sa.String(100), nullable=True, index=True),
        sa.Column('region', sa.String(100), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('additional_data', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_email_tracking_org'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_email_tracking_domain'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_email_event_time', 'email_tracking', ['email_id', 'event_type', 'timestamp'])
    op.create_index('idx_org_time', 'email_tracking', ['organization_id', 'timestamp'])
    op.create_index('idx_recipient_time', 'email_tracking', ['recipient', 'timestamp'])
    op.create_index('idx_event_time', 'email_tracking', ['event_type', 'timestamp'])

    # ── Tracking Statistics ─────────────────────────────────────────

    op.create_table(
        'tracking_statistics',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email_id', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.Enum('delivered', 'opened', 'clicked', 'bounced', 'complained', 'unsubscribed', name='eventtype'), nullable=False, index=True),
        sa.Column('total_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('unique_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('unique_ips', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('first_event', sa.DateTime(), nullable=True),
        sa.Column('last_event', sa.DateTime(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_tracking_stats_org'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_tracking_stats_domain'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_stats_email_event', 'tracking_statistics', ['email_id', 'event_type'])
    op.create_index('idx_stats_org_event', 'tracking_statistics', ['organization_id', 'event_type'])

    # ── API Keys ────────────────────────────────────────────────────

    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key_id', sa.String(100), nullable=False, unique=True),
        sa.Column('key_hash', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=True),
        sa.Column('organization_id', sa.String(100), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('rate_limit', sa.Integer(), nullable=True),
        sa.Column('last_used', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('ip_whitelist', sa.JSON(), nullable=True),
        sa.Column('usage_count', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_api_keys_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_api_key_active', 'api_keys', ['key_id', 'active'])

    # ── Webhook URLs ────────────────────────────────────────────────

    op.create_table(
        'webhook_urls',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('url', sa.String(1000), nullable=False),
        sa.Column('event_types', sa.JSON(), nullable=False),
        sa.Column('service_types', sa.JSON(), nullable=False),
        sa.Column('encryption_key', sa.String(255), nullable=False),
        sa.Column('webhook_secret', sa.String(255), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('timeout_seconds', sa.Integer(), nullable=False, server_default=sa.text('30')),
        sa.Column('retry_attempts', sa.Integer(), nullable=False, server_default=sa.text('3')),
        sa.Column('retry_delay_seconds', sa.Integer(), nullable=False, server_default=sa.text('5')),
        sa.Column('rate_limit_per_minute', sa.Integer(), nullable=True),
        sa.Column('tenant_filter', sa.JSON(), nullable=True),
        sa.Column('domain_filter', sa.JSON(), nullable=True),
        sa.Column('custom_headers', sa.JSON(), nullable=True),
        sa.Column('auth_type', sa.String(50), nullable=True),
        sa.Column('auth_credentials', sa.String(500), nullable=True),
        sa.Column('last_success', sa.DateTime(), nullable=True),
        sa.Column('last_failure', sa.DateTime(), nullable=True),
        sa.Column('success_count', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('failure_count', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('created_by', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_webhook_urls_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_webhook_urls_active', 'webhook_urls', ['active'])
    op.create_index('idx_webhook_urls_priority', 'webhook_urls', ['priority'])
    op.create_index('idx_webhook_urls_last_success', 'webhook_urls', ['last_success'])

    # ── Webhook Delivery Logs ───────────────────────────────────────

    op.create_table(
        'webhook_delivery_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('webhook_url_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(100), nullable=False, index=True),
        sa.Column('event_data', sa.JSON(), nullable=False),
        sa.Column('webhook_url', sa.String(1000), nullable=False, index=True),
        sa.Column('webhook_name', sa.String(255), nullable=True),
        sa.Column('delivery_status', sa.Enum('pending', 'delivered', 'failed', 'retrying', 'abandoned', name='webhookdeliverystatus'), nullable=False, server_default=sa.text("'pending'"), index=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default=sa.text('3')),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('retry_delay_seconds', sa.Integer(), nullable=False, server_default=sa.text('5')),
        sa.Column('backoff_multiplier', sa.Float(), nullable=False, server_default=sa.text('2.0')),
        sa.Column('max_retry_delay', sa.Integer(), nullable=False, server_default=sa.text('3600')),
        sa.Column('http_status_code', sa.Integer(), nullable=True, index=True),
        sa.Column('response_headers', sa.JSON(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('response_size_bytes', sa.Integer(), nullable=True),
        sa.Column('request_duration_ms', sa.Integer(), nullable=True),
        sa.Column('dns_resolution_ms', sa.Integer(), nullable=True),
        sa.Column('connection_time_ms', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_code', sa.String(100), nullable=True),
        sa.Column('last_error_at', sa.DateTime(), nullable=True),
        sa.Column('payload_size_bytes', sa.Integer(), nullable=True),
        sa.Column('payload_encrypted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('signature_verified', sa.Boolean(), nullable=True),
        sa.Column('organization_id', sa.String(100), nullable=True),
        sa.Column('domain_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('first_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('abandoned_at', sa.DateTime(), nullable=True),
        sa.Column('auto_cleanup_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('cleanup_after_hours', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['webhook_url_id'], ['webhook_urls.id'], name='fk_webhook_delivery_url'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_webhook_delivery_org'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_webhook_delivery_domain'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_webhook_delivery_status_retry', 'webhook_delivery_logs', ['delivery_status', 'next_retry_at'])
    op.create_index('idx_webhook_delivery_cleanup_success', 'webhook_delivery_logs', ['delivery_status', 'delivered_at', 'auto_cleanup_enabled'])
    op.create_index('idx_webhook_delivery_cleanup_failed', 'webhook_delivery_logs', ['delivery_status', 'abandoned_at', 'auto_cleanup_enabled'])
    op.create_index('idx_webhook_delivery_attempts', 'webhook_delivery_logs', ['attempts', 'max_attempts'])
    op.create_index('idx_webhook_delivery_org_time', 'webhook_delivery_logs', ['organization_id', 'created_at'])
    op.create_index('idx_webhook_delivery_url_time', 'webhook_delivery_logs', ['webhook_url', 'created_at'])
    op.create_index('idx_webhook_delivery_event_time', 'webhook_delivery_logs', ['event_type', 'created_at'])

    # ── Alerts ──────────────────────────────────────────────────────

    op.create_table(
        'alerts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('alert_type', sa.Enum('rate_limit', 'storage_quota', name='limittype'), nullable=False, index=True),
        sa.Column('alert_level', sa.Enum('warning', 'critical', 'exceeded', name='alertlevel'), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=True),
        sa.Column('email_account_id', sa.Integer(), nullable=True),
        sa.Column('current_usage', sa.BigInteger(), nullable=False),
        sa.Column('limit_value', sa.BigInteger(), nullable=False),
        sa.Column('usage_percentage', sa.Float(), nullable=False),
        sa.Column('context_data', sa.JSON(), nullable=True),
        sa.Column('webhook_sent', sa.Boolean(), nullable=False, server_default=sa.text('0'), index=True),
        sa.Column('webhook_attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('webhook_last_attempt', sa.DateTime(), nullable=True),
        sa.Column('webhook_success', sa.Boolean(), nullable=True),
        sa.Column('resolved', sa.Boolean(), nullable=False, server_default=sa.text('0'), index=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_alerts_org'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_alerts_domain'),
        sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'], name='fk_alerts_email_account'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_alerts_pending_webhook', 'alerts', ['webhook_sent', 'webhook_attempts'])
    op.create_index('idx_alerts_org_type', 'alerts', ['organization_id', 'alert_type'])
    op.create_index('idx_alerts_level_created', 'alerts', ['alert_level', 'created_at'])
    op.create_index('idx_alerts_resolved', 'alerts', ['resolved', 'created_at'])

    # ── Usage History ───────────────────────────────────────────────

    op.create_table(
        'usage_history',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('usage_type', sa.Enum('rate_limit', 'storage_quota', name='limittype'), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=True),
        sa.Column('email_account_id', sa.Integer(), nullable=True),
        sa.Column('hour_key', sa.String(13), nullable=False, index=True),
        sa.Column('day_key', sa.String(10), nullable=False, index=True),
        sa.Column('month_key', sa.String(7), nullable=False, index=True),
        sa.Column('usage_data', sa.JSON(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_usage_history_org'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_usage_history_domain'),
        sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'], name='fk_usage_history_email_account'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_usage_history_org_type_day', 'usage_history', ['organization_id', 'usage_type', 'day_key'])
    op.create_index('idx_usage_history_cleanup', 'usage_history', ['day_key'])
    op.create_index('idx_usage_history_entity_time', 'usage_history', ['email_account_id', 'usage_type', 'hour_key'])

    # ── SSL Certificates ────────────────────────────────────────────

    op.create_table(
        'ssl_certificates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('certificate_path', sa.String(500), nullable=False),
        sa.Column('private_key_path', sa.String(500), nullable=False),
        sa.Column('chain_path', sa.String(500), nullable=True),
        sa.Column('status', sa.Enum('active', 'expired', 'revoked', 'pending', name='certificatestatus'), nullable=False, server_default=sa.text("'active'"), index=True),
        sa.Column('issuer', sa.String(255), nullable=True),
        sa.Column('valid_from', sa.DateTime(), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True, index=True),
        sa.Column('auto_renew', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('last_renewed', sa.DateTime(), nullable=True),
        sa.Column('fingerprint', sa.String(255), nullable=True),
        sa.Column('algorithm', sa.String(50), nullable=True),
        sa.Column('key_size', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_ssl_certs_domain'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_ssl_expiry', 'ssl_certificates', ['valid_until', 'status'])

    # ── DKIM Keys ───────────────────────────────────────────────────

    op.create_table(
        'dkim_keys',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('selector', sa.String(100), nullable=False, server_default=sa.text("'default'")),
        sa.Column('private_key', sa.Text(), nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_dkim_keys_domain'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_dkim_domain_selector', 'dkim_keys', ['domain_id', 'selector'])

    # ── AI Transactions ─────────────────────────────────────────────

    op.create_table(
        'ai_transactions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('transaction_id', sa.String(255), nullable=False, unique=True),
        sa.Column('session_id', sa.String(255), nullable=True, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('user_id', sa.String(100), nullable=True, index=True),
        sa.Column('service_name', sa.String(100), nullable=False, index=True),
        sa.Column('operation_type', sa.String(100), nullable=False, index=True),
        sa.Column('model_provider', sa.String(100), nullable=False, index=True),
        sa.Column('model_name', sa.String(255), nullable=False, index=True),
        sa.Column('model_version', sa.String(100), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('cost_per_token', sa.DECIMAL(10, 8), nullable=True),
        sa.Column('total_cost', sa.DECIMAL(10, 6), nullable=True),
        sa.Column('currency', sa.String(10), nullable=False, server_default=sa.text("'USD'")),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('batch_size', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('input_text_length', sa.Integer(), nullable=True),
        sa.Column('output_text_length', sa.Integer(), nullable=True),
        sa.Column('request_size_bytes', sa.Integer(), nullable=True),
        sa.Column('response_size_bytes', sa.Integer(), nullable=True),
        sa.Column('api_endpoint', sa.String(500), nullable=True),
        sa.Column('api_version', sa.String(50), nullable=True),
        sa.Column('request_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default=sa.text("'success'"), index=True),
        sa.Column('error_code', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('context_type', sa.String(100), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('rate_limit_remaining', sa.Integer(), nullable=True),
        sa.Column('quota_consumed', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('billing_period', sa.String(20), nullable=False, server_default=sa.text("'monthly'"), index=True),
        sa.Column('usage_date', sa.DateTime(), nullable=False, index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_ai_transactions_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_ai_trans_org_date', 'ai_transactions', ['organization_id', 'usage_date'])
    op.create_index('idx_ai_trans_model_date', 'ai_transactions', ['model_provider', 'model_name', 'usage_date'])
    op.create_index('idx_ai_trans_service_date', 'ai_transactions', ['service_name', 'operation_type', 'usage_date'])
    op.create_index('idx_ai_trans_cost', 'ai_transactions', ['total_cost', 'usage_date'])
    op.create_index('idx_ai_trans_tokens', 'ai_transactions', ['total_tokens', 'usage_date'])

    # ── Email Suppressions ──────────────────────────────────────────

    op.create_table(
        'email_suppressions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('suppression_type', sa.Enum('BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL', name='suppression_type'), nullable=False, index=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('bounce_type', sa.Enum('HARD', 'SOFT', 'BLOCK', name='bounce_type'), nullable=True),
        sa.Column('bounce_count', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('last_bounce_reason', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1'), index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_email_suppressions_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('unique_email_suppression', 'email_suppressions', ['email', 'organization_id', 'suppression_type'], unique=True)
    op.create_index('idx_suppression_expires', 'email_suppressions', ['expires_at', 'active'])
    op.create_index('idx_suppression_type_org', 'email_suppressions', ['suppression_type', 'organization_id'])

    # ── Domain Reputation ───────────────────────────────────────────

    op.create_table(
        'domain_reputation',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('overall_score', sa.Integer(), nullable=False, server_default=sa.text('50')),
        sa.Column('deliverability_score', sa.Integer(), nullable=False, server_default=sa.text('50')),
        sa.Column('engagement_score', sa.Integer(), nullable=False, server_default=sa.text('50')),
        sa.Column('emails_sent', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('emails_delivered', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('emails_bounced', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('emails_complained', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('emails_opened', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('emails_clicked', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('period_start', sa.DateTime(), nullable=False, index=True),
        sa.Column('period_end', sa.DateTime(), nullable=False, index=True),
        sa.Column('period_type', sa.Enum('HOURLY', 'DAILY', 'WEEKLY', 'MONTHLY', name='reputation_period'), nullable=False, server_default=sa.text("'DAILY'")),
        sa.Column('isp_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name='fk_domain_reputation_domain'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_domain_reputation_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_domain_reputation_period', 'domain_reputation', ['domain_id', 'period_type', 'period_start'])
    op.create_index('idx_domain_reputation_org', 'domain_reputation', ['organization_id', 'period_start'])

    # ── Feedback Loops ──────────────────────────────────────────────

    op.create_table(
        'feedback_loops',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('original_recipient', sa.String(255), nullable=False, index=True),
        sa.Column('complaint_recipient', sa.String(255), nullable=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('isp_name', sa.String(100), nullable=False, index=True),
        sa.Column('feedback_type', sa.String(50), nullable=False, server_default=sa.text("'abuse'")),
        sa.Column('email_id', sa.String(255), nullable=True, index=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('sender', sa.String(255), nullable=True, index=True),
        sa.Column('raw_feedback', sa.Text(), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('processed', sa.Boolean(), nullable=False, server_default=sa.text('0'), index=True),
        sa.Column('suppression_added', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_feedback_loops_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_fbl_isp_date', 'feedback_loops', ['isp_name', 'created_at'])
    op.create_index('idx_fbl_processing', 'feedback_loops', ['processed', 'created_at'])
    op.create_index('idx_fbl_org', 'feedback_loops', ['organization_id', 'created_at'])

    # ── Analytics Data ──────────────────────────────────────────────

    op.create_table(
        'analytics_data',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('metric_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_value', sa.DECIMAL(15, 4), nullable=False),
        sa.Column('dimensions', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=func.now(), index=True),
        sa.Column('period', sa.String(20), nullable=False, index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_analytics_data_org'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index('idx_analytics_org_metric', 'analytics_data', ['organization_id', 'metric_name', 'timestamp'])


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table('analytics_data')
    op.drop_table('feedback_loops')
    op.drop_table('domain_reputation')
    op.drop_table('email_suppressions')
    op.drop_table('ai_transactions')
    op.drop_table('dkim_keys')
    op.drop_table('ssl_certificates')
    op.drop_table('usage_history')
    op.drop_table('alerts')
    op.drop_table('webhook_delivery_logs')
    op.drop_table('webhook_urls')
    op.drop_table('api_keys')
    op.drop_table('tracking_statistics')
    op.drop_table('email_tracking')
    op.drop_table('mail_logs')
    op.drop_table('mail_queue')
    op.drop_table('user_sessions')
    op.drop_table('aliases')
    op.drop_table('email_accounts')
    op.drop_table('domains')
    op.drop_table('organizations')
    op.drop_table('service_metrics')
    op.drop_table('health_checks')
    op.drop_table('system_config')
