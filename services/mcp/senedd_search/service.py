"""Core retrieval service over the Senedd polymorphic embedding store.

This is the single home for semantic search. The CLI and the MCP server both call
into it, guaranteeing the model's ``query_prefix`` is applied symmetrically with
the document side and that every result carries full citation metadata.

Since Phase 4 the store is polymorphic: one query can span spoken speeches,
written QNR Q&A and vote motion names, gated by the ``source`` argument. Each
source ranks its own chunks (best chunk per item) and resolves its own citation
metadata; the results are merged and re-ranked globally by cosine distance. The
speech path is unchanged from the speech-only release, so speech retrieval
metrics do not move.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text

from senedd_data.session import get_session
from senedd_data.settings import settings
from senedd_embeddings.config import MODEL_METADATA_REGISTRY
from senedd_embeddings.providers import PROVIDER_REGISTER
from senedd_search._dates import DateLike, coerce_datetime

logger = logging.getLogger(__name__)

# Public source filter values → internal source_type discriminator.
_SOURCE_ALIASES: Dict[str, str] = {
    "spoken": "speech",
    "speech": "speech",
    "written": "written",
    "vote": "vote",
}


@dataclass
class SearchResult:
    """A single semantic-search hit with its evidence excerpt and citation data.

    ``source_type`` is the discriminator (``speech`` | ``written`` | ``vote``) and
    ``source_id`` its key in the owning table. ``speech_id``/``speech_text`` are
    retained for backwards compatibility and are populated only for spoken
    speeches (mirroring ``source_id``/``full_text`` there).
    """

    speech_id: Optional[int]
    speaker_name: Optional[str]
    meeting_date: Optional[datetime]
    agenda_item_id: Optional[str]
    chunk_text: str
    speech_text: Optional[str]
    cosine_distance: float
    similarity_score: float
    senedd_tv_url: Optional[str]
    source_type: str = "speech"
    source_id: Optional[int] = None
    meeting_id: Optional[int] = None
    full_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Per-source ranking queries
#
# Each returns the same column shape — source_id, speaker_name, meeting_id,
# meeting_date, agenda_item_id, full_text, chunk_text, cosine_distance,
# senedd_tv_url — so a single mapper builds the result. ROW_NUMBER keeps the best
# chunk per item before the outer query, exactly as the original speech query did.
# ---------------------------------------------------------------------------

def _query_speeches(session, params: dict, conditions: List[str]) -> list:
    where_clause = " AND ".join(conditions)
    sql = text(f"""
        WITH ranked_chunks AS (
            SELECT
                se.embedding_vector <=> :query_embedding  AS cosine_distance,
                se.chunk_text,
                s.speech_id        AS source_id,
                s.speaker_name,
                s.meeting_id,
                s.speech_text      AS full_text,
                s.agenda_item_id,
                m.meeting_date,
                ROW_NUMBER() OVER (
                    PARTITION BY s.speech_id
                    ORDER BY se.embedding_vector <=> :query_embedding ASC
                ) AS rn
            FROM  speech_embeddings se
            JOIN  speeches s  ON se.source_id = s.speech_id
            JOIN  meetings m  ON s.meeting_id  = m.meeting_id
            WHERE {where_clause}
        ),
        best_per_item AS (
            SELECT * FROM ranked_chunks WHERE rn = 1
        )
        SELECT
            b.source_id, b.speaker_name, b.meeting_id, b.meeting_date,
            b.agenda_item_id, b.full_text, b.chunk_text, b.cosine_distance,
            sp.spoken_url AS senedd_tv_url
        FROM best_per_item b
        LEFT JOIN LATERAL (
            SELECT spoken_url
            FROM   speech_parts
            WHERE  speech_id = b.source_id
              AND  spoken_url IS NOT NULL
            ORDER  BY contribution_order_id ASC
            LIMIT  1
        ) sp ON true
        ORDER BY b.cosine_distance ASC
        LIMIT :limit
    """)
    return session.execute(sql, params).fetchall()


def _query_written(session, params: dict, conditions: List[str]) -> list:
    where_clause = " AND ".join(conditions)
    sql = text(f"""
        WITH ranked_chunks AS (
            SELECT
                se.embedding_vector <=> :query_embedding  AS cosine_distance,
                se.chunk_text,
                w.id               AS source_id,
                COALESCE(w.speaker_name_english, w.speaker_job_title_english) AS speaker_name,
                w.meeting_id,
                w.text_english     AS full_text,
                w.agenda_item_id,
                m.meeting_date,
                ROW_NUMBER() OVER (
                    PARTITION BY w.id
                    ORDER BY se.embedding_vector <=> :query_embedding ASC
                ) AS rn
            FROM  speech_embeddings se
            JOIN  written_contributions w ON se.source_id = w.id
            JOIN  meetings m ON w.meeting_id = m.meeting_id
            WHERE {where_clause}
        )
        SELECT
            source_id, speaker_name, meeting_id, meeting_date, agenda_item_id,
            full_text, chunk_text, cosine_distance,
            CAST(NULL AS VARCHAR) AS senedd_tv_url
        FROM ranked_chunks WHERE rn = 1
        ORDER BY cosine_distance ASC
        LIMIT :limit
    """)
    return session.execute(sql, params).fetchall()


def _query_votes(session, params: dict, conditions: List[str]) -> list:
    where_clause = " AND ".join(conditions)
    sql = text(f"""
        WITH ranked_chunks AS (
            SELECT
                se.embedding_vector <=> :query_embedding  AS cosine_distance,
                se.chunk_text,
                v.vote_id          AS source_id,
                CAST(NULL AS VARCHAR) AS speaker_name,
                v.meeting_id,
                v.vote_name_english AS full_text,
                v.agenda_item_id,
                m.meeting_date,
                ROW_NUMBER() OVER (
                    PARTITION BY v.vote_id
                    ORDER BY se.embedding_vector <=> :query_embedding ASC
                ) AS rn
            FROM  speech_embeddings se
            JOIN  votes v ON se.source_id = v.vote_id
            JOIN  meetings m ON v.meeting_id = m.meeting_id
            WHERE {where_clause}
        )
        SELECT
            source_id, speaker_name, meeting_id, meeting_date, agenda_item_id,
            full_text, chunk_text, cosine_distance,
            CAST(NULL AS VARCHAR) AS senedd_tv_url
        FROM ranked_chunks WHERE rn = 1
        ORDER BY cosine_distance ASC
        LIMIT :limit
    """)
    return session.execute(sql, params).fetchall()


def _resolve_sources(source: Optional[str]) -> List[str]:
    """Map the public ``source`` filter to the internal source_types to query."""
    if source is None:
        return ["speech", "written", "vote"]
    key = source.strip().lower()
    if key not in _SOURCE_ALIASES:
        raise ValueError(
            f"Unknown source '{source}'. Use one of: spoken, written, vote."
        )
    return [_SOURCE_ALIASES[key]]


def semantic_search(
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    speaker_filter: Optional[str] = None,
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    agenda_item: Optional[str] = None,
    source: Optional[str] = None,
    provider_string: Optional[str] = None,
    model_string: Optional[str] = None,
) -> List[SearchResult]:
    """Embed ``query_text`` and retrieve the most semantically similar content.

    Returns the single best-matching chunk per item as the evidence excerpt,
    ranked across the requested sources by cosine distance. Structured filters are
    applied as bound parameters inside each source's ranking CTE, so they narrow
    the candidate set *before* ranking.

    Args:
        query_text: Natural-language query.
        top_k: Maximum number of distinct items to return.
        min_similarity: Minimum similarity score (0–100) to include a result.
        speaker_filter: Optional partial speaker name (case-insensitive ``ILIKE``).
            Excludes votes (which have no speaker).
        date_from: Optional inclusive lower bound on meeting date.
        date_to: Optional inclusive upper bound on meeting date.
        agenda_item: Optional exact ``agenda_item_id`` to restrict to.
        source: Restrict to one source: ``spoken`` | ``written`` | ``vote``.
            ``None`` (default) spans all three.
        provider_string: Embedding provider key (defaults to settings).
        model_string: Embedding model name (defaults to settings).
    """
    if not provider_string:
        provider_string = settings.embedding_provider
    if not model_string:
        model_string = settings.embedding_model

    requested = _resolve_sources(source)

    provider = PROVIDER_REGISTER.get(provider_string)(model_string)
    model_meta = MODEL_METADATA_REGISTRY.get(provider.model_name, {})

    # Apply the model's query prefix before embedding — the query and document
    # vectors must live in the same subspace or cosine distance is meaningless.
    query_prefix = model_meta.get("query_prefix", "")
    prefixed_query = f"{query_prefix}{query_text}" if query_prefix else query_text

    logger.info("Embedding query with model '%s': '%s'", provider.model_name, prefixed_query)
    query_vector = provider.embed_batch([prefixed_query])[0]

    # Shared bound parameters. Per-source `source_type` is hardcoded in each CTE's
    # join (not user-supplied); every user value is bound separately.
    base_params: dict = {
        "query_embedding": str(query_vector),
        "model_name": provider.model_name,
        # Over-fetch per source so global re-ranking + thresholding doesn't
        # silently truncate before the top_k cut.
        "limit": top_k * 5,
    }

    def _conditions(source_type: str) -> tuple:
        """Build (conditions, params) for one source's CTE."""
        conds = [
            "se.model_name = :model_name",
            f"se.source_type = '{source_type}'",
        ]
        params = dict(base_params)
        if date_from:
            conds.append("m.meeting_date >= :date_from")
            params["date_from"] = coerce_datetime(date_from)
        if date_to:
            conds.append("m.meeting_date <= :date_to")
            params["date_to"] = coerce_datetime(date_to, end_of_day=True)
        if agenda_item:
            # column name is identical across the source tables
            col = "s.agenda_item_id" if source_type == "speech" else (
                "w.agenda_item_id" if source_type == "written" else "v.agenda_item_id"
            )
            conds.append(f"{col} = :agenda_item")
            params["agenda_item"] = agenda_item
        if speaker_filter:
            if source_type == "speech":
                conds.append("s.speaker_name ILIKE :speaker_filter")
            else:  # written
                conds.append(
                    "COALESCE(w.speaker_name_english, w.speaker_job_title_english) ILIKE :speaker_filter"
                )
            params["speaker_filter"] = f"%{speaker_filter}%"
        return conds, params

    runners = {
        "speech": _query_speeches,
        "written": _query_written,
        "vote": _query_votes,
    }

    rows: list = []
    with get_session(settings.database_url) as session:
        for source_type in requested:
            # Votes carry no speaker — a speaker filter excludes them entirely.
            if source_type == "vote" and speaker_filter:
                continue
            conds, params = _conditions(source_type)
            for row in runners[source_type](session, params, conds):
                rows.append((source_type, row))

    # Global re-rank across sources by cosine distance (smaller = closer).
    rows.sort(key=lambda pair: pair[1].cosine_distance)

    results: List[SearchResult] = []
    for source_type, row in rows:
        similarity = (1 - row.cosine_distance) * 100
        if similarity < min_similarity:
            continue
        is_speech = source_type == "speech"
        results.append(SearchResult(
            speech_id=row.source_id if is_speech else None,
            speaker_name=row.speaker_name,
            meeting_date=row.meeting_date,
            agenda_item_id=row.agenda_item_id,
            chunk_text=row.chunk_text,
            speech_text=row.full_text if is_speech else None,
            cosine_distance=row.cosine_distance,
            similarity_score=round(similarity, 2),
            senedd_tv_url=row.senedd_tv_url,
            source_type=source_type,
            source_id=row.source_id,
            meeting_id=row.meeting_id,
            full_text=row.full_text,
        ))
        if len(results) >= top_k:
            break

    return results
