"""Add 'api_key' to failed_auth_attempts.service (phase-07 H3)

Revision ID: 0004_api_key_rate_limit_service
Revises: 0003_encrypt_private_keys
Create Date: 2026-07-30 00:00:04.000000

worker/api/utils/auth.py had no throttling on API key validation failures
at all (security-model.md H3) -- unlimited key guessing. The fix reuses
the same failed_auth_attempts table phase-03's session-login rate
limiting already uses, but under its own `service` value: the existing
rows for session-login track IP-only failures as (client_ip, username IS
NULL) under service='api', and mixing API-key failures into that same
bucket would let a burst of API-key guesses count towards -- or be
diluted by -- an unrelated tenant's login failures from the same IP, or
vice versa. A distinct service value keeps the two windows independent
without overloading the username column with a sentinel value.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004_api_key_rate_limit_service'
down_revision: Union[str, None] = '0003_encrypt_private_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'failed_auth_attempts', 'service',
        existing_type=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', name='failed_auth_service'),
        type_=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', 'api_key', name='failed_auth_service'),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM failed_auth_attempts WHERE service = 'api_key'")
    op.alter_column(
        'failed_auth_attempts', 'service',
        existing_type=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', 'api_key', name='failed_auth_service'),
        type_=sa.Enum('smtp', 'imap', 'pop3', 'api', 'sieve', name='failed_auth_service'),
        existing_nullable=False,
    )
