"""Create email_bodies

Revision ID: 0012_email_bodies
Revises: 0011_domain_policy
Create Date: 2026-08-22

The Email Logs detail view renders `content.html` / `content.text`, matching
what Mailgun, Postmark and Brevo all show for a sent message. Nothing could
ever populate them: mail_logs is built from Postfix's log, and Postfix logs the
envelope only -- never the body. The subject was recoverable via a header_checks
WARN rule, but a body cannot be, so it has to be captured while the message is
still in the mail path.

tracking_injector.py is the one place that already has the full parsed MIME
message (it walks the parts to inject tracking pixels), so capture happens there
and lands here, keyed by Message-ID. log_ingestor then joins this table when it
emits the delivery webhook, so the body reaches mailyte-api's
delivery_events.payload where EmailLogController::show already looks for it.

Deliberately NOT storing attachments -- only the rendered text/plain and
text/html parts. That matches what the providers above expose and keeps this
table from becoming a second, unmanaged copy of every file anyone ever mailed.
Bodies are truncated at capture time; the columns are MEDIUMTEXT (16MB) so the
cap is a policy decision in the injector rather than a schema limit.

`expires_at` exists because message bodies are the most sensitive thing this
system stores and they should not accumulate forever. log_ingestor prunes on
it, so retention is enforced by a process that is already running rather than
by a cron nobody remembers to add.

message_id is the join key and is indexed but NOT unique: Postfix can legitimately
process the same Message-ID more than once (a resend, or a message fanned out to
several recipients through separate filter invocations), and a unique constraint
would turn that into a hard failure inside a mail-path script.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '0012_email_bodies'
down_revision: Union[str, None] = '0011_domain_policy'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_bodies',
        sa.Column('id', sa.String(26), primary_key=True, nullable=False),
        sa.Column('message_id', sa.String(255), nullable=False),
        sa.Column('organization_id', sa.String(100), nullable=True),
        sa.Column('sender', sa.String(255), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        # MEDIUMTEXT rather than TEXT: TEXT caps at 64KB, which a fairly
        # ordinary marketing HTML email exceeds, and MySQL would silently
        # truncate mid-tag rather than error.
        sa.Column('html', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('text', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('has_attachments', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('captured_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_email_bodies_message_id', 'email_bodies', ['message_id'])
    op.create_index('idx_email_bodies_expires_at', 'email_bodies', ['expires_at'])


def downgrade() -> None:
    op.drop_index('idx_email_bodies_expires_at', table_name='email_bodies')
    op.drop_index('idx_email_bodies_message_id', table_name='email_bodies')
    op.drop_table('email_bodies')
