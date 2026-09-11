"""mailbox_preferences.signature_html -- TEXT becomes MEDIUMTEXT

Revision ID: 0020_signature_mediumtext
Revises: 0019_api_key_hash_only
Create Date: 2026-08-31

A signature with a picture in it is stored as HTML with the picture embedded
as a base64 data: URI (worker/api/utils/signature_html.py allows exactly that
shape on <img src>; on send, shared/imap_mail.build_message turns it into a
Content-ID part). Base64 is 4/3 the size of the image, so a 600px banner card
-- the ordinary case, not an extreme one -- is 100-300 KB, and TEXT holds
64 KB. MySQL truncates silently on insert in non-strict mode and refuses in
strict mode; neither is a stored signature.

MEDIUMTEXT holds 16 MB. The API caps a signature at 2 MB well before the
column is the limit (MAX_SIGNATURE_BYTES).

Rebuild the migrate image before running this (it goes stale and silently
no-ops on old code).
"""

from sqlalchemy.dialects import mysql

from alembic import op

revision = "0020_signature_mediumtext"
down_revision = "0019_api_key_hash_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "mailbox_preferences",
        "signature_html",
        existing_type=mysql.TEXT(),
        type_=mysql.MEDIUMTEXT(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing back to TEXT truncates any signature over 64 KB. Anything the
    # 0020-era API accepted and stored could be lost here, which is the
    # nature of a downgrade past a widening; it is not silent -- MySQL in
    # strict mode refuses the ALTER if a row would not fit.
    op.alter_column(
        "mailbox_preferences",
        "signature_html",
        existing_type=mysql.MEDIUMTEXT(),
        type_=mysql.TEXT(),
        existing_nullable=True,
    )
