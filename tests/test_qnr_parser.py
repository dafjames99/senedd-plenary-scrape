"""Offline tests for the QNR (written Q&A) parser and its cleaning.

Covers the shapes that make QNR awkward: no Contribution_ID, positional Q↔A
pairing over a non-unique source order id, answers attributed by job title with
no Member_Id, and double-escaped HTML in the body.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.parser import parse_qnr_xml
from src.db.transformers import clean_contribution_verbatim

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "qnr_sample.xml"


@pytest.fixture(scope="module")
def parsed():
    return parse_qnr_xml(FIXTURE)


def test_meeting_metadata(parsed):
    meeting, _, _ = parsed
    assert meeting["meeting_id"] == 16060
    assert meeting["assembly"] == 7
    assert meeting["meeting_type"] == "plenary-qnr"


def test_order_index_is_unique_document_order(parsed):
    _, written, _ = parsed
    # Source Contribution_Order_ID repeats (0,1,1,2); parser order_index must not.
    assert [w["order_index"] for w in written] == [0, 1, 2, 3]


def test_roles_assigned_from_contribution_type(parsed):
    _, written, _ = parsed
    assert [w["qa_role"] for w in written] == ["question", "answer", "question", "answer"]


def test_positional_pairing(parsed):
    _, written, _ = parsed
    # Each answer inherits the pair_id of the preceding question.
    assert written[0]["pair_id"] == written[1]["pair_id"] == "16060-1"
    assert written[2]["pair_id"] == written[3]["pair_id"] == "16060-2"


def test_question_has_speaker_answer_has_job_title(parsed):
    _, written, _ = parsed
    question, answer = written[0], written[1]
    assert question["speaker_id"] == 12183  # int, not float
    assert question["speaker_job_title_english"] is None
    assert answer["speaker_id"] is None
    assert answer["speaker_job_title_english"] == "First Minister"


def test_only_askers_collected_for_member_upsert(parsed):
    _, _, members = parsed
    ids = {m["member_id"] for m in members}
    assert ids == {12183, 8696}  # answer rows contribute no member


def test_double_escaped_html_cleans_to_plain_text(parsed):
    _, written, _ = parsed
    answer_raw = written[1]["raw_verbatim"]
    # After pandas XML-decoding the body is single-escaped; cleaning must strip it.
    assert "&lt;p&gt;" in answer_raw
    cleaned = clean_contribution_verbatim(answer_raw)
    assert cleaned == "We recognise the financial pressures faced by local government."
    assert "<" not in cleaned and "&" not in cleaned
