"""Content-addressed cache for embedding vectors.

A vector is a pure function of the exact string handed to the provider
(``doc_prefix + speaker_prefix + chunk``) and the model. This cache stores that
vector keyed on ``sha256(formatted_chunk)`` + ``model_name``, so it can be reused
only when it would be byte-for-byte identical — never a false hit.

The table has no foreign key to ``speeches``: it is content-addressed and
deliberately survives the delete-and-rebuild of speeches on re-ingest. That is
the whole point — re-running a backfill (or reverting a chunking experiment)
reuses every vector instead of recomputing it.

The helpers operate on a caller-supplied ``Session`` so the cache reads/writes
ride the same transaction as the ``speech_embeddings`` insert in the pipeline:
either both land or neither does.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Sequence, Tuple

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.db_schema import EmbeddingCache as EmbeddingCacheRow

logger = logging.getLogger(__name__)

# Provenance tag for cache rows. Bump when the chunking/prefixing logic changes
# in a way you want to be able to evict in bulk. NOTE: correctness does NOT
# depend on this — the content hash already changes whenever the embedded string
# changes, so a stale config can never yield a false hit. This is for the
# ``purge_embedding_cache(p_version => ...)`` eviction path and debugging only.
CHUNK_CONFIG_VERSION = "v1"


def hash_chunk(formatted_chunk: str) -> str:
    """SHA-256 hex digest of the exact string sent to the embedding provider."""
    return hashlib.sha256(formatted_chunk.encode("utf-8")).hexdigest()


def config_version(max_chunk_words: int) -> str:
    """Provenance string capturing the chunk-shaping config (not a hit key)."""
    return f"{CHUNK_CONFIG_VERSION}:mw{max_chunk_words}"


def lookup(
    session: Session, model_name: str, hashes: Sequence[str]
) -> Dict[str, List[float]]:
    """Return ``{text_hash: vector}`` for cached chunks under ``model_name``.

    Touches ``last_used_at``/``hit_count`` on the rows it serves (batched single
    UPDATE) so production age-eviction can use recency. The vector comes back via
    the ORM's pgvector type, so it is a proper numeric sequence ready to re-store.
    """
    unique = list({h for h in hashes})
    if not unique:
        return {}

    rows = (
        session.query(EmbeddingCacheRow.text_hash, EmbeddingCacheRow.embedding_vector)
        .filter(
            EmbeddingCacheRow.model_name == model_name,
            EmbeddingCacheRow.text_hash.in_(unique),
        )
        .all()
    )
    hits = {text_hash: vector for text_hash, vector in rows}

    if hits:
        session.query(EmbeddingCacheRow).filter(
            EmbeddingCacheRow.model_name == model_name,
            EmbeddingCacheRow.text_hash.in_(list(hits)),
        ).update(
            {
                "last_used_at": datetime.now(),
                "hit_count": EmbeddingCacheRow.hit_count + 1,
            },
            synchronize_session=False,
        )
    return hits


def store(
    session: Session,
    model_name: str,
    entries: Sequence[Tuple[str, List[float], int]],
    version: str,
) -> int:
    """Write newly computed vectors back to the cache (idempotent write-through).

    ``entries`` is ``(text_hash, vector, char_len)``. Conflicts on the
    ``(text_hash, model_name)`` key are ignored — a concurrent writer or a repeat
    within the same batch simply keeps the first copy. Returns rows offered.
    """
    if not entries:
        return 0
    payload = [
        {
            "text_hash": text_hash,
            "model_name": model_name,
            "embedding_vector": vector,
            "embed_config_version": version,
            "char_len": char_len,
        }
        for text_hash, vector, char_len in entries
    ]
    stmt = insert(EmbeddingCacheRow).values(payload)
    session.execute(
        stmt.on_conflict_do_nothing(index_elements=["text_hash", "model_name"])
    )
    return len(payload)
