"""Unit tests for the eval harness metrics and dataset integrity.

These are fully offline (no DB, no embedding model) and run as part of the
default ``pytest tests/`` suite. The live retrieval scoreboard lives in
``tests/eval/runner.py`` and is invoked manually.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.loader import load_cases
from tests.eval.metrics import (
    aggregate,
    first_relevant_rank,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    score_case,
)


# ---------------------------------------------------------------------------
# first_relevant_rank / reciprocal_rank
# ---------------------------------------------------------------------------

def test_first_relevant_rank_found():
    assert first_relevant_rank([9, 8, 42, 7], [42]) == 3


def test_first_relevant_rank_absent():
    assert first_relevant_rank([1, 2, 3], [99]) is None


def test_reciprocal_rank_uses_first_hit():
    # Relevant ids at positions 2 and 4 -> RR keyed on the earliest (position 2).
    assert reciprocal_rank([1, 5, 3, 5], [5]) == pytest.approx(0.5)


def test_reciprocal_rank_zero_when_absent():
    assert reciprocal_rank([1, 2, 3], [99]) == 0.0


# ---------------------------------------------------------------------------
# hit_at_k / recall_at_k
# ---------------------------------------------------------------------------

def test_hit_at_k_respects_cutoff():
    ranked = [1, 2, 3, 42]
    assert hit_at_k(ranked, [42], k=3) is False
    assert hit_at_k(ranked, [42], k=4) is True


def test_recall_at_k_partial():
    ranked = [10, 11, 12, 13]
    # Two of three relevant ids fall within the top 4.
    assert recall_at_k(ranked, [10, 13, 99], k=4) == pytest.approx(2 / 3)


def test_recall_at_k_empty_relevant_is_zero():
    assert recall_at_k([1, 2, 3], [], k=3) == 0.0


# ---------------------------------------------------------------------------
# score_case / aggregate
# ---------------------------------------------------------------------------

def test_score_case_populates_all_cutoffs():
    score = score_case("c1", [7, 8, 9], [9], ks=(1, 3))
    assert score.first_rank == 3
    assert score.reciprocal_rank == pytest.approx(1 / 3)
    assert score.hits == {1: False, 3: True}
    assert score.recall == {1: 0.0, 3: 1.0}


def test_aggregate_means():
    s1 = score_case("a", [42], [42], ks=(1,))  # perfect: RR 1.0, hit@1 True
    s2 = score_case("b", [1, 2], [99], ks=(1,))  # miss: RR 0.0, hit@1 False
    summary = aggregate([s1, s2], ks=(1,))
    assert summary["n"] == 2
    assert summary["mrr"] == pytest.approx(0.5)
    assert summary["hit_rate"][1] == pytest.approx(0.5)


def test_aggregate_empty_does_not_raise():
    summary = aggregate([], ks=(1, 5))
    assert summary["n"] == 0
    assert summary["mrr"] == 0.0
    assert summary["hit_rate"] == {1: 0.0, 5: 0.0}


# ---------------------------------------------------------------------------
# dataset integrity (cases.yaml must always load and be well-formed)
# ---------------------------------------------------------------------------

def test_cases_file_loads_and_is_valid():
    cases = load_cases()
    assert len(cases) >= 10, "seed set should hold a meaningful number of cases"
    for case in cases:
        assert case.id and case.query
        assert case.relevant_speech_ids
        assert all(isinstance(sid, int) for sid in case.relevant_speech_ids)
