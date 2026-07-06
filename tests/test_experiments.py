"""Offline tests for the embedding experiment framework.

Everything is mocked/pure — no database, network, or provider required,
matching the rest of the suite.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.experiments.config import (
    CHUNK_STRATEGIES,
    ExperimentConfig,
    load_config,
)
from src.experiments.embedder import EmbedStats, format_chunks, purge_namespace
from src.experiments.evaluation import EvalResult, QueryTiming
from src.experiments.results import (
    append_record,
    build_record,
    latest_per_run_id,
    load_records,
    render_leaderboard,
)
from tests.eval.metrics import score_case


def _config(**overrides) -> ExperimentConfig:
    base = dict(name="test-exp", provider="ollama", model="embeddinggemma:300m")
    base.update(overrides)
    return ExperimentConfig(**base)


# ---------------------------------------------------------------------------
# Config identity and validation
# ---------------------------------------------------------------------------

def test_config_hash_stable_and_name_independent():
    a = _config(name="one")
    b = _config(name="two")
    assert a.config_hash == b.config_hash  # name is identity, not recipe


def test_config_hash_changes_with_recipe():
    assert _config().config_hash != _config(chunk_max_words=200).config_hash
    assert _config().config_hash != _config(speaker_prefix=False).config_hash


def test_notes_do_not_change_hash():
    assert _config(notes="a").config_hash == _config(notes="b").config_hash


def test_namespace_shape():
    cfg = _config()
    assert cfg.namespace == f"exp:test-exp-{cfg.config_hash[:8]}"
    assert cfg.namespace.startswith("exp:")


def test_invalid_strategy_rejected():
    with pytest.raises(ValueError, match="chunk_strategy"):
        _config(chunk_strategy="nonsense")


def test_invalid_name_rejected():
    with pytest.raises(ValueError):
        _config(name="has space")
    with pytest.raises(ValueError):
        _config(name="has:colon")
    with pytest.raises(ValueError):
        _config(name="x" * 61)


# ---------------------------------------------------------------------------
# Resolution against the model registry
# ---------------------------------------------------------------------------

def test_resolve_defaults_from_registry():
    resolved = _config().resolve()
    assert resolved.max_words == 1200  # gemma registry max_chunk_words
    assert resolved.overlap_words == 50
    assert resolved.doc_prefix == "title: none | text: "
    assert resolved.query_prefix == "task: search result | query: "


def test_resolve_whole_item_uses_model_cap_no_overlap():
    resolved = _config(chunk_strategy="whole-item", chunk_max_words=200).resolve()
    assert resolved.max_words == 1200  # cap wins; chunk_max_words ignored
    assert resolved.overlap_words == 0


def test_resolve_fixed_window_zeroes_overlap():
    resolved = _config(
        chunk_strategy="fixed-window", chunk_max_words=250, chunk_overlap_words=50
    ).resolve()
    assert resolved.max_words == 250
    assert resolved.overlap_words == 0


def test_resolve_rejects_window_beyond_model_cap():
    with pytest.raises(ValueError, match="context cap"):
        _config(chunk_max_words=5000).resolve()


def test_resolve_prefix_overrides():
    resolved = _config(doc_prefix="", query_prefix="Q: ").resolve()
    assert resolved.doc_prefix == ""       # explicit empty beats registry
    assert resolved.query_prefix == "Q: "


def test_unregistered_model_requires_explicit_values():
    cfg = _config(provider="ollama", model="not-in-registry")
    with pytest.raises(ValueError, match="not in MODEL_METADATA_REGISTRY"):
        cfg.resolve()
    resolved = _config(
        model="not-in-registry",
        chunk_max_words=400,
        doc_prefix="",
        query_prefix="",
    ).resolve()
    assert resolved.max_words == 400


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def test_load_config_defaults_name_to_stem(tmp_path: Path):
    p = tmp_path / "my-experiment.yaml"
    p.write_text("provider: ollama\nmodel: embeddinggemma:300m\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.name == "my-experiment"


def test_load_config_rejects_unknown_fields(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "provider: ollama\nmodel: m\nchunk_sise: 100\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="chunk_sise"):
        load_config(p)


def test_seed_configs_load_and_resolve():
    configs_dir = Path(__file__).resolve().parent.parent / "experiments" / "configs"
    paths = sorted(configs_dir.glob("*.yaml"))
    assert paths, "seed configs missing"
    for p in paths:
        cfg = load_config(p)
        resolved = cfg.resolve()
        assert resolved.max_words > 0
        assert cfg.chunk_strategy in CHUNK_STRATEGIES


# ---------------------------------------------------------------------------
# Chunk formatting (document-side symmetry)
# ---------------------------------------------------------------------------

def test_format_chunks_applies_speaker_and_doc_prefix():
    resolved = _config().resolve()
    body = "This is a statement about the health service in Wales."
    chunks = format_chunks(body, "Jane Hutt", resolved)
    assert len(chunks) == 1
    idx, stored, formatted = chunks[0]
    assert idx == 0
    assert stored == f"Jane Hutt: {body}"
    assert formatted == f"title: none | text: Jane Hutt: {body}"


def test_format_chunks_speaker_prefix_disabled():
    resolved = _config(speaker_prefix=False, doc_prefix="").resolve()
    body = "This is a statement about the health service in Wales."
    _, stored, formatted = format_chunks(body, "Jane Hutt", resolved)[0]
    assert stored == body
    assert formatted == body


def test_format_chunks_splits_long_text():
    resolved = _config(chunk_max_words=20, chunk_overlap_words=0).resolve()
    body = " ".join(
        f"Sentence number {i} contains exactly six words." for i in range(20)
    )
    chunks = format_chunks(body, None, resolved)
    assert len(chunks) > 1
    assert [c[0] for c in chunks] == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# Purge safety
# ---------------------------------------------------------------------------

def test_purge_refuses_non_experiment_namespace():
    with pytest.raises(ValueError, match="Refusing"):
        purge_namespace(MagicMock(), "ollama/embeddinggemma:300m")


def test_purge_deletes_experiment_namespace():
    session = MagicMock()
    session.execute.return_value.rowcount = 7
    assert purge_namespace(session, "exp:foo-abcd1234") == 7
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Results registry + leaderboard
# ---------------------------------------------------------------------------

def _record(run_id="exp-a", mrr=0.9, partial=False, recorded_at="2026-07-06T00:00:00+00:00"):
    cfg = _config(name=run_id)
    resolved = cfg.resolve()
    stats = EmbedStats(
        items_seen=10, chunks_embedded=12, cache_hits=2,
        provider_calls_chunks=10, wall_seconds=3.0,
    )
    scores = [score_case("c1", [1, 2], [1]), score_case("c2", [3], [9])]
    eval_result = EvalResult(
        scores=scores,
        timings=[QueryTiming(0.01, 0.02), QueryTiming(0.01, 0.03)],
        depth=20,
    )
    record = build_record(
        cfg, resolved, stats, eval_result,
        vector_count=12, dimensions=768, corpus_speeches=10, partial=partial,
    )
    record["run_id"] = run_id
    record["retrieval"]["mrr"] = mrr
    record["recorded_at"] = recorded_at
    return record


def test_append_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    r1, r2 = _record("a"), _record("b", mrr=0.5)
    append_record(r1, path)
    append_record(r2, path)
    loaded = load_records(path)
    assert [r["run_id"] for r in loaded] == ["a", "b"]
    assert loaded[1]["retrieval"]["mrr"] == 0.5


def test_latest_per_run_id_keeps_newest():
    old = _record("a", mrr=0.5, recorded_at="2026-01-01T00:00:00+00:00")
    new = _record("a", mrr=0.9, recorded_at="2026-02-01T00:00:00+00:00")
    latest = latest_per_run_id([old, new])
    assert len(latest) == 1
    assert latest[0]["retrieval"]["mrr"] == 0.9


def test_leaderboard_ranks_by_mrr_and_flags_partial():
    records = [
        _record("low", mrr=0.4),
        _record("high", mrr=0.95),
        _record("part", mrr=0.7, partial=True),
    ]
    md = render_leaderboard(records)
    lines = [l for l in md.splitlines() if l.startswith("| ")]
    data_rows = lines[1:]  # lines[0] is the header (separator doesn't start "| ")
    assert "high" in data_rows[0]
    assert "low" in data_rows[-1]
    assert any("⚠ partial" in l for l in data_rows)


def test_record_shape_is_json_serialisable():
    record = _record()
    json.dumps(record)  # must not raise
    assert record["config"]["provider"] == "ollama"
    assert record["corpus"]["approx_vector_mb"] == round(12 * 768 * 4 / 1_048_576, 2)
    assert record["retrieval"]["latency"]["query_total_p95_s"] > 0


# ---------------------------------------------------------------------------
# Embed loop (mocked session + provider)
# ---------------------------------------------------------------------------

class _FakeRow:
    def __init__(self, source_id, body, prefix_name):
        self.source_id = source_id
        self.body = body
        self.prefix_name = prefix_name


def test_embed_corpus_skips_short_and_advances_cursor():
    from src.experiments.embedder import embed_corpus

    resolved = _config(doc_prefix="", min_item_words=5).resolve()
    long_body = "one two three four five six seven eight nine ten"
    batches = [
        [_FakeRow(1, "too short", "A"), _FakeRow(2, long_body, "B")],
        [],  # cursor advanced past both rows
    ]
    session = MagicMock()
    session.execute.side_effect = [
        MagicMock(fetchall=MagicMock(return_value=b)) for b in batches
    ]
    provider = MagicMock()
    provider.model_name = "ollama/embeddinggemma:300m"
    provider.embed_batch.return_value = [[0.1, 0.2]]

    stats = embed_corpus(
        session, provider, resolved, batch_size=10, use_cache=False
    )

    assert stats.items_seen == 2
    assert stats.items_skipped_short == 1
    assert stats.chunks_embedded == 1
    assert stats.provider_calls_chunks == 1
    # The second SELECT must use the advanced keyset cursor.
    second_call_params = session.execute.call_args_list[1].args[1]
    assert second_call_params["after"] == 2
    session.add_all.assert_called_once()
    added = list(session.add_all.call_args.args[0])
    assert len(added) == 1
    assert added[0].model_name == resolved.config.namespace
    assert added[0].speech_id == 2  # legacy cascade FK populated


def test_embed_corpus_respects_max_items():
    from src.experiments.embedder import embed_corpus

    resolved = _config(doc_prefix="", min_item_words=0).resolve()
    body = "alpha beta gamma delta epsilon zeta eta theta"
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [_FakeRow(1, body, None)]
    provider = MagicMock()
    provider.model_name = "ollama/embeddinggemma:300m"
    provider.embed_batch.return_value = [[0.0]]

    stats = embed_corpus(
        session, provider, resolved, batch_size=10, max_items=1, use_cache=False
    )
    assert stats.items_seen == 1
    assert stats.chunks_embedded == 1


# ---------------------------------------------------------------------------
# Evaluation query symmetry
# ---------------------------------------------------------------------------

def test_retrieve_applies_query_prefix_and_namespace():
    from src.experiments.evaluation import retrieve

    resolved = _config().resolve()  # gemma: has a query prefix
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    provider = MagicMock()
    provider.embed_batch.return_value = [[0.1]]

    ranked, timing = retrieve(session, provider, resolved, "bus services", depth=5)

    provider.embed_batch.assert_called_once_with(
        ["task: search result | query: bus services"]
    )
    params = session.execute.call_args.args[1]
    assert params["namespace"] == resolved.config.namespace
    assert params["limit"] == 5
    assert ranked == []
    assert timing.total_seconds >= 0


def test_retrieve_speaker_filter_bound():
    from src.experiments.evaluation import retrieve

    resolved = _config(doc_prefix="", query_prefix="").resolve()
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    provider = MagicMock()
    provider.embed_batch.return_value = [[0.1]]

    retrieve(session, provider, resolved, "q", depth=3, speaker="Jones")

    sql = str(session.execute.call_args.args[0])
    params = session.execute.call_args.args[1]
    assert "ILIKE :speaker" in sql
    assert params["speaker"] == "%Jones%"
