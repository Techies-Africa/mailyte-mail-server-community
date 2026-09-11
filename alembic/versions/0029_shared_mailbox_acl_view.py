"""View Dovecot reads to decide which shared mailboxes to list for a user.

Dovecot's `acl_shared_dict` answers "which shared mailboxes should I LIST for
this user" before it looks at any ACL file. Pointing it at a VIEW over
shared_mailbox_members instead of a table of its own means discovery has
nothing to synchronise -- add a member in the dashboard and the folder is
listed on their next connection.

The rights themselves cannot come from SQL: Dovecot's only ACL backend is
vfile, plain `dovecot-acl` files inside each Maildir, which is what
mailer/dovecot/scripts/shared-mailbox-acl-sync.py writes.

Both sides are filtered on status = 'active', so suspending either the shared
mailbox or a member's own mailbox withdraws access without anyone having to
remember to edit the membership.

`dummy` exists because the dict config must name a value_field and Dovecot
stores nothing meaningful in it; the key alone carries the fact.

Read-only by construction, which is deliberate: imap_acl is not loaded (see
local.conf), so no client can make Dovecot attempt a write here.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code. Keep revision ids <= 32 chars (alembic_version.version_num
is varchar(32)); see 0028 for what happens otherwise.
"""

from alembic import op

revision: str = "0029_shared_mailbox_acl_view"
down_revision: str | None = "0028_shared_mailbox_member_fk"
branch_labels = None
depends_on = None

VIEW = "dovecot_shared_mailbox_acl"


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {VIEW}")
    op.execute(f"""
        CREATE VIEW {VIEW} AS
        SELECT
            owner.email  AS from_user,
            member.email AS to_user,
            '1'          AS dummy
        FROM shared_mailbox_members smm
        JOIN email_accounts owner
          ON owner.id = smm.shared_mailbox_id
         AND owner.mailbox_type = 'shared'
         AND owner.status = 'active'
        JOIN email_accounts member
          ON member.id = smm.email_account_id
         AND member.status = 'active'
    """)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {VIEW}")
