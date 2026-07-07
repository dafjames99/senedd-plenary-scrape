"""Structured (non-semantic) retrieval over the Senedd schema.

These functions back the MCP's lookup/navigation tools: fetching a full speech,
listing speeches by speaker/date, resolving a member name to an id, and
reconstructing the ordered run of speeches around an agenda item (the
question -> answer -> follow-up "conversation").

Everything returns plain dataclasses (not ORM objects) so the MCP layer can
serialise them directly, and every user value is passed as a bound parameter.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text

from senedd_data.db_schema import QaRoleEnum, VoteResultEnum
from senedd_data.session import get_session
from senedd_data.settings import settings
from senedd_search._dates import DateLike, coerce_datetime

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 240


def _session():
    """Open a session bound to the configured database."""
    return get_session(settings.database_url)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SpeechDetail:
    """A full speech with its meeting/agenda context and citation links."""

    speech_id: int
    speaker_id: Optional[int]
    speaker_name: str
    meeting_id: int
    meeting_date: Optional[datetime]
    agenda_item_id: str
    agenda_item_english: Optional[str]
    speech_language: Optional[str]
    speech_text: str
    source_row_count: Optional[int]
    senedd_tv_url: Optional[str]
    # Transcript-fidelity signal (from speech_fidelity; None if not yet computed).
    # is_suspect flags an anomalous text/time relationship — possible truncation,
    # interruption, or non-speech time in the gap — so a consumer can caveat the
    # quote. fidelity_flag carries the detail; wpm is the inferred speaking rate.
    is_suspect: Optional[bool] = None
    fidelity_flag: Optional[str] = None
    wpm: Optional[float] = None


@dataclass
class SpeechSummary:
    """A lightweight speech row for listings (excerpt, not full text)."""

    speech_id: int
    speaker_name: str
    meeting_id: int
    meeting_date: Optional[datetime]
    agenda_item_id: str
    agenda_item_english: Optional[str]
    excerpt: str
    senedd_tv_url: Optional[str]


@dataclass
class MemberMatch:
    """A candidate member when resolving a name, with activity for disambiguation."""

    member_id: int
    name_english: str
    name_welsh: Optional[str]
    sort_code: Optional[str]
    speech_count: int


@dataclass
class JobTitle:
    meeting_id: int
    meeting_date: Optional[datetime]
    job_title_english: Optional[str]
    job_title_welsh: Optional[str]


@dataclass
class MemberInfo:
    """A member's profile plus their role history and activity volume."""

    member_id: int
    name_english: str
    name_welsh: Optional[str]
    biography_english: Optional[str]
    biography_welsh: Optional[str]
    sort_code: Optional[str]
    speech_count: int
    job_titles: List[JobTitle] = field(default_factory=list)


@dataclass
class AgendaItem:
    agenda_item_id: str
    agenda_item_english: Optional[str]


@dataclass
class MeetingInfo:
    """Meeting metadata with its agenda items and speech volume."""

    meeting_id: int
    meeting_date: Optional[datetime]
    meeting_type: Optional[str]
    assembly: Optional[int]
    speech_count: int
    agenda_items: List[AgendaItem] = field(default_factory=list)


@dataclass
class VoteRecordItem:
    """How one member voted on one motion."""

    member_id: int
    member_name: Optional[str]
    result: str  # For | Against | Abstain | DidNotVote


@dataclass
class VoteSummary:
    """A motion-level vote without the per-member breakdown (for listings)."""

    vote_id: int
    contribution_id: int
    meeting_id: int
    meeting_date: Optional[datetime]
    agenda_item_id: Optional[str]
    vote_name_english: Optional[str]
    result_english: Optional[str]
    total_for: Optional[int]
    total_against: Optional[int]
    total_abstain: Optional[int]


@dataclass
class VoteDetail:
    """A motion-level vote with tallies and the full per-member record."""

    vote_id: int
    contribution_id: int
    meeting_id: int
    meeting_date: Optional[datetime]
    agenda_item_id: Optional[str]
    agenda_item_english: Optional[str]
    vote_name_english: Optional[str]
    vote_name_welsh: Optional[str]
    total_for: Optional[int]
    total_against: Optional[int]
    total_abstain: Optional[int]
    result_english: Optional[str]
    result_welsh: Optional[str]
    records: List[VoteRecordItem] = field(default_factory=list)


@dataclass
class MemberVoteItem:
    """One member's outcome on a single vote, for their voting record."""

    vote_id: int
    meeting_id: int
    meeting_date: Optional[datetime]
    agenda_item_id: Optional[str]
    vote_name_english: Optional[str]
    result: str


@dataclass
class WrittenContributionItem:
    """One side (question or answer) of a written QNR contribution."""

    id: int
    qa_role: str  # question | answer
    speaker_name: Optional[str]
    speaker_job_title: Optional[str]
    text_english: Optional[str]


@dataclass
class WrittenPair:
    """A positionally-paired written question and its answer (either may be absent)."""

    pair_id: Optional[str]
    meeting_id: int
    meeting_date: Optional[datetime]
    agenda_item_id: Optional[str]
    question: Optional[WrittenContributionItem]
    answer: Optional[WrittenContributionItem]


# ---------------------------------------------------------------------------
# Speech lookups
# ---------------------------------------------------------------------------

def get_speech(speech_id: int) -> Optional[SpeechDetail]:
    """Fetch one full speech with meeting/agenda context, or ``None`` if absent."""
    sql = text("""
        SELECT
            s.speech_id, s.speaker_id, s.speaker_name, s.meeting_id,
            m.meeting_date, s.agenda_item_id, s.speech_language,
            s.speech_text, s.source_row_count,
            (SELECT r.agenda_item_english FROM raw_contributions r
              WHERE r.agenda_item_id = s.agenda_item_id AND r.meeting_id = s.meeting_id
                AND r.agenda_item_english IS NOT NULL LIMIT 1) AS agenda_item_english,
            (SELECT sp.spoken_url FROM speech_parts sp
              WHERE sp.speech_id = s.speech_id AND sp.spoken_url IS NOT NULL
              ORDER BY sp.contribution_order_id ASC LIMIT 1) AS senedd_tv_url,
            f.is_suspect, f.flag AS fidelity_flag, f.wpm
        FROM speeches s
        JOIN meetings m ON s.meeting_id = m.meeting_id
        LEFT JOIN speech_fidelity f ON f.speech_id = s.speech_id
        WHERE s.speech_id = :speech_id
    """)
    with _session() as session:
        row = session.execute(sql, {"speech_id": speech_id}).first()
    if row is None:
        return None
    return SpeechDetail(
        speech_id=row.speech_id,
        speaker_id=row.speaker_id,
        speaker_name=row.speaker_name,
        meeting_id=row.meeting_id,
        meeting_date=row.meeting_date,
        agenda_item_id=row.agenda_item_id,
        agenda_item_english=row.agenda_item_english,
        speech_language=row.speech_language,
        speech_text=row.speech_text,
        source_row_count=row.source_row_count,
        senedd_tv_url=row.senedd_tv_url,
        is_suspect=row.is_suspect,
        fidelity_flag=row.fidelity_flag,
        wpm=row.wpm,
    )


def filter_speeches(
    member_id: Optional[int] = None,
    speaker: Optional[str] = None,
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    agenda_item: Optional[str] = None,
    limit: int = 50,
) -> List[SpeechSummary]:
    """List speeches by structured filters (no semantic ranking), newest first.

    Backs chronological use cases ("everything X said in this window"). At least
    one filter is recommended; with none it returns the most recent speeches.
    """
    conditions = ["TRUE"]
    params: dict = {"limit": limit}
    if member_id is not None:
        conditions.append("s.speaker_id = :member_id")
        params["member_id"] = member_id
    if speaker:
        conditions.append("s.speaker_name ILIKE :speaker")
        params["speaker"] = f"%{speaker}%"
    if date_from:
        conditions.append("m.meeting_date >= :date_from")
        params["date_from"] = coerce_datetime(date_from)
    if date_to:
        conditions.append("m.meeting_date <= :date_to")
        params["date_to"] = coerce_datetime(date_to, end_of_day=True)
    if agenda_item:
        conditions.append("s.agenda_item_id = :agenda_item")
        params["agenda_item"] = agenda_item
    where_clause = " AND ".join(conditions)

    sql = text(f"""
        SELECT
            s.speech_id, s.speaker_name, s.meeting_id, m.meeting_date,
            s.agenda_item_id, LEFT(s.speech_text, :excerpt) AS excerpt,
            (SELECT r.agenda_item_english FROM raw_contributions r
              WHERE r.agenda_item_id = s.agenda_item_id AND r.meeting_id = s.meeting_id
                AND r.agenda_item_english IS NOT NULL LIMIT 1) AS agenda_item_english,
            (SELECT sp.spoken_url FROM speech_parts sp
              WHERE sp.speech_id = s.speech_id AND sp.spoken_url IS NOT NULL
              ORDER BY sp.contribution_order_id ASC LIMIT 1) AS senedd_tv_url
        FROM speeches s
        JOIN meetings m ON s.meeting_id = m.meeting_id
        WHERE {where_clause}
        ORDER BY m.meeting_date DESC, s.speech_id DESC
        LIMIT :limit
    """)
    params["excerpt"] = _EXCERPT_CHARS
    with _session() as session:
        rows = session.execute(sql, params).fetchall()
    return [
        SpeechSummary(
            speech_id=r.speech_id,
            speaker_name=r.speaker_name,
            meeting_id=r.meeting_id,
            meeting_date=r.meeting_date,
            agenda_item_id=r.agenda_item_id,
            agenda_item_english=r.agenda_item_english,
            excerpt=(r.excerpt or "").strip(),
            senedd_tv_url=r.senedd_tv_url,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Member lookups (entity resolution)
# ---------------------------------------------------------------------------

def find_member(name: str, limit: int = 10) -> List[MemberMatch]:
    """Resolve a (partial) name to candidate members, busiest speaker first.

    The MCP should call this before applying a speaker filter, rather than
    relying on fuzzy name matching inside search.
    """
    sql = text("""
        SELECT
            mb.member_id, mb.name_english, mb.name_welsh, mb.sort_code,
            COUNT(s.speech_id) AS speech_count
        FROM members mb
        LEFT JOIN speeches s ON s.speaker_id = mb.member_id
        WHERE mb.name_english ILIKE :name OR mb.name_welsh ILIKE :name
        GROUP BY mb.member_id, mb.name_english, mb.name_welsh, mb.sort_code
        ORDER BY speech_count DESC, mb.name_english ASC
        LIMIT :limit
    """)
    with _session() as session:
        rows = session.execute(sql, {"name": f"%{name}%", "limit": limit}).fetchall()
    return [
        MemberMatch(
            member_id=r.member_id,
            name_english=r.name_english,
            name_welsh=r.name_welsh,
            sort_code=r.sort_code,
            speech_count=r.speech_count,
        )
        for r in rows
    ]


def get_member(member_id: int) -> Optional[MemberInfo]:
    """Fetch a member's profile, role history, and speech volume."""
    member_sql = text("""
        SELECT
            mb.member_id, mb.name_english, mb.name_welsh,
            mb.biography_english, mb.biography_welsh, mb.sort_code,
            (SELECT COUNT(*) FROM speeches s WHERE s.speaker_id = mb.member_id)
                AS speech_count
        FROM members mb
        WHERE mb.member_id = :member_id
    """)
    titles_sql = text("""
        SELECT jt.meeting_id, m.meeting_date,
               jt.job_title_english, jt.job_title_welsh
        FROM member_job_titles jt
        JOIN meetings m ON jt.meeting_id = m.meeting_id
        WHERE jt.member_id = :member_id
        ORDER BY m.meeting_date ASC
    """)
    with _session() as session:
        row = session.execute(member_sql, {"member_id": member_id}).first()
        if row is None:
            return None
        title_rows = session.execute(titles_sql, {"member_id": member_id}).fetchall()
    return MemberInfo(
        member_id=row.member_id,
        name_english=row.name_english,
        name_welsh=row.name_welsh,
        biography_english=row.biography_english,
        biography_welsh=row.biography_welsh,
        sort_code=row.sort_code,
        speech_count=row.speech_count,
        job_titles=[
            JobTitle(
                meeting_id=t.meeting_id,
                meeting_date=t.meeting_date,
                job_title_english=t.job_title_english,
                job_title_welsh=t.job_title_welsh,
            )
            for t in title_rows
        ],
    )


def list_members(limit: int = 500) -> List[MemberMatch]:
    """List all members with speech counts, busiest first (roster for the MCP)."""
    sql = text("""
        SELECT mb.member_id, mb.name_english, mb.name_welsh, mb.sort_code,
               COUNT(s.speech_id) AS speech_count
        FROM members mb
        LEFT JOIN speeches s ON s.speaker_id = mb.member_id
        GROUP BY mb.member_id, mb.name_english, mb.name_welsh, mb.sort_code
        ORDER BY speech_count DESC, mb.name_english ASC
        LIMIT :limit
    """)
    with _session() as session:
        rows = session.execute(sql, {"limit": limit}).fetchall()
    return [
        MemberMatch(
            member_id=r.member_id,
            name_english=r.name_english,
            name_welsh=r.name_welsh,
            sort_code=r.sort_code,
            speech_count=r.speech_count,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Meeting lookups
# ---------------------------------------------------------------------------

def list_meetings(
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    meeting_type: Optional[str] = None,
    limit: int = 100,
) -> List[MeetingInfo]:
    """List meetings (newest first) with speech counts, without agenda items."""
    conditions = ["TRUE"]
    params: dict = {"limit": limit}
    if date_from:
        conditions.append("m.meeting_date >= :date_from")
        params["date_from"] = coerce_datetime(date_from)
    if date_to:
        conditions.append("m.meeting_date <= :date_to")
        params["date_to"] = coerce_datetime(date_to, end_of_day=True)
    if meeting_type:
        conditions.append("m.meeting_type ILIKE :meeting_type")
        params["meeting_type"] = f"%{meeting_type}%"
    where_clause = " AND ".join(conditions)

    sql = text(f"""
        SELECT m.meeting_id, m.meeting_date, m.meeting_type, m.assembly,
               (SELECT COUNT(*) FROM speeches s WHERE s.meeting_id = m.meeting_id)
                   AS speech_count
        FROM meetings m
        WHERE {where_clause}
        ORDER BY m.meeting_date DESC
        LIMIT :limit
    """)
    with _session() as session:
        rows = session.execute(sql, params).fetchall()
    return [
        MeetingInfo(
            meeting_id=r.meeting_id,
            meeting_date=r.meeting_date,
            meeting_type=r.meeting_type,
            assembly=r.assembly,
            speech_count=r.speech_count,
        )
        for r in rows
    ]


def get_meeting(meeting_id: int) -> Optional[MeetingInfo]:
    """Fetch a meeting with its distinct agenda items and speech count."""
    meeting_sql = text("""
        SELECT m.meeting_id, m.meeting_date, m.meeting_type, m.assembly,
               (SELECT COUNT(*) FROM speeches s WHERE s.meeting_id = m.meeting_id)
                   AS speech_count
        FROM meetings m
        WHERE m.meeting_id = :meeting_id
    """)
    agenda_sql = text("""
        SELECT DISTINCT s.agenda_item_id,
               (SELECT r.agenda_item_english FROM raw_contributions r
                 WHERE r.agenda_item_id = s.agenda_item_id AND r.meeting_id = s.meeting_id
                   AND r.agenda_item_english IS NOT NULL LIMIT 1) AS agenda_item_english
        FROM speeches s
        WHERE s.meeting_id = :meeting_id
        ORDER BY s.agenda_item_id
    """)
    with _session() as session:
        row = session.execute(meeting_sql, {"meeting_id": meeting_id}).first()
        if row is None:
            return None
        agenda_rows = session.execute(agenda_sql, {"meeting_id": meeting_id}).fetchall()
    return MeetingInfo(
        meeting_id=row.meeting_id,
        meeting_date=row.meeting_date,
        meeting_type=row.meeting_type,
        assembly=row.assembly,
        speech_count=row.speech_count,
        agenda_items=[
            AgendaItem(agenda_item_id=a.agenda_item_id, agenda_item_english=a.agenda_item_english)
            for a in agenda_rows
        ],
    )


# ---------------------------------------------------------------------------
# Conversation reconstruction
# ---------------------------------------------------------------------------

def get_agenda_thread(
    speech_id: Optional[int] = None,
    meeting_id: Optional[int] = None,
    agenda_item_id: Optional[str] = None,
) -> List[SpeechDetail]:
    """Return the ordered run of speeches for an agenda item (the conversation).

    Identify the thread either by a ``speech_id`` (its meeting + agenda item are
    resolved) or directly by ``meeting_id`` + ``agenda_item_id``. Speeches are
    ordered by the earliest source contribution so question -> answer ->
    follow-up reads in sequence — this is how a reply that never repeats the
    question's keywords is recovered.

    Raises:
        ValueError: If neither a speech_id nor a (meeting_id, agenda_item_id)
            pair is supplied.
    """
    if speech_id is None and not (meeting_id is not None and agenda_item_id):
        raise ValueError(
            "Provide either speech_id, or both meeting_id and agenda_item_id."
        )

    with _session() as session:
        if speech_id is not None:
            anchor = session.execute(
                text("SELECT meeting_id, agenda_item_id FROM speeches WHERE speech_id = :sid"),
                {"sid": speech_id},
            ).first()
            if anchor is None:
                return []
            meeting_id, agenda_item_id = anchor.meeting_id, anchor.agenda_item_id

        sql = text("""
            SELECT
                s.speech_id, s.speaker_id, s.speaker_name, s.meeting_id,
                m.meeting_date, s.agenda_item_id, s.speech_language,
                s.speech_text, s.source_row_count,
                (SELECT r.agenda_item_english FROM raw_contributions r
                  WHERE r.agenda_item_id = s.agenda_item_id AND r.meeting_id = s.meeting_id
                    AND r.agenda_item_english IS NOT NULL LIMIT 1) AS agenda_item_english,
                (SELECT sp.spoken_url FROM speech_parts sp
                  WHERE sp.speech_id = s.speech_id AND sp.spoken_url IS NOT NULL
                  ORDER BY sp.contribution_order_id ASC LIMIT 1) AS senedd_tv_url,
                (SELECT MIN(sp2.contribution_order_id) FROM speech_parts sp2
                  WHERE sp2.speech_id = s.speech_id) AS first_order
            FROM speeches s
            JOIN meetings m ON s.meeting_id = m.meeting_id
            WHERE s.meeting_id = :meeting_id AND s.agenda_item_id = :agenda_item_id
            ORDER BY first_order ASC NULLS LAST, s.speech_id ASC
        """)
        rows = session.execute(
            sql, {"meeting_id": meeting_id, "agenda_item_id": agenda_item_id}
        ).fetchall()

    return [
        SpeechDetail(
            speech_id=r.speech_id,
            speaker_id=r.speaker_id,
            speaker_name=r.speaker_name,
            meeting_id=r.meeting_id,
            meeting_date=r.meeting_date,
            agenda_item_id=r.agenda_item_id,
            agenda_item_english=r.agenda_item_english,
            speech_language=r.speech_language,
            speech_text=r.speech_text,
            source_row_count=r.source_row_count,
            senedd_tv_url=r.senedd_tv_url,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------

def get_vote(vote_id: int) -> Optional[VoteDetail]:
    """Fetch one motion-level vote with tallies and the full per-member record."""
    vote_sql = text("""
        SELECT v.vote_id, v.contribution_id, v.meeting_id, m.meeting_date,
               v.agenda_item_id, v.agenda_item_english,
               v.vote_name_english, v.vote_name_welsh,
               v.total_for, v.total_against, v.total_abstain,
               v.result_english, v.result_welsh
        FROM votes v
        JOIN meetings m ON v.meeting_id = m.meeting_id
        WHERE v.vote_id = :vote_id
    """)
    records_sql = text("""
        SELECT vr.member_id, mb.name_english AS member_name, vr.result
        FROM vote_records vr
        LEFT JOIN members mb ON vr.member_id = mb.member_id
        WHERE vr.vote_id = :vote_id
        ORDER BY vr.result, mb.name_english
    """)
    with _session() as session:
        row = session.execute(vote_sql, {"vote_id": vote_id}).first()
        if row is None:
            return None
        record_rows = session.execute(records_sql, {"vote_id": vote_id}).fetchall()
    return VoteDetail(
        vote_id=row.vote_id,
        contribution_id=row.contribution_id,
        meeting_id=row.meeting_id,
        meeting_date=row.meeting_date,
        agenda_item_id=row.agenda_item_id,
        agenda_item_english=row.agenda_item_english,
        vote_name_english=row.vote_name_english,
        vote_name_welsh=row.vote_name_welsh,
        total_for=row.total_for,
        total_against=row.total_against,
        total_abstain=row.total_abstain,
        result_english=row.result_english,
        result_welsh=row.result_welsh,
        records=[
            VoteRecordItem(
                member_id=rec.member_id,
                member_name=rec.member_name,
                result=_vote_result(rec.result),
            )
            for rec in record_rows
        ],
    )


def find_votes(
    motion_contains: Optional[str] = None,
    agenda_item: Optional[str] = None,
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    limit: int = 50,
) -> List[VoteSummary]:
    """List motion-level votes by structured filters, newest first.

    For meaning-based discovery of a vote use ``semantic_search(source='vote')``;
    this is the structured counterpart (exact agenda item, date window, or a
    substring of the motion name).
    """
    conditions = ["TRUE"]
    params: dict = {"limit": limit}
    if motion_contains:
        conditions.append("v.vote_name_english ILIKE :motion")
        params["motion"] = f"%{motion_contains}%"
    if agenda_item:
        conditions.append("v.agenda_item_id = :agenda_item")
        params["agenda_item"] = agenda_item
    if date_from:
        conditions.append("m.meeting_date >= :date_from")
        params["date_from"] = coerce_datetime(date_from)
    if date_to:
        conditions.append("m.meeting_date <= :date_to")
        params["date_to"] = coerce_datetime(date_to, end_of_day=True)
    where_clause = " AND ".join(conditions)

    sql = text(f"""
        SELECT v.vote_id, v.contribution_id, v.meeting_id, m.meeting_date,
               v.agenda_item_id, v.vote_name_english, v.result_english,
               v.total_for, v.total_against, v.total_abstain
        FROM votes v
        JOIN meetings m ON v.meeting_id = m.meeting_id
        WHERE {where_clause}
        ORDER BY m.meeting_date DESC, v.vote_id DESC
        LIMIT :limit
    """)
    with _session() as session:
        rows = session.execute(sql, params).fetchall()
    return [_vote_summary(r) for r in rows]


def get_votes_for_speech(speech_id: int) -> List[VoteSummary]:
    """Return votes taken on the same meeting + agenda item as a speech.

    The rhetoric↔vote bridge: a speech's agenda item is resolved, then every vote
    recorded under that agenda item in that meeting is returned, so a member's
    argument can be set beside how the chamber then divided.
    """
    anchor_sql = text(
        "SELECT meeting_id, agenda_item_id FROM speeches WHERE speech_id = :sid"
    )
    votes_sql = text("""
        SELECT v.vote_id, v.contribution_id, v.meeting_id, m.meeting_date,
               v.agenda_item_id, v.vote_name_english, v.result_english,
               v.total_for, v.total_against, v.total_abstain
        FROM votes v
        JOIN meetings m ON v.meeting_id = m.meeting_id
        WHERE v.meeting_id = :meeting_id AND v.agenda_item_id = :agenda_item_id
        ORDER BY v.vote_id ASC
    """)
    with _session() as session:
        anchor = session.execute(anchor_sql, {"sid": speech_id}).first()
        if anchor is None:
            return []
        rows = session.execute(votes_sql, {
            "meeting_id": anchor.meeting_id,
            "agenda_item_id": anchor.agenda_item_id,
        }).fetchall()
    return [_vote_summary(r) for r in rows]


def get_member_voting_record(
    member_id: int,
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    limit: int = 100,
) -> List[MemberVoteItem]:
    """Return how a member voted across recorded votes, newest first."""
    conditions = ["vr.member_id = :member_id"]
    params: dict = {"member_id": member_id, "limit": limit}
    if date_from:
        conditions.append("m.meeting_date >= :date_from")
        params["date_from"] = coerce_datetime(date_from)
    if date_to:
        conditions.append("m.meeting_date <= :date_to")
        params["date_to"] = coerce_datetime(date_to, end_of_day=True)
    where_clause = " AND ".join(conditions)

    sql = text(f"""
        SELECT v.vote_id, v.meeting_id, m.meeting_date, v.agenda_item_id,
               v.vote_name_english, vr.result
        FROM vote_records vr
        JOIN votes v ON vr.vote_id = v.vote_id
        JOIN meetings m ON v.meeting_id = m.meeting_id
        WHERE {where_clause}
        ORDER BY m.meeting_date DESC, v.vote_id DESC
        LIMIT :limit
    """)
    with _session() as session:
        rows = session.execute(sql, params).fetchall()
    return [
        MemberVoteItem(
            vote_id=r.vote_id,
            meeting_id=r.meeting_id,
            meeting_date=r.meeting_date,
            agenda_item_id=r.agenda_item_id,
            vote_name_english=r.vote_name_english,
            result=_vote_result(r.result),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Written QNR (questions/answers not reached)
# ---------------------------------------------------------------------------

def get_written_answers(
    meeting_id: Optional[int] = None,
    agenda_item: Optional[str] = None,
    speaker: Optional[str] = None,
    date_from: Optional[DateLike] = None,
    date_to: Optional[DateLike] = None,
    limit: int = 50,
) -> List[WrittenPair]:
    """Return written QNR Q&A pairs (question + positionally-paired answer).

    Rows are grouped by their deterministic ``pair_id`` so each result is a
    question with its answer (either side may be missing if the feed omitted it).
    ``speaker`` matches either the questioner's name or the answering office's
    job title (answers carry no member id).
    """
    conditions = ["TRUE"]
    params: dict = {"limit": limit}
    if meeting_id is not None:
        conditions.append("w.meeting_id = :meeting_id")
        params["meeting_id"] = meeting_id
    if agenda_item:
        conditions.append("w.agenda_item_id = :agenda_item")
        params["agenda_item"] = agenda_item
    if speaker:
        conditions.append(
            "(w.speaker_name_english ILIKE :speaker "
            "OR w.speaker_job_title_english ILIKE :speaker)"
        )
        params["speaker"] = f"%{speaker}%"
    if date_from:
        conditions.append("m.meeting_date >= :date_from")
        params["date_from"] = coerce_datetime(date_from)
    if date_to:
        conditions.append("m.meeting_date <= :date_to")
        params["date_to"] = coerce_datetime(date_to, end_of_day=True)
    where_clause = " AND ".join(conditions)

    # Pull all matching contributions; group into pairs in Python (a pair is at
    # most one question + one answer, ordered by document position).
    sql = text(f"""
        SELECT w.id, w.meeting_id, m.meeting_date, w.agenda_item_id,
               w.qa_role, w.pair_id, w.order_index,
               w.speaker_name_english, w.speaker_job_title_english, w.text_english
        FROM written_contributions w
        JOIN meetings m ON w.meeting_id = m.meeting_id
        WHERE {where_clause}
        ORDER BY w.meeting_id ASC, w.order_index ASC
    """)
    with _session() as session:
        rows = session.execute(sql, params).fetchall()

    pairs: List[WrittenPair] = []
    index: dict = {}
    for r in rows:
        item = WrittenContributionItem(
            id=r.id,
            qa_role=_qa_role(r.qa_role),
            speaker_name=r.speaker_name_english,
            speaker_job_title=r.speaker_job_title_english,
            text_english=r.text_english,
        )
        # Group on (meeting_id, pair_id); fall back to a per-row key when pair_id
        # is absent so an unpaired contribution still surfaces.
        key = (r.meeting_id, r.pair_id) if r.pair_id else (r.meeting_id, f"_solo_{r.id}")
        pair = index.get(key)
        if pair is None:
            if len(pairs) >= limit:
                continue
            pair = WrittenPair(
                pair_id=r.pair_id,
                meeting_id=r.meeting_id,
                meeting_date=r.meeting_date,
                agenda_item_id=r.agenda_item_id,
                question=None,
                answer=None,
            )
            index[key] = pair
            pairs.append(pair)
        if item.qa_role == "answer":
            pair.answer = item
        else:
            pair.question = item
    return pairs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vote_result(value) -> str:
    """Normalise a stored vote result to its friendly value (e.g. ``DidNotVote``).

    Raw SQL returns the enum *name* (``FOR``, ``DID_NOT_VOTE``) since SQLAlchemy
    persists ``Enum`` by name; map it back to the human value.
    """
    if hasattr(value, "value"):
        return value.value
    try:
        return VoteResultEnum[value].value
    except (KeyError, TypeError):
        return value


def _qa_role(value) -> str:
    """Normalise a stored QA role (``QUESTION``/``ANSWER`` name) to its value."""
    if hasattr(value, "value"):
        return value.value
    try:
        return QaRoleEnum[value].value
    except (KeyError, TypeError):
        return value


def _vote_summary(r) -> VoteSummary:
    return VoteSummary(
        vote_id=r.vote_id,
        contribution_id=r.contribution_id,
        meeting_id=r.meeting_id,
        meeting_date=r.meeting_date,
        agenda_item_id=r.agenda_item_id,
        vote_name_english=r.vote_name_english,
        result_english=r.result_english,
        total_for=r.total_for,
        total_against=r.total_against,
        total_abstain=r.total_abstain,
    )
