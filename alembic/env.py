"""Alembic environment.

The database URL is sourced from application settings (``DATABASE_URL`` in
``.env``) rather than ``alembic.ini`` so credentials are never committed. An
override URL may still be passed on the command line via
``alembic -x db_url=postgresql://...`` (used to autogenerate the baseline
against a throwaway empty database).
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the application package importable when Alembic runs from the repo root.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db.db_schema import Base  # noqa: E402  (import after sys.path setup)
from src.db.settings import settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
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
