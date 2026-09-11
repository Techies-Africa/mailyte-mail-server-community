"""Add quota override flags to organizations (console phase-02 SS2.6 decision (a))

Revision ID: 0009_quota_override
Revises: 0008_smtp_credentials
Create Date: 2026-08-20 00:00:09.000000

05-sudo-console/phase-02-tenant-operations.md SS2.6 named a real conflict:
Laravel pushes plan-derived limits to the mail server
(03-mailyte-api/phase-03), so an operator who fixes a customer's quota by
hand has it silently reverted by the next plan sync. That doc offered three
resolutions and required one be recorded; option (a) -- "console edits are
overrides with a flag Laravel's sync respects" -- is the recorded choice,
and these three columns are what makes the flag exist.

Why on `organizations` and not a side table: the flag has exactly the same
lifetime and cardinality as the quota it guards (organizations.storage_quotas
/ rate_limits, both already columns here), and PUT /organizations/{id}/quotas
has to read it in the same statement it would otherwise blindly write. A
join to answer "may I write this row?" buys nothing.

quota_override_by stores the operator's EMAIL, not their id -- denormalised
deliberately, for the same reason operator_audit.operator_email is
(0006_operator_identity): the whole point of the field is to tell the next
human "X set this on DATE" months later, and that has to survive the
operator row being renamed, deactivated, or deleted.

Additive only (conventions SS4 rule 11): three columns, two nullable, one
NOT NULL with a server default so existing rows need no backfill and a
forgotten value means "not overridden" -- the least surprising state, and
the one that preserves today's behaviour exactly for every existing tenant.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0009_quota_override'
down_revision: Union[str, None] = '0008_smtp_credentials'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # sa.Boolean() renders as TINYINT(1) on MySQL 8, which is what this
    # schema uses for every other flag (organizations.active,
    # domains.dkim_enabled). server_default is sa.text('0'), not Python
    # False: the DEFAULT has to live in the DDL so a raw INSERT from
    # Laravel's sync -- which does not go through the ORM -- still yields a
    # non-overridden row rather than failing the NOT NULL.
    op.add_column(
        'organizations',
        sa.Column('quota_override', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'organizations',
        sa.Column('quota_override_at', sa.DateTime(), nullable=True),
    )
    # Collation pinned to match the rest of this schema rather than taking
    # the database default (utf8mb4_0900_ai_ci) -- conventions SS4 rule 2,
    # and the same trap 0008_smtp_credentials documented at length.
    op.add_column(
        'organizations',
        sa.Column(
            'quota_override_by',
            sa.String(255, collation='utf8mb4_unicode_ci'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Genuinely reversible (conventions SS4 rule 14): nothing else
    # references these columns, so dropping them restores the pre-0009
    # schema exactly. Dropped in reverse order of addition for symmetry.
    op.drop_column('organizations', 'quota_override_by')
    op.drop_column('organizations', 'quota_override_at')
    op.drop_column('organizations', 'quota_override')
