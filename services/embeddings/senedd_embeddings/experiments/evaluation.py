"""Score an experiment namespace against the labelled retrieval cases.

Runs the same best-chunk-per-speech ranking CTE as production search
(``src/search/service.py``), but keyed on the experiment's vector namespace and
applying the *experiment's* query prefix — retrieval must stay symmetric with
however the documents were embedded, which may differ from the production
model's settings.

Quality metrics (MRR, hit@k, recall@k) come from ``tests/eval/metrics.py`` and
the labelled case set from ``tests/eval/cases.yaml`` — the same yardstick as
the recorded production baseline, so numbers are directly comparable. This
module is a dev/experiment tool and is always run from the repo root, where the
``tests`` package is importable.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from senedd_embeddings.base import BaseEmbeddingProvider
from senedd_embeddings.experiments.config import ResolvedConfig

from tests.eval.loader import EvalCase, load_cases  # noqa: F401  (re-exported)
from tests.eval.metrics import DEFAULT_KS, CaseScore, aggregate, score_case

# Best chunk per speech under one vector namespace — the production speech CTE
# with model_name bound to the experiment namespace and the optional speaker
# filter of the labelled cases.
_RANKING_SQL = """
    WITH ranked_chunks AS (
        SELECT
            se.embedding_vector <=> :query_embedding AS cosine_distance,
            s.speech_id,
            ROW_NUMBER() OVER (
                PARTITION BY s.speech_id
                ORDER BY se.embedding_vector <=> :query_embedding ASC
            ) AS rn
        FROM  speech_embeddings se
        JOIN  speeches s ON se.source_id = s.speech_id
        WHERE se.model_name = :namespace
          AND se.source_type = 'speech'
          {speaker_clause}
    )
    SELECT speech_id
    FROM ranked_chunks
    WHERE rn = 1
    ORDER BY cosine_distance ASC
    LIMIT :limit
"""


@dataclass
class QueryTiming:
    """Wall-clock split for one eval query."""

    embed_seconds: float
    search_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.embed_seconds + self.search_seconds


@dataclass
class EvalResult:
    """Everything the eval phase measured for one experiment run."""

    scores: List[CaseScore]
    timings: List[QueryTiming]
    depth: int
    ks: Sequence[int] = DEFAULT_KS

    @property
    def summary(self) -> Dict[str, object]:
        return aggregate(self.scores, self.ks)

    @property
    def latency_summary(self) -> Dict[str, float]:
        if not self.timings:
            return {}
        totals = sorted(t.total_seconds for t in self.timings)
        searches = sorted(t.search_seconds for t in self.timings)

        def pct(values: List[float], p: float) -> float:
            idx = min(len(values) - 1, max(0, round(p * (len(values) - 1))))
            return values[idx]

        return {
            "query_total_mean_s": statistics.fmean(totals),
            "query_total_p50_s": pct(totals, 0.50),
            "query_total_p95_s": pct(totals, 0.95),
            "search_only_mean_s": statistics.fmean(searches),
            "search_only_p95_s": pct(searches, 0.95),
        }


def retrieve(
    session: Session,
    provider: BaseEmbeddingProvider,
    resolved: ResolvedConfig,
    query: str,
    depth: int,
    speaker: Optional[str] = None,
) -> tuple[List[int], QueryTiming]:
    """Ranked speech ids for one query under the experiment namespace."""
    prefixed = (
        f"{resolved.query_prefix}{query}" if resolved.query_prefix else query
    )

    t0 = time.monotonic()
    query_vector = provider.embed_batch([prefixed])[0]
    t1 = time.monotonic()

    params = {
        "query_embedding": str(query_vector),
        "namespace": resolved.config.namespace,
        "limit": depth,
    }
    speaker_clause = ""
    if speaker:
        speaker_clause = "AND s.speaker_name ILIKE :speaker"
        params["speaker"] = f"%{speaker}%"

    rows = session.execute(
        text(_RANKING_SQL.format(speaker_clause=speaker_clause)), params
    ).fetchall()
    t2 = time.monotonic()

    return [r.speech_id for r in rows], QueryTiming(
        embed_seconds=t1 - t0, search_seconds=t2 - t1
    )


def evaluate(
    session: Session,
    provider: BaseEmbeddingProvider,
    resolved: ResolvedConfig,
    cases: Sequence[EvalCase],
    depth: int = 20,
    ks: Sequence[int] = DEFAULT_KS,
) -> EvalResult:
    """Retrieve and score every labelled case against the namespace."""
    scores: List[CaseScore] = []
    timings: List[QueryTiming] = []
    for case in cases:
        ranked, timing = retrieve(
            session, provider, resolved, case.query, depth, speaker=case.speaker
        )
        scores.append(score_case(case.id, ranked, case.relevant_speech_ids, ks))
        timings.append(timing)
    return EvalResult(scores=scores, timings=timings, depth=depth, ks=ks)
