
"""
Migration to restructure database with Organization and EmailAccount models
This migration merges rate limits and storage quotas into the new structure
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '003_restructure_to_organization_model'
down_revision = '002_add_suppression_and_reputation'
branch_labels = None
depends_on = None

def upgrade():
    """Upgrade to new Organization and EmailAccount structure"""
    
    # Create organizations table
    op.create_table(
        'organizations',
        sa.Column('id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('admin_email', sa.String(255), nullable=True),
        sa.Column('admin_name', sa.String(255), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('rate_limits', sa.JSON(), nullable=True),
        sa.Column('storage_quotas', sa.JSON(), nullable=True),
        sa.Column('webhook_urls', sa.JSON(), nullable=True),
        sa.Column('webhook_secret', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Add indexes for organizations
    op.create_index('idx_org_active', 'organizations', ['active'])
    op.create_index('idx_org_name', 'organizations', ['name'])
    
    # Create email_accounts table (replacement for users)
    op.create_table(
        'email_accounts',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('local_part', sa.String(255), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('password', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', 'SUSPENDED', name='accountstatus'), nullable=False, default='ACTIVE'),
        
        # Storage fields
        sa.Column('storage_quota', sa.BigInteger(), nullable=False, default=1073741824),
        sa.Column('storage_used', sa.BigInteger(), nullable=False, default=0),
        sa.Column('attachment_storage_used', sa.BigInteger(), nullable=False, default=0),
        sa.Column('email_storage_used', sa.BigInteger(), nullable=False, default=0),
        sa.Column('total_files', sa.Integer(), nullable=False, default=0),
        sa.Column('total_attachments', sa.Integer(), nullable=False, default=0),
        sa.Column('total_emails', sa.Integer(), nullable=False, default=0),
        
        # Rate limiting and configuration
        sa.Column('rate_usage_data', sa.JSON(), nullable=True),
        sa.Column('rate_limits', sa.JSON(), nullable=True),
        sa.Column('storage_quotas', sa.JSON(), nullable=True),
        
        # Mail settings
        sa.Column('forward_enabled', sa.Boolean(), nullable=False, default=False),
        sa.Column('forward_destination', sa.String(255), nullable=True),
        sa.Column('vacation_enabled', sa.Boolean(), nullable=False, default=False),
        sa.Column('vacation_message', sa.Text(), nullable=True),
        
        # Activity tracking
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('last_activity', sa.DateTime(), nullable=True),
        sa.Column('last_storage_calculation', sa.DateTime(), nullable=True),
        sa.Column('storage_calculation_time_ms', sa.Integer(), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'])
    )
    
    # Add indexes for email_accounts
    op.create_index('idx_email_unique', 'email_accounts', ['email'], unique=True)
    op.create_index('idx_email_domain_org', 'email_accounts', ['domain_id', 'organization_id'])
    op.create_index('idx_email_status', 'email_accounts', ['status'])
    op.create_index('idx_email_storage_used', 'email_accounts', ['storage_used'])
    op.create_index('idx_email_last_activity', 'email_accounts', ['last_activity'])
    
    # Create unified alerts table
    op.create_table(
        'alerts',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('alert_type', sa.Enum('RATE_LIMIT', 'STORAGE_QUOTA', name='limittype'), nullable=False),
        sa.Column('alert_level', sa.Enum('WARNING', 'CRITICAL', 'EXCEEDED', name='alertlevel'), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=True),
        sa.Column('email_account_id', sa.Integer(), nullable=True),
        sa.Column('current_usage', sa.BigInteger(), nullable=False),
        sa.Column('limit_value', sa.BigInteger(), nullable=False),
        sa.Column('usage_percentage', sa.Float(), nullable=False),
        sa.Column('context_data', sa.JSON(), nullable=True),
        sa.Column('webhook_sent', sa.Boolean(), nullable=False, default=False),
        sa.Column('webhook_attempts', sa.Integer(), nullable=False, default=0),
        sa.Column('webhook_last_attempt', sa.DateTime(), nullable=True),
        sa.Column('webhook_success', sa.Boolean(), nullable=True),
        sa.Column('resolved', sa.Boolean(), nullable=False, default=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'])
    )
    
    # Add indexes for alerts
    op.create_index('idx_alerts_pending_webhook', 'alerts', ['webhook_sent', 'webhook_attempts'])
    op.create_index('idx_alerts_org_type', 'alerts', ['organization_id', 'alert_type'])
    op.create_index('idx_alerts_level_created', 'alerts', ['alert_level', 'created_at'])
    op.create_index('idx_alerts_resolved', 'alerts', ['resolved', 'created_at'])
    
    # Create usage_history table for historical tracking
    op.create_table(
        'usage_history',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('usage_type', sa.Enum('RATE_LIMIT', 'STORAGE_QUOTA', name='limittype'), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=True),
        sa.Column('email_account_id', sa.Integer(), nullable=True),
        sa.Column('hour_key', sa.String(13), nullable=False),
        sa.Column('day_key', sa.String(10), nullable=False),
        sa.Column('month_key', sa.String(7), nullable=False),
        sa.Column('usage_data', sa.JSON(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'])
    )
    
    # Add indexes for usage_history
    op.create_index('idx_usage_history_org_type_day', 'usage_history', ['organization_id', 'usage_type', 'day_key'])
    op.create_index('idx_usage_history_cleanup', 'usage_history', ['day_key'])
    op.create_index('idx_usage_history_entity_time', 'usage_history', ['email_account_id', 'usage_type', 'hour_key'])
    
    # Add organization_id to existing tables
    op.add_column('domains', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('domains', sa.Column('rate_limits', sa.JSON(), nullable=True))
    op.add_column('domains', sa.Column('storage_quotas', sa.JSON(), nullable=True))
    
    # Add storage usage tracking fields to domains
    op.add_column('domains', sa.Column('total_storage_used', sa.BigInteger(), nullable=False, default=0))
    op.add_column('domains', sa.Column('total_attachment_storage', sa.BigInteger(), nullable=False, default=0))
    op.add_column('domains', sa.Column('total_email_storage', sa.BigInteger(), nullable=False, default=0))
    op.add_column('domains', sa.Column('total_email_accounts', sa.Integer(), nullable=False, default=0))
    op.add_column('domains', sa.Column('total_emails', sa.BigInteger(), nullable=False, default=0))
    op.add_column('domains', sa.Column('total_attachments', sa.BigInteger(), nullable=False, default=0))
    op.add_column('domains', sa.Column('rate_usage_data', sa.JSON(), nullable=True))
    op.add_column('domains', sa.Column('last_storage_calculation', sa.DateTime(), nullable=True))
    op.add_column('domains', sa.Column('storage_calculation_time_ms', sa.Integer(), nullable=True))
    
    # Add organization_id to other relevant tables
    op.add_column('webhook_urls', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('email_tracking', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('tracking_statistics', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('mail_queue', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('mail_logs', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('ai_transactions', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('email_suppressions', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('domain_reputation', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('feedback_loops', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('analytics_data', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('webhook_delivery_logs', sa.Column('organization_id', sa.String(100), nullable=True))
    op.add_column('api_keys', sa.Column('organization_id', sa.String(100), nullable=True))
    
    # Update user_sessions to reference email_accounts
    op.add_column('user_sessions', sa.Column('email_account_id', sa.Integer(), nullable=True))
    
    # Add foreign key constraints after data migration would happen
    # (In a real scenario, you'd need data migration scripts here)
    
    print("Migration 003: Created new Organization and EmailAccount structure")

def downgrade():
    """Downgrade from new structure"""
    
    # Remove added columns
    op.drop_column('domains', 'organization_id')
    op.drop_column('domains', 'rate_limits')
    op.drop_column('domains', 'storage_quotas')
    op.drop_column('domains', 'total_storage_used')
    op.drop_column('domains', 'total_attachment_storage')
    op.drop_column('domains', 'total_email_storage')
    op.drop_column('domains', 'total_email_accounts')
    op.drop_column('domains', 'total_emails')
    op.drop_column('domains', 'total_attachments')
    op.drop_column('domains', 'rate_usage_data')
    op.drop_column('domains', 'last_storage_calculation')
    op.drop_column('domains', 'storage_calculation_time_ms')
    
    op.drop_column('webhook_urls', 'organization_id')
    op.drop_column('email_tracking', 'organization_id')
    op.drop_column('tracking_statistics', 'organization_id')
    op.drop_column('mail_queue', 'organization_id')
    op.drop_column('mail_logs', 'organization_id')
    op.drop_column('ai_transactions', 'organization_id')
    op.drop_column('email_suppressions', 'organization_id')
    op.drop_column('domain_reputation', 'organization_id')
    op.drop_column('feedback_loops', 'organization_id')
    op.drop_column('analytics_data', 'organization_id')
    op.drop_column('webhook_delivery_logs', 'organization_id')
    op.drop_column('api_keys', 'organization_id')
    
    op.drop_column('user_sessions', 'email_account_id')
    
    # Drop new tables
    op.drop_table('usage_history')
    op.drop_table('alerts')
    op.drop_table('email_accounts')
    op.drop_table('organizations')
    
    print("Migration 003: Reverted to previous structure")
