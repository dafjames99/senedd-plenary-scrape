"""Tests for the content-addressed embedding cache (src/embeddings/cache.py).

Unit tests — the DB session and provider are mocked, so no PostgreSQL or GPU is
needed. They verify the hashing contract and the cache/embed split in the
pipeline (only misses reach the provider; vectors return in original order).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.embeddings import cache as embed_cache
from src.embeddings.pipeline import EmbeddingPipeline

MODEL = "ollama/embeddinggemma:300m"  # a real key in MODEL_METADATA_REGISTRY


# ---------------------------------------------------------------------------
# Hashing contract
# ---------------------------------------------------------------------------

def test_hash_chunk_is_deterministic():
    assert embed_cache.hash_chunk("Jones: hello") == embed_cache.hash_chunk("Jones: hello")


def test_hash_chunk_is_content_sensitive():
    # Any change to the embedded string — speaker prefix, doc prefix, body — must
    # change the hash, so a cache hit can only ever be byte-identical.
    base = embed_cache.hash_chunk("title: none | text: Jones: hello")
    assert base != embed_cache.hash_chunk("title: none | text: Jones: goodbye")  # body
    assert base != embed_cache.hash_chunk("title: none | text: Smith: hello")    # speaker
    assert base != embed_cache.hash_chunk("Jones: hello")                         # doc prefix


def test_config_version_encodes_chunk_words():
    assert embed_cache.config_version(1200) == f"{embed_cache.CHUNK_CONFIG_VERSION}:mw1200"


def test_seed_reconstruction_matches_pipeline_hash():
    # The pipeline embeds `doc_prefix + speaker_prefix + chunk` but stores only
    # `speaker_prefix + chunk` as chunk_text. The cache seed rebuilds the embedded
    # string as `doc_prefix + chunk_text` — these must hash identically, or seeded
    # vectors would never be hit on re-embed.
    doc_prefix, speaker_prefix, chunk = "title: none | text: ", "Jones: ", "hello world"
    pipeline_hash = embed_cache.hash_chunk(f"{doc_prefix}{speaker_prefix}{chunk}")
    stored_chunk_text = f"{speaker_prefix}{chunk}"
    seed_hash = embed_cache.hash_chunk(f"{doc_prefix}{stored_chunk_text}")
    assert pipeline_hash == seed_hash


# ---------------------------------------------------------------------------
# Pipeline integration: cache lookup -> embed misses only -> write-through
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline():
    provider = MagicMock()
    provider.model_name = MODEL
    # sqlite URL avoids any real connection; the cache helpers are patched out.
    return EmbeddingPipeline(provider=provider, db_path="sqlite://")


def test_all_miss_embeds_everything_and_writes_through(pipeline):
    chunks = ["a", "b", "c"]
    pipeline.provider.embed_batch.return_value = [[1.0], [2.0], [3.0]]
    with patch.object(embed_cache, "lookup", return_value={}) as mock_lookup, \
         patch.object(embed_cache, "store") as mock_store:
        out = pipeline._embed_with_cache(MagicMock(), chunks, max_words=1200)

    pipeline.provider.embed_batch.assert_called_once_with(chunks)
    mock_lookup.assert_called_once()
    assert mock_store.call_count == 1
    assert out == [[1.0], [2.0], [3.0]]


def test_all_hit_skips_the_provider(pipeline):
    chunks = ["a", "b"]
    hits = {embed_cache.hash_chunk("a"): [9.0], embed_cache.hash_chunk("b"): [8.0]}
    with patch.object(embed_cache, "lookup", return_value=hits), \
         patch.object(embed_cache, "store") as mock_store:
        out = pipeline._embed_with_cache(MagicMock(), chunks, max_words=1200)

    pipeline.provider.embed_batch.assert_not_called()
    mock_store.assert_not_called()
    assert out == [[9.0], [8.0]]


def test_mixed_embeds_only_misses_and_preserves_order(pipeline):
    chunks = ["a", "b", "c", "d"]
    # b and d are cached; a and c must be computed, in that order.
    hits = {embed_cache.hash_chunk("b"): [20.0], embed_cache.hash_chunk("d"): [40.0]}
    pipeline.provider.embed_batch.return_value = [[10.0], [30.0]]
    with patch.object(embed_cache, "lookup", return_value=hits), \
         patch.object(embed_cache, "store") as mock_store:
        out = pipeline._embed_with_cache(MagicMock(), chunks, max_words=1200)

    pipeline.provider.embed_batch.assert_called_once_with(["a", "c"])
    assert out == [[10.0], [20.0], [30.0], [40.0]]
    # Only the freshly computed vectors are written back.
    stored_entries = mock_store.call_args.args[2]
    assert [e[0] for e in stored_entries] == [embed_cache.hash_chunk("a"), embed_cache.hash_chunk("c")]


def test_disabled_cache_is_passthrough(pipeline):
    pipeline.cache_enabled = False
    chunks = ["a", "b"]
    pipeline.provider.embed_batch.return_value = [[1.0], [2.0]]
    with patch.object(embed_cache, "lookup") as mock_lookup, \
         patch.object(embed_cache, "store") as mock_store:
        out = pipeline._embed_with_cache(MagicMock(), chunks, max_words=1200)

    pipeline.provider.embed_batch.assert_called_once_with(chunks)
    mock_lookup.assert_not_called()
    mock_store.assert_not_called()
    assert out == [[1.0], [2.0]]
