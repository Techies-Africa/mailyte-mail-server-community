"""Add organizations.sending_profile for outbound stream separation.

SMTP-Send phase-01 (plans/07-smtp-send/phase-01-protection.md). Postfix's
sender-dependent transport map (mysql-marketing-transport.cf) routes mail from
organizations classed 'marketing' out the marketing transport -- a separate
source IP -- so a marketing complaint spike can never blacklist the IP that
carries mailbox and transactional traffic. The column is the single routing
key; Laravel becomes the writer of record for it in phase-04 (synced through
the existing org sync), and until then every org keeps the default and nothing
routes differently.

'transactional' (default) and 'marketing' are the only values the map ever
matches on. 'both' is reserved for phase-04 and deliberately routes as
transactional until per-credential stream classing exists -- routing an org's
entire traffic to the marketing IP because one credential does campaigns would
put their password resets on the bulk stream, the exact mistake this column
exists to prevent.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_org_sending_profile"
down_revision: Union[str, None] = "0024_mailbox_ai_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "sending_profile",
            sa.String(20),
            nullable=False,
            server_default="transactional",
            comment="Outbound stream class: transactional|marketing|both (both routes as transactional until phase-04)",
        ),
    )
    # Postfix resolves the transport per message via this predicate; without
    # the index every outbound MAIL FROM costs a scan on organizations.
    op.create_index(
        "idx_org_sending_profile", "organizations", ["sending_profile"]
    )


def downgrade() -> None:
    op.drop_index("idx_org_sending_profile", table_name="organizations")
    op.drop_column("organizations", "sending_profile")
