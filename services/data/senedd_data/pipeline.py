"""Backward-compatible facade over the split pipeline stages.

The former monolithic ``SeneddPipeline`` has been decomposed into three
single-concern modules:

- :mod:`senedd_data.provisioning` — schema/DDL lifecycle (Alembic + procedures).
- :mod:`senedd_data.acquisition`  — raw network + XML ingestion (source-of-truth tables).
- :mod:`senedd_data.transformation` — derived-table rebuild (no network).

New code should import those directly (and :func:`senedd_data.session.get_session`
for a plain session). This ``SeneddPipeline`` facade is retained only so existing
imports keep working; it delegates to the stages above and will be removed once
callers migrate.
"""
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from sqlalchemy import text

from senedd_data.acquisition import AcquisitionPipeline
from senedd_data.fetcher import Meeting
from senedd_data.provisioning import Provisioner
from senedd_data.session import get_sessionmaker
from senedd_data.transformation import TransformationPipeline

__all__ = ["SeneddPipeline", "AcquisitionPipeline", "TransformationPipeline", "Provisioner"]


class SeneddPipeline:
    """Deprecated facade composing the acquisition + transformation stages."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.SessionLocal = get_sessionmaker(db_url)
        self.provisioner = Provisioner(db_url)
        self.acquisition = AcquisitionPipeline(db_url)
        self.transformation = TransformationPipeline(db_url)

    @property
    def engine(self):
        return self.acquisition.engine

    def create_schema(self):
        self.provisioner.create_schema()

    def run_incremental(
        self,
        data_dir: Optional[Path] = None,
        keep_xml: bool = False,
        last_sync_date: Optional[datetime] = None,
        transcript_type: str = "BilingualTranscript",
    ) -> List[int]:
        """Legacy end-to-end incremental run: raw ingest then transform new meetings."""
        ingested = self.acquisition.run_incremental(
            data_dir=data_dir, keep_xml=keep_xml,
            last_sync_date=last_sync_date, transcript_type=transcript_type,
        )
        if ingested:
            self.transformation.transform_meetings(ingested)
        return ingested

    def run_for_meetings(
        self, meetings: List[Meeting], data_dir: Optional[Path] = None, keep_xml: bool = False
    ) -> int:
        """Legacy backfill: raw ingest an explicit meeting list then transform them."""
        ingested = self.acquisition.acquire_meetings(meetings, data_dir=data_dir, keep_xml=keep_xml)
        if ingested:
            self.transformation.transform_meetings(ingested)
        return len(ingested)

    def reprocess_downstream_from_raw(self, clear_dimensions: bool = True, clear_embeddings: bool = False):
        """Legacy alias for the derived-table rebuild."""
        self.transformation.reprocess_all(
            clear_dimensions=clear_dimensions, clear_embeddings=clear_embeddings
        )

    def run_full_pipeline(self, xml_file: Path):
        """Legacy full DATA rebuild from a single local XML file (schema preserved)."""
        self.provisioner.create_schema()
        with self.SessionLocal() as session:
            with session.begin():
                session.execute(text("CALL purge_all_tables();"))
        with self.SessionLocal() as session:
            with session.begin():
                self.acquisition.ingest_xml(session, xml_file)
        # Every freshly-ingested meeting now lacks speeches → transform discovers them.
        self.transformation.transform_meetings(None)
