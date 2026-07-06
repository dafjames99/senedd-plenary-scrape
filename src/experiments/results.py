"""Durable experiment results registry.

Every run appends one record to ``experiments/runs.jsonl`` (append-only, one
JSON object per line — the machine-readable log) and rewrites
``experiments/RESULTS.md`` (the human-readable leaderboard derived from the
log). Both are committed to git: the registry *is* the experiment record, and
a result you can't find later is a result you don't have.

A record is keyed by ``run_id`` (name + config hash), so re-running the same
config supersedes its previous entry on the leaderboard (the JSONL keeps every
attempt for provenance).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
EXPERIMENTS_DIR = ROOT_DIR / "experiments"
RUNS_JSONL = EXPERIMENTS_DIR / "runs.jsonl"
LEADERBOARD_MD = EXPERIMENTS_DIR / "RESULTS.md"


def build_record(
    config,
    resolved,
    embed_stats,
    eval_result,
    vector_count: int,
    dimensions: int | None,
    corpus_speeches: int,
    partial: bool,
) -> Dict:
    """Assemble the canonical run record from the phase outputs."""
    summary = eval_result.summary
    record = {
        "run_id": config.run_id,
        "namespace": config.namespace,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "partial_corpus": partial,
        "config": asdict(config),
        "resolved": {
            "max_words": resolved.max_words,
            "overlap_words": resolved.overlap_words,
            "min_words": resolved.min_words,
            "doc_prefix": resolved.doc_prefix,
            "query_prefix": resolved.query_prefix,
        },
        "corpus": {
            "speeches": corpus_speeches,
            "vectors": vector_count,
            "dimensions": dimensions,
            "approx_vector_mb": (
                round(vector_count * dimensions * 4 / 1_048_576, 2)
                if dimensions
                else None
            ),
        },
        "embed": {
            "items_seen": embed_stats.items_seen,
            "items_skipped_short": embed_stats.items_skipped_short,
            "chunks_embedded": embed_stats.chunks_embedded,
            "cache_hits": embed_stats.cache_hits,
            "provider_calls_chunks": embed_stats.provider_calls_chunks,
            "wall_seconds": round(embed_stats.wall_seconds, 2),
            "chunks_per_second": round(embed_stats.chunks_per_second, 2),
        },
        "retrieval": {
            "depth": eval_result.depth,
            "n_cases": summary["n"],
            "mrr": round(summary["mrr"], 4),
            "hit_rate": {str(k): round(v, 4) for k, v in summary["hit_rate"].items()},
            "recall": {str(k): round(v, 4) for k, v in summary["recall"].items()},
            "latency": {
                k: round(v, 4) for k, v in eval_result.latency_summary.items()
            },
            "cases": [
                {
                    "id": s.case_id,
                    "first_rank": s.first_rank,
                    "reciprocal_rank": round(s.reciprocal_rank, 4),
                }
                for s in eval_result.scores
            ],
        },
    }
    return record


def append_record(record: Dict, path: Path = RUNS_JSONL) -> None:
    """Append one run record to the JSONL log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def load_records(path: Path = RUNS_JSONL) -> List[Dict]:
    """All recorded runs, oldest first. Missing file = no runs yet."""
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def latest_per_run_id(records: List[Dict]) -> List[Dict]:
    """Collapse re-runs: keep only the newest record per ``run_id``."""
    latest: Dict[str, Dict] = {}
    for record in records:  # oldest → newest, so later entries win
        latest[record["run_id"]] = record
    return list(latest.values())


def render_leaderboard(records: List[Dict]) -> str:
    """Markdown leaderboard: latest record per run, best MRR first."""
    rows = sorted(
        latest_per_run_id(records),
        key=lambda r: r["retrieval"]["mrr"],
        reverse=True,
    )
    lines = [
        "# Embedding experiment leaderboard",
        "",
        "Auto-generated from `runs.jsonl` by `src/experiments/results.py` — do not",
        "edit by hand. Latest record per run id, ranked by MRR. Runs flagged",
        "`partial` embedded only part of the corpus: their quality numbers are not",
        "comparable (fewer distractors) and they rank for bookkeeping only.",
        "",
        "| rank | run | model | strategy | words/overlap | MRR | hit@3 | recall@5 | "
        "p95 query (s) | chunks | embed (s) | ~MB | recorded |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for i, r in enumerate(rows, start=1):
        cfg, res, ret, emb = r["config"], r["resolved"], r["retrieval"], r["embed"]
        run_label = r["run_id"] + (" ⚠ partial" if r.get("partial_corpus") else "")
        lines.append(
            "| {rank} | {run} | {model} | {strategy} | {words}/{overlap} | {mrr:.3f} "
            "| {hit3:.2f} | {rec5:.2f} | {p95} | {chunks} | {wall} | {mb} | {date} |".format(
                rank=i,
                run=run_label,
                model=f"{cfg['provider']}/{cfg['model']}",
                strategy=cfg["chunk_strategy"],
                words=res["max_words"],
                overlap=res["overlap_words"],
                mrr=ret["mrr"],
                hit3=ret["hit_rate"].get("3", 0.0),
                rec5=ret["recall"].get("5", 0.0),
                p95=ret["latency"].get("query_total_p95_s", "—"),
                chunks=emb["chunks_embedded"],
                wall=emb["wall_seconds"],
                mb=r["corpus"]["approx_vector_mb"] or "—",
                date=r["recorded_at"][:10],
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_leaderboard(path: Path = LEADERBOARD_MD, runs_path: Path = RUNS_JSONL) -> None:
    """Regenerate the leaderboard markdown from the JSONL log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_leaderboard(load_records(runs_path)), encoding="utf-8")
