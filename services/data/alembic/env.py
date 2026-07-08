"""Alembic environment.

The database URL is sourced from application settings (``DATABASE_URL`` in
``.env``) rather than ``alembic.ini`` so credentials are never committed. An
override URL may still be passed on the command line via
``alembic -x db_url=postgresql://...`` (used to autogenerate the baseline
against a throwaway empty database).
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# senedd_data is installed (editable) in the workspace venv — no sys.path setup.
from senedd_data.db_schema import Base
from senedd_data.settings import settings

config = context.config

# Only reconfigure logging from alembic.ini when invoked as the Alembic CLI.
# When the pipeline runs migrations programmatically it sets
# attributes["configure_logger"] = False so the app's logging is left intact.
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the URL: command-line ``-x db_url=...`` override, else settings."""
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    return override or settings.database_url


def run_migrations_offline() -> None:
    """Emit migrations as SQL without a live connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
