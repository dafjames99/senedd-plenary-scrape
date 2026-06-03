"""SQLAlchemy models for Senedd speech pipeline."""
from sqlalchemy import Column, String, Integer, Text, DateTime, Float, ForeignKey, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()


class Meeting(Base):
    """Meeting metadata."""
    __tablename__ = "meetings"

    meeting_id = Column(Integer, primary_key=True)
    assembly = Column(Integer, nullable=False)
    meeting_date = Column(DateTime, nullable=False)
    meeting_type = Column(String(50))

    contributions = relationship("RawContribution", back_populates="meeting")
    speeches = relationship("Speech", back_populates="meeting")


class Member(Base):
    """Member/speaker dimension table."""
    __tablename__ = "members"

    member_id = Column(Integer, primary_key=True)
    name_english = Column(String(255), nullable=False)
    name_welsh = Column(String(255))
    job_title_english = Column(String(255))
    job_title_welsh = Column(String(255))
    biography_english = Column(Text)
    biography_welsh = Column(Text)
    sort_code = Column(String(50))

    contributions = relationship("RawContribution", back_populates="member")
    speeches = relationship("Speech", back_populates="speaker")


class RawContribution(Base):
    """Raw XML ingestion (unmodified source)."""
    __tablename__ = "raw_contributions"

    contribution_id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"), nullable=False)
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
    member_id = Column(Integer, ForeignKey("members.member_id"))
    
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
    clean = relationship("CleanContribution", back_populates="raw", uselist=False)


class CleanContribution(Base):
    """Text-normalized contributions."""
    __tablename__ = "clean_contributions"

    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id"), primary_key=True)
    
    # Cleaned text fields (HTML decoded, tags removed, whitespace normalized)
    contribution_verbatim_clean = Column(Text)
    contribution_translated_clean = Column(Text)

    raw = relationship("RawContribution", back_populates="clean")


class RowTypeEnum(enum.Enum):
    """Classification of contribution rows."""
    SPEECH = "speech"
    PROCEDURAL = "procedural"
    NOISE = "noise"


class ClassifiedContribution(Base):
    """Row classification and routing."""
    __tablename__ = "classified_contributions"

    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id"), primary_key=True)
    row_type = Column(Enum(RowTypeEnum), nullable=False)
    classification_reason = Column(String(255))


class Speech(Base):
    """Core semantic unit: reconstructed speech from grouped contributions."""
    __tablename__ = "speeches"

    speech_id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"), nullable=False)
    assembly = Column(Integer)
    agenda_item_id = Column(String(100), nullable=False)
    speaker_id = Column(Integer, ForeignKey("members.member_id"), nullable=False)
    speaker_name = Column(String(255), nullable=False)

    speech_language = Column(String(50))
    speech_text = Column(Text, nullable=False)

    start_time = Column(DateTime)
    end_time = Column(DateTime)
    source_row_count = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)

    meeting = relationship("Meeting", back_populates="speeches")
    speaker = relationship("Member", back_populates="speeches")
    parts = relationship("SpeechPart", back_populates="speech")
    embeddings = relationship("SpeechEmbedding", back_populates="speech")


class SpeechPart(Base):
    """Traceability: maps speech → original XML contributions."""
    __tablename__ = "speech_parts"

    speech_part_id = Column(Integer, primary_key=True, autoincrement=True)
    speech_id = Column(Integer, ForeignKey("speeches.speech_id"), nullable=False)
    contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id"), nullable=False)

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
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"), nullable=False)
    agenda_item_id = Column(String(100))

    event_time = Column(DateTime)
    event_type = Column(String(100))  # agenda_transition, motion_result, ruling, order_statement, instruction
    speaker_name = Column(String(255))

    raw_text = Column(Text)
    source_contribution_id = Column(Integer, ForeignKey("raw_contributions.contribution_id"))
    senedd_tv_url = Column(String(500))


class SpeechEmbedding(Base):
    """Vector embeddings for speeches (future layer)."""
    __tablename__ = "speech_embeddings"

    embedding_id = Column(Integer, primary_key=True, autoincrement=True)
    speech_id = Column(Integer, ForeignKey("speeches.speech_id"), nullable=False)

    embedding_vector = Column(Text)  # JSON serialized float array
    model_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)
