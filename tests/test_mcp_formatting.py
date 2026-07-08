"""Offline tests for MCP response formatting (no DB, no server runtime)."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from senedd_mcp.formatting import envelope, search_hit, thread_item, to_json
from senedd_search.lookups import SpeechDetail
from senedd_search.service import SearchResult


def _search_result(**kw):
    base = dict(
        speech_id=1, speaker_name="Jane Hutt AC", meeting_date=datetime(2026, 3, 2),
        agenda_item_id="260302-3", chunk_text="excerpt here", speech_text="FULL TEXT",
        cosine_distance=0.1, similarity_score=90.0, senedd_tv_url="http://tv/1",
    )
    base.update(kw)
    return SearchResult(**base)


def _speech_detail(text):
    return SpeechDetail(
        speech_id=7, speaker_id=156, speaker_name="Jane Hutt AC", meeting_id=15837,
        meeting_date=datetime(2026, 3, 2), agenda_item_id="260302-3",
        agenda_item_english="3. Item", speech_language="En", speech_text=text,
        source_row_count=3, senedd_tv_url=None,
    )


def test_search_hit_omits_full_text_keeps_excerpt():
    hit = search_hit(_search_result())
    assert hit["excerpt"] == "excerpt here"
    assert "speech_text" not in hit  # full text fetched via get_speech, not search
    assert hit["meeting_date"] == "2026-03-02T00:00:00"
    assert hit["speech_id"] == 1


def test_thread_item_truncates_long_text_with_ellipsis():
    long_text = "word " * 200  # ~1000 chars, exceeds the excerpt cap
    item = thread_item(_speech_detail(long_text))
    assert "speech_text" not in item
    assert item["excerpt"].endswith("…")
    assert len(item["excerpt"]) < len(long_text)


def test_thread_item_short_text_not_ellipsised():
    item = thread_item(_speech_detail("brief remark"))
    assert item["excerpt"] == "brief remark"
    assert not item["excerpt"].endswith("…")


def test_to_json_serialises_datetimes_and_dataclasses():
    payload = json.loads(to_json(_speech_detail("hi")))
    assert payload["speaker_name"] == "Jane Hutt AC"
    assert payload["meeting_date"] == "2026-03-02T00:00:00"


def test_envelope_reports_count_and_extra():
    out = json.loads(envelope([search_hit(_search_result())], query="poverty"))
    assert out["count"] == 1
    assert out["query"] == "poverty"
    assert out["results"][0]["speech_id"] == 1
