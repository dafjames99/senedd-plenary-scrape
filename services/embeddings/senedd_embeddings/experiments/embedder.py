"""Embed the speech corpus under an experiment namespace.

Mirrors the production ``EmbeddingPipeline`` speech path — same skip gate,
speaker prefix and cache integration — but parameterised by a
:class:`~senedd_embeddings.experiments.config.ResolvedConfig` and writing vectors with
``model_name = <experiment namespace>`` so the run is fully isolated from
production search.

The content-addressed cache is keyed on the provider's *real* model name (not
the namespace): a vector is a pure function of (exact string, model), so
experiments that share formatted chunks with production — or with each other —
reuse those vectors instead of recomputing them. That is the cache's designed
purpose.

Only speeches are embedded: the labelled eval set is speech-only, so votes and
written contributions would add cost without adding signal. Extend
``_SELECT_UNEMBEDDED`` alongside the eval set if that changes.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List

from sqlalchemy import text
from sqlalchemy.orm import Session

from senedd_data.db_schema import SpeechEmbedding
from senedd_embeddings import cache as embed_cache
from senedd_embeddings.base import BaseEmbeddingProvider
from senedd_embeddings.chunker import chunk_text
from senedd_embeddings.experiments.config import ResolvedConfig

logger = logging.getLogger(__name__)

# Speeches lacking a vector under the experiment namespace. Same join shape as
# the production pipeline's speech source, so a re-run resumes where it stopped
# instead of duplicating vectors. Keyset-paginated on speech_id: speeches below
# the word gate never receive vectors, so a plain re-poll of this query would
# return them forever — the cursor advances past them.
_SELECT_UNEMBEDDED = """
    SELECT s.speech_id AS source_id,
           s.speech_text AS body,
           s.speaker_name AS prefix_name
    FROM speeches s
    LEFT JOIN speech_embeddings se
      ON se.source_type = 'speech'
     AND se.source_id = s.speech_id
     AND se.model_name = :namespace
    WHERE se.embedding_id IS NULL
      AND s.speech_id > :after
      AND s.speech_text IS NOT NULL
      AND s.speech_text <> ''
    ORDER BY s.speech_id
    LIMIT :limit
"""


@dataclass
class EmbedStats:
    """What the embed phase did and how fast."""

    items_seen: int = 0
    items_skipped_short: int = 0
    chunks_embedded: int = 0
    cache_hits: int = 0
    provider_calls_chunks: int = 0
    wall_seconds: float = 0.0

    @property
    def chunks_per_second(self) -> float:
        return self.chunks_embedded / self.wall_seconds if self.wall_seconds else 0.0


def format_chunks(item_body: str, prefix_name: str | None, resolved: ResolvedConfig) -> List[tuple]:
    """Chunk one speech and apply prefixes per the experiment config.

    Returns ``[(chunk_index, stored_chunk, formatted_chunk)]`` where
    ``stored_chunk`` is what lands in ``speech_embeddings.chunk_text`` (speaker
    prefix, no doc prefix — mirroring production) and ``formatted_chunk`` is the
    exact string sent to the provider (doc prefix included).
    """
    speaker_prefix = (
        f"{prefix_name}: " if (resolved.config.speaker_prefix and prefix_name) else ""
    )
    out = []
    chunks = chunk_text(
        item_body,
        max_words=resolved.max_words,
        overlap_words=resolved.overlap_words,
        min_words=resolved.min_words,
    )
    for idx, chunk in enumerate(chunks):
        contextualized = speaker_prefix + chunk
        formatted = f"{resolved.doc_prefix}{contextualized}" if resolved.doc_prefix else contextualized
        out.append((idx, contextualized, formatted))
    return out


def embed_corpus(
    session: Session,
    provider: BaseEmbeddingProvider,
    resolved: ResolvedConfig,
    batch_size: int = 250,
    max_items: int | None = None,
    use_cache: bool = True,
) -> EmbedStats:
    """Embed every un-embedded speech under the experiment namespace.

    Commits per batch, so an interrupted run resumes. ``max_items`` caps the
    total for smoke tests (quality numbers from a partial corpus are not
    comparable — the runner marks such runs as partial).
    """
    config = resolved.config
    namespace = config.namespace
    stats = EmbedStats()
    started = time.monotonic()
    after = 0

    while True:
        remaining = batch_size
        if max_items is not None:
            remaining = min(batch_size, max_items - stats.items_seen)
            if remaining <= 0:
                break

        rows = session.execute(
            text(_SELECT_UNEMBEDDED),
            {"namespace": namespace, "limit": remaining, "after": after},
        ).fetchall()
        if not rows:
            break
        after = rows[-1].source_id

        chunks_to_embed: List[str] = []
        payloads: List[dict] = []
        for row in rows:
            stats.items_seen += 1
            if config.min_item_words and len(row.body.split()) < config.min_item_words:
                stats.items_skipped_short += 1
                continue
            for idx, stored, formatted in format_chunks(row.body, row.prefix_name, resolved):
                chunks_to_embed.append(formatted)
                payloads.append(
                    {"source_id": row.source_id, "chunk_index": idx, "chunk_text": stored}
                )

        if chunks_to_embed:
            vectors = _embed_batch(session, provider, chunks_to_embed, resolved, stats, use_cache)
            session.add_all(
                SpeechEmbedding(
                    source_type="speech",
                    source_id=p["source_id"],
                    # Legacy cascade FK: populated so a speech reprocess purges
                    # experiment vectors exactly like production ones.
                    speech_id=p["source_id"],
                    chunk_index=p["chunk_index"],
                    chunk_text=p["chunk_text"],
                    embedding_vector=v,
                    model_name=namespace,
                    created_at=datetime.now(),
                )
                for p, v in zip(payloads, vectors)
            )
            stats.chunks_embedded += len(chunks_to_embed)

        session.commit()
        logger.info(
            "[%s] embedded %d chunks (%d speeches seen, %d skipped short)",
            namespace, stats.chunks_embedded, stats.items_seen, stats.items_skipped_short,
        )

    stats.wall_seconds = time.monotonic() - started
    return stats


def _embed_batch(
    session: Session,
    provider: BaseEmbeddingProvider,
    formatted_chunks: List[str],
    resolved: ResolvedConfig,
    stats: EmbedStats,
    use_cache: bool,
) -> List[List[float]]:
    """Vectors for ``formatted_chunks``, via the content-addressed cache."""
    if not use_cache:
        stats.provider_calls_chunks += len(formatted_chunks)
        return provider.embed_batch(formatted_chunks)

    hashes = [embed_cache.hash_chunk(c) for c in formatted_chunks]
    cached = embed_cache.lookup(session, provider.model_name, hashes)
    miss_indices = [i for i, h in enumerate(hashes) if h not in cached]

    stats.cache_hits += len(formatted_chunks) - len(miss_indices)
    stats.provider_calls_chunks += len(miss_indices)

    miss_vectors = (
        provider.embed_batch([formatted_chunks[i] for i in miss_indices])
        if miss_indices
        else []
    )

    vectors: List[List[float]] = [None] * len(formatted_chunks)
    for i, h in enumerate(hashes):
        if h in cached:
            vectors[i] = cached[h]
    for j, i in enumerate(miss_indices):
        vectors[i] = miss_vectors[j]

    if miss_indices:
        embed_cache.store(
            session,
            provider.model_name,
            [(hashes[i], vectors[i], len(formatted_chunks[i])) for i in miss_indices],
            version=embed_cache.config_version(resolved.max_words),
        )
    return vectors


def count_vectors(session: Session, namespace: str) -> int:
    """Number of vectors stored under an experiment namespace."""
    return session.execute(
        text("SELECT COUNT(*) FROM speech_embeddings WHERE model_name = :ns"),
        {"ns": namespace},
    ).scalar_one()


def purge_namespace(session: Session, namespace: str) -> int:
    """Delete all vectors for one experiment namespace; returns rows removed."""
    if not namespace.startswith("exp:"):
        raise ValueError(
            f"Refusing to purge non-experiment namespace {namespace!r} — "
            "production vectors are purged via the embedding pipeline."
        )
    result = session.execute(
        text("DELETE FROM speech_embeddings WHERE model_name = :ns"),
        {"ns": namespace},
    )
    session.commit()
    return result.rowcount
