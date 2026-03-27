
"""Add email suppression and reputation models

Revision ID: 002
Revises: 001
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None

def upgrade():
    # Create email_suppressions table
    op.create_table('email_suppressions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('suppression_type', sa.Enum('BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL', name='suppression_type'), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('bounce_type', sa.Enum('HARD', 'SOFT', 'BLOCK', name='bounce_type'), nullable=True),
        sa.Column('bounce_count', sa.Integer(), nullable=False, default=1),
        sa.Column('last_bounce_reason', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for email_suppressions
    op.create_index('unique_email_suppression', 'email_suppressions', ['email', 'organization_id', 'suppression_type'], unique=True)
    op.create_index('idx_suppression_expires', 'email_suppressions', ['expires_at', 'active'])
    op.create_index('idx_suppression_type_org', 'email_suppressions', ['suppression_type', 'organization_id'])
    op.create_index('idx_suppression_email', 'email_suppressions', ['email'])
    op.create_index('idx_suppression_org', 'email_suppressions', ['organization_id'])
    op.create_index('idx_suppression_type', 'email_suppressions', ['suppression_type'])
    op.create_index('idx_suppression_active', 'email_suppressions', ['active'])

    # Create domain_reputation table
    op.create_table('domain_reputation',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('overall_score', sa.Integer(), nullable=False, default=50),
        sa.Column('deliverability_score', sa.Integer(), nullable=False, default=50),
        sa.Column('engagement_score', sa.Integer(), nullable=False, default=50),
        sa.Column('emails_sent', sa.BigInteger(), nullable=False, default=0),
        sa.Column('emails_delivered', sa.BigInteger(), nullable=False, default=0),
        sa.Column('emails_bounced', sa.BigInteger(), nullable=False, default=0),
        sa.Column('emails_complained', sa.BigInteger(), nullable=False, default=0),
        sa.Column('emails_opened', sa.BigInteger(), nullable=False, default=0),
        sa.Column('emails_clicked', sa.BigInteger(), nullable=False, default=0),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('period_type', sa.Enum('HOURLY', 'DAILY', 'WEEKLY', 'MONTHLY', name='reputation_period'), nullable=False, default='DAILY'),
        sa.Column('isp_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=sa.func.now()),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id']),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for domain_reputation
    op.create_index('idx_domain_reputation_period', 'domain_reputation', ['domain_id', 'period_type', 'period_start'])
    op.create_index('idx_domain_reputation_domain', 'domain_reputation', ['domain_id'])

    # Create feedback_loops table
    op.create_table('feedback_loops',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('original_recipient', sa.String(255), nullable=False),
        sa.Column('complaint_recipient', sa.String(255), nullable=True),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('isp_name', sa.String(100), nullable=False),
        sa.Column('feedback_type', sa.String(50), nullable=False, default='abuse'),
        sa.Column('email_id', sa.String(255), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('sender', sa.String(255), nullable=True),
        sa.Column('raw_feedback', sa.Text(), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('processed', sa.Boolean(), nullable=False, default=False),
        sa.Column('suppression_added', sa.Boolean(), nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=sa.func.now()),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for feedback_loops
    op.create_index('idx_fbl_isp_date', 'feedback_loops', ['isp_name', 'created_at'])
    op.create_index('idx_fbl_processing', 'feedback_loops', ['processed', 'created_at'])
    op.create_index('idx_fbl_recipient', 'feedback_loops', ['original_recipient'])
    op.create_index('idx_fbl_org', 'feedback_loops', ['organization_id'])
    op.create_index('idx_fbl_isp', 'feedback_loops', ['isp_name'])
    op.create_index('idx_fbl_email_id', 'feedback_loops', ['email_id'])

def downgrade():
    # Drop feedback_loops table
    op.drop_table('feedback_loops')
    
    # Drop domain_reputation table
    op.drop_table('domain_reputation')
    
    # Drop email_suppressions table
    op.drop_table('email_suppressions')
    
    # Drop custom enums
    op.execute("DROP TYPE IF EXISTS suppression_type")
    op.execute("DROP TYPE IF EXISTS bounce_type") 
    op.execute("DROP TYPE IF EXISTS reputation_period")
