"""Offline tests for the Votes XML parser (no DB).

Exercised against a committed fixture (the real ``data/`` exports are
gitignored) covering the data shapes that bit during design: long-format
deduplication by ``Contribution_ID``, all four ``Results_Result`` values
including ``DidNotVote``, and the junk ``Vote_Name`` placeholder.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from senedd_data.parser import parse_votes_xml

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "votes_sample.xml"


@pytest.fixture(scope="module")
def parsed():
    return parse_votes_xml(FIXTURE)


def test_meeting_metadata(parsed):
    meeting, _, _, _ = parsed
    assert meeting["meeting_id"] == 15798
    assert meeting["assembly"] == 6
    assert meeting["meeting_type"] == "plenary-votes"
    assert meeting["meeting_date"].year == 2026


def test_votes_deduplicated_by_contribution_id(parsed):
    _, votes, _, _ = parsed
    # Six rows across two distinct motions -> two votes.
    assert len(votes) == 2
    cids = {v["contribution_id"] for v in votes}
    assert cids == {761993, 762001}


def test_vote_uses_english_name_not_junk_placeholder(parsed):
    _, votes, _, _ = parsed
    first = next(v for v in votes if v["contribution_id"] == 761993)
    assert first["vote_name_english"] == "Item 6. Motion without amendment."
    # The raw <Vote_Name> placeholder ('15798_?') must never surface.
    assert "?" not in (first["vote_name_english"] or "")


def test_vote_tallies_and_result(parsed):
    _, votes, _, _ = parsed
    first = next(v for v in votes if v["contribution_id"] == 761993)
    assert (first["total_for"], first["total_against"], first["total_abstain"]) == (2, 1, 1)
    assert first["result_english"] == "Motion has been agreed"
    assert first["result_welsh"] == "Derbyniwyd y cynnig"


def test_records_capture_all_four_result_values(parsed):
    _, _, records, _ = parsed
    results = {r["result"] for r in records}
    assert results == {"For", "Against", "Abstain", "DidNotVote"}
    # One record per member-vote row in the fixture.
    assert len(records) == 6


def test_members_deduplicated_for_upsert(parsed):
    _, _, _, members = parsed
    ids = {m["member_id"] for m in members}
    # Four distinct members across both motions (incl. the absent member).
    assert ids == {336, 4562, 6571, 9999}
    asghar = next(m for m in members if m["member_id"] == 336)
    assert asghar["name_english"] == "Natasha Asghar"


def test_empty_dataframe_returns_empty_tuple(tmp_path):
    empty = tmp_path / "empty.xml"
    empty.write_text('<?xml version="1.0"?>\n<dataroot></dataroot>\n')
    meeting, votes, records, members = parse_votes_xml(empty)
    assert meeting == {} and votes == [] and records == [] and members == []
