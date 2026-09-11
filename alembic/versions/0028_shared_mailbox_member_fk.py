"""shared_mailbox_members.shared_mailbox_id INT -> CHAR(26).

009_ulid_safe.sql converted email_accounts.id to a CHAR(26) ULID and
converted shared_mailbox_members' own id and its email_account_id column to
match -- but its CALL convert_fk_column list simply omitted
shared_mailbox_id, so that column was left behind as INT pointing at a
CHAR(26) primary key.

Nothing noticed because the feature never got as far as inserting a member:
create_shared_mailbox failed first (it omitted the ULID id, so MySQL raised
1364 under STRICT_TRANS_TABLES and the customer saw a bare 500). With that
fixed, this is the next wall -- and the quieter one, because an INT/CHAR
comparison is not an error in MySQL, just a silent cast. The member-count
subquery in list_shared_mailboxes compares smm.shared_mailbox_id = ea.id, so
it would have cast every ULID to a number and counted the wrong rows rather
than failing.

Safe to run, and safe to RE-run: shared_mailbox_members has no foreign keys
(009 dropped them all and re-added only four elsewhere), the table is empty
in production, and MODIFY COLUMN to the type a column already has is a
no-op. Re-running matters here because the first attempt at this migration
applied the ALTER and then died stamping the version (see below), leaving
the schema ahead of alembic_version.

KEEP REVISION IDS <= 32 CHARACTERS. alembic_version.version_num is
varchar(32), and alembic stamps it *after* running upgrade() -- on MySQL,
where DDL auto-commits, an over-long id therefore applies the schema change
and THEN fails with "1406 Data too long for column 'version_num'", which
reads like a migration failure but is really a bookkeeping one. This
revision was first written as 0028_shared_mailbox_member_ulid_fk (34) and
hit exactly that. The longest ids already in this directory are 32.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0028_shared_mailbox_member_fk"
down_revision: str | None = "0027_smtp_cred_stream"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "shared_mailbox_members",
        "shared_mailbox_id",
        existing_type=sa.Integer(),
        type_=sa.CHAR(26),
        existing_nullable=False,
        nullable=False,
        comment="The shared mailbox (email_accounts.id where mailbox_type='shared')",
    )


def downgrade() -> None:
    op.alter_column(
        "shared_mailbox_members",
        "shared_mailbox_id",
        existing_type=sa.CHAR(26),
        type_=sa.Integer(),
        existing_nullable=False,
        nullable=False,
    )
