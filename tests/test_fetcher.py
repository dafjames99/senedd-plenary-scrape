"""Offline tests for DataFetcher filename construction.

The artifact-type suffix matters: before the fix, Votes/QNR downloads were all
written as ``_Bilingual.xml`` and collided with the transcript. These exploit
the "file already exists" early return so no network call is made.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.fetcher import DataFetcher, Meeting


@pytest.fixture
def meeting():
    return Meeting(
        meeting_id="15798",
        meeting_date=datetime(2026, 3, 25, 13, 30),
        meeting_type="Plenary",
        download_url="https://record.senedd.wales/XMLExport?meetingID=15798",
    )


@pytest.mark.parametrize(
    "transcript_type, suffix",
    [
        ("BilingualTranscript", "Bilingual"),
        ("Votes", "Votes"),
        ("QNR", "QNR"),
        ("WelshTranscript", "Welsh"),
    ],
)
def test_download_filename_reflects_transcript_type(meeting, tmp_path, transcript_type, suffix):
    expected = tmp_path / f"20260325_Plenary_{suffix}.xml"
    expected.write_text("<dataroot/>")  # pre-create -> early return, no network
    result = DataFetcher().download_file(meeting, tmp_path, transcript_type=transcript_type)
    assert result == expected


def test_votes_and_transcript_do_not_collide(meeting, tmp_path):
    # The historic bug: Votes overwriting the transcript's _Bilingual.xml.
    (tmp_path / "20260325_Plenary_Bilingual.xml").write_text("transcript")
    (tmp_path / "20260325_Plenary_Votes.xml").write_text("votes")
    bilingual = DataFetcher().download_file(meeting, tmp_path, transcript_type="BilingualTranscript")
    votes = DataFetcher().download_file(meeting, tmp_path, transcript_type="Votes")
    assert bilingual != votes
    assert bilingual.read_text() == "transcript"
    assert votes.read_text() == "votes"


def test_default_transcript_type_is_bilingual(meeting, tmp_path):
    expected = tmp_path / "20260325_Plenary_Bilingual.xml"
    expected.write_text("<dataroot/>")
    assert DataFetcher().download_file(meeting, tmp_path) == expected
