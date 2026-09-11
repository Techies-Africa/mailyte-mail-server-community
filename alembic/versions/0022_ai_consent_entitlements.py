"""Maya entitlements, org policy, and the mailbox AI consent ledger

Revision ID: 0022_ai_consent_entitlements
Revises: 0020_signature_mediumtext
Create Date: 2026-08-31

(0021 was reserved for mailbox push devices; that work was deferred, so this
revision chains directly off 0020_signature_mediumtext. The devices migration
can take any later number when it lands.)

Mobile v1 section 13. Three pieces:

* organizations gains the Maya columns: ai_entitled/ai_plan/ai_monthly_quota
  (the commercial facts Laravel or staff write through
  PUT /api/v1/organizations/{id}/ai-settings) and ai_org_policy with its
  audit stamp (the org's own veto / blanket acceptance). Defaults are the
  fail-safe direction: not entitled, policy 'unset' -- which the gates treat
  as blocked -- so running this migration changes nothing for anyone until a
  row is deliberately flipped.

* mailbox_ai_consents: current consent state, one row per mailbox, unique on
  email_account_id for the same double-insert reason 0016 gives.
  organization_id is denormalised so "revoke every holder in this org" is
  one indexed scan.

* mailbox_ai_consent_events: the append-only ledger. Deliberately FK-free,
  like smtp_credential_events and for the same reason -- a consent record
  must survive the deletion of the mailbox and the organization it
  describes. Each row carries the canonical JSON record, its SHA-256, an
  HMAC seal, and the PDF handed back to the holder; LONGBLOB because an
  acceptance PDF embeds the full terms text.

See 0015_mailbox_sessions for why FK columns are CHAR(26) with the table
collation pinned -- both traps apply to every new table here.

Rebuild the migrate image before running this (it goes stale and silently
no-ops on old code).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0022_ai_consent_entitlements"
down_revision: Union[str, None] = "0020_signature_mediumtext"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORG_POLICY_ENUM = sa.Enum(
    "unset", "allowed", "blocked", "accepted_for_all", name="organizations_ai_org_policy"
)
_ACTION_ENUM = sa.Enum(
    "accepted",
    "revoked",
    "training_accepted",
    "training_revoked",
    "revoked_by_organisation",
    name="mailbox_ai_consent_action",
)


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("ai_entitled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("organizations", sa.Column("ai_plan", sa.String(64), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("ai_org_policy", _ORG_POLICY_ENUM, nullable=False, server_default="unset"),
    )
    op.add_column("organizations", sa.Column("ai_monthly_quota", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("ai_policy_updated_at", sa.DateTime(), nullable=True))
    op.add_column(
        "organizations", sa.Column("ai_policy_updated_by", sa.String(255), nullable=True)
    )

    op.create_table(
        "mailbox_ai_consents",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column(
            "email_account_id",
            sa.CHAR(26),
            sa.ForeignKey("email_accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("organization_id", sa.CHAR(26), nullable=False, index=True),
        sa.Column(
            "ai_assistant_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "ai_training_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("ai_terms_version", sa.String(32), nullable=True),
        sa.Column("ai_assistant_opted_in_at", sa.DateTime(), nullable=True),
        sa.Column("ai_training_opted_in_at", sa.DateTime(), nullable=True),
        sa.Column("consent_event_id", sa.CHAR(26), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
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

    op.create_table(
        "mailbox_ai_consent_events",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        # No FKs on purpose -- the ledger outlives what it describes.
        sa.Column("email_account_id", sa.CHAR(26), nullable=False),
        sa.Column("organization_id", sa.CHAR(26), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("action", _ACTION_ENUM, nullable=False),
        sa.Column("terms_version", sa.String(32), nullable=True),
        sa.Column(
            "included_training", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("actor", sa.String(255), nullable=True),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("record_sha256", sa.CHAR(64), nullable=False),
        sa.Column("record_hmac", sa.CHAR(64), nullable=True),
        sa.Column(
            "document_pdf",
            sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "idx_ai_consent_events_account",
        "mailbox_ai_consent_events",
        ["email_account_id", "created_at"],
    )
    op.create_index(
        "idx_ai_consent_events_org", "mailbox_ai_consent_events", ["organization_id"]
    )


def downgrade() -> None:
    # Dropping the ledger destroys every stored consent record and PDF. That
    # is the nature of downgrading past this revision; export first if any
    # real consent has been recorded.
    op.drop_index("idx_ai_consent_events_org", table_name="mailbox_ai_consent_events")
    op.drop_index("idx_ai_consent_events_account", table_name="mailbox_ai_consent_events")
    op.drop_table("mailbox_ai_consent_events")
    op.drop_table("mailbox_ai_consents")
    op.drop_column("organizations", "ai_policy_updated_by")
    op.drop_column("organizations", "ai_policy_updated_at")
    op.drop_column("organizations", "ai_monthly_quota")
    op.drop_column("organizations", "ai_org_policy")
    op.drop_column("organizations", "ai_plan")
    op.drop_column("organizations", "ai_entitled")
