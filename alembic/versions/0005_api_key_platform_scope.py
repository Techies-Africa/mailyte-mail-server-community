"""Add explicit platform/organization scope to api_keys (ADR-002 SS2, phase-06 task 6.1)

Revision ID: 0005_api_key_platform_scope
Revises: 0004_api_key_rate_limit_service
Create Date: 2026-07-30 00:00:05.000000

Nothing in the system can express "this credential is platform staff, not a
tenant" today -- api_keys.organization_id is nullable, and deep-audit.md
SS3.4 already flagged that an accidental NULL there would silently create a
superuser (every org-scoped query would see nothing to filter on). This adds
an explicit `scope` column instead of overloading NULL, and a CHECK
constraint so the two columns can never disagree: 'organization' scope
requires an organization_id, 'platform' scope forbids one.

Verified live before writing this migration (ADR-002's own instruction --
"classify them before adding the constraint"): `SELECT COUNT(*) FROM
api_keys WHERE organization_id IS NULL` is 0 on this database. There are no
existing latent-superuser rows to classify; the CHECK constraint is safe to
add directly. `DEFAULT 'organization'` is deliberate (phase-06 task 6.1): a
forgotten scope on a future INSERT yields the *least* privilege, not the most.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005_api_key_platform_scope'
down_revision: Union[str, None] = '0004_api_key_rate_limit_service'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'api_keys',
        sa.Column(
            'scope', sa.Enum('platform', 'organization', name='api_key_scope'),
            nullable=False, server_default='organization',
        ),
    )
    # Backfill is a no-op today (every existing row already has a non-NULL
    # organization_id, hence the column default already matches) but kept
    # explicit rather than relying on the DEFAULT alone, per ADR-002 SS2's
    # own migration sketch.
    op.execute("UPDATE api_keys SET scope = 'organization' WHERE organization_id IS NOT NULL")
    op.create_check_constraint(
        'chk_api_key_scope',
        'api_keys',
        "(scope = 'organization' AND organization_id IS NOT NULL) OR "
        "(scope = 'platform' AND organization_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint('chk_api_key_scope', 'api_keys', type_='check')
    op.drop_column('api_keys', 'scope')
