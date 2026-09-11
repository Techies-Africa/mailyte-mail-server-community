"""Bring two ad-hoc-created tables under migration tracking

Revision ID: 0002_adhoc_table_tracking
Revises: 0001_baseline
Create Date: 2026-07-30 00:00:01.000000

Found while building the baseline (phase-08 task 8.2, "diff mysqldump
between a fresh install and production before stamping" -- the live
schema had 73 tables, the frozen SQL chain only produces 70). The
difference: two tables that application code creates for itself with
`CREATE TABLE IF NOT EXISTS` at startup, bypassing the migration system
entirely -- exactly the drift this phase exists to close off.

  - `acme_account_stats` -- mailer/cert_manager/scripts/cert_manager.py,
    setup_certificate_tables(). Created without an explicit COLLATE, so it
    silently defaulted to utf8mb4_0900_ai_ci (conventions.md SS4 rule 2's
    exact failure mode) instead of this schema's utf8mb4_unicode_ci
    everywhere else. Corrected here.
  - `suppression_list` -- worker/delivery_optimizer/app.py, _ensure_tables().
    Distinct from (and not a replacement for) the existing, richer
    `email_suppressions` table from 001_init_schema.sql -- this one is
    delivery_optimizer's own bounce-suppression bookkeeping.

Both services' ad-hoc CREATE TABLE calls are removed in this same change
(they're redundant now -- the migrate service guarantees these tables
exist before either service starts, via a new `depends_on: migrate`).
`ssl_certificates` and `bounce_events`, the other tables these two
services used to create defensively, were already covered by the SQL
chain (001/008) -- their CREATE TABLE IF NOT EXISTS calls were always a
no-op in practice; removed for the same reason.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002_adhoc_table_tracking'
down_revision: Union[str, None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'acme_account_stats',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('certs_issued', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('success_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('failure_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('last_used', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_email', 'acme_account_stats', ['email'])
    op.create_index('idx_last_used', 'acme_account_stats', ['last_used'])

    op.create_table(
        'suppression_list',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('reason', sa.Enum('hard_bounce', 'complaint', 'unsubscribe', 'manual', name='suppression_list_reason'), nullable=False),
        sa.Column('source', sa.String(255), nullable=True),
        sa.Column('bounce_count', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('uk_suppression', 'suppression_list', ['organization_id', 'email'], unique=True)
    op.create_index('idx_suppression_org', 'suppression_list', ['organization_id'])
    op.create_index('idx_suppression_reason', 'suppression_list', ['reason'])


def downgrade() -> None:
    op.drop_table('suppression_list')
    op.drop_table('acme_account_stats')
