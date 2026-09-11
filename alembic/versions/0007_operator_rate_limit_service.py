"""Add 'operator' to failed_auth_attempts.service (phase-06 task 6.7)

Revision ID: 0007_operator_rate_limit_service
Revises: 0006_operator_identity
Create Date: 2026-07-30 00:00:07.000000

Operator login needs its own failed-attempt bucket for the same reason
0004_api_key_rate_limit_service gave API-key validation its own: mixing
operator login failures into the existing 'api' bucket (tenant dashboard
login) would let a burst against one dilute or count towards a lockout on
the other, even though they're unrelated credentials. The phase doc calls
for a tighter limit here (3/15min vs tenant login's 5/15min) -- a distinct
service value is what makes that different threshold possible without
touching the tenant login path at all.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0007_operator_rate_limit_service'
down_revision: Union[str, None] = '0006_operator_identity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'failed_auth_attempts', 'service',
        existing_type=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', 'api_key', name='failed_auth_service'),
        type_=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', 'api_key', 'operator', name='failed_auth_service'),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM failed_auth_attempts WHERE service = 'operator'")
    op.alter_column(
        'failed_auth_attempts', 'service',
        existing_type=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', 'api_key', 'operator', name='failed_auth_service'),
        type_=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', 'api_key', name='failed_auth_service'),
        existing_nullable=False,
    )
