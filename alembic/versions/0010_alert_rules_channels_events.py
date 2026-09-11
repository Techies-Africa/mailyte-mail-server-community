"""Add alert_rules, alert_channels and alert_events (Console PRD SS12 gap #6)

Revision ID: 0010_alert_rules_channels_events
Revises: 0009_quota_override
Create Date: 2026-08-20 00:00:10.000000

The console's Alerts screen needs three things this schema did not have:
a rule definition, a notification channel, and a history of firings.

None of them is the existing `alerts` table. `alerts` (0001_baseline) is a
*fired rate-limit/storage-quota notice* written by the rate_limiter and
storage_usage workers -- its columns are alert_type/alert_level/
current_usage/limit_value/usage_percentage/webhook_sent/resolved, keyed to
an organization/domain/email_account. It has no concept of a rule, no
comparator, no threshold expressed in the console's terms, no channel, and
no severity vocabulary in common with it (warning|critical|exceeded, not
critical|warning|info). Overloading it would mean two unrelated producers
writing incompatible rows into one table; these are additive new tables and
`alerts` is left exactly as it is.

Design notes:

* `metric`, `comparator` and `severity` are VARCHAR validated in the API
  against a server-side allowlist, not DB ENUMs -- same call migration 0006
  made for operator roles, and for the same reason: adding a metric must
  not require a migration and an ALTER on a live table.
* `alert_rules.channel_ids` is a JSON array of alert_channels.id rather
  than a join table. The API validates every id exists on write, so the
  integrity a join table would buy is enforced at the only place that
  writes the column, and the read path stays a single row fetch (the same
  trade `smtp_credentials.allowed_ips` already makes in 0008).
* `alert_channels` stores a webhook signing secret ENCRYPTED, never hashed:
  signing an outbound request needs the plaintext back, so a one-way hash
  cannot work here. Envelope encryption via shared/envelope_encryption.py
  (AES-256-GCM under the mounted KEK) -- the identical three-column layout
  0003_encrypt_private_keys gave dkim_keys/pgp_keys/smime_certs. The
  plaintext is never returned by any endpoint.
* `alert_events.rule_name`/`severity`/`threshold` are denormalised on
  purpose. History has to outlive the rule that produced it: after DELETE
  /alerts/rules/{id} the FK nulls out but the incident record still has to
  say what fired and at what threshold, otherwise deleting a rule quietly
  rewrites the past.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '0010_alert_rules_channels_events'
down_revision: Union[str, None] = '0009_quota_override'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# organizations.id is `char(26) COLLATE utf8mb4_unicode_ci` in the baseline
# (verified against 0001_baseline, not assumed from conventions SS4 rule 6,
# which is stale for ULID-converted tables). Conventions SS4 rule 3: an FK
# column's type AND collation must match its target exactly or MySQL raises
# 3780 at CREATE time, so the collation is pinned per column here rather
# than left to the table default.
_ORG_ID = sa.CHAR(26, collation='utf8mb4_unicode_ci')
_ULID = sa.CHAR(26, collation='utf8mb4_unicode_ci')


def upgrade() -> None:
    op.create_table(
        'alert_channels',
        sa.Column('id', _ULID, nullable=False),
        sa.Column('name', sa.String(255, collation='utf8mb4_unicode_ci'), nullable=False),
        sa.Column('type', sa.String(20, collation='utf8mb4_unicode_ci'), nullable=False,
                  comment="email | webhook -- validated in the API, PRD SS17 v1 scope"),
        sa.Column('target', sa.String(1000, collation='utf8mb4_unicode_ci'), nullable=False,
                  comment='Email address for type=email, absolute https URL for type=webhook'),
        sa.Column('enabled', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        # Envelope-encrypted webhook signing secret. NULL for email channels
        # and for webhook channels created without one. Never SELECTed by a
        # read endpoint -- only by the fire path, which needs the plaintext
        # to compute the HMAC.
        sa.Column('signing_secret_ciphertext', sa.LargeBinary(), nullable=True),
        sa.Column('signing_secret_nonce', sa.LargeBinary(length=12), nullable=True),
        sa.Column('signing_secret_key_version', sa.Integer(), nullable=False,
                  server_default=sa.text('1')),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(collation='utf8mb4_unicode_ci'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_alert_channels_type', 'alert_channels', ['type'])
    op.create_index('idx_alert_channels_enabled', 'alert_channels', ['enabled'])

    op.create_table(
        'alert_rules',
        sa.Column('id', _ULID, nullable=False),
        sa.Column('name', sa.String(255, collation='utf8mb4_unicode_ci'), nullable=False),
        sa.Column('description', sa.Text(collation='utf8mb4_unicode_ci'), nullable=True),
        sa.Column('metric', sa.String(64, collation='utf8mb4_unicode_ci'), nullable=False,
                  comment='Allowlisted in routes/platform.py _ALERT_METRICS -- never free text'),
        sa.Column('comparator', sa.String(2, collation='utf8mb4_unicode_ci'), nullable=False,
                  comment='> >= < <= =='),
        sa.Column('threshold', mysql.DOUBLE(), nullable=False),
        # 0 means "fire on the first evaluation that breaches". Anything
        # larger is the dwell time the evaluator must see the breach hold
        # for before firing; stored here, honoured by whatever runs the
        # rules on a schedule.
        sa.Column('for_seconds', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('severity', sa.String(20, collation='utf8mb4_unicode_ci'), nullable=False,
                  comment='critical | warning | info'),
        sa.Column('enabled', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        sa.Column('channel_ids', sa.JSON(), nullable=True,
                  comment='JSON array of alert_channels.id'),
        sa.Column('organization_id', _ORG_ID, nullable=True,
                  comment='NULL = platform-wide rule; set = scoped to one tenant'),
        sa.Column('last_fired_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                name='fk_alert_rules_org', ondelete='CASCADE'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_alert_rules_enabled', 'alert_rules', ['enabled'])
    op.create_index('idx_alert_rules_metric', 'alert_rules', ['metric'])
    op.create_index('idx_alert_rules_org', 'alert_rules', ['organization_id'])

    op.create_table(
        'alert_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        # SET NULL, not CASCADE: deleting a rule must not delete the record
        # of what it did. rule_name below keeps the row readable afterwards.
        sa.Column('rule_id', _ULID, nullable=True),
        sa.Column('rule_name', sa.String(255, collation='utf8mb4_unicode_ci'), nullable=False),
        sa.Column('severity', sa.String(20, collation='utf8mb4_unicode_ci'), nullable=False),
        sa.Column('value', mysql.DOUBLE(), nullable=True,
                  comment='Observed metric value; NULL when the metric was unavailable'),
        sa.Column('threshold', mysql.DOUBLE(), nullable=False),
        sa.Column('state', sa.String(20, collation='utf8mb4_unicode_ci'), nullable=False,
                  comment='firing | resolved'),
        sa.Column('message', sa.Text(collation='utf8mb4_unicode_ci'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['rule_id'], ['alert_rules.id'],
                                name='fk_alert_events_rule', ondelete='SET NULL'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_alert_events_rule', 'alert_events', ['rule_id', 'created_at'])
    op.create_index('idx_alert_events_state', 'alert_events', ['state', 'created_at'])
    op.create_index('idx_alert_events_severity', 'alert_events', ['severity', 'created_at'])
    op.create_index('idx_alert_events_created', 'alert_events', ['created_at'])


def downgrade() -> None:
    # Reverse creation order: alert_events FKs alert_rules, so it goes
    # first. All three tables are new in this revision and nothing outside
    # it writes them, so dropping them is a complete and safe undo.
    op.drop_table('alert_events')
    op.drop_table('alert_rules')
    op.drop_table('alert_channels')
