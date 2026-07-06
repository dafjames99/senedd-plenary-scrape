#!/usr/bin/env python3
"""Local-dev orchestrator composing the three pipeline stages end-to-end.

The stages are independently runnable for production (each concern is its own
module + entry point):

    python -m src.db.acquisition       # raw ingest only
    python -m src.db.transformation    # derived rebuild only
    python -m src.embeddings.embed     # embedding sweep only (manual)

This script wires them together for convenient local runs. It holds no pipeline
logic — only argument parsing and stage sequencing.
"""
import argparse
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from src import setup_logging, settings
from src.db.acquisition import AcquisitionPipeline
from src.db.transformation import TransformationPipeline
from src.embeddings.embed import run_embedding_sweep

logger = logging.getLogger("orchestrator")

DB_URL = settings.database_url
BATCH_SIZE = settings.embed_batch_size

DATA_DIR = Path(__file__).parent / "data"
XML_FILE = DATA_DIR / "260602_Plenary_Bilingual.xml"


def _run_full_rebuild(acquisition: AcquisitionPipeline, transformation: TransformationPipeline, xml_file: Path):
    """Full DATA rebuild from a single local XML file (schema preserved)."""
    acquisition.create_schema()
    logger.warning("Truncating ALL tables via purge_all_tables() — data reset.")
    with acquisition.SessionLocal() as session:
        with session.begin():
            session.execute(text("CALL purge_all_tables();"))
    with acquisition.SessionLocal() as session:
        with session.begin():
            acquisition.ingest_xml(session, xml_file)
    # Freshly-ingested meetings have no speeches yet → transform discovers them all.
    transformation.transform_meetings(None)


def main():
    parser = argparse.ArgumentParser(description="Senedd pipeline orchestrator")
    parser.add_argument(
        "--mode",
        choices=["sync", "embed-only", "reprocess"],
        default="sync",
        help="'sync' (raw ingest + transform); 'embed-only' (embedding sweep); "
             "'reprocess' (rebuild derived tables from existing raw contributions)."
    )
    parser.add_argument(
        '-f', "--force",
        action="store_true",
        help="Force a full DATA rebuild from the source XML file (schema preserved)."
    )
    parser.add_argument(
        "--embed-loop",
        action="store_true",
        help="Loop the embedding sweep until no unembedded records remain. Overrides -i."
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Prompt between embedding batches to approve continuing. --embed-loop overrides."
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR,
                        help="Directory for XML files (default: ./data/).")
    parser.add_argument("--xml-file", type=Path, default=XML_FILE,
                        help="XML file to process (for --force rebuild).")
    parser.add_argument("--keep-xml", action="store_true",
                        help="Keep raw XML files after processing (default: delete).")
    parser.add_argument("--last-sync", type=str,
                        help="Override last sync date (YYYY-MM-DD, sync mode only).")

    args = parser.parse_args()

    acquisition = AcquisitionPipeline(DB_URL)
    transformation = TransformationPipeline(DB_URL)

    if args.mode == "reprocess":
        transformation.reprocess_all(clear_dimensions=False, clear_embeddings=False)

    elif args.mode == "sync":
        if args.force:
            logger.warning("!!! FORCE REBUILD !!! Wiping all DATA (schema preserved)...")
            if not args.xml_file.exists():
                logger.error("Source XML not found for full rebuild: %s", args.xml_file)
                return
            _run_full_rebuild(acquisition, transformation, args.xml_file)
            run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE,
                                force_reset=True, interactive=args.interactive)
        else:
            logger.info("Running incremental sync (raw acquisition + transform)...")
            last_sync = None
            if args.last_sync:
                try:
                    last_sync = datetime.strptime(args.last_sync, "%Y-%m-%d")
                except ValueError:
                    logger.error("Invalid --last-sync '%s'. Use YYYY-MM-DD.", args.last_sync)
                    return
            ingested = acquisition.run_incremental(
                data_dir=args.data_dir, keep_xml=args.keep_xml, last_sync_date=last_sync
            )
            if ingested:
                transformation.transform_meetings(ingested)
            run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE,
                                force_reset=False, interactive=args.interactive)

    elif args.mode == "embed-only":
        if args.force:
            logger.warning("!!! FORCE EMBED REGEN !!! Wiping vectors for active model...")
        run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE,
                            force_reset=args.force, interactive=args.interactive)

    logger.info("Pipeline operations complete.")


if __name__ == "__main__":
    setup_logging()
    main()
