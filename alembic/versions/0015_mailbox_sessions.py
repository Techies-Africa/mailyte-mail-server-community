"""Mailbox-holder sessions -- the third credential tier

Revision ID: 0015_mailbox_sessions
Revises: 0014_email_archive_producer
Create Date: 2026-08-26

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 1.

The webmail's sessions live in mailyte-api's MySQL today (`mailbox_sessions`,
created by Laravel migration 2026_08_21_000002). That PRD's success criterion
is that the webmail works with mailyte-api stopped, so the table has to exist
here. Phase 6 migrates the existing rows across; this migration only creates
the destination.

**Why a third table rather than reusing web_sessions.**  ADR-002 is explicit
that tenant sessions and operator sessions never share a cookie or a table,
and a mailbox holder is a third identity again: `web_sessions.user_id` is a
dashboard `users` row, and a mailbox holder has no `users` row at all -- they
are an `email_accounts` row, authenticated by Dovecot. Widening web_sessions
with a nullable user_id would make "which kind of principal is this?" a
runtime question on a table that currently answers it structurally.

**Shape mirrors WebSession deliberately** (id/token_hash/ip/user_agent/
expires_at/absolute_expiry/revoked_at/created_at) so `_authenticate_session`'s
idle-plus-absolute expiry logic ports across unchanged rather than being
reinvented with subtly different semantics.

Three differences from web_sessions, each load-bearing:

* `email_account_id` FK to email_accounts, ON DELETE CASCADE -- deleting a
  mailbox must not leave sessions that resolve to a missing account. The
  Dovecot auth cache already delays revocation by up to an hour; a dangling
  session row would extend that indefinitely.
* `organization_id` is denormalised in, exactly as web_sessions does, so
  request-scoping never needs a join back to email_accounts.
* `mfa_satisfied` -- mailbox 2FA (Phase 4) issues a session before the TOTP
  code is presented, following the same pending-session shape platform_auth
  already uses for operators. A session with mfa_satisfied=0 authenticates
  the password step and grants nothing else.

Idle timeout is deliberately NOT encoded here; it is policy, and policy lives
in utils/mailbox_auth.py next to the other tiers' constants.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0015_mailbox_sessions'
down_revision: Union[str, None] = '0014_email_archive_producer'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The foreign key needs BOTH of these to match email_accounts.id, and
    # missing either one fails at CREATE TABLE with errno 3780,
    # "Referencing column ... are incompatible":
    #
    # 1. CHAR(26), not VARCHAR(26). email_accounts.id is CHAR(26), and MySQL
    #    compares the declared type, not just the width -- VARCHAR(26) is
    #    rejected even though both sides hold 26 characters of utf8mb4.
    #
    # 2. mysql_collate='utf8mb4_unicode_ci' explicitly. This schema's DEFAULT
    #    collation is utf8mb4_0900_ai_ci (MySQL 8's), while the tables that
    #    matter -- email_accounts among them -- are utf8mb4_unicode_ci. Naming
    #    mysql_charset without mysql_collate is the trap: it pins the charset
    #    and lets the collation fall back to that charset's default, silently
    #    producing 0900_ai_ci and an incompatible key. 0012_email_bodies sets
    #    both for the same reason; follow it for any new table here.
    op.create_table(
        "mailbox_sessions",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column(
            "email_account_id",
            sa.CHAR(26),
            sa.ForeignKey("email_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("organization_id", sa.CHAR(26), nullable=False, index=True),
        # sha256 hex of the session token -- the token itself is never stored,
        # same as web_sessions.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("absolute_expiry", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "mfa_satisfied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    # failed_auth_attempts.service is an ENUM, so a new tier cannot just start
    # writing its own value -- MySQL rejects it as "Data truncated for column
    # 'service'", which surfaces as a 500 on every FAILED sign-in while
    # successful ones work perfectly. A rate limiter that only breaks on the
    # path it exists to protect is worth being explicit about.
    #
    # 'webmail' is a separate bucket from 'api' on purpose: the dashboard and
    # the webmail are different credential spaces that happen to be keyed by
    # email address, so sharing a bucket would let failed webmail sign-ins
    # lock out an unrelated dashboard account. 'operator' was added to this
    # same ENUM when that tier arrived; this follows it.
    op.execute(
        "ALTER TABLE failed_auth_attempts MODIFY COLUMN service "
        "ENUM('smtp','imap','pop3','api','sieve','api_key','operator','webmail')"
    )


def downgrade() -> None:
    op.drop_table("mailbox_sessions")

    # Rows must go before the value does -- MySQL would silently truncate any
    # surviving 'webmail' row to '' when the ENUM no longer accepts it.
    # Discarding them is correct here: they are transient rate-limit counters,
    # and the tier they belong to is being removed.
    op.execute("DELETE FROM failed_auth_attempts WHERE service = 'webmail'")
    op.execute(
        "ALTER TABLE failed_auth_attempts MODIFY COLUMN service "
        "ENUM('smtp','imap','pop3','api','sieve','api_key','operator')"
    )
