"""Core retrieval service over Senedd speech embeddings.

This is the single home for semantic search. The CLI and the MCP server both call
into it, guaranteeing the model's ``query_prefix`` is applied symmetrically with
the document side and that every result carries full citation metadata.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Union

from sqlalchemy import text

from src.db.pipeline import SeneddPipeline
from src.db.settings import settings
from src.embeddings.config import MODEL_METADATA_REGISTRY
from src.embeddings.providers import PROVIDER_REGISTER

logger = logging.getLogger(__name__)

DateLike = Union[datetime, str]


@dataclass
class SearchResult:
    """A single semantic-search hit with its evidence excerpt and citation data."""

    speech_id: int
    speaker_name: str
    meeting_date: Optional[datetime]
    agenda_item_id: str
    chunk_text: str
    speech_text: str
    cosine_distance: float
    similarity_score: float
    senedd_tv_url: Optional[str]


def _coerce_datetime(value: DateLike, *, end_of_day: bool = False) -> datetime:
    """Normalise a date/datetime input to a ``datetime``.

    Bare ``YYYY-MM-DD`` strings (or dates with no time component) are pinned to
    the start of the day, or to ``23:59:59`` when ``end_of_day`` is set — so an
    inclusive ``date_to`` covers that whole day rather than excluding its
    afternoon sittings.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if end_of_day and value.time() == datetime.min.time():
        return value.replace(hour=23, minute=59, second=59, microsecond=999999)
    return value


def semantic_search(
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    speaker_filter: Optional[str] = None,
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    agenda_item: Optional[str] = None,
    provider_string: Optional[str] = None,
    model_string: Optional[str] = None,
) -> List[SearchResult]:
    """Embed ``query_text`` and retrieve the most semantically similar speeches.

    Returns the single best-matching chunk per speech as the evidence excerpt.
    Structured filters are applied as bound parameters inside the ranking CTE, so
    they narrow the candidate set *before* ranking. With no filters supplied the
    generated SQL is identical to the original speaker-only query.

    Args:
        query_text: Natural-language query.
        top_k: Maximum number of distinct speeches to return.
        min_similarity: Minimum similarity score (0–100) to include a result.
        speaker_filter: Optional partial speaker name (case-insensitive ``ILIKE``).
        date_from: Optional inclusive lower bound on meeting date.
        date_to: Optional inclusive upper bound on meeting date.
        agenda_item: Optional exact ``agenda_item_id`` to restrict to.
        provider_string: Embedding provider key (defaults to settings).
        model_string: Embedding model name (defaults to settings).
    """
    if not provider_string:
        provider_string = settings.embedding_provider
    if not model_string:
        model_string = settings.embedding_model

    provider = PROVIDER_REGISTER.get(provider_string)(model_string)
    model_meta = MODEL_METADATA_REGISTRY.get(provider.model_name, {})

    # Apply the model's query prefix before embedding — the query and document
    # vectors must live in the same subspace or cosine distance is meaningless.
    query_prefix = model_meta.get("query_prefix", "")
    prefixed_query = f"{query_prefix}{query_text}" if query_prefix else query_text

    logger.info("Embedding query with model '%s': '%s'", provider.model_name, prefixed_query)
    query_vector = provider.embed_batch([prefixed_query])[0]

    pipeline = SeneddPipeline(settings.database_url)

    # Build the candidate-filter clause from hardcoded fragments only; every user
    # value is bound separately to avoid any injection risk. The model_name
    # predicate is always present, so the join order below is unchanged when no
    # optional filters are supplied.
    conditions = ["se.model_name = :model_name"]
    params: dict = {
        "query_embedding": str(query_vector),
        "model_name": provider.model_name,
        # Over-fetch so threshold filtering doesn't silently truncate results.
        "limit": top_k * 5,
    }
    if speaker_filter:
        conditions.append("s.speaker_name ILIKE :speaker_filter")
        params["speaker_filter"] = f"%{speaker_filter}%"
    if date_from:
        conditions.append("m.meeting_date >= :date_from")
        params["date_from"] = _coerce_datetime(date_from)
    if date_to:
        conditions.append("m.meeting_date <= :date_to")
        params["date_to"] = _coerce_datetime(date_to, end_of_day=True)
    if agenda_item:
        conditions.append("s.agenda_item_id = :agenda_item")
        params["agenda_item"] = agenda_item
    # Future (Phase 3): a source_type predicate slots in here once embeddings are
    # polymorphic across spoken speeches and written QNR.
    where_clause = " AND ".join(conditions)

    # CTE ranks all chunks within each speech by cosine distance, then the outer
    # query keeps only rank-1 (best chunk per speech) before final sort. A LATERAL
    # join fetches the earliest SeneddTV URL for the speech.
    raw_sql = text(f"""
        WITH ranked_chunks AS (
            SELECT
                se.embedding_vector <=> :query_embedding  AS cosine_distance,
                se.chunk_text,
                s.speech_id,
                s.speaker_name,
                s.speech_text,
                s.agenda_item_id,
                m.meeting_date,
                ROW_NUMBER() OVER (
                    PARTITION BY s.speech_id
                    ORDER BY se.embedding_vector <=> :query_embedding ASC
                ) AS rn
            FROM  speech_embeddings se
            JOIN  speeches s  ON se.speech_id = s.speech_id
            JOIN  meetings m  ON s.meeting_id  = m.meeting_id
            WHERE {where_clause}
        ),
        best_per_speech AS (
            SELECT * FROM ranked_chunks WHERE rn = 1
        )
        SELECT
            b.speech_id,
            b.speaker_name,
            b.speech_text,
            b.agenda_item_id,
            b.meeting_date,
            b.chunk_text,
            b.cosine_distance,
            sp.spoken_url
        FROM best_per_speech b
        LEFT JOIN LATERAL (
            SELECT spoken_url
            FROM   speech_parts
            WHERE  speech_id = b.speech_id
              AND  spoken_url IS NOT NULL
            ORDER  BY contribution_order_id ASC
            LIMIT  1
        ) sp ON true
        ORDER BY b.cosine_distance ASC
        LIMIT :limit
    """)

    with pipeline.SessionLocal() as session:
        rows = session.execute(raw_sql, params).fetchall()

    results: List[SearchResult] = []
    for row in rows:
        similarity = (1 - row.cosine_distance) * 100
        if similarity < min_similarity:
            continue
        results.append(SearchResult(
            speech_id=row.speech_id,
            speaker_name=row.speaker_name,
            meeting_date=row.meeting_date,
            agenda_item_id=row.agenda_item_id,
            chunk_text=row.chunk_text,
            speech_text=row.speech_text,
            cosine_distance=row.cosine_distance,
            similarity_score=round(similarity, 2),
            senedd_tv_url=row.spoken_url,
        ))
        if len(results) >= top_k:
            break

    return results
