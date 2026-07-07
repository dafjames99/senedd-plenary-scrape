"""Tests for the pure speech-fidelity classification logic in src/db/fidelity.py.

Unit tests only — the classifier, word counter and mid-sentence detector take
plain values, so no database is required. They pin the behaviour that makes the
``is_suspect`` signal trustworthy: tiny-numerator noise and em-dash interruptions
must NOT register as truncations, while genuinely slow or broken rows must.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from senedd_data.fidelity import (
    DEFAULT_THRESHOLDS,
    classify,
    count_words,
    ends_midsentence,
)


@pytest.mark.parametrize("text, expected", [
    ("<p>Hello there friend</p>", 3),
    ("  spaced   out  words ", 3),
    ("", 0),
    (None, 0),
    (123, 0),  # non-string guard
])
def test_count_words(text, expected):
    assert count_words(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("A complete sentence.", False),
    ("Really?", False),
    ("Stop!", False),
    ('She said "now."', False),          # closing quote after terminal punctuation
    ("Diolch.</p>", False),               # trailing tag stripped before the check
    ("the speaker was interrupted—", False),  # em-dash = interruption marker, not truncation
    ("ending on an en dash –", False),
    ("ends on a plain word", True),       # genuine mid-sentence / no terminal mark
    ("...amdano.FootnoteLink", True),     # parser artifact, ends on a letter
    ("", False),                          # nothing to judge
    (None, False),
])
def test_ends_midsentence(text, expected):
    assert ends_midsentence(text) is expected


def test_classify_no_duration():
    flag, wpm, suspect = classify(50, None, False, DEFAULT_THRESHOLDS)
    assert flag == "no_duration"
    assert wpm is None
    assert suspect is False


def test_classify_broken_timestamp_is_suspect():
    # Next speech starts at/before this one — provably wrong ordering.
    flag, wpm, suspect = classify(50, 0.0, False, DEFAULT_THRESHOLDS)
    assert flag == "broken_timestamp"
    assert wpm is None
    assert suspect is True


def test_classify_tiny_numerator_is_low_confidence_not_slow():
    # 2 words over a long gap reads as ~0 WPM but is a short utterance, not a
    # truncation: below min_words_for_wpm it must be low_confidence, not suspect.
    flag, wpm, suspect = classify(2, 120.0, False, DEFAULT_THRESHOLDS)
    assert flag == "low_confidence"
    assert suspect is False


def test_classify_long_gap_is_low_confidence():
    # A gap beyond max_gap is a session break, not a speech duration.
    flag, _, suspect = classify(300, 5000.0, False, DEFAULT_THRESHOLDS)
    assert flag == "low_confidence"
    assert suspect is False


def test_classify_too_slow_is_suspect():
    # 300 words over ~9 min => ~33 WPM: substantial text, implausibly slow.
    flag, wpm, suspect = classify(300, 540.0, False, DEFAULT_THRESHOLDS)
    assert flag == "too_slow"
    assert wpm == pytest.approx(33.33, abs=0.1)
    assert suspect is True


def test_classify_normal_speech_is_ok():
    flag, wpm, suspect = classify(150, 60.0, False, DEFAULT_THRESHOLDS)
    assert flag == "ok"
    assert wpm == pytest.approx(150.0)
    assert suspect is False


def test_classify_too_fast():
    flag, _, suspect = classify(400, 60.0, False, DEFAULT_THRESHOLDS)
    assert flag == "too_fast"
    assert suspect is False  # high tail is the contaminated direction; not flagged


def test_classify_midsentence_makes_ok_speech_suspect():
    # Normal rate but ends mid-sentence with enough words -> worth a human look.
    flag, _, suspect = classify(200, 80.0, True, DEFAULT_THRESHOLDS)
    assert flag == "ok"
    assert suspect is True


def test_classify_midsentence_trivial_utterance_not_suspect():
    # Too few words for the mid-sentence signal to mean anything.
    flag, _, suspect = classify(3, 60.0, True, DEFAULT_THRESHOLDS)
    assert suspect is False
