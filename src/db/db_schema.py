"""SQLAlchemy models for Senedd speech pipeline."""
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, Enum, UniqueConstraint, Index, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from datetime import datetime
import enum

Base = declarative_base()


class Meeting(Base):
    """Meeting metadata."""
    __tablename__ = "meetings"

    meeting_id = Column(Integer, primary_key=True)
    assembly = Column(Integer, nullable=False)
    meeting_date = Column(DateTime, nullable=False)
    meeting_type = Column(String(100))

    contributions = relationship("RawContribution", back_populates="meeting", cascade="all, delete-orphan")
    speeches = relationship("Speech", back_populates="meeting", cascade="all, delete-orphan")
    procedural_events = relationship("ProceduralEvent", back_populates="meeting", cascade="all, delete-orphan")


class Member(Base):
    """Member/speaker dimension table."""
    __tablename__ = "members"

    member_id = Column(Integer, primary_key=True)

    name_english = Column(String(255), nullable=False)
    name_welsh = Column(String(255))

    biography_english = Column(Text)
    biography_welsh = Column(Text)

    sort_code = Column(String(50))

    contributions = relationship("RawContribution", back_populates="member")
    speeches = relationship("Speech", back_populates="speaker")

    job_titles = relationship(
        "MemberJobTitle",
        back_populates="member",
        cascade="all, delete-orphan"
    )
    
class MemberJobTitle(Base):
    """
    Meeting-specific member role/title.
    A member may hold different roles across meetings.
    """
    __tablename__ = "member_job_titles"

    id = Column(Integer, primary_key=True, autoincrement=True)

    member_id = Column(
        Integer,
        ForeignKey("members.member_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    meeting_id = Column(
        Integer,
        ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    job_title_english = Column(String(255))
    job_title_welsh = Column(String(255))

    member = relationship("Member", back_populates="job_titles")
    meeting = relationship("Meeting")

    __table_args__ = (
        UniqueConstraint(
            "member_id",
            "meeting_id",
            name="uq_member_meeting_title"
        ),
    )

class RawContribution(Base):
    """Raw XML ingestion (unmodified source)."""
    __tablename__ = "raw_contributions"

    contribution_id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id", ondelete="CASCADE"), nullable=False)
    assembly = Column(Integer)
    meeting_date = Column(DateTime)
    contribution_order_id = Column(Integer)
    contribution_language = Column(String(10))
    contribution_time = Column(DateTime)
    contribution_spoken_seneddtv = Column(String(500))
    contribution_translated_seneddtv = Column(String(500))

    agenda_item_id = Column(String(100))
    agenda_item_welsh = Column(String(500))
    agenda_item_english = Column(String(500))

    contribution_type = Column(String(10))
    attendee_id = Column(Integer)
    member_id = Column(Integer, ForeignKey("members.member_id", ondelete="SET NULL"))
    
    # Member metadata (denormalized from XML)
    member_name_english = Column(String(255))
    member_biog_english = Column(Text)
    member_biog_welsh = Column(Text)
    member_job_title_english = Column(String(255))
    member_job_title_welsh = Column(String(255))
    member_sortcode = Column(String(50))

    contribution_english = Column(Text)
    contribution_welsh = Column(Text)
    contribution_verbatim = Column(Text)
    contribution_translated = Column(Text)

    meeting = relationship("Meeting", back_populates="contributions")
    member = relationship("Member", back_populates="contributions")
    clean = relationship("CleanContribution", back_populates="raw", uselist=False, cascade="all, delete-orphan")
    classified = relationship("ClassifiedContribution", back_populates="raw", uselist=False, cascade="all, delete-orphan")


class CleanContribution(Base):
    """Text-normalized contributions."""
    __tablename__ = "clean_contributions"

    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id", ondelete="CASCADE"), primary_key=True)
    
    # Cleaned text fields (HTML decoded, tags removed, whitespace normalized)
    contribution_verbatim_clean = Column(Text)
    contribution_translated_clean = Column(Text)

    raw = relationship("RawContribution", back_populates="clean")


class RowTypeEnum(enum.Enum):
    """Classification of contribution rows."""
    SPEECH = "speech"
    PROCEDURAL = "procedural"
    NOISE = "noise"
    ORAL_QUESTION = "oral-question"
    TOPICAL_QUESTION = "topical-question"


class ClassifiedContribution(Base):
    """Row classification and routing."""
    __tablename__ = "classified_contributions"

    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id", ondelete="CASCADE"), primary_key=True)
    row_type = Column(Enum(RowTypeEnum), nullable=False)
    classification_reason = Column(String(255))

    raw = relationship("RawContribution", back_populates="classified")

class OralQuestion(Base):
    """
    Formal metadata for oral questions tabled in the Senedd.
    Maps 1:1 or 1:Many back to the raw/clean contribution that introduced it.
    """
    __tablename__ = "oral_questions"

    question_id = Column(String(50), primary_key=True) # e.g., 'OQ64075' or 'TQ1234'
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id", ondelete="CASCADE"), nullable=False, index=True)
    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id", ondelete="CASCADE"), nullable=False, unique=True)
    
    question_number = Column(Integer, nullable=False) # e.g., 1

class Speech(Base):
    """Core semantic unit: reconstructed speech from grouped contributions."""
    __tablename__ = "speeches"

    speech_id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id", ondelete="CASCADE"), nullable=False)
    assembly = Column(Integer)
    agenda_item_id = Column(String(100), nullable=False)
    speaker_id = Column(Integer, ForeignKey("members.member_id", ondelete="CASCADE"), nullable=False)
    speaker_name = Column(String(255), nullable=False)

    speech_language = Column(String(50))
    speech_text = Column(Text, nullable=False)

    source_row_count = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)

    meeting = relationship("Meeting", back_populates="speeches")
    speaker = relationship("Member", back_populates="speeches")
    parts = relationship("SpeechPart", back_populates="speech", cascade="all, delete-orphan")
    embeddings = relationship("SpeechEmbedding", back_populates="speech", cascade="all, delete-orphan")


class SpeechPart(Base):
    """Traceability: maps speech → original XML contributions."""
    __tablename__ = "speech_parts"

    speech_part_id = Column(Integer, primary_key=True, autoincrement=True)
    speech_id = Column(Integer, ForeignKey("speeches.speech_id", ondelete="CASCADE"), nullable=False)
    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id", ondelete="CASCADE"), nullable=False)

    contribution_order_id = Column(Integer)
    contribution_time = Column(DateTime)
    spoken_url = Column(String(500))
    translated_url = Column(String(500))
    verbatim_text = Column(Text)

    speech = relationship("Speech", back_populates="parts")


class ProceduralEvent(Base):
    """Non-speech content: Llywydd interventions, motions, instructions."""
    __tablename__ = "procedural_events"

    procedural_id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id", ondelete="CASCADE"), nullable=False)
    agenda_item_id = Column(String(100))

    event_time = Column(DateTime)
    event_type = Column(String(100))  # agenda_transition, motion_result, ruling, order_statement, instruction
    speaker_name = Column(String(255))

    raw_text = Column(Text)
    source_contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id", ondelete="CASCADE"))
    senedd_tv_url = Column(String(500))

    meeting = relationship("Meeting", back_populates="procedural_events")


class SpeechEmbedding(Base):
    """Polymorphic vector embeddings over any retrievable text source.

    Historically speech-only; generalised in Phase 3 so one semantic search can
    span spoken speeches and written QNR Q&A. The canonical discriminator is the
    ``(source_type, source_id)`` pair — new code keys on it exclusively. The
    legacy ``speech_id`` column (and its cascade FK) is retained for one release
    as a rollback safety net for the populated gemma corpus and will be dropped
    in a follow-up migration; until then speech rows populate both it and
    ``source_id``. Because the generic ``source_id`` carries no FK, cleanup of
    non-speech embeddings on reprocess/purge is handled explicitly in the
    pipeline and the ``purge_*`` SQL procedures.
    """
    __tablename__ = "speech_embeddings"

    embedding_id = Column(Integer, primary_key=True, autoincrement=True)

    # Polymorphic discriminator: 'speech' | 'written' | 'vote'.
    source_type = Column(String(20), nullable=False, server_default="speech")
    source_id = Column(Integer, nullable=False)

    # Legacy speech FK — nullable now; cascade still protects speech rows during
    # the keep-then-drop window. NULL for non-speech sources.
    speech_id = Column(Integer, ForeignKey("speeches.speech_id", ondelete="CASCADE"), nullable=True)

    chunk_index = Column(Integer)
    chunk_text = Column(Text)
    embedding_vector = Column(Vector)
    model_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)

    speech = relationship("Speech", back_populates="embeddings")


class SyncCheckpoint(Base):
    """Track incremental sync progress for resumability."""
    __tablename__ = "sync_checkpoints"

    checkpoint_id = Column(Integer, primary_key=True, autoincrement=True)
    last_sync_date = Column(DateTime, nullable=False)
    last_meeting_id = Column(DateTime)  # Most recent meeting processed
    file_count = Column(Integer)  # Files processed in this sync
    status = Column(String(50))  # success, partial, error
    notes = Column(Text)  # Optional notes
    created_at = Column(DateTime, default=datetime.now)

# Critical lifecycle hook: Automatically ensure pgvector extension is initialized in Postgres
@event.listens_for(Base.metadata, "before_create")
def receive_before_create(target, connection, **kw):
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))