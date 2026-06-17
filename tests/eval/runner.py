"""Run the labelled eval cases against the *live* retrieval stack.

This drives real retrieval (Postgres + the active embedding provider), so it
needs the full local stack running. It is a manual tool, not a pytest test:

    uv run python -m tests.eval.runner
    uv run python -m tests.eval.runner --k 20 --json eval_baseline.json

The retrieval entry point is intentionally the current
``scripts.query_speeches.semantic_search``. When Phase 1 moves that logic into
``src/search/``, re-point ``_retrieve`` and confirm the scoreboard is unchanged.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.query_speeches import semantic_search  # noqa: E402

from tests.eval.loader import EvalCase, load_cases  # noqa: E402
from tests.eval.metrics import DEFAULT_KS, CaseScore, aggregate, score_case  # noqa: E402

logger = logging.getLogger(__name__)


def _retrieve(
    case: EvalCase,
    depth: int,
    provider: Optional[str],
    model: Optional[str],
) -> List[int]:
    """Return retrieved speech ids (best-first) for a case, capped at ``depth``."""
    results = semantic_search(
        case.query,
        top_k=depth,
        min_similarity=0.0,
        speaker_filter=case.speaker,
        provider_string=provider,
        model_string=model,
    )
    return [r.speech_id for r in results]


def run(
    cases: Sequence[EvalCase],
    depth: int = 20,
    ks: Sequence[int] = DEFAULT_KS,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> List[CaseScore]:
    """Retrieve and score every case, returning per-case scores."""
    scores: List[CaseScore] = []
    for case in cases:
        ranked = _retrieve(case, depth, provider, model)
        scores.append(score_case(case.id, ranked, case.relevant_speech_ids, ks))
    return scores


def _print_report(
    cases: Sequence[EvalCase],
    scores: Sequence[CaseScore],
    ks: Sequence[int],
    depth: int,
) -> None:
    by_id = {c.id: c for c in cases}
    print("\n" + "=" * 78)
    print(f"RETRIEVAL EVAL  |  {len(scores)} cases  |  retrieval depth = {depth}")
    print("=" * 78)
    print(f"{'case':<38}{'first rank':>12}{'RR':>8}{'hit@10':>10}")
    print("-" * 78)
    for s in scores:
        rank = str(s.first_rank) if s.first_rank is not None else "—"
        hit10 = "✓" if s.hits.get(10) else "✗"
        flag = "  <- miss" if s.first_rank is None else ""
        print(f"{by_id[s.case_id].id:<38}{rank:>12}{s.reciprocal_rank:>8.3f}{hit10:>10}{flag}")

    summary = aggregate(scores, ks)
    print("-" * 78)
    print(f"MRR: {summary['mrr']:.3f}")
    print("hit-rate@k: " + "  ".join(f"@{k}={summary['hit_rate'][k]:.2f}" for k in ks))
    print("recall@k:   " + "  ".join(f"@{k}={summary['recall'][k]:.2f}" for k in ks))
    print("=" * 78)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run semantic-search retrieval eval")
    parser.add_argument("--cases", type=Path, default=None, help="Path to cases.yaml")
    parser.add_argument("--k", type=int, default=20, help="Retrieval depth (default: 20)")
    parser.add_argument("--provider", type=str, default=None, help="Override embedding provider")
    parser.add_argument("--model", type=str, default=None, help="Override embedding model")
    parser.add_argument("--json", type=Path, default=None, help="Write summary + per-case to JSON")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases) if args.cases else load_cases()
    scores = run(cases, depth=args.k, provider=args.provider, model=args.model)
    _print_report(cases, scores, DEFAULT_KS, args.k)

    if args.json:
        payload = {
            "summary": aggregate(scores, DEFAULT_KS),
            "depth": args.k,
            "cases": [
                {
                    "id": s.case_id,
                    "first_rank": s.first_rank,
                    "reciprocal_rank": s.reciprocal_rank,
                    "hits": s.hits,
                    "recall": s.recall,
                }
                for s in scores
            ],
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote baseline to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
