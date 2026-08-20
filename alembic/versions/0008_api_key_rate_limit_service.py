"""Add 'api_key' to failed_auth_attempts.service

Revision ID: 0008_api_key_rate_limit_service
Revises: 0007_encrypt_private_keys
Create Date: 2026-08-20

Prerequisite for H3 (rate limiting API key validation). Until now
utils/auth.py throttled tenant logins and operator logins but did nothing at
all about API key guessing -- an attacker could try keys as fast as the
server would answer, indefinitely.

API-key failures need their own bucket rather than reusing 'api'. Sharing
one would let a burst of bad keys from a single integration lock out
password logins from the same IP, and the two want different limits anyway:
a bad key carries no identity, and an integration mistyping its key a few
times in a row is far more common than a few wrong passwords. Hence a looser
20-per-5-minutes here against login's 5-per-15.

CE never took EE's 0004_api_key_rate_limit_service, which is why this is a
new revision rather than a port of that file -- CE's 0004 number is already
taken by 0004_api_key_platform_scope, and CE's 0006 appended only 'operator'.
Verified against 0001_baseline's frozen mysqldump plus 0006: CE's current
members are exactly ('smtp','imap','pop3','api','sieve','operator'). Only
'api_key' is appended.

Appending to the end of an ENUM is metadata-only in MySQL 8 (no table
rewrite), which is what keeps this additive per conventions §4 rule 11.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0008_api_key_rate_limit_service'
down_revision: Union[str, None] = '0007_encrypt_private_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BEFORE = ('smtp', 'imap', 'pop3', 'api', 'sieve', 'operator')
_AFTER = ('smtp', 'imap', 'pop3', 'api', 'sieve', 'operator', 'api_key')


def upgrade() -> None:
    op.alter_column(
        'failed_auth_attempts',
        'service',
        existing_type=sa.Enum(*_BEFORE, name='failed_auth_service'),
        type_=sa.Enum(*_AFTER, name='failed_auth_service'),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Rows using the member being removed must go first, or MySQL coerces
    # them to '' and the column stops round-tripping. API-key lockout
    # counters are transient by nature (5-minute windows), so deleting them
    # loses nothing an operator would miss.
    op.execute("DELETE FROM failed_auth_attempts WHERE service = 'api_key'")
    op.alter_column(
        'failed_auth_attempts',
        'service',
        existing_type=sa.Enum(*_AFTER, name='failed_auth_service'),
        type_=sa.Enum(*_BEFORE, name='failed_auth_service'),
        existing_nullable=False,
    )
