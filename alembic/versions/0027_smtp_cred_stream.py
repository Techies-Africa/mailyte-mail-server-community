"""Per-credential egress stream for SMTP relay senders.

SMTP-Send (2026-09-08). A marketing-flagged credential's mail rides the
marketing egress IP with no cooperation from the submitting tool: the
tracking injector reads this column (same cached ${sasl_username} lookup as
tracking_enabled) and inserts X-Mailyte-Stream: marketing at reinjection,
which the cleanup_stream FILTER routes out the marketing transport. Default
'transactional': existing credentials keep today's lane, and every lookup
failure in the injector also reads as transactional -- the lane that always
delivers.

Rebuild the migrate image before running this -- it goes stale and silently
no-ops on old code.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_smtp_cred_stream"
down_revision: Union[str, None] = "0026_smtp_cred_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_credentials",
        sa.Column(
            "stream",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'transactional'"),
            comment="'marketing' = injector stamps X-Mailyte-Stream so this credential's mail egresses the marketing IP",
        ),
    )


def downgrade() -> None:
    op.drop_column("smtp_credentials", "stream")
