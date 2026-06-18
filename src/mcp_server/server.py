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
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db.settings import settings  # noqa: E402
from src.mcp_server.formatting import envelope, search_hit, thread_item, to_json  # noqa: E402
from src.search import lookups  # noqa: E402
from src.search.service import semantic_search  # noqa: E402

logger = logging.getLogger(__name__)

LICENCE = (
    "Senedd Plenary records are public material under the Open Government Licence "
    "v3.0; verbatim quotation is permitted with attribution: 'Contains Senedd Cymru / "
    "Welsh Parliament information licensed under the Open Government Licence v3.0.'"
)

INSTRUCTIONS = (
    "Tools for searching the Senedd (Welsh Parliament) Plenary record — reconstructed "
    "speeches with semantic search. How to use them well:\n"
    "- Put the TOPIC in a search query; put speaker names, dates, and agenda items in "
    "the dedicated FILTER fields, never in the query text.\n"
    "- Resolve any named person with senedd_find_member first, then filter by that name.\n"
    "- For broad or multi-part questions, run several focused searches rather than one "
    "vague one; reformulate or widen the date range if results look weak.\n"
    "- Similarity scores run low in absolute terms (a strong match may score ~40–50); "
    "rank order is what matters — do not set a high min_similarity.\n"
    "- Read full text with senedd_get_speech before quoting at length; use "
    "senedd_get_agenda_thread to see a reply in the context of the question it answered.\n"
    "- Cite every claim with speaker, meeting date, speech_id, and SeneddTV URL; assert "
    "only what the retrieved text supports.\n"
    f"- Licence: {LICENCE}\n"
    "- Coverage is a limited date range (see senedd://corpus-stats); text is bilingual "
    "(Welsh/English); written answers (QNR) and votes are not yet included."
)

mcp = FastMCP("senedd_mcp", instructions=INSTRUCTIONS)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# Reusable parameter annotations (flat tool inputs — the model passes these at the
# top level, e.g. {"speech_id": 496}, not wrapped in a params object).
DateFrom = Annotated[
    Optional[str],
    Field(description="Inclusive lower bound on meeting date (YYYY-MM-DD)", pattern=_DATE_PATTERN),
]
DateTo = Annotated[
    Optional[str],
    Field(description="Inclusive upper bound on meeting date (YYYY-MM-DD)", pattern=_DATE_PATTERN),
]
SpeakerFilter = Annotated[
    Optional[str],
    Field(description="Restrict to a speaker name (partial, case-insensitive); prefer resolving with senedd_find_member first"),
]
AgendaItem = Annotated[Optional[str], Field(description="Restrict to an exact agenda_item_id (e.g. '260302-3')")]


def _error(exc: Exception) -> str:
    """Format an exception as an actionable, non-leaky error string."""
    if isinstance(exc, ValueError):
        return f"Error: {exc}"
    logger.exception("Unhandled MCP tool error")
    return f"Error: {type(exc).__name__} while querying the corpus. Check inputs and try again."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(name="senedd_search_speeches", annotations={"title": "Search Speeches (semantic)", **_READ_ONLY})
def senedd_search_speeches(
    query: Annotated[
        str,
        Field(
            description="The TOPIC to search for, as natural language (e.g. 'NHS waiting "
            "times'). Do NOT put speaker names or dates here — use the filter fields.",
            min_length=1,
            max_length=400,
        ),
    ],
    limit: Annotated[int, Field(description="Max speeches to return", ge=1, le=50)] = 5,
    min_similarity: Annotated[
        float,
        Field(description="Minimum similarity score 0–100. Scores run low; keep near 0.", ge=0, le=100),
    ] = 0.0,
    speaker: SpeakerFilter = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    agenda_item: AgendaItem = None,
) -> str:
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
            query,
            top_k=limit,
            min_similarity=min_similarity,
            speaker_filter=speaker,
            date_from=date_from,
            date_to=date_to,
            agenda_item=agenda_item,
        )
        return envelope([search_hit(r) for r in results], query=query)
    except Exception as exc:  # noqa: BLE001 - surfaced as an actionable tool error
        return _error(exc)


@mcp.tool(name="senedd_get_speech", annotations={"title": "Get Full Speech", **_READ_ONLY})
def senedd_get_speech(
    speech_id: Annotated[int, Field(description="The speech_id to fetch", ge=1)],
) -> str:
    """Fetch one complete speech with its meeting/agenda context and citation links.

    Use after senedd_search_speeches to read the full text behind an excerpt. The
    text is public record under the Open Government Licence v3.0 and may be quoted
    verbatim with attribution.

    Returns JSON for {speech_id, speaker_id, speaker_name, meeting_id,
    meeting_date, agenda_item_id, agenda_item_english, speech_language,
    speech_text, source_row_count, senedd_tv_url}, or an error if not found.
    """
    try:
        speech = lookups.get_speech(speech_id)
        if speech is None:
            return f"Error: no speech found with speech_id {speech_id}."
        return to_json(speech)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_filter_speeches", annotations={"title": "Filter Speeches (structured)", **_READ_ONLY})
def senedd_filter_speeches(
    member_id: Annotated[Optional[int], Field(description="Restrict to a member id", ge=1)] = None,
    speaker: SpeakerFilter = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    agenda_item: AgendaItem = None,
    limit: Annotated[int, Field(description="Max speeches to return", ge=1, le=100)] = 20,
) -> str:
    """List speeches by structured filters (speaker/member, date range, agenda), newest first.

    Non-semantic — use this for "everything X said in this window" style queries.
    For topical relevance, use senedd_search_speeches instead. Resolve names to a
    member_id with senedd_find_member for precise speaker filtering.

    Returns JSON: {count, results: [{speech_id, speaker_name, meeting_id,
    meeting_date, agenda_item_id, agenda_item_english, excerpt, senedd_tv_url}]}.
    """
    try:
        rows = lookups.filter_speeches(
            member_id=member_id,
            speaker=speaker,
            date_from=date_from,
            date_to=date_to,
            agenda_item=agenda_item,
            limit=limit,
        )
        return envelope(rows)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_find_member", annotations={"title": "Resolve Member Name", **_READ_ONLY})
def senedd_find_member(
    name: Annotated[str, Field(description="Full or partial member name", min_length=1, max_length=200)],
    limit: Annotated[int, Field(description="Max candidates to return", ge=1, le=50)] = 10,
) -> str:
    """Resolve a (partial) name to candidate members, busiest speaker first.

    Call this BEFORE filtering by speaker, so downstream tools use a precise
    member_id rather than a fuzzy name. Names may appear in English or Welsh.

    Returns JSON: {count, results: [{member_id, name_english, name_welsh,
    sort_code, speech_count}]}.
    """
    try:
        return envelope(lookups.find_member(name, limit=limit), query=name)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_get_member", annotations={"title": "Get Member Profile", **_READ_ONLY})
def senedd_get_member(
    member_id: Annotated[int, Field(description="The member_id to fetch", ge=1)],
) -> str:
    """Fetch a member's profile, role history across meetings, and speech volume.

    Returns JSON for {member_id, name_english, name_welsh, biography_english,
    biography_welsh, sort_code, speech_count, job_titles: [{meeting_id,
    meeting_date, job_title_english, job_title_welsh}]}, or an error if not found.
    """
    try:
        member = lookups.get_member(member_id)
        if member is None:
            return f"Error: no member found with member_id {member_id}."
        return to_json(member)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_list_meetings", annotations={"title": "List Meetings", **_READ_ONLY})
def senedd_list_meetings(
    date_from: DateFrom = None,
    date_to: DateTo = None,
    meeting_type: Annotated[Optional[str], Field(description="Filter by meeting type (partial match)")] = None,
    limit: Annotated[int, Field(description="Max meetings to return", ge=1, le=200)] = 50,
) -> str:
    """List Plenary meetings (newest first) with speech counts, optionally filtered by date/type.

    Returns JSON: {count, results: [{meeting_id, meeting_date, meeting_type,
    assembly, speech_count}]}.
    """
    try:
        rows = lookups.list_meetings(
            date_from=date_from,
            date_to=date_to,
            meeting_type=meeting_type,
            limit=limit,
        )
        return envelope(rows)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_get_meeting", annotations={"title": "Get Meeting + Agenda", **_READ_ONLY})
def senedd_get_meeting(
    meeting_id: Annotated[int, Field(description="The meeting_id to fetch", ge=1)],
) -> str:
    """Fetch a meeting with its distinct agenda items and speech count.

    Returns JSON for {meeting_id, meeting_date, meeting_type, assembly,
    speech_count, agenda_items: [{agenda_item_id, agenda_item_english}]}, or an
    error if not found.
    """
    try:
        meeting = lookups.get_meeting(meeting_id)
        if meeting is None:
            return f"Error: no meeting found with meeting_id {meeting_id}."
        return to_json(meeting)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(name="senedd_get_agenda_thread", annotations={"title": "Reconstruct Conversation", **_READ_ONLY})
def senedd_get_agenda_thread(
    speech_id: Annotated[Optional[int], Field(description="Any speech in the thread", ge=1)] = None,
    meeting_id: Annotated[Optional[int], Field(description="Meeting id (with agenda_item_id)", ge=1)] = None,
    agenda_item_id: Annotated[Optional[str], Field(description="Agenda item id (with meeting_id)")] = None,
) -> str:
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
            speech_id=speech_id,
            meeting_id=meeting_id,
            agenda_item_id=agenda_item_id,
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
        "## Licence\n"
        f"{LICENCE}\n\n"
        "## Caveats\n"
        "- Text is bilingual (Welsh/English); English is preferred where a translation "
        "exists, but some speeches remain in Welsh.\n"
        "- Similarity scores run low in absolute terms; trust the ranking, not a threshold.\n"
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
        "licence": "Open Government Licence v3.0",
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
        "2–4 focused searches rather than one vague one. Similarity scores run low — judge "
        "by rank, not absolute score. If results look weak, reformulate or widen the date "
        "range and try again.\n"
        "4. READ before quoting: call senedd_get_speech for the full text behind any "
        "excerpt you rely on. Use senedd_get_agenda_thread to see a reply in the context "
        "of the question it answered.\n"
        "5. SYNTHESISE: answer only what the retrieved text supports. Cite every claim with "
        "the speaker, the meeting date, the speech_id, and the SeneddTV URL. The record is "
        "public under the Open Government Licence v3.0 and may be quoted verbatim with "
        "attribution. If the evidence is thin or absent, say so rather than guessing.\n"
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
        "what stayed constant, what shifted, and when. Note the venue (committee vs "
        "chamber vs questions), which affects register as much as time does. Cite each "
        "point with the date, speech_id, and SeneddTV URL. If coverage is sparse, say so.\n"
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
