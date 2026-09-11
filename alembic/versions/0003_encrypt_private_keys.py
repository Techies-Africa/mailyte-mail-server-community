"""Envelope-encrypt DKIM/PGP/S-MIME private keys (phase-07 C2)

Revision ID: 0003_encrypt_private_keys
Revises: 0002_adhoc_table_tracking
Create Date: 2026-07-30 00:00:03.000000

dkim_keys.private_key, pgp_keys.private_key and smime_certs.private_key
were plaintext TEXT/LONGTEXT. A database dump -- SQL injection, a stolen
backup, a compromised replica, an insider -- yielded every customer's DKIM
signing key, letting an attacker send mail as their domain that passes
DKIM and DMARC (security-model.md C2). This is additive-only (conventions.md
SS4 rule 11/rule 3 in the phase-08 numbering): new nullable columns, no
rewrite of existing rows here, no drop of the old column. Existing/new
writers switch to encrypting on write in the same change that ships this
migration (worker/api/routes/domains.py, scripts/generate_dkim.py,
worker/encryption/app.py); scripts/generate_dkim.py --rotate-all generates
fresh key material for every existing active DKIM key and nulls its old
plaintext column afterward -- see rotate_all() in that script for why
rotation, not merely encrypt-in-place, is required here (assume
already-compromised keys).

Dropping the plaintext `private_key` columns is deliberately a separate,
later migration (conventions.md SS4 rule 13: never drop in the same release
that stops writing to it) -- run once every environment has confirmed
`SELECT COUNT(*) FROM dkim_keys WHERE private_key IS NOT NULL` is 0.

smime_certs also gains a `fingerprint` column here. worker/encryption/app.py's
S/MIME import INSERT already referenced columns that don't exist on this
table (certificate_pem, serial_number, has_private_key, fingerprint --
none of them this table's real names) -- found while wiring encryption
into that same INSERT, which was unreachable dead code before this because
every call would have errored on an unknown column. Fixed in the same
change rather than filed separately, since encrypting a write path
necessarily means editing that exact INSERT anyway.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003_encrypt_private_keys'
down_revision: Union[str, None] = '0002_adhoc_table_tracking'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('dkim_keys', 'pgp_keys', 'smime_certs'):
        op.add_column(table, sa.Column('private_key_ciphertext', sa.LargeBinary(), nullable=True))
        op.add_column(table, sa.Column('private_key_nonce', sa.LargeBinary(length=12), nullable=True))
        op.add_column(table, sa.Column('key_version', sa.Integer(), nullable=False, server_default=sa.text('1')))

    # dkim_keys.private_key was NOT NULL -- every new row from this point on
    # writes only the encrypted columns above, so the plaintext column must
    # accept NULL. pgp_keys/smime_certs.private_key were already nullable.
    op.alter_column('dkim_keys', 'private_key', existing_type=sa.Text(), nullable=True)

    op.add_column('smime_certs', sa.Column('fingerprint', sa.String(64), nullable=True))
    op.create_unique_constraint('uk_smime_fingerprint', 'smime_certs', ['fingerprint'])


def downgrade() -> None:
    op.drop_constraint('uk_smime_fingerprint', 'smime_certs', type_='unique')
    op.drop_column('smime_certs', 'fingerprint')
    op.alter_column('dkim_keys', 'private_key', existing_type=sa.Text(), nullable=False)
    for table in ('dkim_keys', 'pgp_keys', 'smime_certs'):
        op.drop_column(table, 'key_version')
        op.drop_column(table, 'private_key_nonce')
        op.drop_column(table, 'private_key_ciphertext')
