"""Add 'operator' to failed_auth_attempts.service

Revision ID: 0006_operator_rate_limit_service
Revises: 0005_operator_identity
Create Date: 2026-08-20 00:00:06.000000

Ported from mailyte-email-server's phase-06 build (its own revision id was
0007_operator_rate_limit_service). Operator login needs its own
failed-attempt bucket: mixing operator login failures into the existing 'api'
bucket would let a burst against one dilute or count towards a lockout on the
other, even though they are unrelated credentials. A distinct service value
is also what makes the tighter operator threshold (3 failures / 15 min, vs
tenant login's 5) expressible without touching any other login path.

DELIBERATE DIVERGENCE FROM EE: EE's enum at this point in its chain is
('smtp','imap','pop3','api','sieve','api_key','operator') because its
0004_api_key_rate_limit_service added 'api_key' for API-key-guessing
throttling. CE never took that revision, and CE's utils/auth.py does not
throttle API-key validation, so adding 'api_key' here would create an enum
member with no writer. Verified against 0001_baseline's frozen mysqldump:
CE's current members are exactly ('smtp','imap','pop3','api','sieve'). Only
'operator' is appended.

Appending to the end of an ENUM is metadata-only in MySQL 8 (no table
rewrite), which is what keeps this additive per conventions §4 rule 11.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0006_operator_rate_limit_service'
down_revision: Union[str, None] = '0005_operator_identity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'failed_auth_attempts',
        'service',
        existing_type=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', name='failed_auth_service'),
        type_=sa.Enum(
            'smtp', 'imap', 'pop3', 'api', 'sieve', 'operator', name='failed_auth_service'
        ),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Rows using the member being removed must go first, or MySQL coerces
    # them to '' and the column stops round-tripping. Operator lockout
    # counters are transient by nature (15-minute windows), so deleting them
    # loses nothing an operator would miss.
    op.execute("DELETE FROM failed_auth_attempts WHERE service = 'operator'")
    op.alter_column(
        'failed_auth_attempts',
        'service',
        existing_type=sa.Enum(
            'smtp', 'imap', 'pop3', 'api', 'sieve', 'operator', name='failed_auth_service'
        ),
        type_=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', name='failed_auth_service'),
        existing_nullable=False,
    )
