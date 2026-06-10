"""Main orchestrator for the Senedd XML-to-speech reconstruction pipeline."""
import os
from pathlib import Path
from datetime import datetime
from typing import Literal, Optional, List
from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy_utils import database_exists, create_database
from src.db.db_schema import (
    Base, Meeting, Member, MemberJobTitle, RawContribution, CleanContribution, 
    ClassifiedContribution, Speech, SpeechPart, ProceduralEvent, 
    RowTypeEnum, SyncCheckpoint, OralQuestion
)
from src.db.transformers import classify_contribution, clean_contribution_verbatim, parse_oral_question_meta
from src.db.fetcher import DataFetcher
from src.db.parser import parse_senedd_xml
import logging

logger = logging.getLogger(__name__)

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
    
    def create_schema(self):
        """Verify database cluster target existence, compile extensions, and build tables."""
        # 1. Step out of the session context to verify the database container itself exists
        self._ensure_database_exists()
        Base.metadata.create_all(self.engine)
        logger.info("Database schema created successfully.")
        procedures_dir = Path(__file__).resolve().parent / "procedures"
        if procedures_dir.exists():
            logger.info("Discovering repo-tracked SQL procedures...")
            
            # Sort ensures 001 runs before 002
            for sql_file in sorted(procedures_dir.glob("*.sql")):
                try:
                    with open(sql_file, "r", encoding="utf-8") as f:
                        sql_script = f.read()
                    
                    # Execute the raw CREATE OR REPLACE PROCEDURE block
                    with self.engine.connect() as conn:
                        # Using execution_options(isolate_level="AUTOCOMMIT") handles complex blocks cleanly
                        conn.execute(text(sql_script))
                    logger.info(f"[✓] Embedded native database routine: {sql_file.name}")
                    
                except Exception as e:
                    logger.error(f"[!] Failed to seed target database routine {sql_file.name}: {e}")
        
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

            if row_type == "oral-question":
                q_num, q_id, clean_text = parse_oral_question_meta(cleaned_verbatim)
                
                if q_id and q_num:
                    logger.debug("Extracted Oral Question entity metadata: ID=%s, Num=%s", q_id, q_num)
                    cleaned_verbatim = clean_text

                    if cleaned_translated:
                        _, _, clean_trans_text = parse_oral_question_meta(cleaned_translated)
                        cleaned_translated = clean_trans_text
                    
                    # oral_q = OralQuestion(
                    #     question_id=q_id,
                    #     meeting_id=raw.meeting_id,
                    #     contribution_id=raw.contribution_id,
                    #     question_number=q_num,
                    # )
                    # session.merge(oral_q)
                    oral_questions_batch.append({
                        "question_id": q_id,
                        "meeting_id": raw.meeting_id,
                        "contribution_id": raw.contribution_id,
                        "question_number": q_num,
                    })

            # clean = CleanContribution(
            #     contribution_id=raw.contribution_id,
            #     contribution_verbatim_clean=cleaned_verbatim,
            #     contribution_translated_clean=cleaned_translated,
            # )
            # classified = ClassifiedContribution(
            #     contribution_id=raw.contribution_id,
            #     row_type=RowTypeEnum(row_type),
            #     classification_reason=reason,
            # )
            
            # session.merge(clean)
            # session.merge(classified)
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
                'created_at': datetime.now()
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
        """Run all pipeline phases (fresh rebuild)."""
        logger.info("Initializing full pipeline database reset and sync routine execution sequence.")
        # Drop existing schema for fresh start
        self._ensure_database_exists()        
        Base.metadata.drop_all(self.engine)
        logger.info("Dropped all tables from schema target.")
        
        self.create_schema()
        
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
            logger.info("No incremental updates found on remote Senedd feeds. Hibernating.")
            return
        
        logger.info("Discovered %d new plenary session transcripts requiring transformation processing.", len(new_meetings))
        
        # Dispatch to the worker method
        files_processed = 0
        for meeting in new_meetings:
            if self.process_single_meeting(meeting, data_dir, keep_xml):
                files_processed += 1
                
        # Record checkpoint
        if files_processed > 0:
            with self.SessionLocal() as session:
                with session.begin():
                    self.record_sync_checkpoint(session, files_processed, status="success")
        
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
            if self.process_single_meeting(meeting, data_dir, keep_xml):
                files_processed += 1
                
        logger.info("Targeted manual execution processing complete. Ingested profiles: %d", files_processed)
        return files_processed