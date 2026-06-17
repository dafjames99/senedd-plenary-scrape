
#!/usr/bin/env python3
"""Entry point for Senedd XML parsing pipeline."""
import logging
import argparse
from pathlib import Path
from datetime import datetime

from src import EmbeddingPipeline, SeneddPipeline, setup_logging, settings

logger = logging.getLogger("orchestrator")

# Configuration
DB_URL = settings.database_url
BATCH_SIZE = settings.embed_batch_size

DATA_DIR = Path(__file__).parent / "data"
XML_FILE = DATA_DIR / "260602_Plenary_Bilingual.xml"

def run_embedding_sweep(
    loop_until_empty: bool = False,
    batch_size: int = 100,
    force_reset: bool = False,
    interactive: bool = False):
    """
    Executes a sweeping batch cycle across newly extracted text segments.
    Supports continuous loop tracking, interactive step-debugging, and clean exits.
    """
    logger.info("Initializing vector embedding generation routine...")
    try:
        embed_pipeline = EmbeddingPipeline(db_path=DB_URL)
        if force_reset:
            embed_pipeline.purge_active_model_embeddings()
        
        batch_count = 0
        while True:
            # 1. Check current capacity context
            with embed_pipeline.SessionLocal() as session:
                unembedded_remaining = embed_pipeline.get_unembedded_speeches(session, limit=1)
                
            if not unembedded_remaining:
                logger.info("No unembedded records remaining. Vector sweep target complete.")
                break
            batch_count += 1
            logger.info("Starting processing execution block for Batch #%d...", batch_count)
            embed_pipeline.run(batch_size=batch_size)
            
            if loop_until_empty:
                logger.debug("Loop mode active. Proceeding automatically to the next batch sequence.")
                continue
            
            elif interactive:
                user_input = input(f"\n[Batch #{batch_count} Complete] Continue to next batch sweep? (y/N): ").strip().lower()
                if user_input not in ('y', 'yes'):
                    logger.info("Interactive exit requested by operator. Halting vector execution loop.")
                    break
            else:
                logger.info("Single-batch sweep executed successfully. Halting as per configuration defaults.")
                break    
    except Exception as e:
        logger.error("Non-fatal vectorization intercept: Embedding pipeline failed: %s", e)

def main():
    parser = argparse.ArgumentParser(
        description="Senedd XML → Speech Reconstruction Pipeline"
    )
    parser.add_argument(
        "--mode",
        choices=["sync", "embed-only", "reprocess"], # Simplified to sync vs metadata maintenance
        default="sync",
        help="Pipeline mode: 'sync' (incremental processing run);'embed-only' (process embeddings); reprocess (downstream reprocessing from existing raw_contributions)"
    )
    parser.add_argument(
        '-f', "--force",
        action="store_true",
        help="Force a full DATA rebuild from the source files (schema preserved; structure is managed by Alembic)."
    )
    parser.add_argument(
        "--embed-loop",
        action="store_true",
        help="If set, loop the embedding sweep dynamically until no unembedded records remain. Overrides -i / --interactive."
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Prompt the user manually between vectorization batch loops to approve continuing. --embed-loop overrides this."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory for XML files (default: ./data/)"
    )
    
    parser.add_argument(
        "--xml-file",
        type=Path,
        default=XML_FILE,
        help="XML file to process (for full mode, default: ./data/260602_Plenary_Bilingual.xml)"
    )
    
    parser.add_argument(
        "--keep-xml",
        action="store_true",
        help="Keep raw XML files after processing (default: delete)"
    )
    
    parser.add_argument(
        "--last-sync",
        type=str,
        help="Override last sync date (format: YYYY-MM-DD, only for incremental mode)"
    )
    
    args = parser.parse_args()
    
    # Initialize pipeline
    pipeline = SeneddPipeline(DB_URL)
    
    if args.mode == "reprocess":
        pipeline.reprocess_downstream_from_raw(clear_dimensions=False, clear_embeddings=False)
    elif args.mode == "sync":
        if args.force:
            logger.warning("!!! FORCE REBUILD ACTIVATED !!! Wiping all DATA (schema preserved)...")
            if not args.xml_file.exists():
                logger.error("Target source payload template not found for full rebuild: %s", args.xml_file)
                return
            # Truncates all data then re-ingests; schema structure stays under Alembic

            pipeline.run_full_pipeline(args.xml_file)
            run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE, force_reset=True, interactive=args.interactive)
        else:
            logger.info("Executing standard incremental sync. Scanning for new feed updates...")
            
            last_sync = None
            if args.last_sync:
                try:
                    last_sync = datetime.strptime(args.last_sync, "%Y-%m-%d")
                except ValueError:
                    logger.error("Invalid input date mask override assignment '%s'. Use YYYY-MM-DD", args.last_sync)
                    return
            
            # Runs safe delta-append
            pipeline.run_incremental(
                data_dir=args.data_dir,
                keep_xml=args.keep_xml,
                last_sync_date=last_sync
            )
            run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE, force_reset=False, interactive=args.interactive)
    elif args.mode == "embed-only":
        if args.force:
            logger.warning("!!! FORCE EMBED REGEN ACTIVATED !!! Wiping vectors for active model tier...")
            run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE, force_reset=True, interactive=args.interactive)
        else:
            logger.info("Executing standard incremental embedding sweep. Bypassing data sync.")
            run_embedding_sweep(loop_until_empty=args.embed_loop, batch_size=BATCH_SIZE, force_reset=False, interactive=args.interactive)

    logger.info("Pipeline workflow operations finalized successfully.")


if __name__ == "__main__":
    setup_logging()
    main()
