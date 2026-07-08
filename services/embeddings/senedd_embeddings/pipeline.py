import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker, Session

from senedd_data.db_schema import SpeechEmbedding
from senedd_data.settings import settings
from .base import BaseEmbeddingProvider
from .chunker import chunk_text
from .providers import PROVIDER_REGISTER
from .config import MODEL_METADATA_REGISTRY
from . import cache as embed_cache

logger = logging.getLogger(__name__)


@dataclass
class EmbeddableItem:
    """One retrievable text unit awaiting embedding, normalised across sources."""

    source_type: str
    source_id: int
    body: str
    prefix_name: Optional[str]


@dataclass
class _SourceConfig:
    """How to find and frame the unembedded rows of one polymorphic source.

    ``select_sql`` must return columns ``source_id``, ``body`` and ``prefix_name``
    for rows of this source that lack an embedding under the active model. The
    join keys on ``(source_type, source_id, model_name)`` — the canonical
    discriminator — so a source is "done" per-model, exactly like speeches.
    """

    source_type: str
    select_sql: str
    min_words: int


# Each source resolves to the same (source_id, body, prefix_name) shape. The
# speaker/role name becomes the chunk prefix so the embedded text always carries
# its attribution (votes have no speaker, so prefix_name is NULL there).
_SOURCE_CONFIGS: Dict[str, _SourceConfig] = {
    "speech": _SourceConfig(
        source_type="speech",
        select_sql="""
            SELECT s.speech_id AS source_id,
                   s.speech_text AS body,
                   s.speaker_name AS prefix_name
            FROM speeches s
            LEFT JOIN speech_embeddings se
              ON se.source_type = 'speech'
             AND se.source_id = s.speech_id
             AND se.model_name = :model_name
            WHERE se.embedding_id IS NULL
              AND s.speech_text IS NOT NULL
              AND s.speech_text <> ''
            LIMIT :limit
        """,
        min_words=10,
    ),
    "written": _SourceConfig(
        source_type="written",
        select_sql="""
            SELECT w.id AS source_id,
                   COALESCE(w.text_english, w.text_welsh) AS body,
                   COALESCE(w.speaker_name_english, w.speaker_job_title_english) AS prefix_name
            FROM written_contributions w
            LEFT JOIN speech_embeddings se
              ON se.source_type = 'written'
             AND se.source_id = w.id
             AND se.model_name = :model_name
            WHERE se.embedding_id IS NULL
              AND COALESCE(w.text_english, w.text_welsh) IS NOT NULL
              AND COALESCE(w.text_english, w.text_welsh) <> ''
            LIMIT :limit
        """,
        min_words=10,
    ),
    "vote": _SourceConfig(
        source_type="vote",
        # Vote names are short, self-describing motion titles — no min-word gate,
        # and no speaker prefix.
        select_sql="""
            SELECT v.vote_id AS source_id,
                   v.vote_name_english AS body,
                   NULL AS prefix_name
            FROM votes v
            LEFT JOIN speech_embeddings se
              ON se.source_type = 'vote'
             AND se.source_id = v.vote_id
             AND se.model_name = :model_name
            WHERE se.embedding_id IS NULL
              AND v.vote_name_english IS NOT NULL
              AND v.vote_name_english <> ''
            LIMIT :limit
        """,
        min_words=0,
    ),
}


class EmbeddingPipeline:
    """Chunk and embed every retrievable source into ``speech_embeddings``.

    Polymorphic since Phase 4: one sweep covers spoken speeches, written QNR Q&A
    and vote motion names, all keyed on ``(source_type, source_id)``.
    """

    def __init__(self, provider: BaseEmbeddingProvider = None, db_path: str = None):
        db_path = db_path or settings.database_url
        self.engine = create_engine(db_path)
        self.SessionLocal = sessionmaker(bind=self.engine)

        self.provider = provider or PROVIDER_REGISTER[settings.embedding_provider](settings.embedding_model)
        self.model_meta = MODEL_METADATA_REGISTRY.get(self.provider.model_name, None)

        if self.model_meta is None:
            raise ValueError(f"{self.provider} & {self.provider.model_name} are not valid.")

        self.cache_enabled = settings.embed_cache_enabled

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _fetch_unembedded(
        self, session: Session, source_type: str, limit: int
    ) -> List[EmbeddableItem]:
        """Fetch up to ``limit`` rows of one source lacking an active-model vector."""
        config = _SOURCE_CONFIGS[source_type]
        rows = session.execute(
            text(config.select_sql),
            {"model_name": self.provider.model_name, "limit": limit},
        ).fetchall()
        return [
            EmbeddableItem(
                source_type=source_type,
                source_id=row.source_id,
                body=row.body,
                prefix_name=row.prefix_name,
            )
            for row in rows
        ]

    def get_unembedded_speeches(self, session: Session, limit: int = 100) -> List[EmbeddableItem]:
        """Backwards-compatible alias: unembedded *speeches* only."""
        return self._fetch_unembedded(session, "speech", limit)

    def count_unembedded(self) -> int:
        """Total rows across all sources still missing an active-model vector."""
        with self.SessionLocal() as session:
            return sum(
                len(self._fetch_unembedded(session, st, limit=1_000_000))
                for st in _SOURCE_CONFIGS
            )

    def has_unembedded(self) -> bool:
        """True if any source has rows still awaiting an active-model vector."""
        with self.SessionLocal() as session:
            for st in _SOURCE_CONFIGS:
                if self._fetch_unembedded(session, st, limit=1):
                    return True
        return False

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def purge_active_model_embeddings(self):
        """Delete every vector (all sources) for the active model."""
        with self.SessionLocal() as session:
            with session.begin():
                logger.warning(
                    "Purging all database vectors matching model target: %s",
                    self.provider.model_name,
                )
                session.query(SpeechEmbedding).filter(
                    SpeechEmbedding.model_name == self.provider.model_name
                ).delete(synchronize_session=False)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def run(self, batch_size: int = 100) -> int:
        """Embed one batch per source (speech, written, vote); return rows written."""
        total_written = 0
        with self.SessionLocal() as session:
            for source_type in _SOURCE_CONFIGS:
                total_written += self._run_source(session, source_type, batch_size)
        if total_written == 0:
            logger.info("No new content to embed. Hibernating")
        return total_written

    def _run_source(self, session: Session, source_type: str, batch_size: int) -> int:
        """Fetch, chunk, embed and persist one batch of a single source."""
        config = _SOURCE_CONFIGS[source_type]
        max_words = self.model_meta["max_chunk_words"]
        doc_prefix = self.model_meta["doc_prefix"]

        items = self._fetch_unembedded(session, source_type, limit=batch_size)
        if not items:
            return 0

        logger.info("Processing %d %s records for embedding.", len(items), source_type)

        chunks_to_embed: List[str] = []
        metadata_payloads: List[dict] = []
        for item in items:
            if config.min_words and len(item.body.split()) < config.min_words:
                logger.debug(
                    "Skipping %s %d: below %d-word threshold.",
                    source_type, item.source_id, config.min_words,
                )
                continue
            speaker_prefix = f"{item.prefix_name}: " if item.prefix_name else ""
            for idx, chunk in enumerate(chunk_text(item.body, max_words=max_words)):
                contextualized = speaker_prefix + chunk
                formatted_chunk = f"{doc_prefix}{contextualized}" if doc_prefix else contextualized
                chunks_to_embed.append(formatted_chunk)
                metadata_payloads.append({
                    "source_type": source_type,
                    "source_id": item.source_id,
                    "chunk_index": idx,
                    "chunk_text": contextualized,
                })

        if not chunks_to_embed:
            return 0

        vectors = self._embed_with_cache(session, chunks_to_embed, max_words)

        embedding_objects = [
            SpeechEmbedding(
                source_type=meta["source_type"],
                source_id=meta["source_id"],
                # Legacy FK column populated only for speeches (the keep-then-drop
                # cascade safety net); NULL for written/vote sources.
                speech_id=meta["source_id"] if meta["source_type"] == "speech" else None,
                chunk_index=meta["chunk_index"],
                chunk_text=meta["chunk_text"],
                embedding_vector=vector,
                model_name=self.provider.model_name,
                created_at=datetime.now(),
            )
            for meta, vector in zip(metadata_payloads, vectors)
        ]

        try:
            session.add_all(embedding_objects)
            session.commit()
            logger.info(
                "Successfully embedded and saved %d %s records.",
                len(embedding_objects), source_type,
            )
        except Exception as e:
            session.rollback()
            logger.error("Error during database insertion, rolling back. Details: %s", e)
            raise e

        return len(embedding_objects)

    def backfill_cache(self, batch_size: int = 1000) -> Dict[str, int]:
        """Seed the embedding cache from vectors already in ``speech_embeddings``.

        One-off operational helper (see ``scripts/backfill_embedding_cache.py``):
        every existing vector becomes a cache entry, so a later re-ingest/re-embed
        reuses it instead of recomputing. Idempotent. Returns rows scanned per
        model.
        """
        with self.SessionLocal() as session:
            return embed_cache.populate_from_embeddings(session, batch_size=batch_size)

    def _embed_with_cache(
        self, session: Session, formatted_chunks: List[str], max_words: int
    ) -> List[List[float]]:
        """Return a vector per chunk, reusing cached vectors and embedding the rest.

        Content-addressed on the exact embedded string + active model. Only the
        cache misses hit the provider; freshly computed vectors are written back
        (write-through) in the caller's transaction. With the cache disabled this
        is a thin pass-through to ``embed_batch``.
        """
        if not self.cache_enabled:
            logger.info(
                "Generating vectors for %d chunks using Model=%s (cache off)",
                len(formatted_chunks), self.provider.model_name,
            )
            return self.provider.embed_batch(formatted_chunks)

        hashes = [embed_cache.hash_chunk(c) for c in formatted_chunks]
        cached = embed_cache.lookup(session, self.provider.model_name, hashes)

        miss_indices = [i for i, h in enumerate(hashes) if h not in cached]
        logger.info(
            "Embedding %d chunk(s) via Model=%s: %d cache hit(s), %d to compute.",
            len(formatted_chunks), self.provider.model_name,
            len(formatted_chunks) - len(miss_indices), len(miss_indices),
        )

        miss_vectors = (
            self.provider.embed_batch([formatted_chunks[i] for i in miss_indices])
            if miss_indices else []
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
                self.provider.model_name,
                [
                    (hashes[i], vectors[i], len(formatted_chunks[i]))
                    for i in miss_indices
                ],
                version=embed_cache.config_version(max_words),
            )
        return vectors
