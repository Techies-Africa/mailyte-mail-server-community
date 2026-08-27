"""mail_logs.sasl_username -- attribute delivered mail to the SMTP API key that sent it

Revision ID: 0011_mail_logs_sasl_username
Revises: 0010_smtp_credential_lifecycle
Create Date: 2026-08-27

Part of 03-mailyte-api/00-PRD-smtp-api-keys Phase K2. Postfix logs the
authenticated SASL identity on the smtpd `client=` line for the same queue
id the delivery lines carry; the log ingestor now remembers it per qid and
writes it here, which is what makes "messages sent via this key" (the
per-credential usage endpoint) answerable at all -- before this column no
mail_logs row could be tied to a credential.

Indexed because the usage endpoint filters on (sasl_username, timestamp).
Collation pinned for the same reason as 0003/0010.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_mail_logs_sasl_username"
down_revision = "0010_smtp_credential_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_logs",
        sa.Column("sasl_username", sa.String(255, collation="utf8mb4_unicode_ci"), nullable=True),
    )
    op.create_index(
        "idx_mail_logs_sasl_time", "mail_logs", ["sasl_username", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("idx_mail_logs_sasl_time", table_name="mail_logs")
    op.drop_column("mail_logs", "sasl_username")
