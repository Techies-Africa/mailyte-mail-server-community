
"""Add dashboard and analytics tables

Revision ID: 003
Revises: 002
Create Date: 2024-01-15 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None

def upgrade():
    """Add dashboard-related tables"""
    
    # Email logs table for tracking sent emails
    op.create_table(
        'email_logs',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('message_id', sa.String(255), nullable=False, index=True),
        sa.Column('from_address', sa.String(255), nullable=False),
        sa.Column('to_address', sa.String(255), nullable=False),
        sa.Column('domain', sa.String(255), nullable=False, index=True),
        sa.Column('subject', sa.Text),
        sa.Column('delivery_status', sa.Enum('sent', 'delivered', 'bounced', 'failed'), nullable=False),
        sa.Column('bounce_reason', sa.Text),
        sa.Column('created_at', sa.DateTime, default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.current_timestamp(), onupdate=sa.func.current_timestamp()),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )
    
    # Dashboard configurations table
    op.create_table(
        'dashboard_configs',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer, nullable=True),
        sa.Column('user_id', sa.Integer, nullable=True),
        sa.Column('config_key', sa.String(100), nullable=False),
        sa.Column('config_value', sa.JSON),
        sa.Column('created_at', sa.DateTime, default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.current_timestamp(), onupdate=sa.func.current_timestamp()),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )
    
    # Dashboard widgets table
    op.create_table(
        'dashboard_widgets',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, nullable=False),
        sa.Column('widget_type', sa.String(50), nullable=False),
        sa.Column('widget_config', sa.JSON),
        sa.Column('position_x', sa.Integer, default=0),
        sa.Column('position_y', sa.Integer, default=0),
        sa.Column('width', sa.Integer, default=4),
        sa.Column('height', sa.Integer, default=3),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.current_timestamp(), onupdate=sa.func.current_timestamp()),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )
    
    # Analytics cache table for performance
    op.create_table(
        'analytics_cache',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('cache_key', sa.String(255), nullable=False, unique=True),
        sa.Column('cache_data', sa.JSON),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('created_at', sa.DateTime, default=sa.func.current_timestamp()),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )
    
    # Add indexes for performance
    op.create_index('idx_email_logs_domain_date', 'email_logs', ['domain', 'created_at'])
    op.create_index('idx_email_logs_status_date', 'email_logs', ['delivery_status', 'created_at'])
    op.create_index('idx_dashboard_configs_org', 'dashboard_configs', ['organization_id', 'config_key'])
    op.create_index('idx_dashboard_widgets_user', 'dashboard_widgets', ['user_id', 'is_active'])
    op.create_index('idx_analytics_cache_expires', 'analytics_cache', ['expires_at'])

def downgrade():
    """Remove dashboard tables"""
    op.drop_table('analytics_cache')
    op.drop_table('dashboard_widgets')
    op.drop_table('dashboard_configs')
    op.drop_table('email_logs')
