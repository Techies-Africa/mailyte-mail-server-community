"""Mailbox password policy + platform-aware session lifetimes

Revision ID: 0023_mailbox_password_policy
Revises: 0022_ai_consent_entitlements
Create Date: 2026-08-31

Filed by the mobile team's 2026-08-31 API test (their §2, §4, §12). Two
tables, six nullable-or-defaulted columns, no data step -- every existing
row keeps behaving exactly as it did before this ran.

email_accounts
--------------
* must_change_password (bool, default 0) -- set by an admin reset with
  temporary=true (POST /api/v1/mailboxes/email-accounts/{id}/reset-password).
  While set, the holder's session is refused on every /api/v1/mailbox/*
  route except the password endpoint and sign-out (403
  password_change_required); a successful POST /api/v1/mailbox/security/
  password clears it. Sign-in still succeeds and reports the flag, so a
  client can route straight to its "set a new password" screen.
* password_change_reason (varchar(20), nullable) -- why the change is
  required: temporary | expired | admin_reset. A VARCHAR with the values
  enforced in code, not a MySQL ENUM: adding a reason must not need an
  ALTER TABLE on a 100k-row table, and 'expired' is reserved for a future
  expiry sweep that does not exist yet.
* password_changed_at (datetime, nullable) -- last credential change by any
  path (holder or admin). NULL for every pre-existing account, which reads
  correctly as "never changed since provisioning".
* two_factor_confirmed_at (datetime, nullable) -- when /2fa/confirm last
  succeeded. Lives here rather than in totp_secrets because that table has
  no such column: its created_at is the enrolment START (written by
  /totp/setup), and its updated_at is ON UPDATE CURRENT_TIMESTAMP, so it
  moves on every sign-in verify. GET /api/v1/mailbox/security used to
  report created_at as the confirmation time -- non-null the moment the QR
  was shown, before any code had been entered. Cleared by /2fa/disable;
  only ever reported while the totp service says enabled=1, so a value
  left behind by an out-of-band disable can never surface.

mailbox_sessions
----------------
* client_platform (varchar(20), nullable) -- the normalised
  X-Client-Platform sent at sign-in (web|ios|android|macos|windows|linux),
  NULL when absent or unrecognised. Shown in GET /security/sessions.
* idle_timeout_seconds (int, default 28800 = 8h) -- this session's own
  idle window. The sliding-expiry update in the session resolver used to
  add a module constant on every request, so a per-platform window at
  sign-in would have been silently overwritten by the first request that
  followed. Persisting it on the row is what makes "native clients idle
  out after 30 days, browsers after 8 hours" actually hold. The default is
  exactly the old constant, so every existing session keeps its behaviour.

Numbering note: this was drafted as 0021 but three migrations landed the
same day; it now follows 0022_ai_consent_entitlements to keep the revision
graph linear. Run `alembic heads` before merging further same-day work --
a second head here breaks `upgrade head` for everyone.

Deploy order: run this BEFORE the API code that reads these columns --
the mailbox session resolver and the login SELECT name them, and a missing
column is a hard error on every webmail request. Rebuild the migrate image
first (it goes stale and silently no-ops on old code).
"""

import sqlalchemy as sa

from alembic import op

revision = "0023_mailbox_password_policy"
down_revision = "0022_ai_consent_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_accounts",
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "email_accounts",
        sa.Column(
            "password_change_reason",
            sa.String(20, collation="utf8mb4_unicode_ci"),
            nullable=True,
        ),
    )
    op.add_column(
        "email_accounts", sa.Column("password_changed_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "email_accounts", sa.Column("two_factor_confirmed_at", sa.DateTime(), nullable=True)
    )

    op.add_column(
        "mailbox_sessions",
        sa.Column(
            "client_platform",
            sa.String(20, collation="utf8mb4_unicode_ci"),
            nullable=True,
        ),
    )
    op.add_column(
        "mailbox_sessions",
        sa.Column(
            "idle_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("28800"),
        ),
    )


def downgrade() -> None:
    op.drop_column("mailbox_sessions", "idle_timeout_seconds")
    op.drop_column("mailbox_sessions", "client_platform")
    op.drop_column("email_accounts", "two_factor_confirmed_at")
    op.drop_column("email_accounts", "password_changed_at")
    op.drop_column("email_accounts", "password_change_reason")
    op.drop_column("email_accounts", "must_change_password")
