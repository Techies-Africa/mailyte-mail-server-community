"""Empty view for the "shared with anyone" dict path.

When listing the shared namespace Dovecot probes TWO dict prefixes: the
per-user one (shared/shared-boxes/user/$to/$from, answered by
dovecot_shared_mailbox_acl) and an "anyone" one for mailboxes shared with
every user on the server. 0029 mapped only the first, so every single folder
listing logged

    Error: dict-server returned failure: sql dict iterate failed for
    shared/shared-boxes/anyone/: Invalid/unmapped path

The listing still worked -- the per-user lookup is what finds a member's
shared mailboxes -- but an Error line on every LIST is how real errors get
buried, so it is worth removing rather than tolerating.

This view is deliberately EMPTY, not a variant of the real one. Mapping
"anyone" onto the actual shares would hand every mailbox on the server a
listing for every shared mailbox in every organization, which is the exact
tenant leak the per-user path exists to prevent. We do not support
shared-with-anyone, and this states that in the one place Dovecot asks.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code. Keep revision ids <= 32 chars (see 0028).
"""

from alembic import op

revision: str = "0030_shared_mailbox_anyone_view"
down_revision: str | None = "0029_shared_mailbox_acl_view"
branch_labels = None
depends_on = None

VIEW = "dovecot_shared_mailbox_anyone"


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {VIEW}")
    # WHERE 1 = 0 so the optimiser returns no rows without touching a table.
    # The columns still have to exist and be typed, because the dict issues a
    # real SELECT against them.
    op.execute(f"""
        CREATE VIEW {VIEW} AS
        SELECT
            CAST('' AS CHAR(255)) AS from_user,
            '1'                   AS dummy
        FROM (SELECT 1) AS unused
        WHERE 1 = 0
    """)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {VIEW}")
