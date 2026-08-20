"""Add explicit platform/organization scope to api_keys (ADR-002 §2)

Revision ID: 0004_api_key_platform_scope
Revises: 0003_smtp_credentials
Create Date: 2026-08-20 00:00:04.000000

Ported from mailyte-email-server's phase-06 build (its own revision id was
0005_api_key_platform_scope -- the number differs because CE's chain is
shorter, the schema is the same). CE needs this because the Mailyte Console
(ADR-004) runs against a CE instance, and ADR-004's "one thing that must be
true first" is that the privilege boundary exists on the instance being
administered.

Nothing in the schema can express "this credential is platform staff, not a
tenant" today: api_keys.organization_id is nullable, so an accidental NULL
would silently create a superuser (every org-scoped query would have nothing
to filter on). This adds an explicit `scope` column instead of overloading
NULL, plus the CHECK constraint from ADR-002 §2 so the two columns can never
disagree -- 'organization' scope requires an organization_id, 'platform'
scope forbids one.

`DEFAULT 'organization'` is deliberate: a forgotten scope on a future INSERT
yields the *least* privilege, not the most.

The backfill runs before the constraint is added rather than relying on the
column default alone. On a CE install that already has api_keys rows with a
NULL organization_id, that row would fail the CHECK -- so it is classified
as 'platform' first (its effective behaviour today, since there is no org to
scope it to) and every other row as 'organization'. ADR-002 §2's instruction
is "classify them before adding the constraint"; unlike EE, CE cannot verify
a single live database, so the classification is done in SQL for whatever the
self-hoster's data happens to be.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004_api_key_platform_scope'
down_revision: Union[str, None] = '0003_smtp_credentials'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'api_keys',
        sa.Column(
            'scope',
            sa.Enum('platform', 'organization', name='api_key_scope'),
            nullable=False,
            server_default='organization',
        ),
    )
    op.execute("UPDATE api_keys SET scope = 'organization' WHERE organization_id IS NOT NULL")
    op.execute("UPDATE api_keys SET scope = 'platform' WHERE organization_id IS NULL")
    op.create_check_constraint(
        'chk_api_key_scope',
        'api_keys',
        "(scope = 'organization' AND organization_id IS NOT NULL) OR "
        "(scope = 'platform' AND organization_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint('chk_api_key_scope', 'api_keys', type_='check')
    op.drop_column('api_keys', 'scope')
