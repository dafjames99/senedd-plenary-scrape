"""Database provisioning: schema (DDL) lifecycle, owned by Alembic.

Separated from the data-lifecycle pipelines so both the acquisition and
transformation stages can bring a database up to head without dragging in each
other's ingestion/transformation logic. Alembic owns all DDL; this module only
drives ``alembic upgrade head`` and registers the repo-tracked SQL procedures.
"""
import argparse
import logging
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy_utils import create_database, database_exists

from senedd_data.session import get_engine
from senedd_data.settings import settings

# Postgres identifiers we interpolate into DDL (role/db names) must match this;
# grants and CREATE ROLE can't be parameterised, so we validate then interpolate.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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

    def provision_readonly_role(self):
        """Create/refresh a SELECT-only login role for read-only consumers.

        The web app and MCP server only ever read the schema; a role that lacks
        INSERT/UPDATE/DELETE means a bug or injection in a read path is contained
        by the database itself. Idempotent — safe to re-run on every provision.

        ``ALTER DEFAULT PRIVILEGES`` (without ``FOR ROLE``) is scoped to objects
        created by the *current* role, so this must run as the same role Alembic
        uses to create tables — which is exactly who the provisioner connects as.
        Skipped for non-Postgres URLs (e.g. sqlite dev).
        """
        url = make_url(self.db_url)
        if url.get_backend_name() != "postgresql":
            logger.info("Read-only role provisioning skipped (non-postgres URL).")
            return

        role = settings.readonly_role
        database = url.database
        if not _SAFE_IDENTIFIER.match(role) or not _SAFE_IDENTIFIER.match(database or ""):
            raise ValueError(
                f"Unsafe identifier for read-only provisioning: role={role!r} "
                f"database={database!r} (must match {_SAFE_IDENTIFIER.pattern})."
            )

        # role/database are validated identifiers; the password is a config value
        # embedded as a SQL string literal (single-quotes doubled). Bind params
        # can't reach inside a DO $$..$$ body, hence the manual construction.
        stmts = [
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} LOGIN; "
            f"END IF; END $$;",
            f"GRANT CONNECT ON DATABASE {database} TO {role};",
            f"GRANT USAGE ON SCHEMA public TO {role};",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role};",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role};",
        ]
        if settings.readonly_password is not None:
            pw_literal = "'" + settings.readonly_password.replace("'", "''") + "'"
            # Insert after CREATE ROLE so the password is set whether or not the
            # role already existed.
            stmts.insert(1, f"ALTER ROLE {role} PASSWORD {pw_literal};")

        logger.info("Provisioning read-only role %r on %r...", role, database)
        with self.engine.connect() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))
            conn.commit()
        logger.info("[✓] Read-only role %r provisioned (SELECT-only).", role)

    def create_schema(self):
        """Ensure the database exists, migrate to head, register procedures, and
        provision the read-only consumer role."""
        self.ensure_database_exists()
        self.run_migrations()
        self.load_procedures()
        self.provision_readonly_role()
