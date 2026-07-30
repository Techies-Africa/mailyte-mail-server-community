"""Bring an ad-hoc-created table under migration tracking

Revision ID: 0002_adhoc_table_tracking
Revises: 0001_baseline
Create Date: 2026-07-30 00:00:01.000000

Ported from mailyte-email-server's phase-08 build: `acme_account_stats` is
created by mailer/cert_manager/scripts/cert_manager.py,
setup_certificate_tables() with `CREATE TABLE IF NOT EXISTS`, bypassing the
migration system entirely -- and without an explicit COLLATE, so it
silently defaulted to utf8mb4_0900_ai_ci (conventions.md SS4 rule 2's exact
failure mode) instead of this schema's utf8mb4_unicode_ci everywhere else.
Corrected here. cert_manager.py's ad-hoc CREATE TABLE calls (for this and
for ssl_certificates, which 0001_baseline already covers) are removed in
this same change -- compose now makes cert_manager depend_on
`migrate: service_completed_successfully`, so the table is guaranteed to
exist before it starts.

EE also has `suppression_list` at this revision number (worker/delivery_optimizer's
own ad-hoc table) -- CE doesn't ship delivery_optimizer, so there is nothing
to port for it here.
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


def downgrade() -> None:
    op.drop_table('acme_account_stats')
