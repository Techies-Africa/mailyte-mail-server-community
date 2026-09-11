"""Per-mailbox Maya quota, so a tier's allowance can differ within one org

Revision ID: 0024_mailbox_ai_quota
Revises: 0023_mailbox_password_policy
Create Date: 2026-09-02

0022 gave every mailbox in an organization the same monthly Maya allowance,
because at the time an organization had one plan. Billing now sells tiers per
MAILBOX -- 50 actions on Standard, 300 on Pro, and both can sit in the same
organization on the same invoice -- so a single org-level number can no
longer express what was bought.

The two ways to fudge it are both wrong: taking the lowest tier under-delivers
an allowance a customer paid for, and taking the highest hands Pro's allowance
to every Standard mailbox, which is exactly the "one Pro seat unlocks it for
everyone" leak the commercial model is built to prevent.

So the quota becomes a per-mailbox value. NULL means "use the organization's
`ai_monthly_quota`", which is what every existing row does today -- so this
migration changes nothing for anyone until Laravel starts writing the column.

`consume_ai_call()` already takes the quota as an argument (it is passed
`maya["quota"]`), so only the resolver changes; the metering itself is
untouched.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0024_mailbox_ai_quota"
down_revision: Union[str, None] = "0023_mailbox_password_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_accounts",
        sa.Column(
            "ai_monthly_quota",
            sa.Integer(),
            nullable=True,
            comment="Maya actions per month for THIS mailbox. NULL falls back to the org value.",
        ),
    )

    # The commercial tier that produced the quota above. Not used for
    # enforcement -- the number is -- but an operator looking at a mailbox
    # that is out of Maya calls needs to see which tier it was sold, without
    # having to ask Laravel.
    op.add_column(
        "email_accounts",
        sa.Column(
            "billing_tier",
            sa.String(16, collation="utf8mb4_unicode_ci"),
            nullable=True,
            comment="free | standard | pro | workplace, as sold by Laravel.",
        ),
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "billing_tier")
    op.drop_column("email_accounts", "ai_monthly_quota")
