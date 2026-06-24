"""Serialization helpers for MCP tool responses.

Tools return JSON strings: the consumer is an LLM that must cite exact
``speech_id`` values and SeneddTV URLs, so unambiguous structured output is worth
more here than prose. These helpers also control context size — listings carry
excerpts, never full speech text (fetch that with ``senedd_get_speech``).
"""
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any, List

from src.search.lookups import SpeechDetail
from src.search.service import SearchResult

_THREAD_EXCERPT_CHARS = 320


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def _serialise(obj: Any) -> Any:
    """Recursively convert dataclasses/lists into JSON-able structures."""
    if isinstance(obj, list):
        return [_serialise(item) for item in obj]
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def to_json(payload: Any) -> str:
    """Dump any dataclass/list/dict payload to a pretty JSON string."""
    return json.dumps(_serialise(payload), default=_json_default, indent=2, ensure_ascii=False)


def envelope(items: List[Any], **extra: Any) -> str:
    """Wrap a list of results with a count (and any extra metadata) as JSON."""
    return to_json({"count": len(items), **extra, "results": _serialise(items)})


def search_hit(result: SearchResult) -> dict:
    """Shape a semantic-search hit for output, omitting the full speech text.

    The evidence excerpt is the best-matching chunk; call ``senedd_get_speech``
    with the ``speech_id`` for the complete text before quoting at length.
    """
    return {
        "source_type": result.source_type,
        "source_id": result.source_id,
        # speech_id retained for spoken speeches (None for written/vote sources)
        "speech_id": result.speech_id,
        "speaker_name": result.speaker_name,
        "meeting_date": result.meeting_date.isoformat() if result.meeting_date else None,
        "agenda_item_id": result.agenda_item_id,
        "similarity_score": result.similarity_score,
        "excerpt": result.chunk_text,
        "senedd_tv_url": result.senedd_tv_url,
    }


def thread_item(speech: SpeechDetail) -> dict:
    """Shape one speech in an agenda thread: an excerpt, not the full text.

    A thread can run to many speeches; keeping each compact bounds the response.
    Fetch any single speech in full with ``senedd_get_speech``.
    """
    text = speech.speech_text or ""
    excerpt = text[:_THREAD_EXCERPT_CHARS].strip()
    if len(text) > _THREAD_EXCERPT_CHARS:
        excerpt += "…"
    return {
        "speech_id": speech.speech_id,
        "speaker_name": speech.speaker_name,
        "meeting_date": speech.meeting_date.isoformat() if speech.meeting_date else None,
        "excerpt": excerpt,
        "senedd_tv_url": speech.senedd_tv_url,
    }
