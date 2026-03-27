
"""Initial database tables

Revision ID: 001
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Domains table
    op.create_table(
        'domains',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('domain', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('max_quota', sa.BigInteger(), nullable=False, default=10737418240),
        sa.Column('max_users', sa.Integer(), nullable=False, default=1000),
        sa.Column('dkim_enabled', sa.Boolean(), nullable=False, default=True),
        sa.Column('dkim_selector', sa.String(100), nullable=False, default='default'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('domain')
    )

    # Users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('username', sa.String(255), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('password', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', 'SUSPENDED', name='user_status'), nullable=False, default='ACTIVE'),
        sa.Column('quota', sa.BigInteger(), nullable=False, default=1073741824),
        sa.Column('used_quota', sa.BigInteger(), nullable=False, default=0),
        sa.Column('local_part', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('forward_enabled', sa.Boolean(), nullable=False, default=False),
        sa.Column('forward_destination', sa.String(255), nullable=True),
        sa.Column('vacation_enabled', sa.Boolean(), nullable=False, default=False),
        sa.Column('vacation_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.Index('idx_user_domain', 'local_part', 'domain_id'),
        sa.Index('idx_user_status', 'status')
    )

    # Aliases table
    op.create_table(
        'aliases',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(255), nullable=False),
        sa.Column('destination', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'])
    )

    # Email tracking table
    op.create_table(
        'email_tracking',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('email_id', sa.String(255), nullable=False, index=True),
        sa.Column('recipient', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('domain_id', sa.String(100), nullable=False, index=True),
        sa.Column('event_type', sa.Enum('DELIVERED', 'OPENED', 'CLICKED', 'BOUNCED', 'COMPLAINED', 'UNSUBSCRIBED', name='event_type'), nullable=False, index=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, default=func.now(), index=True),
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
        sa.Index('idx_email_event_time', 'email_id', 'event_type', 'timestamp'),
        sa.Index('idx_tenant_time', 'organization_id', 'timestamp'),
        sa.Index('idx_recipient_time', 'recipient', 'timestamp'),
        sa.Index('idx_event_time', 'event_type', 'timestamp')
    )

    # Tracking statistics table
    op.create_table(
        'tracking_statistics',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('email_id', sa.String(255), nullable=False, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('domain_id', sa.String(100), nullable=False, index=True),
        sa.Column('event_type', sa.Enum('DELIVERED', 'OPENED', 'CLICKED', 'BOUNCED', 'COMPLAINED', 'UNSUBSCRIBED', name='event_type'), nullable=False, index=True),
        sa.Column('total_count', sa.Integer(), nullable=False, default=0),
        sa.Column('unique_count', sa.Integer(), nullable=False, default=0),
        sa.Column('unique_ips', sa.Integer(), nullable=False, default=0),
        sa.Column('first_event', sa.DateTime(), nullable=True),
        sa.Column('last_event', sa.DateTime(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), nullable=False, default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_stats_email_event', 'email_id', 'event_type'),
        sa.Index('idx_stats_tenant_event', 'organization_id', 'event_type')
    )

    # Mail queue table
    op.create_table(
        'mail_queue',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('sender', sa.String(255), nullable=False, index=True),
        sa.Column('recipient', sa.String(255), nullable=False, index=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, default=5, index=True),
        sa.Column('status', sa.Enum('QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'BOUNCED', 'REJECTED', 'DEFERRED', name='mail_status'), nullable=False, default='QUEUED', index=True),
        sa.Column('attempts', sa.Integer(), nullable=False, default=0),
        sa.Column('max_attempts', sa.Integer(), nullable=False, default=3),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('worker_id', sa.String(100), nullable=True),
        sa.Column('processing_time', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_queue_processing', 'status', 'scheduled_at', 'priority')
    )

    # Mail logs table
    op.create_table(
        'mail_logs',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.Column('sender', sa.String(255), nullable=False, index=True),
        sa.Column('recipient', sa.String(255), nullable=False, index=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'BOUNCED', 'REJECTED', 'DEFERRED', name='mail_status'), nullable=False, index=True),
        sa.Column('message_id', sa.String(255), nullable=True, index=True),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('relay', sa.String(255), nullable=True),
        sa.Column('delays', sa.String(100), nullable=True),
        sa.Column('dsn', sa.String(10), nullable=True),
        sa.Column('bounce_reason', sa.Text(), nullable=True),
        sa.Column('spam_score', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_mail_logs_time_status', 'timestamp', 'status'),
        sa.Index('idx_mail_logs_sender_time', 'sender', 'timestamp')
    )

    # SSL certificates table
    op.create_table(
        'ssl_certificates',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('certificate_path', sa.String(500), nullable=False),
        sa.Column('private_key_path', sa.String(500), nullable=False),
        sa.Column('chain_path', sa.String(500), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'EXPIRED', 'REVOKED', 'PENDING', name='certificate_status'), nullable=False, default='ACTIVE', index=True),
        sa.Column('issuer', sa.String(255), nullable=True),
        sa.Column('valid_from', sa.DateTime(), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True, index=True),
        sa.Column('auto_renew', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('last_renewed', sa.DateTime(), nullable=True),
        sa.Column('fingerprint', sa.String(255), nullable=True),
        sa.Column('algorithm', sa.String(50), nullable=True),
        sa.Column('key_size', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.Index('idx_ssl_expiry', 'valid_until', 'status')
    )

    # DKIM keys table
    op.create_table(
        'dkim_keys',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('selector', sa.String(100), nullable=False, default='default'),
        sa.Column('private_key', sa.Text(), nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.Index('idx_dkim_domain_selector', 'domain_id', 'selector')
    )

    # API keys table
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('key_id', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('key_hash', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=True),
        sa.Column('organization_id', sa.String(100), nullable=True, index=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('rate_limit', sa.Integer(), nullable=True),
        sa.Column('last_used', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('ip_whitelist', sa.JSON(), nullable=True),
        sa.Column('usage_count', sa.BigInteger(), nullable=False, default=0),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_api_key_active', 'key_id', 'active')
    )

    # User sessions table
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.String(255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('last_activity', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'])
    )

    # Rate limit configurations table
    op.create_table(
        'rate_limit_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('type', sa.Enum('organization', 'domain', 'mailbox', name='rate_limit_type'), nullable=False, index=True),
        sa.Column('identifier', sa.String(255), nullable=False, index=True),
        sa.Column('direction', sa.Enum('inbound', 'outbound', name='rate_limit_direction'), nullable=False, index=True),
        sa.Column('second_limit', sa.Integer(), nullable=True),
        sa.Column('minute_limit', sa.Integer(), nullable=True),
        sa.Column('hourly_limit', sa.Integer(), nullable=True),
        sa.Column('daily_limit', sa.Integer(), nullable=True),
        sa.Column('monthly_limit', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True, index=True),
        sa.Column('priority', sa.Integer(), nullable=False, default=1),
        sa.Column('warning_threshold', sa.Integer(), nullable=False, default=80),
        sa.Column('critical_threshold', sa.Integer(), nullable=False, default=95),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('created_by', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_rate_limit_config', 'type', 'identifier', 'direction', unique=True),
        sa.Index('idx_rate_config_type_active', 'type', 'active'),
        sa.Index('idx_rate_config_priority', 'priority')
    )

    # Rate limit usage table
    op.create_table(
        'rate_limit_usage',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('type', sa.Enum('organization', 'domain', 'mailbox', name='rate_limit_usage_type'), nullable=False, index=True),
        sa.Column('identifier', sa.String(255), nullable=False, index=True),
        sa.Column('direction', sa.Enum('inbound', 'outbound', name='rate_limit_usage_direction'), nullable=False, index=True),
        sa.Column('second_key', sa.String(19), nullable=False, index=True),
        sa.Column('minute_key', sa.String(16), nullable=False, index=True),
        sa.Column('hour_key', sa.String(13), nullable=False, index=True),
        sa.Column('day_key', sa.String(10), nullable=False, index=True),
        sa.Column('month_key', sa.String(7), nullable=False, index=True),
        sa.Column('second_count', sa.Integer(), nullable=False, default=0),
        sa.Column('minute_count', sa.Integer(), nullable=False, default=0),
        sa.Column('hourly_count', sa.Integer(), nullable=False, default=0),
        sa.Column('daily_count', sa.Integer(), nullable=False, default=0),
        sa.Column('monthly_count', sa.Integer(), nullable=False, default=0),
        sa.Column('first_request', sa.DateTime(), nullable=True),
        sa.Column('last_request', sa.DateTime(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), nullable=False, default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_rate_usage', 'type', 'identifier', 'direction', 'hour_key', unique=True),
        sa.Index('idx_rate_usage_cleanup', 'day_key'),
        sa.Index('idx_rate_usage_lookup', 'type', 'identifier', 'direction', 'hour_key')
    )

    # Rate limit alerts table
    op.create_table(
        'rate_limit_alerts',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('type', sa.Enum('organization', 'domain', 'mailbox', name='rate_limit_alert_type'), nullable=False, index=True),
        sa.Column('identifier', sa.String(255), nullable=False, index=True),
        sa.Column('direction', sa.Enum('inbound', 'outbound', name='rate_limit_alert_direction'), nullable=False, index=True),
        sa.Column('alert_level', sa.Enum('warning', 'critical', 'exceeded', name='rate_limit_alert_level'), nullable=False, index=True),
        sa.Column('current_usage', sa.Integer(), nullable=False),
        sa.Column('limit_value', sa.Integer(), nullable=False),
        sa.Column('usage_percentage', sa.Float(), nullable=False),
        sa.Column('window_type', sa.Enum('second', 'minute', 'hourly', 'daily', 'monthly', name='rate_limit_window'), nullable=False),
        sa.Column('webhook_sent', sa.Boolean(), nullable=False, default=False, index=True),
        sa.Column('webhook_attempts', sa.Integer(), nullable=False, default=0),
        sa.Column('webhook_last_attempt', sa.DateTime(), nullable=True),
        sa.Column('webhook_success', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_rate_alerts_pending', 'webhook_sent', 'webhook_attempts'),
        sa.Index('idx_rate_alerts_identifier', 'type', 'identifier'),
        sa.Index('idx_rate_alerts_level', 'alert_level', 'created_at')
    )

    # Webhook URLs table
    op.create_table(
        'webhook_urls',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('organization_id', sa.String(100), nullable=True, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('url', sa.String(1000), nullable=False),
        sa.Column('event_types', sa.JSON(), nullable=False),
        sa.Column('service_types', sa.JSON(), nullable=False),
        sa.Column('encryption_key', sa.String(255), nullable=False),
        sa.Column('webhook_secret', sa.String(255), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('priority', sa.Integer(), nullable=False, default=1),
        sa.Column('timeout_seconds', sa.Integer(), nullable=False, default=30),
        sa.Column('retry_attempts', sa.Integer(), nullable=False, default=3),
        sa.Column('retry_delay_seconds', sa.Integer(), nullable=False, default=5),
        sa.Column('rate_limit_per_minute', sa.Integer(), nullable=True),
        sa.Column('tenant_filter', sa.JSON(), nullable=True),
        sa.Column('domain_filter', sa.JSON(), nullable=True),
        sa.Column('custom_headers', sa.JSON(), nullable=True),
        sa.Column('auth_type', sa.String(50), nullable=True),
        sa.Column('auth_credentials', sa.String(500), nullable=True),
        sa.Column('last_success', sa.DateTime(), nullable=True),
        sa.Column('last_failure', sa.DateTime(), nullable=True),
        sa.Column('success_count', sa.BigInteger(), nullable=False, default=0),
        sa.Column('failure_count', sa.BigInteger(), nullable=False, default=0),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('created_by', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_webhook_urls_active', 'active'),
        sa.Index('idx_webhook_urls_priority', 'priority'),
        sa.Index('idx_webhook_urls_event_types', 'event_types'),
        sa.Index('idx_webhook_urls_service_types', 'service_types'),
        sa.Index('idx_webhook_urls_last_success', 'last_success')
    )

    # Webhook delivery logs table (merged WebhookEvent functionality)
    op.create_table(
        'webhook_delivery_logs',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('webhook_url_id', sa.Integer(), nullable=True, index=True),
        sa.Column('event_type', sa.String(100), nullable=False, index=True),
        sa.Column('event_data', sa.JSON(), nullable=False),
        sa.Column('webhook_url', sa.String(1000), nullable=False, index=True),
        sa.Column('webhook_name', sa.String(255), nullable=True),
        sa.Column('delivery_status', sa.Enum('PENDING', 'DELIVERED', 'FAILED', 'RETRYING', 'ABANDONED', name='webhook_delivery_status'), nullable=False, default='PENDING', index=True),
        sa.Column('attempts', sa.Integer(), nullable=False, default=0),
        sa.Column('max_attempts', sa.Integer(), nullable=False, default=3),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('retry_delay_seconds', sa.Integer(), nullable=False, default=5),
        sa.Column('backoff_multiplier', sa.Float(), nullable=False, default=2.0),
        sa.Column('max_retry_delay', sa.Integer(), nullable=False, default=3600),
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
        sa.Column('payload_encrypted', sa.Boolean(), nullable=False, default=False),
        sa.Column('signature_verified', sa.Boolean(), nullable=True),
        sa.Column('organization_id', sa.String(100), nullable=True, index=True),
        sa.Column('domain_id', sa.String(100), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.Column('first_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('abandoned_at', sa.DateTime(), nullable=True),
        sa.Column('auto_cleanup_enabled', sa.Boolean(), nullable=False, default=True),
        sa.Column('cleanup_after_hours', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['webhook_url_id'], ['webhook_urls.id']),
        sa.Index('idx_webhook_delivery_status_retry', 'delivery_status', 'next_retry_at'),
        sa.Index('idx_webhook_delivery_cleanup_success', 'delivery_status', 'delivered_at', 'auto_cleanup_enabled'),
        sa.Index('idx_webhook_delivery_cleanup_failed', 'delivery_status', 'abandoned_at', 'auto_cleanup_enabled'),
        sa.Index('idx_webhook_delivery_attempts', 'attempts', 'max_attempts'),
        sa.Index('idx_webhook_delivery_org_time', 'organization_id', 'created_at'),
        sa.Index('idx_webhook_delivery_url_time', 'webhook_url', 'created_at'),
        sa.Index('idx_webhook_delivery_event_time', 'event_type', 'created_at')
    )

    # AI transactions table
    op.create_table(
        'ai_transactions',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('transaction_id', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('session_id', sa.String(255), nullable=True, index=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('user_id', sa.String(100), nullable=True, index=True),
        sa.Column('service_name', sa.String(100), nullable=False, index=True),
        sa.Column('operation_type', sa.String(100), nullable=False, index=True),
        sa.Column('model_provider', sa.String(100), nullable=False, index=True),
        sa.Column('model_name', sa.String(255), nullable=False, index=True),
        sa.Column('model_version', sa.String(100), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, default=0),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, default=0),
        sa.Column('total_tokens', sa.Integer(), nullable=False, default=0),
        sa.Column('cost_per_token', sa.DECIMAL(10, 8), nullable=True),
        sa.Column('total_cost', sa.DECIMAL(10, 6), nullable=True),
        sa.Column('currency', sa.String(10), nullable=False, default='USD'),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('batch_size', sa.Integer(), nullable=False, default=1),
        sa.Column('input_text_length', sa.Integer(), nullable=True),
        sa.Column('output_text_length', sa.Integer(), nullable=True),
        sa.Column('request_size_bytes', sa.Integer(), nullable=True),
        sa.Column('response_size_bytes', sa.Integer(), nullable=True),
        sa.Column('api_endpoint', sa.String(500), nullable=True),
        sa.Column('api_version', sa.String(50), nullable=True),
        sa.Column('request_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, default='success', index=True),
        sa.Column('error_code', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('context_type', sa.String(100), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('rate_limit_remaining', sa.Integer(), nullable=True),
        sa.Column('quota_consumed', sa.Boolean(), nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('billing_period', sa.String(20), nullable=False, default='monthly', index=True),
        sa.Column('usage_date', sa.DateTime(), nullable=False, index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_ai_trans_tenant_date', 'organization_id', 'usage_date'),
        sa.Index('idx_ai_trans_model_date', 'model_provider', 'model_name', 'usage_date'),
        sa.Index('idx_ai_trans_service_date', 'service_name', 'operation_type', 'usage_date'),
        sa.Index('idx_ai_trans_cost', 'total_cost', 'usage_date'),
        sa.Index('idx_ai_trans_tokens', 'total_tokens', 'usage_date')
    )

    # Storage quota configurations table
    op.create_table(
        'storage_quota_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('type', sa.Enum('organization', 'domain', 'mailbox', name='storage_quota_type'), nullable=False, index=True),
        sa.Column('identifier', sa.String(255), nullable=False, index=True),
        sa.Column('total_storage_limit', sa.BigInteger(), nullable=True),
        sa.Column('attachment_storage_limit', sa.BigInteger(), nullable=True),
        sa.Column('max_file_size', sa.BigInteger(), nullable=True),
        sa.Column('max_attachment_size', sa.BigInteger(), nullable=True),
        sa.Column('max_email_size', sa.BigInteger(), nullable=True),
        sa.Column('allowed_file_types', sa.JSON(), nullable=True),
        sa.Column('blocked_file_types', sa.JSON(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True, index=True),
        sa.Column('priority', sa.Integer(), nullable=False, default=1),
        sa.Column('enforce_limits', sa.Boolean(), nullable=False, default=True),
        sa.Column('warning_threshold', sa.Integer(), nullable=False, default=80),
        sa.Column('critical_threshold', sa.Integer(), nullable=False, default=95),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('created_by', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_storage_quota_config', 'type', 'identifier', unique=True),
        sa.Index('idx_storage_config_type_active', 'type', 'active'),
        sa.Index('idx_storage_config_priority', 'priority')
    )

    # Storage usage table
    op.create_table(
        'storage_usage',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('type', sa.Enum('organization', 'domain', 'mailbox', name='storage_usage_type'), nullable=False, index=True),
        sa.Column('identifier', sa.String(255), nullable=False, index=True),
        sa.Column('total_storage_used', sa.BigInteger(), nullable=False, default=0),
        sa.Column('attachment_storage_used', sa.BigInteger(), nullable=False, default=0),
        sa.Column('email_storage_used', sa.BigInteger(), nullable=False, default=0),
        sa.Column('total_files', sa.Integer(), nullable=False, default=0),
        sa.Column('total_attachments', sa.Integer(), nullable=False, default=0),
        sa.Column('total_emails', sa.Integer(), nullable=False, default=0),
        sa.Column('largest_file_size', sa.BigInteger(), nullable=True),
        sa.Column('largest_file_name', sa.String(500), nullable=True),
        sa.Column('largest_file_date', sa.DateTime(), nullable=True),
        sa.Column('daily_storage_delta', sa.BigInteger(), nullable=False, default=0),
        sa.Column('weekly_storage_delta', sa.BigInteger(), nullable=False, default=0),
        sa.Column('monthly_storage_delta', sa.BigInteger(), nullable=False, default=0),
        sa.Column('last_calculated', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('last_cleanup', sa.DateTime(), nullable=True),
        sa.Column('calculation_time_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_storage_usage', 'type', 'identifier', unique=True),
        sa.Index('idx_storage_usage_type', 'type'),
        sa.Index('idx_storage_usage_last_calculated', 'last_calculated'),
        sa.Index('idx_storage_usage_total', 'total_storage_used')
    )

    # Storage alerts table
    op.create_table(
        'storage_alerts',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('type', sa.Enum('organization', 'domain', 'mailbox', name='storage_alert_type'), nullable=False, index=True),
        sa.Column('identifier', sa.String(255), nullable=False, index=True),
        sa.Column('alert_level', sa.Enum('warning', 'critical', 'exceeded', name='storage_alert_level'), nullable=False, index=True),
        sa.Column('current_usage', sa.BigInteger(), nullable=False),
        sa.Column('quota_limit', sa.BigInteger(), nullable=False),
        sa.Column('usage_percentage', sa.Float(), nullable=False),
        sa.Column('storage_type', sa.Enum('total', 'attachment', 'email', name='storage_alert_storage_type'), nullable=False, default='total'),
        sa.Column('webhook_sent', sa.Boolean(), nullable=False, default=False, index=True),
        sa.Column('webhook_attempts', sa.Integer(), nullable=False, default=0),
        sa.Column('webhook_last_attempt', sa.DateTime(), nullable=True),
        sa.Column('webhook_success', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_storage_alerts_pending', 'webhook_sent', 'webhook_attempts'),
        sa.Index('idx_storage_alerts_identifier', 'type', 'identifier'),
        sa.Index('idx_storage_alerts_level', 'alert_level', 'created_at')
    )

    # Analytics data table
    op.create_table(
        'analytics_data',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('organization_id', sa.String(100), nullable=False, index=True),
        sa.Column('metric_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_value', sa.DECIMAL(15, 4), nullable=False),
        sa.Column('dimensions', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.Column('period', sa.String(20), nullable=False, index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_analytics_tenant_metric', 'organization_id', 'metric_name', 'timestamp')
    )

    # System configuration table
    op.create_table(
        'system_config',
        sa.Column('key', sa.String(255), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(100), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=func.now()),
        sa.PrimaryKeyConstraint('key')
    )

    # Health checks table
    op.create_table(
        'health_checks',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('service_name', sa.String(100), nullable=False, index=True),
        sa.Column('status', sa.String(50), nullable=False, index=True),
        sa.Column('response_time', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_health_service_time', 'service_name', 'timestamp')
    )

    # Service metrics table
    op.create_table(
        'service_metrics',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('service_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_value', sa.Float(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False, default=func.now(), index=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_metrics_service_metric', 'service_name', 'metric_name', 'timestamp')
    )

def downgrade() -> None:
    op.drop_table('service_metrics')
    op.drop_table('health_checks')
    op.drop_table('system_config')
    op.drop_table('analytics_data')
    op.drop_table('storage_alerts')
    op.drop_table('storage_usage')
    op.drop_table('storage_quota_configs')
    op.drop_table('ai_transactions')
    op.drop_table('webhook_delivery_logs')
    op.drop_table('webhook_urls')
    op.drop_table('rate_limit_alerts')
    op.drop_table('rate_limit_usage')
    op.drop_table('rate_limit_configs')
    op.drop_table('user_sessions')
    op.drop_table('api_keys')
    op.drop_table('dkim_keys')
    op.drop_table('ssl_certificates')
    op.drop_table('mail_logs')
    op.drop_table('mail_queue')
    op.drop_table('tracking_statistics')
    op.drop_table('email_tracking')
    op.drop_table('aliases')
    op.drop_table('users')
    op.drop_table('domains')
