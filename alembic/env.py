import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text
from alembic import context

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import our models
from database.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


# Get database URL from environment variables
def get_database_url():
    """Get database URL from environment variables"""
    db_config = {
        "host": os.getenv("DB_HOST", "mysql"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "database": os.getenv("DB_NAME", "mailserver"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", "password"),
    }
    return f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['database']}?charset=utf8mb4"


# Set the sqlalchemy URL
config.set_main_option("sqlalchemy.url", get_database_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Support for MySQL
    )

    with context.begin_transaction():
        context.run_migrations()


# Advisory lock name (phase-08 task 8.3): prevents two `migrate` containers
# -- e.g. a second replica started mid-deploy -- from racing to apply the
# same revision concurrently. MySQL's GET_LOCK/RELEASE_LOCK are scoped to
# the connection that acquired them, so both calls must run on the same
# connection that then runs the migration.
_MIGRATION_LOCK_NAME = "mailyte_schema_migration"
_MIGRATION_LOCK_TIMEOUT_SECONDS = 60


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # The lock connection is deliberately separate from the one handed to
    # Alembic below. GET_LOCK/RELEASE_LOCK must pair on one session, but
    # that session doesn't need to be the migration's own connection --
    # and sharing it caused a real bug here: SQLAlchemy 2.x autobegins a
    # transaction on that connection's first execute() (the GET_LOCK call
    # itself), and MySQL's "non-transactional DDL" migration context never
    # explicitly commits it, so alembic_version's own bookkeeping row --
    # written as ordinary DML on the same connection -- was silently rolled
    # back on close even though every CREATE TABLE in the same run had
    # already taken effect (DDL auto-commits in MySQL; that row's DML
    # didn't). Verified live: a 2-revision upgrade left every table from
    # both revisions in place but alembic_version stuck on the first one.
    lock_connection = connectable.connect()
    got_lock = lock_connection.execute(
        text("SELECT GET_LOCK(:name, :timeout)"),
        {"name": _MIGRATION_LOCK_NAME, "timeout": _MIGRATION_LOCK_TIMEOUT_SECONDS},
    ).scalar()
    lock_connection.commit()
    if not got_lock:
        lock_connection.close()
        raise RuntimeError(
            f"Could not acquire migration lock '{_MIGRATION_LOCK_NAME}' within "
            f"{_MIGRATION_LOCK_TIMEOUT_SECONDS}s -- another migration run is in progress"
        )
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,  # Support for MySQL
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        lock_connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _MIGRATION_LOCK_NAME})
        lock_connection.commit()
        lock_connection.close()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
