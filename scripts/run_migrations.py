#!/usr/bin/env python3
"""Migration entrypoint for the `migrate` compose service (phase-08 task 8.3).

`alembic upgrade head` alone isn't safe to run unconditionally, because two
databases that are schema-identical can disagree about what alembic_version
says:

  1. CE's first-boot path (and this repo's own dev volumes, created before
     this phase) run database/migrations/sql/*.sql via MySQL's
     docker-entrypoint-initdb.d. That produces the exact schema
     0001_baseline describes, but alembic_version either doesn't exist or
     -- on any DB touched by 009_ulid_safe.sql -- holds a stray marker
     ('009_ulid_primary_keys') that isn't a real Alembic revision at all.
  2. A database already tracked correctly by a previous `migrate` run.

Replaying 0001_baseline's CREATE TABLE statements against case (1) fails
with "table already exists". The fix is to detect "schema present,
revision untracked or unrecognized" and `alembic stamp` the matching
revision instead of executing it -- then `upgrade head` proceeds normally
from there (case 2 is already a no-op at this point).

"Which revision matches" depends on how far case (1) got before this ran:
initdb.d only ever produces the 0001_baseline schema (70 tables) -- the two
tables 0002_adhoc_table_tracking formalizes are created by cert_manager and
delivery_optimizer at their own startup, which now happens *after* this
script (they depend_on migrate: service_completed_successfully). So a
freshly initdb.d'd volume stamps to 0001_baseline and then genuinely
upgrades through 0002. A database that already has those two tables (any
instance that was running before this phase shipped) stamps straight to
0002 -- replaying it would fail the same "already exists" way.
"""

import logging
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from alembic import command

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s"
)
logger = logging.getLogger("run_migrations")

# Tables that exist once the frozen SQL chain has run, but before any
# Alembic revision has ever been applied -- used to tell "empty database"
# apart from "schema already here, just untracked".
_BASELINE_SENTINEL_TABLE = "organizations"
_ADHOC_SENTINEL_TABLES = {"acme_account_stats", "suppression_list"}


def get_database_url() -> str:
    host = os.getenv("DB_HOST", "mysql")
    port = int(os.getenv("DB_PORT", 3306))
    database = os.getenv("DB_NAME", "mailserver")
    user = os.getenv("DB_USER", "mailuser")
    password = os.getenv("DB_PASSWORD", "")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def main() -> None:
    url = get_database_url()
    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)

    script = ScriptDirectory.from_config(alembic_cfg)
    known_revisions = {r.revision for r in script.walk_revisions()}

    engine = create_engine(url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    engine.dispose()

    if _BASELINE_SENTINEL_TABLE not in tables:
        logger.info("Empty database -- 'alembic upgrade head' will create the schema from scratch.")
    else:
        current = None
        if "alembic_version" in tables:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
                current = row[0] if row else None

        if current in known_revisions:
            logger.info(
                f"Schema already tracked at revision {current!r} -- proceeding to upgrade normally."
            )
        else:
            stamp_target = (
                "0002_adhoc_table_tracking" if tables >= _ADHOC_SENTINEL_TABLES else "0001_baseline"
            )
            logger.info(
                f"Schema present but alembic_version is {current!r} (untracked or a stale non-Alembic "
                f"marker) -- stamping {stamp_target!r} instead of replaying its CREATE TABLE statements."
            )
            # Not `alembic stamp`: it first calls get_current_heads(), which
            # tries to resolve `current` (e.g. 009_ulid_safe.sql's leftover
            # marker row, or any other value no revision file defines)
            # against the script directory and raises before purge ever
            # gets a chance to clear it. Writing the row directly is exactly
            # what conventions.md SS4 rule 9 prescribes for this table:
            # DELETE then INSERT, never UPDATE (it has no PK to key an
            # UPDATE off when the existing value doesn't matter).
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS alembic_version ("
                        "version_num VARCHAR(32) NOT NULL, PRIMARY KEY (version_num)"
                        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                    )
                )
                conn.execute(text("DELETE FROM alembic_version"))
                conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                    {"v": stamp_target},
                )

    command.upgrade(alembic_cfg, "head")
    logger.info("Migrations complete -- database is at head.")


if __name__ == "__main__":
    main()
