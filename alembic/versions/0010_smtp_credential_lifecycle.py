"""SMTP credential lifecycle -- expiry, per-key limits, metadata, audit events

Revision ID: 0010_smtp_credential_lifecycle
Revises: 0009_domain_policy
Create Date: 2026-08-27 (CE parity port of EE 0017 -- 00-PRD-smtp-api-keys K6)

Part of 03-mailyte-api/00-PRD-smtp-api-keys Phase K1. The email server
becomes the authoritative store for the whole key lifecycle (the console
consumes it directly -- CE deployments have no Laravel), so the columns
Laravel held alone since phase-09 move here, where enforcement happens:

* expires_at        -- checked in the Dovecot passdb query itself; an
                       expired key stops authenticating with no scheduler
                       involved.
* ip_allowlist_enabled -- gates whether allowed_ips (0008) is emitted as a
                       Dovecot `allow_nets` extra field. Separate from the
                       list so a tenant can stage IPs before turning
                       enforcement on.
* hourly_limit / daily_limit -- per-key outbound caps, NULL = inherit the
                       org's limits (enforced in K2's rate limiter work).
* last_used_at      -- maintained by the log ingestor (K2), never by the
                       auth path: a passdb UPDATE per AUTH would serialize
                       logins on row locks.
* name / prefix / created_by -- display metadata. Lives here rather than in
                       Laravel because the console has no Laravel to ask.

smtp_credential_events is the audit trail (PRD section 6.7). Deliberately
FK-free: audit rows must survive the deletion of the credential -- and of
the organization -- they describe, so the ids are plain CHAR(26) columns
and `username` is denormalized for display after the credential is gone.

String/char columns pin utf8mb4_unicode_ci explicitly -- this schema does
not use the database default collation, and 0003 documents the two distinct
errors (3780 on FKs, illegal-mix on UNIONs) that omitting it produces (see EE 0008).
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_smtp_credential_lifecycle"
down_revision = "0009_domain_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_credentials",
        sa.Column("name", sa.String(255, collation="utf8mb4_unicode_ci"), nullable=True),
    )
    op.add_column(
        "smtp_credentials",
        sa.Column("prefix", sa.String(16, collation="utf8mb4_unicode_ci"), nullable=True),
    )
    op.add_column(
        "smtp_credentials",
        sa.Column("created_by", sa.String(255, collation="utf8mb4_unicode_ci"), nullable=True),
    )
    op.add_column(
        "smtp_credentials",
        sa.Column(
            "ip_allowlist_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("smtp_credentials", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.add_column("smtp_credentials", sa.Column("hourly_limit", sa.Integer(), nullable=True))
    op.add_column("smtp_credentials", sa.Column("daily_limit", sa.Integer(), nullable=True))
    op.add_column("smtp_credentials", sa.Column("last_used_at", sa.DateTime(), nullable=True))

    op.create_table(
        "smtp_credential_events",
        sa.Column("id", sa.CHAR(26, collation="utf8mb4_unicode_ci"), primary_key=True),
        sa.Column(
            "credential_id",
            sa.CHAR(26, collation="utf8mb4_unicode_ci"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            # String(100), not CHAR(26): CE organization ids are VARCHAR(100)
            # values (no ULID conversion here, unlike EE).
            "organization_id",
            sa.String(100, collation="utf8mb4_unicode_ci"),
            nullable=False,
            index=True,
        ),
        sa.Column("username", sa.String(255, collation="utf8mb4_unicode_ci"), nullable=False),
        sa.Column("event", sa.String(32, collation="utf8mb4_unicode_ci"), nullable=False),
        sa.Column("actor", sa.String(255, collation="utf8mb4_unicode_ci"), nullable=True),
        sa.Column("source_ip", sa.String(45, collation="utf8mb4_unicode_ci"), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade() -> None:
    op.drop_table("smtp_credential_events")
    op.drop_column("smtp_credentials", "last_used_at")
    op.drop_column("smtp_credentials", "daily_limit")
    op.drop_column("smtp_credentials", "hourly_limit")
    op.drop_column("smtp_credentials", "expires_at")
    op.drop_column("smtp_credentials", "ip_allowlist_enabled")
    op.drop_column("smtp_credentials", "created_by")
    op.drop_column("smtp_credentials", "prefix")
    op.drop_column("smtp_credentials", "name")
