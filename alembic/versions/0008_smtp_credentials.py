"""Add smtp_credentials table (SMTP-credentials workstream)

Revision ID: 0008_smtp_credentials
Revises: 0007_operator_rate_limit_service
Create Date: 2026-08-07 00:00:08.000000

Domain-scoped, Mailgun-style SMTP credential distinct from a mailbox
password. Checked by a new protocol-scoped Dovecot passdb that falls
through to the existing mailbox-backed passdb when not found (see
01-mailyte-email-server/phase-10-smtp-credential-auth.md) -- this table is
additive and does not touch email_accounts at all.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0008_smtp_credentials'
down_revision: Union[str, None] = '0007_operator_rate_limit_service'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This whole schema (organizations.id, domains.id, email_accounts.email,
    # aliases.destination, ...) uses utf8mb4_unicode_ci explicitly, not this
    # database's own default (utf8mb4_0900_ai_ci) -- confirmed via
    # information_schema.COLUMNS, not assumed. Two independent MySQL errors
    # come from the same root cause if any column here is left on the
    # default collation instead: error 3780 ("FK incompatible") on the two
    # id columns against their FKs, and "Illegal mix of collations" on
    # `username` the moment mysql-sender-login-maps.cf UNIONs it against
    # email_accounts.email/aliases.destination. Every string/char column
    # below is pinned to match.
    op.create_table(
        'smtp_credentials',
        sa.Column('id', sa.CHAR(26, collation='utf8mb4_unicode_ci'), primary_key=True),
        sa.Column('organization_id', sa.CHAR(26, collation='utf8mb4_unicode_ci'), sa.ForeignKey('organizations.id'), nullable=False, index=True),
        sa.Column('domain_id', sa.CHAR(26, collation='utf8mb4_unicode_ci'), sa.ForeignKey('domains.id'), nullable=False, index=True),
        sa.Column('username', sa.String(255, collation='utf8mb4_unicode_ci'), nullable=False, unique=True, index=True),
        sa.Column('password', sa.String(255, collation='utf8mb4_unicode_ci'), nullable=False),
        sa.Column('allowed_ips', sa.JSON(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('smtp_credentials')
