"""CLI entry point for the embedding sweep stage (run manually).

Kept separate from raw acquisition and derived transformation: embedding is
triggered on demand during dev/testing, not on the automated data loop.
"""
import argparse
import logging

from src.db.settings import settings, setup_logging
from src.embeddings.pipeline import EmbeddingPipeline

logger = logging.getLogger("embed")


def run_embedding_sweep(
    loop_until_empty: bool = False,
    batch_size: int = 100,
    force_reset: bool = False,
    interactive: bool = False,
    db_url: str = None,
):
    """Sweep unembedded records across all sources in batches.

    Supports a continuous loop, an interactive step between batches, and a clean
    single-batch default.
    """
    logger.info("Initializing embedding sweep...")
    try:
        embed_pipeline = EmbeddingPipeline(db_path=db_url or settings.database_url)
        if force_reset:
            embed_pipeline.purge_active_model_embeddings()

        batch_count = 0
        while True:
            if not embed_pipeline.has_unembedded():
                logger.info("No unembedded records remaining. Sweep complete.")
                break
            batch_count += 1
            logger.info("Processing embedding batch #%d...", batch_count)
            embed_pipeline.run(batch_size=batch_size)

            if loop_until_empty:
                logger.debug("Loop mode active; continuing to next batch.")
                continue
            elif interactive:
                user_input = input(f"\n[Batch #{batch_count} complete] Continue? (y/N): ").strip().lower()
                if user_input not in ('y', 'yes'):
                    logger.info("Interactive exit requested. Halting.")
                    break
            else:
                logger.info("Single-batch sweep complete (default). Halting.")
                break
    except Exception as e:
        logger.error("Embedding pipeline failed: %s", e)


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Senedd embedding sweep")
    parser.add_argument("--batch-size", type=int, default=settings.embed_batch_size,
                        help="Speeches per embedding batch.")
    parser.add_argument("--loop", action="store_true",
                        help="Loop until no unembedded records remain. Overrides --interactive.")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Prompt between batches to approve continuing.")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Wipe vectors for the active model before sweeping.")
    args = parser.parse_args()

    run_embedding_sweep(
        loop_until_empty=args.loop,
        batch_size=args.batch_size,
        force_reset=args.force,
        interactive=args.interactive,
    )


if __name__ == "__main__":
    main()
