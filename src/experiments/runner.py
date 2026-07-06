"""Run one embedding experiment end-to-end: embed → evaluate → record.

    uv run python -m src.experiments.runner --config experiments/configs/foo.yaml
    uv run python -m src.experiments.runner --config ... --limit 200   # smoke test
    uv run python -m src.experiments.runner --list                     # namespaces in DB
    uv run python -m src.experiments.runner --purge exp:foo-a1b2c3d4   # drop vectors

Needs the full local stack (Postgres with the transformed corpus + the
config's embedding provider). Vectors are kept after the run by default so the
namespace can be inspected interactively; pass --no-keep to purge on success,
or --purge later. See experiments/README.md for the procedure this implements.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import text  # noqa: E402

from src.db.session import get_session  # noqa: E402
from src.db.settings import settings, setup_logging  # noqa: E402
from src.embeddings.providers import PROVIDER_REGISTER  # noqa: E402
from src.experiments import embedder, results  # noqa: E402
from src.experiments.config import ExperimentConfig, load_config  # noqa: E402
from src.experiments.evaluation import evaluate, load_cases  # noqa: E402

logger = logging.getLogger(__name__)


def _build_provider(config: ExperimentConfig):
    provider_cls = PROVIDER_REGISTER.get(config.provider)
    if provider_cls is None:
        raise ValueError(
            f"Unknown provider {config.provider!r}. "
            f"Registered: {', '.join(PROVIDER_REGISTER)}."
        )
    return provider_cls(config.model)


def run_experiment(
    config: ExperimentConfig,
    depth: int = 20,
    limit: Optional[int] = None,
    keep: bool = True,
    batch_size: Optional[int] = None,
    use_cache: Optional[bool] = None,
) -> dict:
    """Execute one experiment and return its recorded result."""
    resolved = config.resolve()
    provider = _build_provider(config)
    batch_size = batch_size or settings.embed_batch_size
    use_cache = settings.embed_cache_enabled if use_cache is None else use_cache

    logger.info("Experiment %s → namespace %s", config.run_id, config.namespace)
    if limit:
        logger.warning(
            "--limit %d: partial corpus. Quality numbers will NOT be comparable "
            "to full-corpus runs (fewer distractors); the record is flagged.",
            limit,
        )

    session = get_session(settings.database_url)
    try:
        corpus_speeches = session.execute(
            text("SELECT COUNT(*) FROM speeches")
        ).scalar_one()

        # ---- Phase 1: embed ------------------------------------------------
        embed_stats = embedder.embed_corpus(
            session,
            provider,
            resolved,
            batch_size=batch_size,
            max_items=limit,
            use_cache=use_cache,
        )
        vector_count = embedder.count_vectors(session, config.namespace)
        if vector_count == 0:
            raise RuntimeError(
                "No vectors were produced — is the speeches table populated?"
            )
        dimensions = session.execute(
            text(
                "SELECT vector_dims(embedding_vector) FROM speech_embeddings "
                "WHERE model_name = :ns LIMIT 1"
            ),
            {"ns": config.namespace},
        ).scalar_one_or_none()

        # ---- Phase 2: evaluate ---------------------------------------------
        cases = load_cases()
        eval_result = evaluate(session, provider, resolved, cases, depth=depth)

        # ---- Phase 3: record -----------------------------------------------
        partial = bool(limit) and (
            embed_stats.items_seen + embed_stats.items_skipped_short < corpus_speeches
            or limit < corpus_speeches
        )
        record = results.build_record(
            config,
            resolved,
            embed_stats,
            eval_result,
            vector_count=vector_count,
            dimensions=dimensions,
            corpus_speeches=corpus_speeches,
            partial=partial,
        )
        results.append_record(record)
        results.write_leaderboard()
        _print_report(record)

        if not keep:
            removed = embedder.purge_namespace(session, config.namespace)
            logger.info("Purged %d vectors from %s", removed, config.namespace)
        return record
    finally:
        session.close()


def _print_report(record: dict) -> None:
    ret = record["retrieval"]
    emb = record["embed"]
    print("\n" + "=" * 72)
    print(f"EXPERIMENT {record['run_id']}"
          + ("  (PARTIAL CORPUS — not comparable)" if record["partial_corpus"] else ""))
    print("=" * 72)
    print(f"vectors: {record['corpus']['vectors']}  "
          f"dims: {record['corpus']['dimensions']}  "
          f"~{record['corpus']['approx_vector_mb']} MB")
    print(f"embed:   {emb['chunks_embedded']} chunks in {emb['wall_seconds']}s "
          f"({emb['chunks_per_second']}/s, {emb['cache_hits']} cache hits)")
    print(f"quality: MRR {ret['mrr']:.3f}  "
          + "  ".join(f"hit@{k}={v:.2f}" for k, v in ret["hit_rate"].items()))
    print("         "
          + "  ".join(f"recall@{k}={v:.2f}" for k, v in ret["recall"].items()))
    if ret["latency"]:
        print(f"latency: mean {ret['latency']['query_total_mean_s']}s  "
              f"p95 {ret['latency']['query_total_p95_s']}s "
              f"(search-only p95 {ret['latency']['search_only_p95_s']}s)")
    print("logged:  experiments/runs.jsonl + experiments/RESULTS.md")
    print("=" * 72)


def _list_namespaces() -> int:
    session = get_session(settings.database_url)
    try:
        rows = session.execute(
            text(
                "SELECT model_name, COUNT(*) AS vectors "
                "FROM speech_embeddings WHERE model_name LIKE 'exp:%' "
                "GROUP BY model_name ORDER BY model_name"
            )
        ).fetchall()
    finally:
        session.close()
    if not rows:
        print("No experiment namespaces in the database.")
        return 0
    for row in rows:
        print(f"{row.model_name:<60} {row.vectors} vectors")
    return 0


def _purge(namespace: str) -> int:
    session = get_session(settings.database_url)
    try:
        removed = embedder.purge_namespace(session, namespace)
    finally:
        session.close()
    print(f"Purged {removed} vectors from {namespace}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run an embedding experiment")
    parser.add_argument("--config", type=Path, help="Experiment YAML config")
    parser.add_argument("--depth", type=int, default=20, help="Retrieval depth (default 20)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Embed at most N speeches (smoke test; flags run as partial)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Embedding batch size (default: EMBED_BATCH_SIZE)")
    parser.add_argument("--no-keep", action="store_true",
                        help="Purge the namespace's vectors after a successful run")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass the embedding cache (true provider timings)")
    parser.add_argument("--list", action="store_true",
                        help="List experiment namespaces present in the database")
    parser.add_argument("--purge", type=str, metavar="NAMESPACE",
                        help="Delete all vectors for one exp: namespace and exit")
    args = parser.parse_args(argv)

    setup_logging()

    if args.list:
        return _list_namespaces()
    if args.purge:
        return _purge(args.purge)
    if not args.config:
        parser.error("--config is required (or use --list / --purge)")

    config = load_config(args.config)
    run_experiment(
        config,
        depth=args.depth,
        limit=args.limit,
        keep=not args.no_keep,
        batch_size=args.batch_size,
        use_cache=False if args.no_cache else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
