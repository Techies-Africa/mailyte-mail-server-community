
"""Add external_id to organizations table

Revision ID: 004_add_external_id_to_organizations
Revises: 003_restructure_to_organization_model
Create Date: 2024-01-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '004_add_external_id_to_organizations'
down_revision = '003_restructure_to_organization_model'
branch_labels = None
depends_on = None

def upgrade():
    """Add external_id column to organizations table"""
    # Add external_id column
    op.add_column('organizations', sa.Column('external_id', sa.String(255), nullable=True))
    
    # Add unique constraint and index
    op.create_unique_constraint('uq_organizations_external_id', 'organizations', ['external_id'])
    op.create_index('idx_org_external_id', 'organizations', ['external_id'])
    
    print("Migration 004: Added external_id to organizations table")

def downgrade():
    """Remove external_id column from organizations table"""
    # Drop index and constraint first
    op.drop_index('idx_org_external_id', table_name='organizations')
    op.drop_constraint('uq_organizations_external_id', 'organizations', type_='unique')
    
    # Drop column
    op.drop_column('organizations', 'external_id')
    
    print("Migration 004: Removed external_id from organizations table")
