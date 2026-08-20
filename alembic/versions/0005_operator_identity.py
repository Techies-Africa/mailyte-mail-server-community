"""Operator identity: platform_operators, operator_sessions, operator_audit (ADR-002 §3/§6)

Revision ID: 0005_operator_identity
Revises: 0004_api_key_platform_scope
Create Date: 2026-08-20 00:00:05.000000

Ported from mailyte-email-server's phase-06 build (its own revision id was
0006_operator_identity). ADR-004 makes the Mailyte Console CE's admin panel,
and a console pointed at an instance whose only privileged credential is a
static ADMIN_TOKEN_SECRET is "a padlock on a tent" -- these three tables are
what replace it with real, individually-revocable identities.

- platform_operators -- one row per operator. On a CE box that is the
  self-hoster themselves: ADR-004's insight is that a self-hoster *is* the
  platform operator of their own instance. `role` is one of
  support|operator|admin|owner (ADR-002 §4), enforced in application code
  rather than a DB enum so a new tier doesn't require a migration.
- operator_sessions -- deliberately NOT named `user_sessions`. That table
  already exists in 0001_baseline and belongs to mailbox/webmail users
  (email_account_id), an unrelated concept; ADR-002 §3 calls this out
  explicitly. Carries both `expires_at` (idle, slides forward on activity)
  and `absolute_expiry` (never slides) because ADR-002 §7 requires "4-hour
  idle / 12-hour absolute", which needs two columns to express.
- operator_audit -- append-only, written only by app.py's
  operator_audit_middleware. operator_id/operator_email are nullable, which
  ADR-002's own sketch has as NOT NULL: the middleware must be able to log a
  *tenant* credential's denied attempt at a platform-only endpoint, and that
  caller has no operator identity at all. `caller_scope` records the
  caller's own resolved scope so a denied row is distinguishable as "a tenant
  tried this" from "an operator without the required role tried this",
  without a join back to an api_keys row that may since have been rotated.

Column types follow the same rule 0003_smtp_credentials learned the hard way:
this schema pins utf8mb4_unicode_ci explicitly rather than inheriting the
database default, or the CHAR(26) foreign keys fail with error 3780
("Referencing column and referenced column are incompatible"). CE's
`organizations.id` and `api_keys.organization_id` are `char(26)` -- verified
against 0001_baseline's frozen mysqldump, not assumed from conventions §4
rule 6's pre-ULID `VARCHAR(100)`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005_operator_identity'
down_revision: Union[str, None] = '0004_api_key_platform_scope'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLLATION = 'utf8mb4_unicode_ci'


def upgrade() -> None:
    op.create_table(
        'platform_operators',
        sa.Column('id', sa.CHAR(26, collation=_COLLATION), nullable=False),
        sa.Column('email', sa.String(255, collation=_COLLATION), nullable=False),
        sa.Column('full_name', sa.String(255, collation=_COLLATION), nullable=False),
        sa.Column('password_hash', sa.String(255, collation=_COLLATION), nullable=False),
        sa.Column('role', sa.String(50, collation=_COLLATION), nullable=False),
        sa.Column('mfa_required', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        sa.Column('is_active', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.CHAR(26, collation=_COLLATION), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_platform_operators_email'),
        # SET NULL rather than CASCADE: deleting the owner who minted an
        # operator must never delete the operator they minted.
        sa.ForeignKeyConstraint(
            ['created_by'],
            ['platform_operators.id'],
            name='fk_platform_operators_created_by',
            ondelete='SET NULL',
        ),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate=_COLLATION,
    )

    op.create_table(
        'operator_sessions',
        sa.Column('id', sa.CHAR(26, collation=_COLLATION), nullable=False),
        sa.Column('operator_id', sa.CHAR(26, collation=_COLLATION), nullable=False),
        sa.Column('token_hash', sa.CHAR(64, collation=_COLLATION), nullable=False),
        sa.Column('ip_address', sa.String(45, collation=_COLLATION), nullable=False),
        sa.Column('user_agent', sa.String(500, collation=_COLLATION), nullable=True),
        sa.Column('mfa_satisfied', sa.SmallInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('absolute_expiry', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_operator_sessions_token_hash'),
        sa.ForeignKeyConstraint(
            ['operator_id'],
            ['platform_operators.id'],
            name='fk_operator_sessions_operator',
            ondelete='CASCADE',
        ),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate=_COLLATION,
    )
    op.create_index('idx_operator_sessions_operator', 'operator_sessions', ['operator_id'])

    op.create_table(
        'operator_audit',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('operator_id', sa.CHAR(26, collation=_COLLATION), nullable=True),
        sa.Column('operator_email', sa.String(255, collation=_COLLATION), nullable=True),
        sa.Column('caller_scope', sa.String(20, collation=_COLLATION), nullable=True),
        sa.Column('action', sa.String(100, collation=_COLLATION), nullable=False),
        sa.Column('target_type', sa.String(50, collation=_COLLATION), nullable=True),
        sa.Column('target_id', sa.String(255, collation=_COLLATION), nullable=True),
        sa.Column('organization_id', sa.CHAR(26, collation=_COLLATION), nullable=True),
        sa.Column('request_body', sa.JSON(), nullable=True),
        sa.Column('result', sa.String(20, collation=_COLLATION), nullable=False),
        sa.Column('ip_address', sa.String(45, collation=_COLLATION), nullable=False),
        sa.Column('correlation_id', sa.String(100, collation=_COLLATION), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.PrimaryKeyConstraint('id'),
        # No FK to platform_operators on purpose: the audit trail must
        # outlive the identity it records (ADR-002 §6 "append-only"), and a
        # FK would either block the delete or cascade the evidence away.
        # operator_email is denormalised for the same reason.
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate=_COLLATION,
    )
    op.create_index('idx_opaudit_op', 'operator_audit', ['operator_id', 'created_at'])
    op.create_index('idx_opaudit_org', 'operator_audit', ['organization_id', 'created_at'])
    op.create_index('idx_opaudit_action', 'operator_audit', ['action', 'created_at'])


def downgrade() -> None:
    # Drop order is the reverse of create: operator_sessions carries the FK
    # onto platform_operators, so the parent cannot go first.
    op.drop_index('idx_opaudit_action', table_name='operator_audit')
    op.drop_index('idx_opaudit_org', table_name='operator_audit')
    op.drop_index('idx_opaudit_op', table_name='operator_audit')
    op.drop_table('operator_audit')
    op.drop_index('idx_operator_sessions_operator', table_name='operator_sessions')
    op.drop_table('operator_sessions')
    op.drop_table('platform_operators')
