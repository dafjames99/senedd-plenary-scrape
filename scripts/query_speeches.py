"""Semantic search over Senedd speech embeddings."""
import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from sqlalchemy import text
import sys
from pathlib import Path
import logging

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import setup_logging, settings
from src.db.pipeline import SeneddPipeline
from src.embeddings.providers import PROVIDER_REGISTER
from src.embeddings.config import MODEL_METADATA_REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    speech_id: int
    speaker_name: str
    meeting_date: Optional[datetime]
    agenda_item_id: str
    chunk_text: str
    speech_text: str
    cosine_distance: float
    similarity_score: float
    senedd_tv_url: Optional[str]


def semantic_search(
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    speaker_filter: Optional[str] = None,
    provider_string: Optional[str] = None,
    model_string: Optional[str] = None,
) -> List[SearchResult]:
    """
    Embeds query_text and retrieves the top_k most semantically similar speeches,
    returning the single best-matching chunk per speech as the evidence excerpt.

    Args:
        query_text:      Natural language query.
        top_k:           Maximum number of distinct speeches to return.
        min_similarity:  Minimum similarity score (0–100) to include a result.
        speaker_filter:  Optional partial speaker name to restrict search to.
        provider_string: Embedding provider key (defaults to settings).
        model_string:    Embedding model name (defaults to settings).
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

    # The speaker_clause is a hardcoded string — user value is bound separately
    # via :speaker_filter to avoid any injection risk.
    speaker_clause = "AND s.speaker_name ILIKE :speaker_filter" if speaker_filter else ""

    # CTE ranks all chunks within each speech by cosine distance, then the
    # outer query keeps only rank-1 (best chunk per speech) before final sort.
    # A LATERAL join fetches the earliest SeneddTV URL for the speech.
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
            WHERE se.model_name = :model_name
            {speaker_clause}
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

    params: dict = {
        "query_embedding": str(query_vector),
        "model_name": provider.model_name,
        # Over-fetch so threshold filtering doesn't silently truncate results
        "limit": top_k * 5,
    }
    if speaker_filter:
        params["speaker_filter"] = f"%{speaker_filter}%"

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


def display_results(query_text: str, results: List[SearchResult]) -> None:
    print("\n" + "=" * 80)
    print(f"SEMANTIC SEARCH: '{query_text}'")
    print("=" * 80)

    if not results:
        print("[-] No matching speeches found.")
        return

    for i, r in enumerate(results, 1):
        date_str = r.meeting_date.strftime("%d %b %Y") if r.meeting_date else "unknown date"
        print(f"\n[{i}] {r.speaker_name}  |  {date_str}  |  Agenda: {r.agenda_item_id}")
        print(f"    Confidence: {r.similarity_score:.1f}%  |  Speech ID: {r.speech_id}")
        print(f"    Excerpt:    {r.chunk_text[:220].strip()}")
        if r.senedd_tv_url:
            print(f"    SeneddTV:   {r.senedd_tv_url}")
        print("-" * 60)


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Semantic search over Senedd speech embeddings")
    parser.add_argument("query", type=str, help="Natural language query, e.g. 'NHS waiting times'")
    parser.add_argument("--limit", type=int, default=5, help="Number of speeches to return (default: 5)")
    parser.add_argument("--min-similarity", type=float, default=0.0,
                        help="Minimum similarity score 0–100 to include a result (default: 0)")
    parser.add_argument("--speaker", type=str, default=None,
                        help="Restrict to a speaker name (partial match, case-insensitive)")
    args = parser.parse_args()

    results = semantic_search(
        args.query,
        top_k=args.limit,
        min_similarity=args.min_similarity,
        speaker_filter=args.speaker,
    )
    display_results(args.query, results)
