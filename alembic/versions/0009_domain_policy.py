"""Create domain_policy

Revision ID: 0009_domain_policy
Revises: 0008_api_key_rate_limit_service
Create Date: 2026-08-20

Ported from mailyte-email-server's 0011_domain_policy; 0009 here because
CE's migration chain is its own.

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
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Plain assignments, no PEP 604 annotations: the migrate image runs Python
# 3.9, where `str | None` at module level raises TypeError -- this file's
# original annotations meant NO CE migration after 0008 could ever run
# (found while applying 0010, 00-PRD-smtp-api-keys K6).
revision = '0009_domain_policy'
down_revision = '0008_api_key_rate_limit_service'
branch_labels = None
depends_on = None


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
