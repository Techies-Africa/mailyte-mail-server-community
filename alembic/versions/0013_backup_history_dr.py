"""Give backup_history the columns a real backup producer needs

Revision ID: 0013_backup_history_dr
Revises: 0012_email_bodies
Create Date: 2026-08-22

backup_history has existed since 0001_baseline with zero rows: `backup.sh` is a
host-side script that never touched the database, and the only code that did
write it -- worker/archiver's /backup/* endpoints -- INSERTed
`backup_path`/`file_size`, columns that do not exist, so every one of those
writes failed. The DR work (plans/06-operations/00-PRD-disaster-recovery.md,
DR-1) makes backup.sh the real producer, and four facts it has to record have
nowhere to go in the baseline shape:

* `hostname` -- backups now run on two machines. The mail server writes its own
  rows through the mysql container; the web server has no route to this
  database and reports through the API instead. Without this column the two
  streams are indistinguishable, and "no successful full in 26 h" cannot be
  evaluated per host, which is the alert that actually matters.
* `backup_id` -- the YYYYmmdd_HHMMSS stamp that names the backup directory and
  the S3 prefix. It is how a row is tied to the bytes it describes; storage_path
  alone stops being enough the moment a restore drill copies objects around.
* `checksum` -- phase-01 task 1.2 asks for it explicitly, and the escrow bundle
  needs a published digest so silent drift is detectable (PRD §4 L0).
* `encrypted` -- backups written before the age stage landed are plaintext and
  contain DKIM private keys and a full .env. A restore has to know which it is
  holding, and the pre-encryption dirs have to be identifiable to be shredded.

Additive and nullable throughout (conventions §4 rule 11): the table has no
rows today, but the same migration runs against CE installs that may, and a
NOT NULL backfill on a table whose writer is being replaced in the same release
buys nothing.

`type` stays as-is. The baseline ENUM already covers full/incremental and the
PRD adds no third kind.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0013_backup_history_dr'
down_revision: Union[str, None] = '0012_email_bodies'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'backup_history',
        sa.Column('backup_id', sa.String(32), nullable=True,
                  comment='YYYYmmdd_HHMMSS stamp shared by the backup dir and its S3 prefix'),
    )
    op.add_column(
        'backup_history',
        sa.Column('hostname', sa.String(255), nullable=True,
                  comment='Which machine produced this backup'),
    )
    op.add_column(
        'backup_history',
        # sha256 hex is 64 chars; 128 leaves room for a longer digest without a
        # second migration.
        sa.Column('checksum', sa.String(128), nullable=True,
                  comment='sha256 of the uploaded artefact, or of the manifest for multi-file runs'),
    )
    op.add_column(
        'backup_history',
        sa.Column('encrypted', sa.Boolean(), nullable=False, server_default=sa.text('0'),
                  comment='1 once the age stage exists; 0 marks pre-DR-1 plaintext backups'),
    )

    # The absence alerts ask "what is the newest successful full for this host",
    # which is a per-host, per-type scan of recent rows.
    op.create_index(
        'idx_backup_host_type_started',
        'backup_history',
        ['hostname', 'backup_type', 'started_at'],
    )


def downgrade() -> None:
    op.drop_index('idx_backup_host_type_started', table_name='backup_history')
    op.drop_column('backup_history', 'encrypted')
    op.drop_column('backup_history', 'checksum')
    op.drop_column('backup_history', 'hostname')
    op.drop_column('backup_history', 'backup_id')
