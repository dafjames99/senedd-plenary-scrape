"""Offline tests for the search layer's pure logic (no DB).

Date coercion and the agenda-thread argument guard are exercised here; the
database-backed query behaviour is verified manually against the live corpus.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.search._dates import coerce_datetime
from src.search.lookups import get_agenda_thread


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
