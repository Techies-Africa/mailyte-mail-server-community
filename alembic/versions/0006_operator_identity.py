"""Operator identity: platform_operators, operator_sessions, operator_audit (ADR-002 SS3/SS6, phase-06 tasks 6.2/6.8)

Revision ID: 0006_operator_identity
Revises: 0005_api_key_platform_scope
Create Date: 2026-07-30 00:00:06.000000

Replaces the static ADMIN_TOKEN_SECRET shared-password mechanism with real,
individually-revocable staff identities. Three tables:

- platform_operators -- one row per Mailyte staff member. `role` is one of
  support|operator|admin|owner (ADR-002 SS4); enforced in application code,
  not a DB enum, so a new tier doesn't require a migration.
- operator_sessions -- deliberately NOT named `user_sessions` (that table is
  mailbox/webmail sessions keyed on email_account_id, an unrelated concept --
  deep-audit.md SS4.1) or `web_sessions` (tenant dashboard logins,
  012_web_sessions.sql). Adds an `absolute_expiry` column beyond ADR-002's own
  sketch, mirroring web_sessions' proven idle+absolute dual-expiry pattern --
  the ADR sketch only had `expires_at`, but SS7 requires "4-hour idle /
  12-hour absolute", which needs both columns to express independently
  (idle resets on activity, absolute does not).
- operator_audit -- append-only. `operator_id`/`operator_email` are nullable,
  which ADR-002's own sketch has as NOT NULL -- loosened here because the
  mandatory boundary test (ADR-002 SS8, phase-06 verification step 5)
  requires logging a TENANT credential's attempt to reach a platform-only
  endpoint, and that caller has no operator identity at all. Added
  `caller_scope` (not in the ADR sketch) to record the caller's own resolved
  scope ('platform'/'organization'/NULL-for-unauthenticated) alongside
  operator_id, so a denied row is distinguishable as "a tenant tried this"
  vs "an operator without the required role tried this" without needing a
  join back to api_keys (whose row may since have been deleted/rotated).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0006_operator_identity'
down_revision: Union[str, None] = '0005_api_key_platform_scope'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_operators',
        sa.Column('id', sa.CHAR(26), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('mfa_required', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        sa.Column('is_active', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.CHAR(26), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_platform_operators_email'),
        sa.ForeignKeyConstraint(['created_by'], ['platform_operators.id'], name='fk_platform_operators_created_by', ondelete='SET NULL'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )

    op.create_table(
        'operator_sessions',
        sa.Column('id', sa.CHAR(26), nullable=False),
        sa.Column('operator_id', sa.CHAR(26), nullable=False),
        sa.Column('token_hash', sa.CHAR(64), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=False),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('mfa_satisfied', sa.SmallInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('absolute_expiry', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_operator_sessions_token_hash'),
        sa.ForeignKeyConstraint(['operator_id'], ['platform_operators.id'], name='fk_operator_sessions_operator', ondelete='CASCADE'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_operator_sessions_operator', 'operator_sessions', ['operator_id'])

    op.create_table(
        'operator_audit',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('operator_id', sa.CHAR(26), nullable=True),
        sa.Column('operator_email', sa.String(255), nullable=True),
        sa.Column('caller_scope', sa.String(20), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('target_type', sa.String(50), nullable=True),
        sa.Column('target_id', sa.String(255), nullable=True),
        sa.Column('organization_id', sa.CHAR(26), nullable=True),
        sa.Column('request_body', sa.JSON(), nullable=True),
        sa.Column('result', sa.String(20), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=False),
        sa.Column('correlation_id', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_opaudit_op', 'operator_audit', ['operator_id', 'created_at'])
    op.create_index('idx_opaudit_org', 'operator_audit', ['organization_id', 'created_at'])
    op.create_index('idx_opaudit_action', 'operator_audit', ['action', 'created_at'])


def downgrade() -> None:
    op.drop_table('operator_audit')
    op.drop_table('operator_sessions')
    op.drop_table('platform_operators')
