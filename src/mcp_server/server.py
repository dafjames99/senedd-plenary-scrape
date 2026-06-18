#!/usr/bin/env python3
"""MCP server over the Senedd Plenary corpus.

Exposes the `src/search` retrieval service as MCP tools (semantic search,
speech/member/meeting lookups, and conversation reconstruction), plus resources
that describe the dataset and prompts that steer good retrieval. All tools are
read-only over a fixed corpus.

Run locally (stdio):      uv run python -m src.mcp_server
Run as an HTTP service:   uv run python -m src.mcp_server --transport streamable-http
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db.settings import settings  # noqa: E402
from src.mcp_server.formatting import envelope, search_hit, thread_item, to_json  # noqa: E402
from src.search import lookups  # noqa: E402
from src.search.service import semantic_search  # noqa: E402

logger = logging.getLogger(__name__)

mcp = FastMCP("senedd_mcp")

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _error(exc: Exception) -> str:
    """Format an exception as an actionable, non-leaky error string."""
    if isinstance(exc, ValueError):
        return f"Error: {exc}"
    logger.exception("Unhandled MCP tool error")
    return f"Error: {type(exc).__name__} while querying the corpus. Check inputs and try again."


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class SearchSpeechesInput(BaseModel):
    """Inputs for semantic search over speeches."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="The TOPIC to search for, as natural language (e.g. 'NHS waiting "
        "times'). Do NOT put speaker names or dates here — use the filter fields.",
        min_length=1,
        max_length=400,
    )
    limit: int = Field(default=5, description="Max speeches to return", ge=1, le=50)
    min_similarity: float = Field(
        default=0.0, description="Minimum similarity score 0–100 to include", ge=0, le=100
    )
    speaker: Optional[str] = Field(
        default=None,
        description="Restrict to a speaker name (partial, case-insensitive). Prefer "
        "resolving the name with senedd_find_member first.",
    )
    date_from: Optional[str] = Field(
        default=None, description="Inclusive lower bound on meeting date (YYYY-MM-DD)", pattern=_DATE_PATTERN
    )
    date_to: Optional[str] = Field(
        default=None, description="Inclusive upper bound on meeting date (YYYY-MM-DD)", pattern=_DATE_PATTERN
    )
    agenda_item: Optional[str] = Field(
        default=None, description="Restrict to an exact agenda_item_id (e.g. '260302-3')"
    )


class GetSpeechInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speech_id: int = Field(..., description="The speech_id to fetch", ge=1)


class FilterSpeechesInput(BaseModel):
    """Inputs for non-semantic, structured speech listing (chronological)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    member_id: Optional[int] = Field(default=None, description="Restrict to a member id", ge=1)
    speaker: Optional[str] = Field(default=None, description="Speaker name (partial, case-insensitive)")
    date_from: Optional[str] = Field(default=None, description="Inclusive lower bound (YYYY-MM-DD)", pattern=_DATE_PATTERN)
    date_to: Optional[str] = Field(default=None, description="Inclusive upper bound (YYYY-MM-DD)", pattern=_DATE_PATTERN)
    agenda_item: Optional[str] = Field(default=None, description="Exact agenda_item_id")
    limit: int = Field(default=20, description="Max speeches to return", ge=1, le=100)


class FindMemberInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Full or partial member name", min_length=1, max_length=200)
    limit: int = Field(default=10, description="Max candidates to return", ge=1, le=50)


class GetMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: int = Field(..., description="The member_id to fetch", ge=1)


class ListMeetingsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    date_from: Optional[str] = Field(default=None, description="Inclusive lower bound (YYYY-MM-DD)", pattern=_DATE_PATTERN)
    date_to: Optional[str] = Field(default=None, description="Inclusive upper bound (YYYY-MM-DD)", pattern=_DATE_PATTERN)
    meeting_type: Optional[str] = Field(default=None, description="Filter by meeting type (partial match)")
    limit: int = Field(default=50, description="Max meetings to return", ge=1, le=200)


class GetMeetingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meeting_id: int = Field(..., description="The meeting_id to fetch", ge=1)


class GetAgendaThreadInput(BaseModel):
    """Identify a thread by speech_id, or by meeting_id + agenda_item_id."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    speech_id: Optional[int] = Field(default=None, description="Any speech in the thread", ge=1)
    meeting_id: Optional[int] = Field(default=None, description="Meeting id (with agenda_item_id)", ge=1)
    agenda_item_id: Optional[str] = Field(default=None, description="Agenda item id (with meeting_id)")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(name="senedd_search_speeches", annotations={"title": "Search Speeches (semantic)", **_READ_ONLY})
def senedd_search_speeches(params: SearchSpeechesInput) -> str:
    """Semantically search Senedd Plenary speeches and return the best matches.

    Embeds the query and ranks speeches by meaning (not keywords), returning the
    single best-matching excerpt per speech. Put the TOPIC in `query`; put
    speaker/date/agenda constraints in the dedicated filter fields rather than in
    the query text. For multi-faceted questions, issue several focused searches.

    Returns JSON: {count, results: [{speech_id, speaker_name, meeting_date,
    agenda_item_id, similarity_score, excerpt, senedd_tv_url}]}. Use
    senedd_get_speech with a speech_id for the full text before quoting.
    """
    try:
        results = semantic_search(
            params.query,
            top_k=params.limit,
            min_similarity=params.min_similarity,
            speaker_filter=params.speaker,
            date_from=params.date_from,
            date_to=params.date_to,
            agenda_item=params.agenda_item,
        )
        return envelope([search_hit(r) for r in results], query=params.query)
    except Exception as exc:  # noqa: BLE001 - surfaced as an actionable tool error
        return _error(exc)


@mcp.tool(name="senedd_get_speech", annotations={"title": "Get Full Speech", **_READ_ONLY})
def senedd_get_speech(params: GetSpeechInput) -> str:
    """Fetch one complete speech with its meeting/agenda context and citation links.

    Use after senedd_search_speeches to read the full text behind an excerpt.

    Returns JSON for {speech_id, speaker_id, speaker_name, meeting_id,
    meeting_date, agenda_item_id, agenda_item_english, speech_language,
    speech_text, source_row_count, senedd_tv_url}, or an error if not found.
    """
    try:
        speech = lookups.get_speech(params.speech_id)
        if speech is None:
            return f"Error: no speech found with speech_id {params.speech_id}."
        return to_json(speech)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_filter_speeches", annotations={"title": "Filter Speeches (structured)", **_READ_ONLY})
def senedd_filter_speeches(params: FilterSpeechesInput) -> str:
    """List speeches by structured filters (speaker/member, date range, agenda), newest first.

    Non-semantic — use this for "everything X said in this window" style queries.
    For topical relevance, use senedd_search_speeches instead. Resolve names to a
    member_id with senedd_find_member for precise speaker filtering.

    Returns JSON: {count, results: [{speech_id, speaker_name, meeting_id,
    meeting_date, agenda_item_id, agenda_item_english, excerpt, senedd_tv_url}]}.
    """
    try:
        rows = lookups.filter_speeches(
            member_id=params.member_id,
            speaker=params.speaker,
            date_from=params.date_from,
            date_to=params.date_to,
            agenda_item=params.agenda_item,
            limit=params.limit,
        )
        return envelope(rows)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_find_member", annotations={"title": "Resolve Member Name", **_READ_ONLY})
def senedd_find_member(params: FindMemberInput) -> str:
    """Resolve a (partial) name to candidate members, busiest speaker first.

    Call this BEFORE filtering by speaker, so downstream tools use a precise
    member_id rather than a fuzzy name. Names may appear in English or Welsh.

    Returns JSON: {count, results: [{member_id, name_english, name_welsh,
    sort_code, speech_count}]}.
    """
    try:
        return envelope(lookups.find_member(params.name, limit=params.limit), query=params.name)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_get_member", annotations={"title": "Get Member Profile", **_READ_ONLY})
def senedd_get_member(params: GetMemberInput) -> str:
    """Fetch a member's profile, role history across meetings, and speech volume.

    Returns JSON for {member_id, name_english, name_welsh, biography_english,
    biography_welsh, sort_code, speech_count, job_titles: [{meeting_id,
    meeting_date, job_title_english, job_title_welsh}]}, or an error if not found.
    """
    try:
        member = lookups.get_member(params.member_id)
        if member is None:
            return f"Error: no member found with member_id {params.member_id}."
        return to_json(member)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_list_meetings", annotations={"title": "List Meetings", **_READ_ONLY})
def senedd_list_meetings(params: ListMeetingsInput) -> str:
    """List Plenary meetings (newest first) with speech counts, optionally filtered by date/type.

    Returns JSON: {count, results: [{meeting_id, meeting_date, meeting_type,
    assembly, speech_count}]}.
    """
    try:
        rows = lookups.list_meetings(
            date_from=params.date_from,
            date_to=params.date_to,
            meeting_type=params.meeting_type,
            limit=params.limit,
        )
        return envelope(rows)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_get_meeting", annotations={"title": "Get Meeting + Agenda", **_READ_ONLY})
def senedd_get_meeting(params: GetMeetingInput) -> str:
    """Fetch a meeting with its distinct agenda items and speech count.

    Returns JSON for {meeting_id, meeting_date, meeting_type, assembly,
    speech_count, agenda_items: [{agenda_item_id, agenda_item_english}]}, or an
    error if not found.
    """
    try:
        meeting = lookups.get_meeting(params.meeting_id)
        if meeting is None:
            return f"Error: no meeting found with meeting_id {params.meeting_id}."
        return to_json(meeting)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_get_agenda_thread", annotations={"title": "Reconstruct Conversation", **_READ_ONLY})
def senedd_get_agenda_thread(params: GetAgendaThreadInput) -> str:
    """Return the ordered run of speeches for an agenda item — the conversation.

    Identify the thread by `speech_id` (its meeting + agenda item are resolved
    automatically) OR by `meeting_id` + `agenda_item_id`. Speeches come back in
    spoken order, so a question and its answer read in sequence — this recovers a
    reply that never repeats the question's keywords (and so wouldn't surface via
    semantic search alone).

    Each item is an excerpt to bound the response; use senedd_get_speech for any
    speech in full. Returns JSON: {count, results: [{speech_id, speaker_name,
    meeting_date, excerpt, senedd_tv_url}]}.
    """
    try:
        speeches = lookups.get_agenda_thread(
            speech_id=params.speech_id,
            meeting_id=params.meeting_id,
            agenda_item_id=params.agenda_item_id,
        )
        return envelope([thread_item(s) for s in speeches])
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("senedd://data-dictionary")
def data_dictionary() -> str:
    """What the Senedd corpus contains and how to query it well."""
    return (
        "# Senedd Plenary corpus — data dictionary\n\n"
        "Reconstructed speeches from Welsh Parliament (Senedd) Plenary transcripts, "
        "with vector embeddings for semantic search.\n\n"
        "## Core entities\n"
        "- **speech**: a contiguous run of contributions by one speaker on one agenda "
        "item. Has speech_id, speaker, meeting, agenda_item_id, full text, SeneddTV URL.\n"
        "- **member**: a speaker (member_id, English/Welsh name, biography URL, role "
        "history per meeting).\n"
        "- **meeting**: a Plenary sitting (meeting_id, date, type) with agenda items.\n\n"
        "## How to query well\n"
        "- Use `senedd_search_speeches` for topical/meaning-based questions; put only the "
        "TOPIC in the query and use filter fields for speaker/date/agenda.\n"
        "- Resolve a name with `senedd_find_member` before filtering by speaker.\n"
        "- Use `senedd_get_agenda_thread` to read a reply in the context of its question.\n"
        "- Always cite results by speech_id, speaker, date, and SeneddTV URL; assert only "
        "what the retrieved text supports.\n\n"
        "## Caveats\n"
        "- Text is bilingual (Welsh/English); English is preferred where a translation "
        "exists, but some speeches remain in Welsh.\n"
        "- `agenda_item_id` (e.g. '260302-3') repeats across the different meetings held on "
        "the same date — always pair it with a meeting.\n"
        "- Coverage is limited to the ingested date range (see senedd://corpus-stats).\n"
        "- Written answers (QNR) and votes are not yet ingested (planned).\n"
    )


@mcp.resource("senedd://corpus-stats")
def corpus_stats() -> str:
    """Live corpus coverage: counts, date range, and the active embedding model."""
    from sqlalchemy import text as _text

    from src.db.pipeline import SeneddPipeline

    with SeneddPipeline(settings.database_url).SessionLocal() as session:
        speeches = session.execute(_text("SELECT COUNT(*) FROM speeches")).scalar()
        meetings = session.execute(_text("SELECT COUNT(*) FROM meetings")).scalar()
        members = session.execute(_text("SELECT COUNT(*) FROM members")).scalar()
        date_range = session.execute(
            _text("SELECT MIN(meeting_date), MAX(meeting_date) FROM meetings")
        ).first()
    return to_json({
        "speeches": speeches,
        "meetings": meetings,
        "members": members,
        "earliest_meeting": date_range[0],
        "latest_meeting": date_range[1],
        "active_embedding_model": f"{settings.embedding_provider}/{settings.embedding_model}",
    })


@mcp.resource("senedd://members")
def members_roster() -> str:
    """The full member roster with speech counts (for name disambiguation)."""
    return envelope(lookups.list_members())


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt(name="senedd_search_strategy")
def search_strategy() -> str:
    """Guidance for answering a question against the Senedd corpus with citations."""
    return (
        "You are answering a question using the Senedd Plenary MCP tools. Follow this "
        "strategy:\n\n"
        "1. EXTRACT filters from the question. Speaker names, date ranges, and agenda "
        "items are filters — never embed them in the search query text. The query string "
        "should be the TOPIC only.\n"
        "2. RESOLVE any named person with senedd_find_member to get a precise member_id "
        "before filtering by speaker.\n"
        "3. SEARCH with senedd_search_speeches. For a broad or multi-part question, issue "
        "2–4 focused searches rather than one vague one. If results look weak, reformulate "
        "or widen the date range and try again.\n"
        "4. READ before quoting: call senedd_get_speech for the full text behind any "
        "excerpt you rely on. Use senedd_get_agenda_thread to see a reply in the context "
        "of the question it answered.\n"
        "5. SYNTHESISE: answer only what the retrieved text supports. Cite every claim with "
        "the speaker, the meeting date, the speech_id, and the SeneddTV URL. If the "
        "evidence is thin or absent, say so rather than guessing.\n"
    )


@mcp.prompt(name="senedd_position_over_time")
def position_over_time(member_name: str, issue: str) -> str:
    """Trace how one member's position on an issue has evolved over time."""
    return (
        f"Trace how {member_name} has addressed '{issue}' over time, using the Senedd "
        "tools.\n\n"
        f"1. Call senedd_find_member('{member_name}') and pick the right member_id.\n"
        f"2. Call senedd_search_speeches with query='{issue}' and that speaker filter, a "
        "generous limit, and no date bound first — to see the full span.\n"
        "3. For the most relevant hits, call senedd_get_speech to read the full text, and "
        "senedd_get_agenda_thread where the surrounding debate matters.\n"
        "4. Order the evidence chronologically and describe how the position developed — "
        "what stayed constant, what shifted, and when. Cite each point with the date, "
        "speech_id, and SeneddTV URL. If coverage is sparse, state that plainly.\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Senedd Plenary MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport: 'stdio' for a local client (default), 'streamable-http' for a remote service.",
    )
    args = parser.parse_args(argv)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
