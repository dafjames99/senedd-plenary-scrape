"""Tests for semantic_search in src/search/service.py (+ the CLI display helper).

These are unit tests — the database session and embedding provider are mocked,
so no PostgreSQL instance or GPU is required. The tests verify query construction
logic, result filtering, and output structure, not vector retrieval accuracy.
"""
import io
import sys
from datetime import datetime
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from scripts.query_speeches import display_results
from src.search.service import SearchResult, semantic_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Row:
    """Stand-in for a SQLAlchemy Row with the common per-source column shape.

    Every per-source ranking query in ``service.py`` projects to the same column
    names (source_id, speaker_name, meeting_id, meeting_date, agenda_item_id,
    full_text, chunk_text, cosine_distance, senedd_tv_url), so one row stub covers
    all of them. The ``speech_id``/``speech_text``/``spoken_url`` keyword aliases
    keep the older call sites readable.
    """
    def __init__(
        self,
        speech_id: int = 1,
        speaker_name: str = "Test Speaker",
        speech_text: str = "Full speech text.",
        agenda_item_id: str = "AGN-001",
        meeting_date: Optional[datetime] = None,
        chunk_text: str = "Relevant passage from speech.",
        cosine_distance: float = 0.2,
        spoken_url: Optional[str] = None,
        meeting_id: int = 100,
    ):
        self.source_id = speech_id
        self.speaker_name = speaker_name
        self.full_text = speech_text
        self.agenda_item_id = agenda_item_id
        self.meeting_id = meeting_id
        self.meeting_date = meeting_date or datetime(2026, 1, 15)
        self.chunk_text = chunk_text
        self.cosine_distance = cosine_distance
        self.senedd_tv_url = spoken_url


def _make_search_setup(rows, model_name="test/model", query_prefix=""):
    """Return (mock_register, mock_registry, mock_pipeline) configured for a test run."""
    mock_provider = MagicMock()
    mock_provider.model_name = model_name
    mock_provider.embed_batch.return_value = [[0.1, 0.2, 0.3]]

    mock_register = MagicMock()
    mock_register.get.return_value = lambda _: mock_provider

    mock_registry = {model_name: {"query_prefix": query_prefix, "doc_prefix": ""}}

    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.execute.return_value.fetchall.return_value = rows

    mock_pipeline = MagicMock()
    mock_pipeline.return_value.SessionLocal.return_value = mock_session

    return mock_provider, mock_register, mock_registry, mock_pipeline


# ---------------------------------------------------------------------------
# Tests: query construction
# ---------------------------------------------------------------------------

def test_query_prefix_applied():
    """Model-specific query_prefix must be prepended before embed_batch is called."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(
        rows=[], query_prefix="task: search | query: "
    )
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("NHS reform", source="spoken", provider_string="test", model_string="test/model")

    mock_provider.embed_batch.assert_called_once_with(["task: search | query: NHS reform"])


def test_no_query_prefix_when_empty():
    """When query_prefix is empty the raw query text is embedded unchanged."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(
        rows=[], query_prefix=""
    )
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("climate policy", source="spoken", provider_string="test", model_string="test/model")

    mock_provider.embed_batch.assert_called_once_with(["climate policy"])


def test_model_name_passed_to_sql():
    """model_name must appear in the SQL parameters so results are scoped to one model."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=[])
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("budget", source="spoken", provider_string="test", model_string="test/model")

    call_params = mock_pipeline.return_value.SessionLocal.return_value.execute.call_args[0][1]
    assert call_params["model_name"] == "test/model"


def test_speaker_filter_in_params():
    """speaker_filter must be passed as a wildcard-wrapped bound parameter."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=[])
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("housing", speaker_filter="Jones",
                        source="spoken", provider_string="test", model_string="test/model")

    call_params = mock_pipeline.return_value.SessionLocal.return_value.execute.call_args[0][1]
    assert call_params["speaker_filter"] == "%Jones%"


def test_no_speaker_filter_param_when_omitted():
    """speaker_filter key must not appear in params when the argument is None."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=[])
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("housing", source="spoken", provider_string="test", model_string="test/model")

    call_params = mock_pipeline.return_value.SessionLocal.return_value.execute.call_args[0][1]
    assert "speaker_filter" not in call_params


def test_structured_filters_bound_when_provided():
    """date_from/date_to/agenda_item must be passed as bound params when given."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=[])
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("housing", date_from="2026-03-01", date_to="2026-03-31",
                        agenda_item="260301-2",
                        source="spoken", provider_string="test", model_string="test/model")

    call_params = mock_pipeline.return_value.SessionLocal.return_value.execute.call_args[0][1]
    assert call_params["date_from"] == datetime(2026, 3, 1, 0, 0, 0)
    # Bare date_to is pushed to end-of-day so the whole day is inclusive.
    assert call_params["date_to"] == datetime(2026, 3, 31, 23, 59, 59, 999999)
    assert call_params["agenda_item"] == "260301-2"


def test_structured_filters_absent_when_omitted():
    """No date/agenda keys should appear in params when the arguments are None."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=[])
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        semantic_search("housing", source="spoken", provider_string="test", model_string="test/model")

    call_params = mock_pipeline.return_value.SessionLocal.return_value.execute.call_args[0][1]
    assert "date_from" not in call_params
    assert "date_to" not in call_params
    assert "agenda_item" not in call_params


# ---------------------------------------------------------------------------
# Tests: result filtering
# ---------------------------------------------------------------------------

def test_min_similarity_excludes_low_confidence():
    """Rows whose similarity score falls below min_similarity must be dropped."""
    rows = [
        _Row(speech_id=1, cosine_distance=0.6),   # similarity = 40%
        _Row(speech_id=2, cosine_distance=0.1),   # similarity = 90%
    ]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("test query", min_similarity=50.0,
                                  source="spoken", provider_string="test", model_string="test/model")

    assert len(results) == 1
    assert results[0].speech_id == 2


def test_top_k_limits_results():
    """No more than top_k results should be returned even if DB returns more."""
    rows = [_Row(speech_id=i, cosine_distance=0.1 * i) for i in range(1, 11)]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("test query", top_k=3,
                                  source="spoken", provider_string="test", model_string="test/model")

    assert len(results) == 3


def test_no_results_returns_empty_list():
    """Empty DB response must return an empty list without raising."""
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=[])
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("anything", source="spoken", provider_string="test", model_string="test/model")

    assert results == []


# ---------------------------------------------------------------------------
# Tests: SearchResult structure
# ---------------------------------------------------------------------------

def test_result_fields_populated():
    """SearchResult must carry all context fields from the DB row."""
    meeting_date = datetime(2026, 3, 10)
    rows = [_Row(
        speech_id=42,
        speaker_name="Eluned Morgan AS",
        speech_text="Full text of the speech.",
        agenda_item_id="260310-4",
        meeting_date=meeting_date,
        chunk_text="Eluned Morgan AS: We must act on this.",
        cosine_distance=0.15,
        spoken_url="http://www.senedd.tv/en/9999?startPos=120",
    )]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("mental health", source="spoken", provider_string="test", model_string="test/model")

    assert len(results) == 1
    r = results[0]
    assert r.speech_id == 42
    assert r.speaker_name == "Eluned Morgan AS"
    assert r.meeting_date == meeting_date
    assert r.agenda_item_id == "260310-4"
    assert r.speech_text == "Full text of the speech."
    assert r.chunk_text == "Eluned Morgan AS: We must act on this."
    assert r.senedd_tv_url == "http://www.senedd.tv/en/9999?startPos=120"
    assert abs(r.similarity_score - 85.0) < 0.1
    assert isinstance(r, SearchResult)


def test_similarity_score_computed_correctly():
    """similarity_score = (1 - cosine_distance) * 100, rounded to 2dp."""
    rows = [_Row(cosine_distance=0.234)]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("test", source="spoken", provider_string="test", model_string="test/model")

    assert results[0].similarity_score == round((1 - 0.234) * 100, 2)


# ---------------------------------------------------------------------------
# Tests: display_results
# ---------------------------------------------------------------------------

def test_display_results_empty(capsys):
    display_results("climate change", [])
    captured = capsys.readouterr()
    assert "No matching speeches found" in captured.out


def test_display_results_shows_speaker_and_date(capsys):
    results = [SearchResult(
        speech_id=1,
        speaker_name="Mark Drakeford AS",
        meeting_date=datetime(2026, 3, 1),
        agenda_item_id="260301-2",
        chunk_text="Mark Drakeford AS: We are committed to this.",
        speech_text="Full speech.",
        cosine_distance=0.1,
        similarity_score=90.0,
        senedd_tv_url=None,
    )]
    display_results("NHS", results)
    captured = capsys.readouterr()
    assert "Mark Drakeford AS" in captured.out
    assert "01 Mar 2026" in captured.out
    assert "90.0%" in captured.out
    assert "260301-2" in captured.out


def test_display_results_shows_senedd_tv_url(capsys):
    results = [SearchResult(
        speech_id=2,
        speaker_name="Speaker",
        meeting_date=datetime(2026, 1, 1),
        agenda_item_id="AGN-1",
        chunk_text="Some text.",
        speech_text="Full.",
        cosine_distance=0.2,
        similarity_score=80.0,
        senedd_tv_url="http://www.senedd.tv/en/1234",
    )]
    display_results("query", results)
    captured = capsys.readouterr()
    assert "http://www.senedd.tv/en/1234" in captured.out


# ---------------------------------------------------------------------------
# Tests: source filter (polymorphic search)
# ---------------------------------------------------------------------------

def test_unknown_source_raises():
    """An unrecognised source value is rejected before any DB/embedding work."""
    with pytest.raises(ValueError, match="Unknown source"):
        semantic_search("anything", source="bogus",
                        provider_string="test", model_string="test/model")


def test_speech_source_sets_discriminator_and_back_compat_ids():
    """A speech hit carries source_type='speech' and mirrors source_id→speech_id."""
    rows = [_Row(speech_id=7, speech_text="Body.", spoken_url=None)]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("x", source="spoken",
                                  provider_string="test", model_string="test/model")
    r = results[0]
    assert r.source_type == "speech"
    assert r.source_id == 7
    assert r.speech_id == 7          # back-compat mirror
    assert r.speech_text == "Body."  # populated for speeches


def test_written_source_maps_non_speech_fields():
    """A written hit has source_type='written', source_id set, and speech_id None."""
    rows = [_Row(speech_id=3, speaker_name="First Minister", speech_text="Answer text.")]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("x", source="written",
                                  provider_string="test", model_string="test/model")
    r = results[0]
    assert r.source_type == "written"
    assert r.source_id == 3
    assert r.speech_id is None       # not a speech
    assert r.speech_text is None     # speech-only convenience field
    assert r.full_text == "Answer text."


def test_votes_excluded_when_speaker_filter_supplied():
    """Votes have no speaker, so a speaker filter must yield no vote results."""
    rows = [_Row(speech_id=1, cosine_distance=0.1)]
    mock_provider, mock_register, mock_registry, mock_pipeline = _make_search_setup(rows=rows)
    with (
        patch("src.search.service.PROVIDER_REGISTER", mock_register),
        patch("src.search.service.MODEL_METADATA_REGISTRY", mock_registry),
        patch("src.search.service.SeneddPipeline", mock_pipeline),
    ):
        results = semantic_search("x", source="vote", speaker_filter="Jones",
                                  provider_string="test", model_string="test/model")
    assert results == []
    # The vote query must never have been executed.
    mock_pipeline.return_value.SessionLocal.return_value.execute.assert_not_called()


def test_spoken_alias_resolves_to_speech_source():
    """'spoken' and 'speech' are accepted aliases for the speech source."""
    from src.search.service import _resolve_sources
    assert _resolve_sources("spoken") == ["speech"]
    assert _resolve_sources("speech") == ["speech"]
    assert _resolve_sources("written") == ["written"]
    assert _resolve_sources("vote") == ["vote"]
    assert _resolve_sources(None) == ["speech", "written", "vote"]
