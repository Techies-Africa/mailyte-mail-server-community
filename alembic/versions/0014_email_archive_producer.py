"""Reconcile email_archive with the producer that now actually writes it

Revision ID: 0014_email_archive_producer
Revises: 0013_backup_history_dr
Create Date: 2026-08-22

email_archive has had zero rows since 0001_baseline created it, and DR-2 found
out why: worker/archiver's INSERT never matched this table. It wrote
`content_hash` and `file_size` (neither exists), relied on a unique key
`uk_archive_msg` (never created), and set storage_type values the ENUM does not
accept. The service also declared its own conflicting version of the table with
CREATE TABLE IF NOT EXISTS, which was a silent no-op against the real one -- the
same failure mode as backup_history in 0013.

Now that both producers are wired (tracking_injector.py outbound, a Dovecot
sieve pipe inbound), the table needs the three things the archive genuinely
depends on:

* `content_hash` -- sha256 of the message PLAINTEXT. This is what makes a
  restore verifiable: age output is non-deterministic, so a ciphertext digest
  could never confirm that a message pulled back from S3 is the message that
  was archived. It is also the only way to recognise a duplicate.
* a unique key on (message_id, recipient) -- Postfix can legitimately process
  the same Message-ID more than once (a resend, or a fan-out handled as
  separate filter invocations). Without the key, the archiver's upsert inserts
  a duplicate row for the same object every time. Applied on an empty table, so
  there is nothing to deduplicate first.
* `spool` in storage_type -- an object held locally because S3 was unreachable
  is a distinct state from one deliberately stored locally: it is temporary and
  a drain pass will move it. Conflating it with 'local' means the spool cannot
  be found again after a restart.

message_id is VARCHAR(255) and recipient VARCHAR(255); a combined unique index
on both is 1024 bytes at utf8mb4 (4 bytes/char would be 2040), which is inside
InnoDB's 3072-byte limit for DYNAMIC row format.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0014_email_archive_producer'
down_revision: Union[str, None] = '0013_backup_history_dr'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'email_archive',
        sa.Column('content_hash', sa.String(64), nullable=True,
                  comment='sha256 of the message plaintext, for restore verification and dedupe'),
    )
    op.create_index('idx_archive_content_hash', 'email_archive', ['content_hash'])

    # ALTER rather than a new column: the ENUM is the type, and 'spool' is a
    # value it should always have had.
    op.execute(
        "ALTER TABLE email_archive "
        "MODIFY COLUMN storage_type ENUM('s3','azure','gcs','local','spool') "
        "NOT NULL DEFAULT 's3'"
    )

    op.create_unique_constraint(
        'uk_archive_msg', 'email_archive', ['message_id', 'recipient']
    )


def downgrade() -> None:
    op.drop_constraint('uk_archive_msg', 'email_archive', type_='unique')
    # Rows written while 'spool' was valid would violate the narrower ENUM, so
    # fold them into 'local' -- the closest surviving meaning -- before shrinking it.
    op.execute("UPDATE email_archive SET storage_type = 'local' WHERE storage_type = 'spool'")
    op.execute(
        "ALTER TABLE email_archive "
        "MODIFY COLUMN storage_type ENUM('s3','azure','gcs','local') "
        "NOT NULL DEFAULT 's3'"
    )
    op.drop_index('idx_archive_content_hash', table_name='email_archive')
    op.drop_column('email_archive', 'content_hash')
