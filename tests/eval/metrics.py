"""Pure ranking metrics for known-item retrieval evaluation.

Every function operates on a ranked sequence of retrieved ``speech_id`` values
and the set of ids judged relevant for a query. Nothing here touches a database
or an embedding model, so this module is unit-tested in the default test suite
and is safe to import anywhere.

Metric definitions (known-item / binary relevance):

* **first relevant rank** — 1-based position of the first relevant id, or ``None``.
* **reciprocal rank** — ``1 / first_relevant_rank`` (0 if none retrieved). Mean
  over cases is MRR.
* **hit@k** — whether any relevant id appears in the top ``k``.
* **recall@k** — fraction of the relevant set retrieved within the top ``k``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

DEFAULT_KS: tuple[int, ...] = (1, 3, 5, 10)


def first_relevant_rank(
    ranked_ids: Sequence[int], relevant_ids: Iterable[int]
) -> Optional[int]:
    """Return the 1-based rank of the first relevant id, or ``None`` if absent."""
    relevant = set(relevant_ids)
    for position, speech_id in enumerate(ranked_ids, start=1):
        if speech_id in relevant:
            return position
    return None


def reciprocal_rank(ranked_ids: Sequence[int], relevant_ids: Iterable[int]) -> float:
    """Return ``1 / rank`` of the first relevant id, or ``0.0`` if none retrieved."""
    rank = first_relevant_rank(ranked_ids, relevant_ids)
    return 1.0 / rank if rank is not None else 0.0


def hit_at_k(ranked_ids: Sequence[int], relevant_ids: Iterable[int], k: int) -> bool:
    """Return ``True`` if any relevant id appears within the top ``k`` results."""
    relevant = set(relevant_ids)
    return any(speech_id in relevant for speech_id in ranked_ids[:k])


def recall_at_k(ranked_ids: Sequence[int], relevant_ids: Iterable[int], k: int) -> float:
    """Return the fraction of the relevant set found within the top ``k``."""
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    found = sum(1 for speech_id in ranked_ids[:k] if speech_id in relevant)
    return found / len(relevant)


@dataclass
class CaseScore:
    """Per-case scores for one query."""

    case_id: str
    first_rank: Optional[int]
    reciprocal_rank: float
    hits: Dict[int, bool] = field(default_factory=dict)
    recall: Dict[int, float] = field(default_factory=dict)


def score_case(
    case_id: str,
    ranked_ids: Sequence[int],
    relevant_ids: Iterable[int],
    ks: Sequence[int] = DEFAULT_KS,
) -> CaseScore:
    """Compute all metrics for a single query.

    Args:
        case_id: Identifier of the eval case (for reporting).
        ranked_ids: Retrieved ``speech_id`` values, best-first.
        relevant_ids: The ids judged relevant for this query.
        ks: Cut-offs at which to report hit-rate and recall.

    Returns:
        A populated :class:`CaseScore`.
    """
    relevant = set(relevant_ids)
    return CaseScore(
        case_id=case_id,
        first_rank=first_relevant_rank(ranked_ids, relevant),
        reciprocal_rank=reciprocal_rank(ranked_ids, relevant),
        hits={k: hit_at_k(ranked_ids, relevant, k) for k in ks},
        recall={k: recall_at_k(ranked_ids, relevant, k) for k in ks},
    )


def aggregate(
    scores: Sequence[CaseScore], ks: Sequence[int] = DEFAULT_KS
) -> Dict[str, object]:
    """Average per-case scores into a dataset-level summary.

    Returns a dict with ``n`` (case count), ``mrr``, and per-``k`` ``hit_rate``
    and ``recall`` means. Empty input yields zeroed metrics rather than raising.
    """
    n = len(scores)
    if n == 0:
        return {
            "n": 0,
            "mrr": 0.0,
            "hit_rate": {k: 0.0 for k in ks},
            "recall": {k: 0.0 for k in ks},
        }

    mrr = sum(s.reciprocal_rank for s in scores) / n
    hit_rate = {k: sum(s.hits[k] for s in scores) / n for k in ks}
    recall = {k: sum(s.recall[k] for s in scores) / n for k in ks}
    return {"n": n, "mrr": mrr, "hit_rate": hit_rate, "recall": recall}
