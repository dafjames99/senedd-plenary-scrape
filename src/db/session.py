"""Central SQLAlchemy engine + session factory.

A single place to obtain a database session. Consumers (the acquisition and
transformation pipelines, the embedding pipeline, the retrieval service, the
MCP) share one engine per database URL instead of each constructing their own —
and, importantly, the read-only retrieval layer no longer instantiates an
ingestion pipeline just to borrow its ``SessionLocal``.
"""
from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.settings import settings


@lru_cache(maxsize=None)
def get_engine(db_url: Optional[str] = None) -> Engine:
    """Return a process-wide engine for ``db_url`` (defaults to settings)."""
    return create_engine(db_url or settings.database_url)


@lru_cache(maxsize=None)
def get_sessionmaker(db_url: Optional[str] = None) -> sessionmaker:
    """Return a cached ``sessionmaker`` bound to ``db_url`` (defaults to settings)."""
    return sessionmaker(bind=get_engine(db_url or settings.database_url))


def get_session(db_url: Optional[str] = None) -> Session:
    """Open a new session bound to the configured (or given) database."""
    return get_sessionmaker(db_url)()
