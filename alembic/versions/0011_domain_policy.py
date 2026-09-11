"""Create domain_policy

Revision ID: 0011_domain_policy
Revises: 0010_alert_rules_channels_events
Create Date: 2026-08-20

routes/domains.py has always read and written a `domain_policy` table that no
migration has ever created. GET /api/v1/domains/get/domain/policy/{domain}
therefore answered 500 with "Table 'mailserver.domain_policy' doesn't exist" on
every call, and the matching edit endpoint failed the same way -- so per-domain
spam policy has never been usable through the API.

Columns are taken from the queries themselves rather than invented:

    SELECT dp.policy_bl_only, dp.policy_reject_spam,
           dp.policy_greylist, dp.policy_rbl
      FROM domains d LEFT JOIN domain_policy dp ON d.domain = dp.domain

    INSERT INTO domain_policy (domain, policy_bl_only, policy_reject_spam,
                               policy_greylist, policy_rbl, created, modified)
    ... ON DUPLICATE KEY UPDATE ...

`domain` is the primary key, which is what makes ON DUPLICATE KEY UPDATE
resolve to an upsert on that column; without a unique constraint there the
edit endpoint would silently insert a duplicate row on every save and the read
would then return whichever the optimiser happened to reach first.

VARCHAR(255) matches domains.domain exactly (conventions §4: FK types must
match). The FK cascades on delete -- a policy for a domain that no longer
exists is unreachable by every query above, since all of them join through
domains.

`created`/`modified` keep the names the INSERT already uses rather than the
created_at/updated_at convention used elsewhere; renaming them would mean
editing working SQL to match a new table, which is the wrong way round.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0011_domain_policy'
down_revision: Union[str, None] = '0010_alert_rules_channels_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'domain_policy',
        sa.Column('domain', sa.String(255), primary_key=True, nullable=False),
        # Postfix/rspamd policy switches. Stored as the tinyint(1) MySQL gives
        # a BOOLEAN, which is what the existing INSERT binds.
        sa.Column('policy_bl_only', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('policy_reject_spam', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('policy_greylist', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('policy_rbl', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('modified', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['domain'], ['domains.domain'],
            name='fk_domain_policy_domain',
            ondelete='CASCADE',
        ),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )


def downgrade() -> None:
    op.drop_table('domain_policy')
