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

from src.db.pipeline import SeneddPipeline
from src.db.settings import settings
from src.search._dates import DateLike, coerce_datetime

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 240


def _session():
    """Open a session bound to the configured database."""
    return SeneddPipeline(settings.database_url).SessionLocal()


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
              ORDER BY sp.contribution_order_id ASC LIMIT 1) AS senedd_tv_url
        FROM speeches s
        JOIN meetings m ON s.meeting_id = m.meeting_id
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
