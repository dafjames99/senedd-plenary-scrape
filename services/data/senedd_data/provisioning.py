"""Database provisioning: schema (DDL) lifecycle, owned by Alembic.

Separated from the data-lifecycle pipelines so both the acquisition and
transformation stages can bring a database up to head without dragging in each
other's ingestion/transformation logic. Alembic owns all DDL; this module only
drives ``alembic upgrade head`` and registers the repo-tracked SQL procedures.
"""
import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy_utils import create_database, database_exists

from senedd_data.session import get_engine

logger = logging.getLogger(__name__)

# Data-service root (owns alembic.ini + alembic/): provisioning.py -> parents[1]
ROOT_DIR = Path(__file__).resolve().parents[1]


class Provisioner:
    """Provision a database: ensure it exists, migrate to head, register procedures."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.engine = get_engine(db_url)

    def ensure_database_exists(self):
        """Create the underlying database if it does not yet exist."""
        if not database_exists(self.db_url):
            logger.info("Target database does not exist; creating it...")
            create_database(self.db_url)
            logger.info("Database created successfully.")

    def _alembic_config(self) -> Config:
        """Build an Alembic Config bound to this database URL.

        The URL is passed to ``env.py`` via ``-x db_url=`` so we migrate whatever
        database we were constructed against, and ``configure_logger`` is disabled
        so Alembic does not clobber the application's logging setup.
        """
        cfg = Config(str(ROOT_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
        cfg.cmd_opts = argparse.Namespace(x=[f"db_url={self.db_url}"])
        cfg.attributes["configure_logger"] = False
        return cfg

    def run_migrations(self):
        """Bring the schema up to the latest Alembic revision. Idempotent."""
        logger.info("Applying database migrations (alembic upgrade head)...")
        command.upgrade(self._alembic_config(), "head")
        logger.info("Database schema is at head revision.")

    def load_procedures(self):
        """Register repo-tracked SQL stored procedures (DATA-lifecycle helpers)."""
        procedures_dir = Path(__file__).resolve().parent / "procedures"
        if not procedures_dir.exists():
            return
        logger.info("Registering repo-tracked SQL procedures...")
        # Sort ensures 001 runs before 002.
        for sql_file in sorted(procedures_dir.glob("*.sql")):
            try:
                sql_script = sql_file.read_text(encoding="utf-8")
                with self.engine.connect() as conn:
                    conn.execute(text(sql_script))
                    conn.commit()
                logger.info("[✓] Registered database procedure: %s", sql_file.name)
            except Exception as e:
                logger.error("[!] Failed to register procedure %s: %s", sql_file.name, e)

    def create_schema(self):
        """Ensure the database exists, migrate to head, and register procedures."""
        self.ensure_database_exists()
        self.run_migrations()
        self.load_procedures()
