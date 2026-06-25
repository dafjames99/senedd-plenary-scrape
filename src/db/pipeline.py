"""Main orchestrator for the Senedd XML-to-speech reconstruction pipeline."""
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Literal, Optional, List
from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy_utils import database_exists, create_database
from alembic import command
from alembic.config import Config
from src.db.db_schema import (
    Meeting, Member, MemberJobTitle, RawContribution, CleanContribution,
    ClassifiedContribution, Speech, SpeechPart, ProceduralEvent,
    RowTypeEnum, SyncCheckpoint, OralQuestion, Vote, VoteRecord, VoteResultEnum,
    WrittenContribution, QaRoleEnum, ArtifactWatch
)
from src.db.transformers import classify_contribution, clean_contribution_verbatim, parse_oral_question_meta
from src.db.fetcher import DataFetcher
from src.db.parser import parse_senedd_xml, parse_votes_xml, parse_qnr_xml
from src.db.settings import settings
import logging

logger = logging.getLogger(__name__)

# Repository root: src/db/pipeline.py -> parents[2]
ROOT_DIR = Path(__file__).resolve().parents[2]


class SeneddPipeline:
    """Orchestrator for XML parsing, cleaning, classification, and speech reconstruction."""
    
    def __init__(self, db_url: str):
        """Initialize pipeline with database connection."""
        self.db_url = db_url
        self.engine = create_engine(db_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
    def _ensure_database_exists(self):
        """Internal helper to ensure the underlying PostgreSQL database container exists."""
        if not database_exists(self.db_url):
            logger.info("Target PostgreSQL database does not exist. Spawning 'senedd_db' container on host...")
            create_database(self.db_url)
            logger.info("Database spawned successfully.")
    
    def _alembic_config(self) -> Config:
        """Build an Alembic Config bound to this pipeline's database URL.

        The URL is passed to ``env.py`` via the ``-x db_url=`` argument so the
        pipeline can migrate whatever database it was constructed against, and
        ``configure_logger`` is disabled so Alembic does not clobber the
        application's logging setup.
        """
        cfg = Config(str(ROOT_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
        cfg.cmd_opts = argparse.Namespace(x=[f"db_url={self.db_url}"])
        cfg.attributes["configure_logger"] = False
        return cfg

    def run_migrations(self):
        """Bring the database schema up to the latest Alembic revision.

        Alembic owns all DDL. This is idempotent — a no-op when already at head.
        """
        logger.info("Applying database migrations (alembic upgrade head)...")
        command.upgrade(self._alembic_config(), "head")
        logger.info("Database schema is at head revision.")

    def _load_procedures(self):
        """Register repo-tracked SQL stored procedures (DATA-lifecycle helpers)."""
        procedures_dir = Path(__file__).resolve().parent / "procedures"
        if not procedures_dir.exists():
            return
        logger.info("Discovering repo-tracked SQL procedures...")
        # Sort ensures 001 runs before 002
        for sql_file in sorted(procedures_dir.glob("*.sql")):
            try:
                sql_script = sql_file.read_text(encoding="utf-8")
                with self.engine.connect() as conn:
                    conn.execute(text(sql_script))
                    conn.commit()
                logger.info(f"[✓] Embedded native database routine: {sql_file.name}")
            except Exception as e:
                logger.error(f"[!] Failed to seed target database routine {sql_file.name}: {e}")

    def create_schema(self):
        """Provision the database: ensure it exists, migrate to head, register procedures.

        Schema structure is owned by Alembic (``run_migrations``); this method no
        longer issues ``create_all`` — that could not ALTER existing tables and was
        the source of the historic rebuild brittleness.
        """
        self._ensure_database_exists()
        self.run_migrations()
        self._load_procedures()

    def ingest_xml(self, session: Session, xml_file: Path) -> int:
        """
        Phase 1: Parse XML and load into raw_contributions, meetings, and members.
        Returns: number of rows ingested
        """
        logger.info("Phase 1: Ingesting XML payload from path source: %s", xml_file)
        meeting_data, members_list, contributions_list = parse_senedd_xml(xml_file)
        
        # Merge meeting
        meeting = Meeting(**meeting_data)
        session.merge(meeting)
        session.flush()
        
        # Merge members
        if members_list:
            stmt = insert(Member).values(members_list)
            upsert_stmt = stmt.on_conflict_do_nothing(index_elements=['member_id'])
            session.execute(upsert_stmt)
            session.flush()
        if contributions_list:
            stmt = insert(RawContribution).values(contributions_list)
            upsert_stmt = stmt.on_conflict_do_nothing(index_elements=['contribution_id'])
            session.execute(upsert_stmt)
            session.flush()
            
        logger.info("Successfully ingested %d raw contribution rows via bulk mapping profiles.", len(contributions_list))
        return len(contributions_list)

    
    def ingest_votes(self, session: Session, xml_file: Path) -> int:
        """Ingest a Plenary *Votes* XML export into ``votes`` + ``vote_records``.

        Designed to run *after* the meeting's transcript so the motion
        ``contribution_id`` and most members already exist. It is defensive
        regardless: it never clobbers the shared ``meetings`` row, upserts any
        member it hasn't seen, and skips (with a warning) any vote whose motion
        contribution is not yet present — those are retried idempotently once the
        transcript lands. Returns the number of vote_records written.
        """
        logger.info("Ingesting Votes payload from: %s", xml_file)
        meeting_data, votes, vote_records, members = parse_votes_xml(xml_file)
        if not meeting_data or not votes:
            logger.warning("No votes parsed from %s; nothing to ingest.", xml_file)
            return 0

        # Ensure the meeting exists without overwriting the transcript's metadata
        # (the meeting row is shared; meeting_type must stay 'plenary').
        session.execute(
            insert(Meeting).values(**meeting_data).on_conflict_do_nothing(index_elements=["meeting_id"])
        )

        # Defensive member upsert — a member may vote without ever having spoken.
        if members:
            session.execute(
                insert(Member).values(members).on_conflict_do_nothing(index_elements=["member_id"])
            )
        session.flush()

        # Only ingest votes whose motion contribution is already present; the FK
        # would otherwise hard-fail. Missing ones are picked up on a later pass.
        candidate_cids = [v["contribution_id"] for v in votes]
        existing_cids = {
            row[0] for row in session.query(RawContribution.contribution_id)
            .filter(RawContribution.contribution_id.in_(candidate_cids)).all()
        }
        ready_votes = [v for v in votes if v["contribution_id"] in existing_cids]
        skipped = [v["contribution_id"] for v in votes if v["contribution_id"] not in existing_cids]
        if skipped:
            logger.warning(
                "Deferring %d vote(s) whose motion contribution is not yet ingested: %s",
                len(skipped), skipped,
            )
        if not ready_votes:
            return 0

        session.execute(
            insert(Vote).values(ready_votes).on_conflict_do_nothing(index_elements=["contribution_id"])
        )
        session.flush()

        # Map motion contribution_id -> assigned vote_id (covers both freshly
        # inserted and pre-existing votes).
        cid_to_vote_id = {
            cid: vid for vid, cid in session.query(Vote.vote_id, Vote.contribution_id)
            .filter(Vote.contribution_id.in_([v["contribution_id"] for v in ready_votes])).all()
        }

        record_rows = []
        for rec in vote_records:
            vote_id = cid_to_vote_id.get(rec["contribution_id"])
            if vote_id is None:
                continue  # belonged to a deferred vote
            try:
                result_enum = VoteResultEnum(rec["result"])
            except ValueError:
                logger.warning("Unknown vote result %r; skipping record.", rec["result"])
                continue
            record_rows.append({
                "vote_id": vote_id,
                "member_id": rec["member_id"],
                "result": result_enum,
            })

        if record_rows:
            session.execute(
                insert(VoteRecord).values(record_rows)
                .on_conflict_do_nothing(index_elements=["vote_id", "member_id"])
            )
            session.flush()

        logger.info(
            "Votes ingest complete: %d motions, %d member records.",
            len(ready_votes), len(record_rows),
        )
        return len(record_rows)

    def ingest_qnr(self, session: Session, xml_file: Path) -> int:
        """Ingest a Plenary *QNR* export into ``written_contributions``.

        The QNR feed has no ``Contribution_ID`` and no clean FK to
        ``raw_contributions``, so this is independent of the transcript apart from
        the shared meeting row (which is never clobbered). Text is double-escaped
        HTML; it is decoded and tag-stripped here via ``clean_contribution_verbatim``.
        Idempotent on the synthetic ``(meeting_id, order_index)`` key.
        Returns the number of written contributions written.
        """
        logger.info("Ingesting QNR payload from: %s", xml_file)
        meeting_data, written, members = parse_qnr_xml(xml_file)
        if not meeting_data or not written:
            logger.warning("No QNR rows parsed from %s; nothing to ingest.", xml_file)
            return 0

        session.execute(
            insert(Meeting).values(**meeting_data).on_conflict_do_nothing(index_elements=["meeting_id"])
        )
        if members:
            session.execute(
                insert(Member).values(members).on_conflict_do_nothing(index_elements=["member_id"])
            )
        session.flush()

        rows = []
        for w in written:
            verbatim = w.pop("raw_verbatim")
            translated = w.pop("raw_translated")
            english = clean_contribution_verbatim(translated) or clean_contribution_verbatim(verbatim)
            # Only keep a Welsh field when the verbatim genuinely differs from the
            # English translation (answers are English-only, duplicated across both).
            welsh = clean_contribution_verbatim(verbatim) if verbatim and verbatim != translated else None
            rows.append({
                **w,
                "qa_role": QaRoleEnum(w["qa_role"]),
                "text_english": english,
                "text_welsh": welsh,
            })

        session.execute(
            insert(WrittenContribution).values(rows)
            .on_conflict_do_nothing(index_elements=["meeting_id", "order_index"])
        )
        session.flush()

        logger.info("QNR ingest complete: %d written contributions.", len(rows))
        return len(rows)

    def process_and_classify_contributions(self, session: Session, meeting_id: Optional[int] = None):
        """Combines text cleaning, metadata extraction, and row classification 

        into a unified execution phase.
        """
        logger.info("Phase 2/3: Processing and classifying rows for meeting_id=%s", meeting_id)
        
        query = session.query(RawContribution)
        if meeting_id:
            query = query.filter(RawContribution.meeting_id == meeting_id)
        raw_contribs = query.all()
        
        logger.debug("Found %d raw rows available for pipeline transformation workflows.", len(raw_contribs))
        
        oral_questions_batch = []
        clean_contributions_batch = []
        classified_contributions_batch = []
        
        for raw in raw_contribs:

            row_dict = {
                'Member_Id': raw.member_id,
                'Member_job_title_English': raw.member_job_title_english,
                'contribution_type': raw.contribution_type,
                'contribution_verbatim': raw.contribution_verbatim,
                'contribution_translated': raw.contribution_translated,
            }
            row_type, reason = classify_contribution(row_dict)
            cleaned_verbatim = clean_contribution_verbatim(raw.contribution_verbatim)
            cleaned_translated = clean_contribution_verbatim(raw.contribution_translated)

            if row_type == "oral-question" or row_type == "topical-question":
                q_num, q_id, clean_text = parse_oral_question_meta(cleaned_verbatim)
                
                if q_id and q_num:
                    logger.debug("Extracted Oral Question entity metadata: ID=%s, Num=%s", q_id, q_num)
                    cleaned_verbatim = clean_text

                    if cleaned_translated:
                        _, _, clean_trans_text = parse_oral_question_meta(cleaned_translated)
                        cleaned_translated = clean_trans_text

                    oral_questions_batch.append({
                        "question_id": q_id,
                        "meeting_id": raw.meeting_id,
                        "contribution_id": raw.contribution_id,
                        "question_number": q_num,
                    })

            clean_contributions_batch.append({
                "contribution_id": raw.contribution_id,
                "contribution_verbatim_clean": cleaned_verbatim,
                "contribution_translated_clean": cleaned_translated,
            })
            classified_contributions_batch.append({
                "contribution_id": raw.contribution_id,
                "row_type": RowTypeEnum(row_type),
                "classification_reason": reason,
            })
            if oral_questions_batch:
                session.execute(insert(OralQuestion).values(oral_questions_batch).on_conflict_do_nothing(index_elements=['question_id']))
            if clean_contributions_batch:
                session.execute(insert(CleanContribution).values(clean_contributions_batch).on_conflict_do_nothing(index_elements=['contribution_id']))
            if classified_contributions_batch:
                session.execute(insert(ClassifiedContribution).values(classified_contributions_batch).on_conflict_do_nothing(index_elements=['contribution_id']))
                
            session.flush()
    
    def save_reconstructed_speeches(self, session: Session, speech_records: list) -> int:
        """Saves speeches using Postgres RETURNING statement to wire children."""
        if not speech_records:
            return 0

        speech_part_records = []
        
        for record in speech_records:
            # Pop out our temporary raw parts tracking field so it doesn't break the query table mapping
            raw_parts = record.pop('_raw_parts', [])
            
            # Construct statement returning the freshly assigned auto-increment key
            stmt = insert(Speech).values(record)
            safe_stmt = stmt.on_conflict_do_nothing(index_elements=['speech_id']).returning(Speech.speech_id)
            
            result = session.execute(safe_stmt)
            returned_row = result.fetchone()
            
            if returned_row:
                # Get the fresh serial ID assigned by Postgres
                generated_id = returned_row[0]
                
                for part in raw_parts:
                    speech_part_records.append({
                        'speech_id': generated_id,
                        'contribution_id': part['contribution_id'],
                        'contribution_order_id': part['contribution_order_id'],
                        'contribution_time': part['contribution_time'],
                        'spoken_url': part['spoken_url'],
                        'translated_url': part['translated_url'],
                        'verbatim_text': part['verbatim_text'],
                    })
                    
        # Batch insert all children downstream in one fast driver payload
        if speech_part_records:
            session.execute(insert(SpeechPart).values(speech_part_records))
            
        session.flush()
        return len(speech_records)
    
    def reconstruct_speeches(self, session: Session, meeting_id: Optional[int] = None) -> int:
        """
        Phase 4: Reconstruct speeches by grouping rows chronologically.
        Speech boundary: speaker changes OR agenda changes.
        Returns: number of speeches created
        """
        logger.info("Phase 4: Initiating speech reconstruction logic. Target filter: meeting_id=%s", meeting_id)
        if meeting_id is not None:
            meeting_ids = [meeting_id]
        else:
            meeting_ids = [m[0] for m in session.query(RawContribution.meeting_id).distinct().all()]
            
        total_speeches = 0
        for m_id in meeting_ids:
            total_speeches += self._reconstruct_meeting_speeches(session, m_id)
            
        logger.info("Successfully consolidated %d semantic speech blocks from row sequences.", total_speeches)
        return total_speeches

    def _deduplicate_overlap(self, existing_text: str, new_text: str) -> str:
        """Removes overlapping words at the boundary of two text segments."""
        existing_words = existing_text.strip().split()
        new_words = new_text.strip().split()
        
        if not existing_words or not new_words:
            return new_text

        # Check for overlapping phrases, starting from the longest possible match
        max_overlap = min(len(existing_words), len(new_words))
        for i in range(max_overlap, 0, -1):
            if existing_words[-i:] == new_words[:i]:
                # Found a match! Return the new text minus the overlapping prefix
                return " ".join(new_words[i:])
                
        return new_text

    def _reconstruct_meeting_speeches(self, session: Session, meeting_id: int) -> int:
        """Reconstruct speeches for a specific meeting chronologically."""
        meeting = session.query(Meeting).filter_by(meeting_id=meeting_id).first()
        if not meeting:
            logger.warning("Aborting speech reconstruction: meeting_id=%s does not exist in schema references.", meeting_id)
            return 0

        # Idempotent rebuild: speeches carry an autoincrement PK with no natural
        # key, so re-running would otherwise duplicate them. Purge this meeting's
        # existing speeches first; the FK cascade clears their speech_parts and
        # (now-stale) speech_embeddings, which the embed sweep regenerates.
        deleted = (
            session.query(Speech)
            .filter(Speech.meeting_id == meeting_id)
            .delete(synchronize_session=False)
        )
        if deleted:
            logger.debug("Meeting %s: cleared %d existing speeches before rebuild.", meeting_id, deleted)

        # Get all speech-classified rows, ordered by contribution_order_id
        speech_rows = session.query(
            RawContribution, CleanContribution
        ).join(
            ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id
        ).join(
            CleanContribution, RawContribution.contribution_id == CleanContribution.contribution_id
        ).filter(
            RawContribution.meeting_id == meeting_id,
            ((ClassifiedContribution.row_type == RowTypeEnum.SPEECH) | (ClassifiedContribution.row_type == RowTypeEnum.ORAL_QUESTION))
        ).order_by(
            RawContribution.contribution_order_id
        ).all()
        
        speeches = []
        current_speech = None
        
        for raw, clean in speech_rows:
            # Check if speaker or agenda item changes
            if current_speech is None or \
               current_speech['speaker_id'] != raw.member_id or \
               current_speech['agenda_item_id'] != raw.agenda_item_id:
                
                if current_speech is not None:
                    speeches.append(current_speech)
                
                current_speech = {
                    'meeting_id': raw.meeting_id,
                    'assembly': raw.assembly,
                    'agenda_item_id': raw.agenda_item_id,
                    'speaker_id': raw.member_id,
                    'speaker_name': raw.member_name_english or 'Unknown',
                    'speech_language': raw.contribution_language,
                    'speech_parts': [],
                    'texts': [],
                }
            
            # Select English translation if available, otherwise verbatim
            text = None
            if clean.contribution_translated_clean:
                text = clean.contribution_translated_clean
            elif clean.contribution_verbatim_clean:
                text = clean.contribution_verbatim_clean
                
            if text:
                if not current_speech['texts']:
                    current_speech['texts'].append(text)
                else:
                    # Reconstruct the current string to check against
                    current_full_text = " ".join(current_speech['texts'])
                    processed_text = self._deduplicate_overlap(current_full_text, text)
                    if processed_text: # Avoid appending empty strings if it was a total duplicate
                        current_speech['texts'].append(processed_text)

            current_speech['speech_parts'].append({
                'contribution_id': raw.contribution_id,
                'contribution_order_id': raw.contribution_order_id,
                'contribution_time': raw.contribution_time,
                'spoken_url': raw.contribution_spoken_seneddtv,
                'translated_url': raw.contribution_translated_seneddtv,
                'verbatim_text': clean.contribution_translated_clean or clean.contribution_verbatim_clean,
            })
            
        if current_speech is not None:
            speeches.append(current_speech)
        
        speech_records = []
        for speech_dict in speeches:
            # Prepare plain dictionaries instead of instantiation objects
            speech_records.append({
                'meeting_id': speech_dict['meeting_id'],
                'assembly': speech_dict['assembly'],
                'agenda_item_id': speech_dict['agenda_item_id'],
                'speaker_id': speech_dict['speaker_id'],
                'speaker_name': speech_dict['speaker_name'],
                'speech_language': speech_dict['speech_language'],
                'speech_text': ' '.join(speech_dict['texts']),
                'source_row_count': len(speech_dict['speech_parts']),
                'created_at': datetime.now(),
                '_raw_parts': speech_dict['speech_parts']
            })
            
            # Cache the speech parts metadata for child tables
            # Note: Because we are inserting parents manually, we will map 
            # their lineage associations right after the parents are saved.
        # Invoke our new explicit PostgreSQL driver routines safely
        inserted_count = self.save_reconstructed_speeches(session, speech_records)
        logger.debug("Meeting %s: Safely processed %d explicit speeches via upsert logic.", meeting_id, inserted_count)
        return inserted_count
    
    def build_members_dimension(self, session: Session, meeting_id: Optional[int] = None):
        """Phase 5a: Build/complete members dimension table."""
        logger.info("Phase 5a: Compiling members dimension constraints for meeting_id=%s", meeting_id)
        query = session.query(RawContribution).filter(RawContribution.member_id.isnot(None))
        if meeting_id:
            query = query.filter(RawContribution.meeting_id == meeting_id)
        raw_rows = query.all()

        unique_members = {}
        unique_job_titles = {}

        for raw in raw_rows:
            # Safely group profile info by member_id.
            # Using raw.member_name_english guarantees the name field is populated.
            unique_members[raw.member_id] = {
                "member_id": raw.member_id,
                "name_english": raw.member_name_english or "Unknown Speaker",
                "biography_english": raw.member_biog_english,
                "biography_welsh": raw.member_biog_welsh,
                "sort_code": raw.member_sortcode
            }

            # Safely group unique job title changes for this specific meeting
            unique_job_titles[(raw.member_id, raw.meeting_id)] = {
                "member_id": raw.member_id,
                "meeting_id": raw.meeting_id,
                "job_title_english": raw.member_job_title_english,
                "job_title_welsh": raw.member_job_title_welsh,
            }

        # Issue updates on completely unique lists
        if unique_members:
            stmt = insert(Member).values(list(unique_members.values()))
            session.execute(stmt.on_conflict_do_update(
                index_elements=['member_id'],
                set_={
                    'biography_english': stmt.excluded.biography_english,
                    'biography_welsh': stmt.excluded.biography_welsh,
                    'sort_code': stmt.excluded.sort_code
                }
            ))

        if unique_job_titles:
            stmt = insert(MemberJobTitle).values(list(unique_job_titles.values()))
            session.execute(stmt.on_conflict_do_update(
                index_elements=['member_id', 'meeting_id'],
                set_={
                    'job_title_english': stmt.excluded.job_title_english,
                    'job_title_welsh': stmt.excluded.job_title_welsh
                }
            ))

        session.flush()
        member_count = session.query(Member).count()
        title_count = session.query(MemberJobTitle).count()
        logger.info("Dimension builds complete. Registry metrics -> Unique Members: %d | Roles Matrix Entries: %d", member_count, title_count)
    
    def build_procedural_events(self, session: Session, meeting_id: Optional[int] = None) -> int:
        """
        Phase 5b: Extract procedural events.
        """
        logger.info("Phase 5b: Processing non-speech procedural entities for meeting_id=%s", meeting_id)
        if meeting_id is not None:
            meeting_ids = [meeting_id]
        else:
            meeting_ids = [m[0] for m in session.query(RawContribution.meeting_id).distinct().all()]
            
        total_events = 0
        for m_id in meeting_ids:
            total_events += self._build_meeting_procedural_events(session, m_id)
            
        logger.info("Successfully synchronized %d discrete procedural logs.", total_events)
        return total_events

    def _build_meeting_procedural_events(self, session: Session, meeting_id: int) -> int:
        meeting = session.query(Meeting).filter_by(meeting_id=meeting_id).first()
        if not meeting:
            return 0

        # Idempotent rebuild: procedural_events has an autoincrement PK and no
        # conflict guard, so purge this meeting's rows before re-inserting.
        session.query(ProceduralEvent).filter(
            ProceduralEvent.meeting_id == meeting_id
        ).delete(synchronize_session=False)

        procedural_rows = (
            session.query(RawContribution)
            .filter(RawContribution.meeting_id == meeting_id)
            .join(ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id)
            .filter(ClassifiedContribution.row_type == RowTypeEnum.PROCEDURAL)
            .all()
        )
        unique_events = {}
        for raw in procedural_rows:
            event_type = 'ruling' if raw.member_job_title_english and 'Llywydd' in raw.member_job_title_english else raw.contribution_type
            
            unique_events[raw.contribution_id] = {
                "meeting_id": meeting_id,
                "agenda_item_id": raw.agenda_item_id,
                "event_time": raw.contribution_time,
                "event_type": event_type,
                "speaker_name": raw.member_name_english or 'Unknown',
                "raw_text": raw.contribution_verbatim or raw.contribution_translated,
                "source_contribution_id": raw.contribution_id,
                "senedd_tv_url": raw.contribution_spoken_seneddtv,
            }
            
        # Execute a direct, safe batch insert without forcing an ON CONFLICT check on unindexed columns
        if unique_events:
            stmt = insert(ProceduralEvent).values(list(unique_events.values()))
            session.execute(stmt)
            session.flush()
        return len(unique_events)
    
    def validate_pipeline(self) -> dict:
        """
        Phase 6: Validate pipeline output.
        Returns: validation report
        """
        logger.info("Phase 6: Executing data-lineage and database structural integrity validation tests.")
        session = self.SessionLocal()
        
        report = {
            'raw_contributions': session.query(RawContribution).count(),
            'clean_contributions': session.query(CleanContribution).count(),
            'classified_contributions': session.query(ClassifiedContribution).count(),
            'speeches': session.query(Speech).count(),
            'speech_parts': session.query(SpeechPart).count(),
            'members': session.query(Member).count(),
            'procedural_events': session.query(ProceduralEvent).count(),
        }
        
        # Check traceability
        speech_ids_with_parts = session.query(SpeechPart.speech_id).distinct().count()
        missing_traceability = report['speeches'] - speech_ids_with_parts
        report['speeches_with_parts'] = speech_ids_with_parts
        report['missing_traceability'] = missing_traceability
        
        # Check for empty speeches
        empty_speeches = session.query(Speech).filter(
            (Speech.speech_text == None) | (Speech.speech_text == '')
        ).count()
        report['empty_speeches'] = empty_speeches
        
        session.close()
        # Log data anomalies as warnings or errors based on threshold flags
        if missing_traceability > 0:
            logger.warning("Lineage tracking structural gap detected: %d speeches lack corresponding records in speech_parts.", missing_traceability)
        if empty_speeches > 0:
            logger.error("Data Quality Exception: Found %d speech records containing empty or null text strings.", empty_speeches)
        
        logger.info(
            "\n"
            "============================================================\n"
            "                     VALIDATION REPORT                      \n"
            "============================================================\n"
            f"Raw contributions ingested:        {report['raw_contributions']}\n"
            f"Cleaned contributions:             {report['clean_contributions']}\n"
            f"Classified contributions:          {report['classified_contributions']}\n"
            f"Reconstructed speeches:            {report['speeches']}\n"
            f"Speech parts (lineage):            {report['speech_parts']}\n"
            f"Unique members:                    {report['members']}\n"
            f"Procedural events:                 {report['procedural_events']}\n"
            f"Speeches with parts:               {report['speeches_with_parts']}\n"
            f"Missing traceability:              {report['missing_traceability']}\n"
            f"Empty speeches (data quality):     {report['empty_speeches']}\n"
            "============================================================"
        )
        
        return report

    def process_meetings(self, session: Session, meeting_ids: List[int]):
        """Run all pipeline transformation phases for a list of meeting IDs atomically."""
        for m_id in meeting_ids:
            self.process_and_classify_contributions(session, m_id)
            self.reconstruct_speeches(session, m_id)
            self.build_members_dimension(session, m_id)
            self.build_procedural_events(session, m_id)
    
    def run_full_pipeline(self, xml_file: Path):
        """Rebuild all DATA from a source XML file, preserving the schema.

        Re-scoped from the historic "drop and recreate schema" behaviour: schema
        structure is owned by Alembic and left intact. ``--force`` now means a full
        DATA wipe (``purge_all_tables`` truncates every table, including
        sync_checkpoints) followed by re-ingestion.
        """
        logger.info("Initializing full DATA rebuild (schema preserved, managed by Alembic).")
        # Ensure schema exists / is at head and procedures are registered.
        self.create_schema()

        # Wipe all data (CASCADE truncation) without touching the schema.
        logger.warning("Truncating ALL tables via purge_all_tables() — data reset.")
        with self.SessionLocal() as session:
            with session.begin():
                session.execute(text("CALL purge_all_tables();"))

        # Ingest XML
        with self.SessionLocal() as session:
            with session.begin():
                self.ingest_xml(session, xml_file)
                
        # Get all identified target meeting IDs
        with self.SessionLocal() as session:
            meeting_ids = [m[0] for m in session.query(RawContribution.meeting_id).distinct().all()]
            
        # Execute transformation loop per meeting chunk atomically
        for m_id in meeting_ids:
            with self.SessionLocal() as session:
                with session.begin():
                    self.process_meetings(session, [m_id])
                    
        self.validate_pipeline()
        logger.info("Full orchestration sequence complete without unhandled pipeline errors.")

    def get_last_sync_date(self, session: Session) -> datetime:
        """Get the date of the most recent processed meeting from sync checkpoints."""
        latest = session.query(SyncCheckpoint).order_by(SyncCheckpoint.created_at.desc()).first()
        if latest and latest.last_sync_date:
            return latest.last_sync_date
        return datetime(2000, 1, 1)

    def record_sync_checkpoint(self, session: Session, file_count: int, status: str = "success", notes: str = ""):
        """Record sync checkpoint for resumability."""
        latest_meeting = session.query(RawContribution.meeting_date).order_by(RawContribution.meeting_date.desc()).first()
        checkpoint = SyncCheckpoint(
            last_sync_date=datetime.utcnow(),
            last_meeting_id=latest_meeting[0] if latest_meeting else None,
            file_count=file_count,
            status=status,
            notes=notes
        )
        session.add(checkpoint)
        
    def process_single_meeting(self, meeting: Meeting, data_dir: Path, keep_xml: bool) -> bool:
        """
        Core pipeline execution block for an isolated meeting instance.
        Returns True if fully processed and committed, False otherwise.
        """
        meeting_id = int(meeting.meeting_id)
        logger.info("Starting targeted extraction process loop on Meeting ID context scope: %s", meeting_id)
        
        fetcher = DataFetcher()
        xml_path = fetcher.download_file(meeting, data_dir)
        if not xml_path or not xml_path.exists():
            logger.error("HTTP Fetch Error: Download payload validation failed for meeting resource: %s. Skipping target entry.", meeting_id)
            return False
        
        try:
            # Ingest XML inside its own transaction
            with self.SessionLocal() as session:
                with session.begin():
                    self.ingest_xml(session, xml_path)
            
            # Transform the meeting atomically inside its own transaction
            with self.SessionLocal() as session:
                with session.begin():
                    self.process_meetings(session, [meeting_id])
            
            success = True
        except Exception as e:
            logger.exception("Pipeline Engine Failure: Fatal exception encountered during parse phase on assembly meeting context %s: %s", meeting_id, e)
            success = False
        finally:
            # Cleanup XML if requested
            if not keep_xml and xml_path and xml_path.exists():
                fetcher.cleanup_file(xml_path)
                logger.info("Cleaned up working manifest cache payload: %s", xml_path)
                
        if success:
            logger.info("Meeting %s fully committed to operational dimensions.", meeting_id)
        return success

    def process_meeting_all_artifacts(self, meeting: Meeting, data_dir: Path, keep_xml: bool) -> bool:
        """Process a meeting's transcript plus any Votes/QNR artifacts in one pass.

        Used by the backfill path, where Votes/QNR are typically already published
        so there is no need to defer them to the artifact-watch sweep. Ordering is
        deliberate: the transcript is ingested first (committed in its own
        transaction by ``process_single_meeting``) so the vote motion's
        contribution row and members exist before ``ingest_votes`` runs; QNR is
        independent of the transcript. Artifact failures are logged but
        non-fatal — a missing Votes export must not lose the transcript.

        Returns True if the transcript was processed.
        """
        if not self.process_single_meeting(meeting, data_dir, keep_xml):
            return False

        artifacts = meeting.artifacts or {}
        for artifact_type, ingest_fn in (
            ("Votes", self.ingest_votes),
            ("QNR", self.ingest_qnr),
        ):
            if artifact_type in artifacts:
                self._ingest_meeting_artifact(
                    meeting, artifact_type, ingest_fn, data_dir, keep_xml
                )
        return True

    def _ingest_meeting_artifact(self, meeting, artifact_type, ingest_fn, data_dir, keep_xml) -> bool:
        """Download and ingest a single non-transcript artifact (Votes/QNR)."""
        fetcher = DataFetcher()
        xml_path = fetcher.download_file(meeting, data_dir, transcript_type=artifact_type)
        if not xml_path or not xml_path.exists():
            logger.warning(
                "Could not download %s for meeting %s; skipping artifact.",
                artifact_type, meeting.meeting_id,
            )
            return False
        try:
            with self.SessionLocal() as session:
                with session.begin():
                    ingest_fn(session, xml_path)
            return True
        except Exception as e:
            logger.exception(
                "Failed ingesting %s for meeting %s: %s",
                artifact_type, meeting.meeting_id, e,
            )
            return False
        finally:
            if not keep_xml and xml_path and xml_path.exists():
                fetcher.cleanup_file(xml_path)

    # Artifact types we re-check for after a transcript lands, mapped to the
    # portal's transcript-type filter and the ingest method that consumes them.
    _WATCHED_ARTIFACTS = {
        "votes": "Votes",
        "qnr": "QNR",
    }

    def register_artifact_watches(self, session: Session, meeting_id: int, meeting_date: datetime):
        """Open pending Votes/QNR watches for a freshly-ingested transcript meeting.

        Idempotent on (meeting_id, artifact_type). Skips meetings already older
        than the watch window (e.g. historical backfill) — those artifacts, if
        they exist, are already published and not subject to late attachment.
        """
        deadline = meeting_date + timedelta(days=settings.artifact_watch_days)
        if deadline < datetime.utcnow():
            return
        rows = [
            {"meeting_id": meeting_id, "artifact_type": at, "status": "pending", "deadline": deadline}
            for at in self._WATCHED_ARTIFACTS
        ]
        session.execute(
            insert(ArtifactWatch).values(rows)
            .on_conflict_do_nothing(index_elements=["meeting_id", "artifact_type"])
        )

    def run_artifact_watch_sweep(self, data_dir: Optional[Path] = None, keep_xml: bool = False) -> int:
        """Re-check the portal for any pending Votes/QNR and attach those now available.

        The portal's default (unfiltered) listing covers the recent meetings —
        comfortably wider than the watch window — so it is fetched once and each
        artifact type is matched by ``meeting_id``. (Date-parameterised URLs
        return a linkless page, so per-meeting queries are not viable.) For each
        pending watch: expire it silently once past its deadline; otherwise, if
        the download is now present, ingest it idempotently and mark the watch
        done. Returns the number of artifacts ingested this sweep.
        """
        data_dir = data_dir or Path("data")
        data_dir.mkdir(exist_ok=True)

        with self.SessionLocal() as session:
            pending = (
                session.query(
                    ArtifactWatch.id, ArtifactWatch.meeting_id,
                    ArtifactWatch.artifact_type, ArtifactWatch.deadline,
                )
                .filter(ArtifactWatch.status == "pending")
                .all()
            )

        if not pending:
            logger.info("Artifact watch sweep: no pending watches.")
            return 0

        logger.info("Artifact watch sweep: %d pending watch(es) to re-check.", len(pending))
        fetcher = DataFetcher()
        now = datetime.utcnow()
        ingested = 0

        # One portal fetch; build a {meeting_id(str) -> Meeting} map per artifact.
        try:
            html = fetcher.get_html_page()
        except Exception as e:
            logger.warning("Artifact watch sweep aborted — portal fetch failed: %s", e)
            return 0
        available: dict = {}
        for artifact_type, transcript_type in self._WATCHED_ARTIFACTS.items():
            available[artifact_type] = {
                str(m.meeting_id): m
                for m in fetcher.parse_meetings_from_html(html, transcript_type=transcript_type)
            }

        for watch_id, meeting_id, artifact_type, deadline in pending:
            if now > deadline:
                logger.info(
                    "Watch %s (%s for meeting %s) past deadline; expiring.",
                    watch_id, artifact_type, meeting_id,
                )
                self._update_watch(watch_id, status="expired", checked_at=now)
                continue

            transcript_type = self._WATCHED_ARTIFACTS[artifact_type]
            match = available.get(artifact_type, {}).get(str(meeting_id))
            if match is None:
                logger.debug("%s not yet available for meeting %s.", transcript_type, meeting_id)
                self._update_watch(watch_id, checked_at=now, bump_attempt=True)
                continue

            xml_path = fetcher.download_file(match, data_dir, transcript_type=transcript_type)
            if not xml_path or not xml_path.exists():
                self._update_watch(watch_id, checked_at=now, bump_attempt=True)
                continue

            try:
                with self.SessionLocal() as session:
                    with session.begin():
                        if artifact_type == "votes":
                            self.ingest_votes(session, xml_path)
                        else:
                            self.ingest_qnr(session, xml_path)
                self._update_watch(watch_id, status="done", checked_at=now)
                ingested += 1
                logger.info("Attached %s for meeting %s.", artifact_type, meeting_id)
            except Exception as e:
                logger.exception("Failed to ingest %s for meeting %s: %s", artifact_type, meeting_id, e)
                self._update_watch(watch_id, checked_at=now, bump_attempt=True)
            finally:
                if not keep_xml and xml_path and xml_path.exists():
                    fetcher.cleanup_file(xml_path)

        logger.info("Artifact watch sweep complete: %d artifact(s) attached.", ingested)
        return ingested

    def _update_watch(self, watch_id: int, status: Optional[str] = None,
                      checked_at: Optional[datetime] = None, bump_attempt: bool = False):
        """Apply a small status/bookkeeping update to a single watch row."""
        values: dict = {}
        if status is not None:
            values["status"] = status
        if checked_at is not None:
            values["last_checked"] = checked_at
        if bump_attempt:
            values["attempts"] = ArtifactWatch.attempts + 1
        if not values:
            return
        with self.SessionLocal() as session:
            with session.begin():
                session.query(ArtifactWatch).filter(ArtifactWatch.id == watch_id).update(
                    values, synchronize_session=False
                )

    def run_incremental(
        self,
        data_dir: Optional[Path] = None,
        keep_xml: bool = False,
        last_sync_date: Optional[datetime] = None,
        transcript_type: Literal["BilingualTranscript", "WelshTranscript", "EnglishTranscript", "Votes", "QNR"] = "BilingualTranscript"
    ):
        """
        Run incremental pipeline: automatically detect changes since last sync date and execute.
        """
        logger.info("Initializing scheduled incremental compilation workflow task.")
        data_dir = data_dir or Path("data")
        data_dir.mkdir(exist_ok=True)
        
        self.create_schema()
        fetcher = DataFetcher()
        
        # Get last sync date from database
        with self.SessionLocal() as session:
            if last_sync_date is None:
                last_sync_date = self.get_last_sync_date(session)
            logger.info("Scanning index registry for plenary meetings uploaded since target timestamp: %s", last_sync_date.date())
            
        # Detect new meetings
        new_meetings = fetcher.check_for_updates(last_sync_date, transcript_type)
        if not new_meetings:
            logger.info("No new transcripts on remote Senedd feeds.")
        else:
            logger.info("Discovered %d new plenary session transcripts requiring transformation processing.", len(new_meetings))

        # Dispatch to the worker method, registering a Votes/QNR watch per success.
        files_processed = 0
        for meeting in new_meetings:
            if self.process_single_meeting(meeting, data_dir, keep_xml):
                files_processed += 1
                with self.SessionLocal() as session:
                    with session.begin():
                        self.register_artifact_watches(
                            session, int(meeting.meeting_id), meeting.meeting_date
                        )

        # Record checkpoint
        if files_processed > 0:
            with self.SessionLocal() as session:
                with session.begin():
                    self.record_sync_checkpoint(session, files_processed, status="success")

        # Always sweep for late-publishing Votes/QNR — they attach to meetings
        # processed on earlier runs, not just this one, so this must run even when
        # no new transcripts were found.
        self.run_artifact_watch_sweep(data_dir, keep_xml)

        logger.info("Incremental synchronization job execution finalized. Total synchronized sets: %d", files_processed)

    def run_for_meetings(
        self, 
        meetings: List[Meeting], 
        data_dir: Optional[Path] = None, 
        keep_xml: bool = False
    ) -> int:
        """
        Bypass entry point: Force execution on an explicit list of Meeting objects.
        Perfect for streaming direct results straight out of a backfill fetcher cycle.
        """
        logger.info("Initializing targeted bypass compilation task for %d specific entities.", len(meetings))
        data_dir = data_dir or Path("data")
        data_dir.mkdir(exist_ok=True)
        
        self.create_schema()
        
        files_processed = 0
        for meeting in meetings:
            if self.process_meeting_all_artifacts(meeting, data_dir, keep_xml):
                files_processed += 1
                # Open watches for any artifact not yet published. This self-skips
                # historic meetings (deadline already past), so it is a no-op for a
                # back-dated backfill but keeps a recent-window backfill correct —
                # late Votes/QNR get attached by the next incremental sweep.
                with self.SessionLocal() as session:
                    with session.begin():
                        self.register_artifact_watches(
                            session, int(meeting.meeting_id), meeting.meeting_date
                        )

        logger.info("Targeted manual execution processing complete. Ingested profiles: %d", files_processed)
        return files_processed
    
    def reprocess_downstream_from_raw(self, clear_dimensions: bool = True, clear_embeddings: bool = False):
        """Bypasses network operations. 
        Uses a native SQL procedure to drop dirty downstream tables, then leverages 
        local Python logic to recalculate speech boundaries and text transformations.
        """
        logger.info("Initializing downstream reprocessing workflow from local RAW cache context.")
        
        # STAGE 1: Let Postgres handle the heavy lifting of wiping old data safely
        with self.SessionLocal() as session:
            with session.begin():
                logger.info("Calling database-native purge procedure...")
                try:
                    session.execute(
                        text("CALL purge_downstream_tables(:clear_dims, :clear_embs);"),
                        {"clear_dims": clear_dimensions, "clear_embs": clear_embeddings}
                    )
                except Exception as e:
                    logger.exception("Failed to execute native purge procedure. Aborting reprocessing sequence: %s", e)
                    return
        with self.SessionLocal() as session:
            meeting_ids = [
                m[0] for m in session.query(RawContribution.meeting_id)
                .distinct()
                .order_by(RawContribution.meeting_id)
                .all()
            ]
        if not meeting_ids:
            logger.warning("[!] No raw data found in `raw_contributions`. Reprocessing halted.")
            return
            
        logger.info(f"[+] Identified {len(meeting_ids)} distinct meetings ready for local rebuilding.")

        for i, m_id in enumerate(meeting_ids, 1):
            logger.info(f"[*] [{i}/{len(meeting_ids)}] Processing local rebuild for meeting ID: {m_id}")
            try:
                with self.SessionLocal() as session:
                    with session.begin():
                        self.process_meetings(session, [m_id])
            except Exception as e:
                logger.error(f"[!] Compilation failed on local meeting checkpoint context {m_id}: {e}")
                continue
                
        self.validate_pipeline()
        logger.info("[✓] Downstream transformation reconstruction finalized successfully.")