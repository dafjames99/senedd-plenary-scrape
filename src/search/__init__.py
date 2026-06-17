"""Retrieval service layer.

Shared by the CLI (`scripts/query_speeches.py`) and, in later phases, the MCP
server. Centralises semantic search and structured lookups so query-time prefix
symmetry and citation metadata live in exactly one place.
"""
from src.search.lookups import (
    AgendaItem,
    JobTitle,
    MeetingInfo,
    MemberInfo,
    MemberMatch,
    SpeechDetail,
    SpeechSummary,
    filter_speeches,
    find_member,
    get_agenda_thread,
    get_meeting,
    get_member,
    get_speech,
    list_meetings,
)
from src.search.service import SearchResult, semantic_search

__all__ = [
    "SearchResult",
    "semantic_search",
    "SpeechDetail",
    "SpeechSummary",
    "MemberMatch",
    "MemberInfo",
    "JobTitle",
    "MeetingInfo",
    "AgendaItem",
    "get_speech",
    "filter_speeches",
    "find_member",
    "get_member",
    "list_meetings",
    "get_meeting",
    "get_agenda_thread",
]
