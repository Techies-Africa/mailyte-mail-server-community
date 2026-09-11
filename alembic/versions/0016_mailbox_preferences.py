"""Mailbox-holder preferences -- signature, density, undo-send

Revision ID: 0016_mailbox_preferences
Revises: 0015_mailbox_sessions
Create Date: 2026-08-26

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 4. Mirrors Laravel's
`mailbox_preferences` (created 2026_08_21_000001, extended 2026_08_22_000001)
so the rows migrate across in Phase 6 without transformation, and so the
webmail's existing settings payload keeps working unchanged.

**Every column carries a DB-side default.** The Laravel version shipped a bug
where `firstOrCreate` returned NULL for unset columns and `(int) null` became
a 0-second undo window -- an undo-send setting that silently did nothing. The
defaults live here rather than only in application code so a row inserted by
any path is complete.

Columns intentionally match Laravel's names one for one:

* signature_html / signature_on_reply -- the outbound signature. Sanitised in
  the application layer before it is stored, never trusted on the way out.
* display_density -- 'comfortable' | 'compact'. Held as a short string rather
  than an ENUM: the webmail may add a third density, and an ENUM would need a
  migration for what is a presentation choice.
* undo_send_enabled / undo_send_seconds -- OFF and 5s by default, matching the
  owner's decision recorded in 01-PRD-webmail C4/section 12. The delay applies
  to every message the mailbox sends, so someone who never wants to recall one
  should not pay for the option.

See 0015_mailbox_sessions for why the FK columns are CHAR(26) with an explicit
collation -- the same two traps apply to every new table here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0016_mailbox_preferences'
down_revision: Union[str, None] = '0015_mailbox_sessions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mailbox_preferences",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column(
            "email_account_id",
            sa.CHAR(26),
            sa.ForeignKey("email_accounts.id", ondelete="CASCADE"),
            nullable=False,
            # One row per mailbox. Unique rather than merely indexed so a
            # racing double-insert fails loudly instead of leaving two rows
            # whose settings silently disagree.
            unique=True,
        ),
        sa.Column("signature_html", sa.Text(), nullable=True),
        sa.Column(
            "signature_on_reply", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "display_density", sa.String(20), nullable=False, server_default="comfortable"
        ),
        sa.Column(
            "undo_send_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "undo_send_seconds",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("5"),
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade() -> None:
    op.drop_table("mailbox_preferences")
