"""Offline tests for the search layer's pure logic (no DB).

Date coercion and the agenda-thread argument guard are exercised here; the
database-backed query behaviour is verified manually against the live corpus.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.search._dates import coerce_datetime
from src.search.lookups import (
    _qa_role,
    _vote_result,
    get_agenda_thread,
    get_written_answers,
)


def test_coerce_datetime_parses_iso_date():
    assert coerce_datetime("2026-03-01") == datetime(2026, 3, 1, 0, 0, 0)


def test_coerce_datetime_end_of_day_for_bare_date():
    assert coerce_datetime("2026-03-01", end_of_day=True) == datetime(
        2026, 3, 1, 23, 59, 59, 999999
    )


def test_coerce_datetime_preserves_explicit_time():
    # A supplied time component is never overwritten, even with end_of_day.
    value = datetime(2026, 3, 1, 9, 30)
    assert coerce_datetime(value, end_of_day=True) == value


def test_coerce_datetime_passthrough_datetime():
    value = datetime(2026, 3, 1, 12, 0)
    assert coerce_datetime(value) == value


def test_get_agenda_thread_requires_an_anchor():
    # Validation happens before any DB access, so this is safe offline.
    with pytest.raises(ValueError):
        get_agenda_thread()
    with pytest.raises(ValueError):
        get_agenda_thread(meeting_id=123)  # agenda_item_id missing


# ---------------------------------------------------------------------------
# Enum name → value mapping (raw SQL returns the stored enum *name*)
# ---------------------------------------------------------------------------

def test_vote_result_maps_enum_name_to_value():
    # SQLAlchemy persists Enum by name, so raw SQL yields 'FOR' / 'DID_NOT_VOTE'.
    assert _vote_result("FOR") == "For"
    assert _vote_result("DID_NOT_VOTE") == "DidNotVote"
    assert _vote_result("AGAINST") == "Against"


def test_vote_result_passthrough_unknown_or_value():
    # An already-friendly value (or anything unrecognised) is returned unchanged.
    assert _vote_result("For") == "For"
    assert _vote_result(None) is None


def test_qa_role_maps_enum_name_to_value():
    assert _qa_role("QUESTION") == "question"
    assert _qa_role("ANSWER") == "answer"


# ---------------------------------------------------------------------------
# QNR Q&A pairing (group by pair_id, normalise uppercase role names)
# ---------------------------------------------------------------------------

def _written_row(**kw):
    base = dict(
        id=1, meeting_id=900, meeting_date=datetime(2026, 6, 2),
        agenda_item_id="A-QNR", qa_role="QUESTION", pair_id="900-1",
        order_index=0, speaker_name_english=None,
        speaker_job_title_english=None, text_english="...",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_session_returning(rows):
    """Patch lookups._session to yield a session whose execute().fetchall() == rows."""
    session = MagicMock()
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)
    session.execute.return_value.fetchall.return_value = rows
    return patch("src.search.lookups._session", return_value=session)


def test_written_pairing_groups_question_and_answer():
    rows = [
        _written_row(id=1, qa_role="QUESTION", pair_id="900-1",
                     order_index=0, speaker_name_english="Tom Montgomery"),
        _written_row(id=2, qa_role="ANSWER", pair_id="900-1",
                     order_index=1, speaker_job_title_english="First Minister"),
    ]
    with _patch_session_returning(rows):
        pairs = get_written_answers()
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.pair_id == "900-1"
    assert pair.question.speaker_name == "Tom Montgomery"
    assert pair.question.qa_role == "question"
    assert pair.answer.speaker_job_title == "First Minister"
    assert pair.answer.qa_role == "answer"


def test_written_pairing_handles_solo_contribution():
    # A pair_id-less row still surfaces on its own rather than being dropped.
    rows = [_written_row(id=5, qa_role="QUESTION", pair_id=None, order_index=0,
                         speaker_name_english="Solo Member")]
    with _patch_session_returning(rows):
        pairs = get_written_answers()
    assert len(pairs) == 1
    assert pairs[0].question.speaker_name == "Solo Member"
    assert pairs[0].answer is None
