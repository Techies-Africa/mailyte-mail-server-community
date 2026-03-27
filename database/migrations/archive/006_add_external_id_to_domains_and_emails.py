
"""Add external_id to domains and email accounts

Revision ID: 006_add_external_id_to_domains_and_emails
Revises: 005_update_rag_for_organizations
Create Date: 2024-01-20 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '006_add_external_id_to_domains_and_emails'
down_revision = '005_update_rag_for_organizations'
branch_labels = None
depends_on = None

def upgrade():
    """Add external_id columns to domains and email_accounts tables"""
    
    # Add external_id to domains table
    try:
        op.add_column('domains', sa.Column('external_id', sa.String(255), nullable=True))
        op.create_index('idx_domain_external_id', 'domains', ['external_id'], unique=True)
    except Exception as e:
        print(f"Could not add external_id to domains: {e}")
    
    # Add external_id to email_accounts table
    try:
        op.add_column('email_accounts', sa.Column('external_id', sa.String(255), nullable=True))
        op.create_index('idx_email_external_id', 'email_accounts', ['external_id'], unique=True)
    except Exception as e:
        print(f"Could not add external_id to email_accounts: {e}")
    
    print("Migration 006: Added external_id to domains and email accounts")

def downgrade():
    """Remove external_id columns from domains and email_accounts tables"""
    
    # Drop indexes first
    try:
        op.drop_index('idx_domain_external_id', 'domains')
        op.drop_index('idx_email_external_id', 'email_accounts')
    except Exception:
        pass
    
    # Drop columns
    try:
        op.drop_column('domains', 'external_id')
        op.drop_column('email_accounts', 'external_id')
    except Exception:
        pass
    
    print("Migration 006: Removed external_id from domains and email accounts")
