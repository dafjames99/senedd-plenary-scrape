"""Main orchestrator for the Senedd XML-to-speech reconstruction pipeline."""
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

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
        self.engine = create_engine(db_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
    def create_schema(self):
        """Create all tables in the database."""
        Base.metadata.create_all(self.engine)
        logger.info("Database schema created successfully.")
        
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
        
        # Merge members
        for member_data in members_list:
            member = Member(**member_data)
            session.merge(member)
            
        # Merge contributions
        for contrib_data in contributions_list:
            contrib = RawContribution(**contrib_data)
            session.merge(contrib)
            
        logger.info("Successfully ingested %d raw contribution rows.", len(contributions_list))
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
                    
                    oral_q = OralQuestion(
                        question_id=q_id,
                        meeting_id=raw.meeting_id,
                        contribution_id=raw.contribution_id,
                        question_number=q_num,
                    )
                    session.merge(oral_q)

            clean = CleanContribution(
                contribution_id=raw.contribution_id,
                contribution_verbatim_clean=cleaned_verbatim,
                contribution_translated_clean=cleaned_translated,
            )
            classified = ClassifiedContribution(
                contribution_id=raw.contribution_id,
                row_type=RowTypeEnum(row_type),
                classification_reason=reason,
            )
            
            session.merge(clean)
            session.merge(classified)
    
    
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
            
        # Create Speeches and SpeechParts
        new_speeches = []
        for speech_dict in speeches:
            speech = Speech(
                meeting_id=speech_dict['meeting_id'],
                assembly=speech_dict['assembly'],
                agenda_item_id=speech_dict['agenda_item_id'],
                speaker_id=speech_dict['speaker_id'],
                speaker_name=speech_dict['speaker_name'],
                speech_language=speech_dict['speech_language'],
                speech_text=' '.join(speech_dict['texts']),
                source_row_count=len(speech_dict['speech_parts']),
            )
            
            speech.parts = [
                SpeechPart(
                    contribution_id=part_dict['contribution_id'],
                    contribution_order_id=part_dict['contribution_order_id'],
                    contribution_time=part_dict['contribution_time'],
                    spoken_url=part_dict['spoken_url'],
                    translated_url=part_dict['translated_url'],
                    verbatim_text=part_dict['verbatim_text'],
                )
                for part_dict in speech_dict['speech_parts']
            ]
            new_speeches.append(speech)
            
        meeting.speeches = new_speeches
        logger.debug("Meeting %s: Grouped %d individual speeches.", meeting_id, len(new_speeches))
        return len(new_speeches)
    
    def build_members_dimension(self, session: Session, meeting_id: Optional[int] = None):
        """
        Phase 5a: Build/complete members dimension table.
        """
        logger.info("Phase 5a: Compiling members dimension constraints for meeting_id=%s", meeting_id)
        query = session.query(RawContribution).filter(
            RawContribution.member_id.isnot(None)
        )
        if meeting_id:
            query = query.filter(RawContribution.meeting_id == meeting_id)
        raw_rows = query.all()

        for raw in raw_rows:
            member = (
                session.query(Member)
                .filter_by(member_id=raw.member_id)
                .first()
            )

            if member:
                if not member.biography_english:
                    member.biography_english = raw.member_biog_english
                if not member.biography_welsh:
                    member.biography_welsh = raw.member_biog_welsh
                if not member.sort_code:
                    member.sort_code = raw.member_sortcode

            existing_title = (
                session.query(MemberJobTitle)
                .filter_by(
                    member_id=raw.member_id,
                    meeting_id=raw.meeting_id
                )
                .first()
            )

            if existing_title:
                existing_title.job_title_english = raw.member_job_title_english
                existing_title.job_title_welsh = raw.member_job_title_welsh
            else:
                session.add(
                    MemberJobTitle(
                        member_id=raw.member_id,
                        meeting_id=raw.meeting_id,
                        job_title_english=raw.member_job_title_english,
                        job_title_welsh=raw.member_job_title_welsh,
                    )
                )

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
        
        new_events = []
        for raw in procedural_rows:
            event_type = 'ruling' if raw.member_job_title_english and 'Llywydd' in raw.member_job_title_english else raw.contribution_type
            
            event = ProceduralEvent(
                meeting_id=meeting_id,
                agenda_item_id=raw.agenda_item_id,
                event_time=raw.contribution_time,
                event_type=event_type,
                speaker_name=raw.member_name_english or 'Unknown',
                raw_text=raw.contribution_verbatim or raw.contribution_translated,
                source_contribution_id=raw.contribution_id,
                senedd_tv_url=raw.contribution_spoken_seneddtv,
            )
            new_events.append(event)
            
        meeting.procedural_events = new_events
        return len(new_events)
    
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
    
    def run_incremental(self, data_dir: Path = None, keep_xml: bool = False, last_sync_date: Optional[datetime] = None):
        """
        Run incremental pipeline: fetch → parse → transform → upsert.
        """
        logger.info("Initializing scheduled incremental compilation workflow task.")
        
        if data_dir is None:
            data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        
        self.create_schema()
        fetcher = DataFetcher()
        
        # Get last sync date from database
        with self.SessionLocal() as session:
            if last_sync_date is None:
                last_sync_date = self.get_last_sync_date(session)
            logger.info("Scanning index registry for plenary meetings uploaded since target timestamp: %s", last_sync_date.date())
            
        # Detect and download new meetings
        new_meetings = fetcher.check_for_updates(last_sync_date)
        if not new_meetings:
            logger.info("No incremental updates found on remote Senedd feeds. Hibernating.")
            return
        
        logger.info("Discovered %d new plenary session transcripts requiring transformation processing.", len(new_meetings))
        
        # Process each meeting
        files_processed = 0
        for meeting in new_meetings:
            meeting_id = int(meeting.meeting_id)
            logger.info("Starting targeted extraction process loop on Meeting ID context scope: %s", meeting_id)
            
            # Download XML
            xml_path = fetcher.download_file(meeting, data_dir)
            if not xml_path or not xml_path.exists():
                logger.error("HTTP Fetch Error: Download payload validation failed for meeting resource: %s. Skipping target entry.", meeting_id)
                continue
            
            try:
                # Ingest XML inside its own transaction
                with self.SessionLocal() as session:
                    with session.begin():
                        self.ingest_xml(session, xml_path)
                
                # Transform the meeting atomically inside its own transaction
                with self.SessionLocal() as session:
                    with session.begin():
                        self.process_meetings(session, [meeting_id])
                
                files_processed += 1
                
            except Exception as e:
                logger.exception("Pipeline Engine Failure: Fatal exception encountered during parse phase on assembly meeting context %s: %s", meeting_id, e)
                if not keep_xml:
                    fetcher.cleanup_file(xml_path)
                continue
            
            # Cleanup XML if requested
            if not keep_xml:
                fetcher.cleanup_file(xml_path)
                logger.info("Cleaned up working manifest cache payload: %s", xml_path)
            
            logger.info("Meeting %s fully committed to operational dimensions.", meeting_id)
        # Record checkpoint
        if files_processed > 0:
            with self.SessionLocal() as session:
                with session.begin():
                    self.record_sync_checkpoint(session, files_processed, status="success")
        
        logger.info("Incremental synchronization job execution finalized. Total synchronized sets: %d", files_processed)