
"""Update RAG models for organization integration

Revision ID: 005_update_rag_for_organizations
Revises: 004_add_external_id_to_organizations
Create Date: 2024-01-20 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '005_update_rag_for_organizations'
down_revision = '004_add_external_id_to_organizations'
branch_labels = None
depends_on = None

def upgrade():
    """Add organization support to RAG models and optimize indexes"""
    
    # Add organization_id to rag_configurations if not exists
    try:
        op.add_column('rag_configurations', sa.Column('organization_id', sa.String(100), nullable=True))
        op.create_index('idx_rag_config_org', 'rag_configurations', ['organization_id'])
    except Exception:
        pass  # Column might already exist
    
    # Add domain_id to rag_queries if not exists
    try:
        op.add_column('rag_queries', sa.Column('domain_id', sa.Integer, nullable=True))
        op.create_index('idx_rag_queries_domain_created', 'rag_queries', ['domain_id', 'created_at'])
    except Exception:
        pass
    
    # Add external_id fields to RAG models
    try:
        op.add_column('rag_documents', sa.Column('domain_external_id', sa.String(255), nullable=True))
        op.add_column('rag_documents', sa.Column('user_external_id', sa.String(255), nullable=True))
        op.add_column('rag_chunks', sa.Column('domain_external_id', sa.String(255), nullable=True))
        op.add_column('rag_chunks', sa.Column('user_external_id', sa.String(255), nullable=True))
        op.add_column('rag_queries', sa.Column('domain_external_id', sa.String(255), nullable=True))
        op.add_column('rag_queries', sa.Column('user_external_id', sa.String(255), nullable=True))
        
        # Create indexes for external_id fields
        op.create_index('idx_rag_docs_domain_ext_id', 'rag_documents', ['domain_external_id'])
        op.create_index('idx_rag_docs_user_ext_id', 'rag_documents', ['user_external_id'])
        op.create_index('idx_rag_chunks_domain_ext_id', 'rag_chunks', ['domain_external_id'])
        op.create_index('idx_rag_chunks_user_ext_id', 'rag_chunks', ['user_external_id'])
        op.create_index('idx_rag_queries_domain_ext_id', 'rag_queries', ['domain_external_id'])
        op.create_index('idx_rag_queries_user_ext_id', 'rag_queries', ['user_external_id'])
    except Exception as e:
        print(f"Some RAG external_id fields may already exist: {e}")
    
    # Add performance indexes for core models
    try:
        # Organization indexes
        op.create_index('idx_org_active_created', 'organizations', ['active', 'created_at'])
        op.create_index('idx_org_name_active', 'organizations', ['name', 'active'])
        
        # Domain indexes  
        op.create_index('idx_domain_org_active', 'domains', ['organization_id', 'active'])
        op.create_index('idx_domain_name_org', 'domains', ['domain', 'organization_id'])
        op.create_index('idx_domain_active_created', 'domains', ['active', 'created_at'])
        op.create_index('idx_domain_org_updated', 'domains', ['organization_id', 'updated_at'])
        op.create_index('idx_domain_storage_used', 'domains', ['total_storage_used'])
        op.create_index('idx_domain_storage_calc', 'domains', ['last_storage_calculation'])
        
        # Email account indexes
        op.create_index('idx_email_org_status', 'email_accounts', ['organization_id', 'status'])
        op.create_index('idx_email_org_created', 'email_accounts', ['organization_id', 'created_at'])
        op.create_index('idx_email_local_domain', 'email_accounts', ['local_part', 'domain_id'])
        op.create_index('idx_email_status_activity', 'email_accounts', ['status', 'last_activity'])
        op.create_index('idx_email_org_activity', 'email_accounts', ['organization_id', 'last_activity'])
        
    except Exception as e:
        print(f"Some indexes may already exist: {e}")
    
    # Add RAG-specific indexes
    try:
        # RAG queries indexes
        op.create_index('idx_rag_queries_org_created', 'rag_queries', ['organization_id', 'created_at'])
        op.create_index('idx_rag_queries_org_type', 'rag_queries', ['organization_id', 'query_type'])
        op.create_index('idx_rag_queries_user_org', 'rag_queries', ['user_id', 'organization_id'])
        
        # RAG collections indexes
        op.create_index('idx_rag_collections_name_tenant', 'rag_collections', ['collection_name', 'tenant_id'])
        op.create_index('idx_rag_collections_type_active', 'rag_collections', ['document_type', 'active'])
        op.create_index('idx_rag_collections_created_active', 'rag_collections', ['created_at', 'active'])
        op.create_index('idx_rag_collections_updated_tenant', 'rag_collections', ['updated_at', 'tenant_id'])
        
    except Exception as e:
        print(f"Some RAG indexes may already exist: {e}")
    
    print("Migration 005: Updated RAG models for organization integration")

def downgrade():
    """Remove organization integration from RAG models"""
    
    # Drop RAG-specific indexes
    indexes_to_drop = [
        'idx_rag_queries_org_created',
        'idx_rag_queries_org_type', 
        'idx_rag_queries_user_org',
        'idx_rag_queries_domain_created',
        'idx_rag_collections_name_tenant',
        'idx_rag_collections_type_active',
        'idx_rag_collections_created_active',
        'idx_rag_collections_updated_tenant',
        'idx_rag_config_org',
        # Core model indexes
        'idx_org_active_created',
        'idx_org_name_active',
        'idx_domain_org_active',
        'idx_domain_name_org',
        'idx_domain_active_created',
        'idx_domain_org_updated',
        'idx_domain_storage_used',
        'idx_domain_storage_calc',
        'idx_email_org_status',
        'idx_email_org_created',
        'idx_email_local_domain',
        'idx_email_status_activity',
        'idx_email_org_activity',
    ]
    
    for index_name in indexes_to_drop:
        try:
            op.drop_index(index_name)
        except Exception:
            pass
    
    # Drop columns
    try:
        op.drop_column('rag_configurations', 'organization_id')
        op.drop_column('rag_queries', 'domain_id')
    except Exception:
        pass
    
    print("Migration 005: Removed RAG organization integration")
