"""Seed the embedding cache from existing speech_embeddings (one-off operational).

Every vector already in ``speech_embeddings`` is content-hashed and inserted into
``embedding_cache``, so a subsequent re-ingest or re-embed reuses it instead of
recomputing. Idempotent (write-through uses ON CONFLICT DO NOTHING) — safe to
re-run. Like a backfill, this is an occasional operational task, not part of the
normal pipeline.

    python scripts/backfill_embedding_cache.py
    python scripts/backfill_embedding_cache.py --batch-size 2000
"""
import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine, text

from src import setup_logging, settings
from src.embeddings.pipeline import EmbeddingPipeline

logger = logging.getLogger(__name__)


def _cache_count(database_url: str) -> int:
    with create_engine(database_url).connect() as conn:
        return conn.execute(text("SELECT count(*) FROM embedding_cache")).scalar()


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Seed embedding_cache from existing speech_embeddings rows."
    )
    parser.add_argument(
        "--batch-size", type=int, default=1000, help="Rows per commit (default 1000)."
    )
    args = parser.parse_args()

    before = _cache_count(settings.database_url)
    logger.info("[*] embedding_cache rows before: %d", before)

    pipeline = EmbeddingPipeline()
    scanned = pipeline.backfill_cache(batch_size=args.batch_size)

    after = _cache_count(settings.database_url)
    total_scanned = sum(scanned.values())
    logger.info("[#] Per-model scanned: %s", scanned)
    logger.info(
        "[✓] Cache seed complete: scanned %d embedding(s); cache grew %d -> %d (+%d).",
        total_scanned, before, after, after - before,
    )
