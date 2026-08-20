"""Envelope-encrypt DKIM/PGP/S-MIME private keys (phase-07 C2)

Revision ID: 0007_encrypt_private_keys
Revises: 0006_operator_rate_limit_service
Create Date: 2026-08-20 00:00:07.000000

Ported from mailyte-email-server's phase-07 build (its own revision id was
0003_encrypt_private_keys -- CE's chain already used 0003 for
smtp_credentials, so this lands at the current head instead; verified
against the versions/ directory rather than assumed).

dkim_keys.private_key, pgp_keys.private_key and smime_certs.private_key
were plaintext TEXT/LONGTEXT. A database dump -- SQL injection, a stolen
backup, a compromised replica, an insider -- yielded every customer's DKIM
signing key, letting an attacker send mail as their domain that passes DKIM
and DMARC (security-model.md C2). This is additive-only (conventions §4
rule 11): new nullable columns, no rewrite of existing rows here, no drop of
the old column.

NO BACKFILL HERE, AND THAT IS THE POINT. Encrypting the existing plaintext
in place would protect key material that must already be assumed
compromised -- it has been sitting readable in every backup this install has
ever taken. EE resolved this the same way and CE mirrors it: writers switch
to encrypt-on-write in the same change that ships this migration
(worker/api/routes/domains.py, scripts/generate_dkim.py), and
`scripts/generate_dkim.py --rotate-all` generates genuinely fresh key
material for every existing active DKIM key and NULLs its old plaintext
column afterward. Rotation is customer-visible (a new DNS TXT record per
domain), which is why it is an explicit operator step rather than something
a migration does silently at 3am.

Dropping the plaintext `private_key` columns is deliberately a separate,
later migration (conventions §4 rule 13: never drop in the same release that
stops writing to it) -- run once every environment has confirmed
`SELECT COUNT(*) FROM dkim_keys WHERE private_key IS NOT NULL` is 0.

CE DIVERGENCE FROM EE: EE's revision also adds `smime_certs.fingerprint`
plus a unique constraint on it. That column exists in EE to repair its
`worker/encryption/app.py` S/MIME import, whose INSERT named columns this
table does not have. CE ships no `worker/encryption` service and has no
S/MIME write path at all, so adding a column with no writer would be schema
CE never uses. Omitted deliberately, not overlooked.

Types are taken from CE's own 0001_baseline mysqldump, not from EE:
dkim_keys.private_key is `text NOT NULL` (so it needs the NULL-ability
change below), while pgp_keys.private_key and smime_certs.private_key are
already-nullable `longtext` and need no alter.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0007_encrypt_private_keys'
down_revision: Union[str, None] = '0006_operator_rate_limit_service'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All three carry a private key column. pgp_keys/smime_certs have no writer
# in CE today; they get the columns anyway so the schema is consistent and a
# future CE writer has nowhere to store plaintext even by accident.
_KEY_TABLES = ('dkim_keys', 'pgp_keys', 'smime_certs')


def upgrade() -> None:
    for table in _KEY_TABLES:
        op.add_column(table, sa.Column('private_key_ciphertext', sa.LargeBinary(), nullable=True))
        op.add_column(table, sa.Column('private_key_nonce', sa.LargeBinary(length=12), nullable=True))
        op.add_column(table, sa.Column('key_version', sa.Integer(), nullable=False, server_default=sa.text('1')))

    # dkim_keys.private_key was NOT NULL -- every new row from this point on
    # writes only the encrypted columns above, so the plaintext column must
    # accept NULL. pgp_keys/smime_certs.private_key were already nullable.
    op.alter_column('dkim_keys', 'private_key', existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Rows written after the upgrade have private_key = NULL, so restoring
    # NOT NULL would fail on them. There is no honest way to recover their
    # plaintext without the KEK, and a downgrade must not silently invent
    # one, so they are dropped: a DKIM key whose only copy was the ciphertext
    # about to be deleted is unusable either way, and leaving it as a row
    # with no key material would break signing more confusingly than its
    # absence does. Re-run scripts/generate_dkim.py afterwards.
    op.execute("DELETE FROM dkim_keys WHERE private_key IS NULL")
    op.alter_column('dkim_keys', 'private_key', existing_type=sa.Text(), nullable=False)
    for table in _KEY_TABLES:
        op.drop_column(table, 'key_version')
        op.drop_column(table, 'private_key_nonce')
        op.drop_column(table, 'private_key_ciphertext')
