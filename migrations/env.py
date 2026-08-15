"""
Alembic environment. Wired directly to the app's own settings and
SQLAlchemy models so migrations never fall out of sync with
app/models/orm.py — there is exactly one source of truth for the schema.

Supports mssql and postgres (the two dialects SQLRepository targets; see
app/db/sql_repository.py). mysql also works through the same SQLAlchemy
engine but is not exercised by the project's own tests.

Usage:
    # generate a new migration from ORM changes
    alembic revision --autogenerate -m "add xyz column"

    # apply migrations
    alembic upgrade head

    # point at a specific database instead of DATABASE_URL from .env
    DATABASE_URL="postgresql+psycopg2://..." alembic upgrade head
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app.*` importable when alembic is invoked from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.models.orm import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """
    DATABASE_URL env var wins (lets CI / migration runs target a database
    other than whatever a developer's local .env points at); otherwise fall
    back to the app's own Settings, so `alembic upgrade head` targets the
    same database the API would connect to.
    """
    return os.environ.get("DATABASE_URL") or get_settings().DATABASE_URL


def include_object(object_, name, type_, reflected, compare_to):
    """
    Mongo/Neo4j are handled by their own repository classes (see
    app/db/mongo_repository.py, app/db/neo4j_repository.py) and never go
    through SQLAlchemy/Alembic — nothing to exclude here today, but this is
    the hook to use if a future table should be managed outside migrations.
    """
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`alembic upgrade head --sql`)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
            # SQLite needs batch mode to ALTER tables (no native ALTER
            # support); mssql/postgres/mysql all support ALTER directly, so
            # batch mode would just add unnecessary temp-table churn there.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
