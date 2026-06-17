"""CLI for semantic search over Senedd speech embeddings.

The retrieval logic lives in ``src/search/service.py``; this module is a thin
command-line front end over it (argument parsing + terminal presentation).
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import setup_logging
from src.search.service import SearchResult, semantic_search

logger = logging.getLogger(__name__)


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
    parser.add_argument("--date-from", type=str, default=None,
                        help="Inclusive lower bound on meeting date (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=str, default=None,
                        help="Inclusive upper bound on meeting date (YYYY-MM-DD)")
    parser.add_argument("--agenda-item", type=str, default=None,
                        help="Restrict to an exact agenda_item_id")
    args = parser.parse_args()

    results = semantic_search(
        args.query,
        top_k=args.limit,
        min_similarity=args.min_similarity,
        speaker_filter=args.speaker,
        date_from=args.date_from,
        date_to=args.date_to,
        agenda_item=args.agenda_item,
    )
    display_results(args.query, results)
