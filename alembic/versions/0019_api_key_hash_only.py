"""api_keys -- stop storing the raw key; auth by key_hash only

Revision ID: 0019_api_key_hash_only
Revises: 0018_mail_logs_sasl_username
Create Date: 2026-08-30

Before this, key_id held the raw presented key and auth matched on it
directly (worker/api/utils/auth.py, worker/jmap/app.py) -- the key_hash
written at creation was never read, so any read access to the database
was credential theft. Auth now matches on SHA-256(presented key) against
key_hash; key_id becomes a non-secret display identifier.

Two data steps, in this order, both idempotent:

1. Backfill key_hash from key_id for rows where it is missing/empty
   (rows inserted by hand; bootstrap always wrote a correct hash).
2. Redact key_id -> CONCAT(LEFT(key_id, 8), '_', id) for exactly the
   rows where key_hash = SHA2(key_id, 256). That equality holds iff
   key_id still IS the raw key, so rows already written in the new
   format (display id + real hash) can never be touched, and a re-run
   is a no-op. The row ULID in the suffix keeps the unique constraint
   on key_id satisfied.

The redaction is deliberately irreversible -- raw keys are gone after
this runs. downgrade() only drops the lookup index; it does not (and
cannot) restore raw keys, and pre-0019 code cannot authenticate
redacted rows. Roll forward, not back.

Deploy-order note: the new auth code falls back to the legacy key_id
match until this migration runs, so code-first or migration-first both
work. Rebuild the migrate image before running this (it goes stale and
silently no-ops on old code).
"""

from alembic import op

revision = "0019_api_key_hash_only"
down_revision = "0018_mail_logs_sasl_username"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE api_keys
        SET key_hash = SHA2(key_id, 256)
        WHERE key_hash IS NULL OR key_hash = ''
        """
    )
    op.execute(
        """
        UPDATE api_keys
        SET key_id = CONCAT(LEFT(key_id, 8), '_', id)
        WHERE key_hash = SHA2(key_id, 256)
        """
    )
    op.create_index("idx_api_keys_key_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_index("idx_api_keys_key_hash", table_name="api_keys")
