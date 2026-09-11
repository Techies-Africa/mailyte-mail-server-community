"""Per-credential tracking toggle for SMTP relay senders.

SMTP-Send phase-02 (plans/07-smtp-send/phase-02-compliance.md). A campaign
tool relaying through an SMTP credential usually rewrites its own links for
click tracking; our injector then wraps those again, and every click bounces
through two redirectors. tracking_enabled=0 lets one credential opt out of
pixel/link injection (and with it our List-Unsubscribe injection -- the AUP
then requires the tool to carry its own header) while suppression checks,
delivery pacing, and rate limits still apply. Default 1: existing credentials
keep today's behavior.

The injector learns the credential from ${sasl_username} on the pipe argv
(master.cf) and reads this column with its usual cached PyMySQL lookup.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_smtp_cred_tracking"
down_revision: Union[str, None] = "0025_org_sending_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_credentials",
        sa.Column(
            "tracking_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="0 = injector skips pixel/link/List-Unsubscribe injection for this credential's mail",
        ),
    )


def downgrade() -> None:
    op.drop_column("smtp_credentials", "tracking_enabled")
