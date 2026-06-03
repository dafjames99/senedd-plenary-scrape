"""Main orchestrator for the Senedd XML-to-speech reconstruction pipeline."""
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db_schema import (
    Base, Meeting, Member, MemberJobTitle, RawContribution, CleanContribution, 
    ClassifiedContribution, Speech, SpeechPart, ProceduralEvent, 
    RowTypeEnum
)
from src.transformers import classify_contribution, clean_contribution_verbatim
from src.fetcher import DataFetcher
from src.upsert import DatabaseUpserter


class SeneddPipeline:
    """Orchestrator for XML parsing, cleaning, classification, and speech reconstruction."""
    
    def __init__(self, db_url: str):
        """Initialize pipeline with database connection."""
        self.engine = create_engine(db_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
    def create_schema(self):
        """Create all tables in the database."""
        Base.metadata.create_all(self.engine)
        print("✓ Schema created")
        
    def ingest_xml(self, xml_file: Path) -> int:
        """
        Phase 1: Parse XML and load into raw_contributions.
        Returns: number of rows ingested
        """
        print(f"\nPhase 1: Ingesting XML from {xml_file}")
        
        df = pd.read_xml(xml_file)
        session = self.SessionLocal()
        
        # Helper to convert numpy types to python natives
        def to_native(val):
            if pd.isna(val):
                return None
            if hasattr(val, 'item'):  # numpy type
                return val.item()
            return val
        
        def to_datetime(val):
            if pd.isna(val) or val is None:
                return None
            try:
                return pd.to_datetime(val)
            except:
                return None
        
        # Ensure Meeting exists
        meeting_id = to_native(df.iloc[0]['Meeting_ID'])
        meeting = session.query(Meeting).filter_by(meeting_id=meeting_id).first()
        
        if not meeting:
            meeting = Meeting(
                meeting_id=meeting_id,
                assembly=to_native(df.iloc[0]['Assembly']),
                meeting_date=to_datetime(df.iloc[0]['MeetingDate']),
            )
            session.add(meeting)
        
        # Ensure all Members exist
        members_df = df[df['Member_Id'].notna()][['Member_Id', 'Member_name_English', 
                                                     'Member_job_title_English', 'Member_Sortcode']].drop_duplicates()
        for _, member_row in members_df.iterrows():
            member_id = to_native(member_row['Member_Id'])
            if member_id is None:
                continue
            existing = session.query(Member).filter_by(member_id=member_id).first()
            if not existing:
                member = Member(
                    member_id=member_id,
                    name_english=to_native(member_row['Member_name_English']) or '',
                    job_title_english=to_native(member_row['Member_job_title_English']),
                    sort_code=to_native(member_row['Member_Sortcode']),
                )
                session.add(member)
        
        session.commit()
        
        # Ingest contributions
        for _, row in df.iterrows():
            contrib = RawContribution(
                contribution_id=to_native(row['Contribution_ID']),
                meeting_id=to_native(row['Meeting_ID']),
                assembly=to_native(row['Assembly']),
                meeting_date=to_datetime(row['MeetingDate']),
                contribution_order_id=to_native(row['Contribution_Order_ID']),
                contribution_language=to_native(row['contribution_language']),
                contribution_time=to_datetime(row['ContributionTime']),
                contribution_spoken_seneddtv=to_native(row['contribution_spoken_seneddTv']),
                contribution_translated_seneddtv=to_native(row['contribution_translated_seneddTv']),
                agenda_item_id=to_native(row['Agenda_Item_ID']),
                agenda_item_welsh=to_native(row['Agenda_item_welsh']),
                agenda_item_english=to_native(row['Agenda_item_english']),
                contribution_type=to_native(row['contribution_type']),
                attendee_id=to_native(row['Attendee_Id']) if pd.notna(row['Attendee_Id']) else None,
                member_id=to_native(row['Member_Id']) if pd.notna(row['Member_Id']) else None,
                member_name_english=to_native(row['Member_name_English']),
                member_biog_english=to_native(row['Member_biog_English']),
                member_biog_welsh=to_native(row['Member_biog_Welsh']),
                member_job_title_english=to_native(row['Member_job_title_English']),
                member_job_title_welsh=to_native(row['Member_job_title_Welsh']),
                member_sortcode=to_native(row['Member_Sortcode']),
                contribution_english=to_native(row['Contribution_English']),
                contribution_welsh=to_native(row['Contribution_Welsh']),
                contribution_verbatim=to_native(row['contribution_verbatim']),
                contribution_translated=to_native(row['contribution_translated']),
            )
            session.add(contrib)
        
        session.commit()
        count = session.query(RawContribution).count()
        session.close()
        print(f"✓ Ingested {count} raw contributions")
        return count
    
    def clean_text_fields(self) -> int:
        """
        Phase 2: Clean text fields and store in clean_contributions.
        Applies: HTML double-unescape, tag removal, whitespace normalization.
        Returns: number of rows cleaned
        """
        print("\nPhase 2: Cleaning text fields")
        session = self.SessionLocal()
        
        raw_contribs = session.query(RawContribution).all()
        
        for raw in raw_contribs:
            cleaned_verbatim = clean_contribution_verbatim(raw.contribution_verbatim)
            cleaned_translated = clean_contribution_verbatim(raw.contribution_translated)
            
            clean = CleanContribution(
                contribution_id=raw.contribution_id,
                contribution_verbatim_clean=cleaned_verbatim,
                contribution_translated_clean=cleaned_translated,
            )
            session.add(clean)
        
        session.commit()
        count = session.query(CleanContribution).count()
        session.close()
        print(f"✓ Cleaned {count} contributions")
        return count
    
    def classify_rows(self) -> dict:
        """
        Phase 3: Classify contributions as speech/procedural/noise.
        Returns: classification counts
        """
        print("\nPhase 3: Classifying rows")
        session = self.SessionLocal()
        
        raw_contribs = session.query(RawContribution).all()
        counts = {'speech': 0, 'procedural': 0, 'noise': 0}
        
        for raw in raw_contribs:
            row_dict = {
                'Member_Id': raw.member_id,
                'Member_job_title_English': raw.member_job_title_english,
                'contribution_type': raw.contribution_type,
                'contribution_verbatim': raw.contribution_verbatim,
                'contribution_translated': raw.contribution_translated,
            }
            
            row_type, reason = classify_contribution(row_dict)
            classified = ClassifiedContribution(
                contribution_id=raw.contribution_id,
                row_type=RowTypeEnum(row_type),
                classification_reason=reason,
            )
            session.add(classified)
            counts[row_type] += 1
        
        session.commit()
        session.close()
        print(f"✓ Classification: speech={counts['speech']}, procedural={counts['procedural']}, noise={counts['noise']}")
        return counts
    
    def reconstruct_speeches(self) -> int:
        """
        Phase 4: Reconstruct speeches by grouping rows.
        Speech boundary: speaker changes OR agenda changes.
        Returns: number of speeches created
        """
        print("\nPhase 4: Reconstructing speeches")
        session = self.SessionLocal()
        
        # Get all speech-classified rows, ordered by contribution_order_id
        speech_rows = session.query(
            RawContribution, CleanContribution
        ).join(
            ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id
        ).join(
            CleanContribution, RawContribution.contribution_id == CleanContribution.contribution_id
        ).filter(
            ClassifiedContribution.row_type == RowTypeEnum.SPEECH
        ).order_by(
            RawContribution.contribution_order_id
        ).all()
        
        speeches = []
        current_speech = None
        
        for raw, clean in speech_rows:
            # Check if this starts a new speech
            if current_speech is None or \
               current_speech['speaker_id'] != raw.member_id or \
               current_speech['agenda_item_id'] != raw.agenda_item_id:
                # Save previous speech if exists
                if current_speech is not None:
                    speeches.append(current_speech)
                
                # Start new speech
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
            
            # Add to current speech
            if clean.contribution_verbatim_clean:
                if clean.contribution_translated_clean:
                    current_speech['texts'].append(clean.contribution_translated_clean)
                else:
                    current_speech['texts'].append(clean.contribution_verbatim_clean)
            
            current_speech['speech_parts'].append({
                'contribution_id': raw.contribution_id,
                'contribution_order_id': raw.contribution_order_id,
                'contribution_time': raw.contribution_time,
                'spoken_url': raw.contribution_spoken_seneddtv,
                'translated_url': raw.contribution_translated_seneddtv,
                'verbatim_text': clean.contribution_translated_text or clean.contribution_verbatim_clean,
            })
            
        # Don't forget last speech
        if current_speech is not None:
            speeches.append(current_speech)
        
        # Persist speeches to database
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
            session.add(speech)
            session.flush()  # Ensure speech_id is generated
            
            # Add speech parts
            for part_dict in speech_dict['speech_parts']:
                part = SpeechPart(
                    speech_id=speech.speech_id,
                    contribution_id=part_dict['contribution_id'],
                    contribution_order_id=part_dict['contribution_order_id'],
                    contribution_time=part_dict['contribution_time'],
                    spoken_url=part_dict['spoken_url'],
                    translated_url=part_dict['translated_url'],
                    verbatim_text=part_dict['verbatim_text'],
                )
                session.add(part)
        
        session.commit()
        count = session.query(Speech).count()
        session.close()
        print(f"✓ Reconstructed {count} speeches from grouped contributions")
        return count
    def build_members_dimension(self):
        """
        Phase 5a: Build/complete members dimension table.
        """
        print("\nPhase 5a: Building members dimension")

        session = self.SessionLocal()

        raw_rows = session.query(RawContribution).filter(
            RawContribution.member_id.isnot(None)
        ).all()

        for raw in raw_rows:

            # --------------------
            # Populate member info
            # --------------------
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
                    member.sort_code = raw.member_sort_code

            # --------------------
            # Populate meeting role
            # --------------------
            existing_title = (
                session.query(MemberJobTitle)
                .filter_by(
                    member_id=raw.member_id,
                    meeting_id=raw.meeting_id
                )
                .first()
            )

            if not existing_title:
                session.add(
                    MemberJobTitle(
                        member_id=raw.member_id,
                        meeting_id=raw.meeting_id,
                        job_title_english=raw.member_job_title_english,
                        job_title_welsh=raw.member_job_title_welsh,
                    )
                )

        session.commit()

        member_count = session.query(Member).count()
        title_count = session.query(MemberJobTitle).count()

        session.close()

        print(f"✓ Members dimension complete: {member_count} unique members")
        print(f"✓ Member titles complete: {title_count} meeting-specific roles")
    
    def build_procedural_events(self) -> int:
        """
        Phase 5b: Extract procedural events.
        """
        print("\nPhase 5b: Building procedural events")
        session = self.SessionLocal()
        
        procedural_rows = session.query(RawContribution).join(
            ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id
        ).filter(
            ClassifiedContribution.row_type == RowTypeEnum.PROCEDURAL
        ).all()
        
        for raw in procedural_rows:
            event_type = 'ruling' if raw.member_job_title_english and 'Llywydd' in raw.member_job_title_english else raw.contribution_type
            
            event = ProceduralEvent(
                meeting_id=raw.meeting_id,
                agenda_item_id=raw.agenda_item_id,
                event_time=raw.contribution_time,
                event_type=event_type,
                speaker_name=raw.member_name_english or 'Unknown',
                raw_text=raw.contribution_verbatim or raw.contribution_translated,
                source_contribution_id=raw.contribution_id,
                senedd_tv_url=raw.contribution_spoken_seneddtv,
            )
            session.add(event)
        
        session.commit()
        count = session.query(ProceduralEvent).count()
        session.close()
        print(f"✓ Procedural events: {count} entries")
        return count
    
    def validate_pipeline(self) -> dict:
        """
        Phase 6: Validate pipeline output.
        Returns: validation report
        """
        print("\nPhase 6: Validating pipeline")
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
        
        print("\n" + "="*60)
        print("VALIDATION REPORT")
        print("="*60)
        print(f"Raw contributions ingested:        {report['raw_contributions']}")
        print(f"Cleaned contributions:             {report['clean_contributions']}")
        print(f"Classified contributions:          {report['classified_contributions']}")
        print(f"Reconstructed speeches:            {report['speeches']}")
        print(f"Speech parts (lineage):            {report['speech_parts']}")
        print(f"Unique members:                    {report['members']}")
        print(f"Procedural events:                 {report['procedural_events']}")
        print(f"Speeches with parts:               {report['speeches_with_parts']}")
        print(f"Missing traceability:              {report['missing_traceability']}")
        print(f"Empty speeches (data quality):     {report['empty_speeches']}")
        print("="*60)
        
        return report
    
    def run_full_pipeline(self, xml_file: Path):
        """Run all pipeline phases (fresh rebuild)."""
        print("\n" + "="*60)
        print("SENEDD XML → SPEECH RECONSTRUCTION PIPELINE (FULL REBUILD)")
        print("="*60)
        
        # Drop existing schema for fresh start
        Base.metadata.drop_all(self.engine)
        print("✓ Dropped existing schema")
        
        self.create_schema()
        self.ingest_xml(xml_file)
        self.clean_text_fields()
        self.classify_rows()
        self.reconstruct_speeches()
        self.build_members_dimension()
        self.build_procedural_events()
        self.validate_pipeline()
        
        print("\n✓ Pipeline complete!")
    
    def run_incremental(self, data_dir: Path = None, keep_xml: bool = False, last_sync_date: Optional[datetime] = None):
        """
        Run incremental pipeline: fetch → parse → transform → upsert.
        
        Args:
            data_dir: Directory to fetch/store XML files (defaults to ./data/)
            keep_xml: Whether to keep raw XML files after processing (default: delete)
            last_sync_date: Override last sync date for testing (uses DB checkpoint by default)
        """
        print("\n" + "="*60)
        print("SENEDD INCREMENTAL PIPELINE")
        print("="*60)
        
        # Setup
        if data_dir is None:
            data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        
        self.create_schema()
        session = self.SessionLocal()
        fetcher = DataFetcher(data_dir)
        upserter = DatabaseUpserter(session)
        
        # Get last sync date (or use override)
        if last_sync_date is None:
            last_sync_date = upserter.get_last_sync_date()
        print(f"Checking for meetings since: {last_sync_date.date()}")
        
        # Detect and download new meetings
        new_meetings = fetcher.check_for_updates(last_sync_date)
        if not new_meetings:
            print("No new meetings found.")
            session.close()
            return
        
        print(f"Found {len(new_meetings)} new meeting(s)")
        
        # Process each meeting
        files_processed = 0
        for meeting_info in new_meetings:
            meeting_id = meeting_info.get('id')
            xml_file = meeting_info.get('file')
            
            print(f"\n--- Processing Meeting {meeting_id} ---")
            
            # Download XML
            xml_path = fetcher.download_file(meeting_info)
            if not xml_path or not xml_path.exists():
                print(f"✗ Failed to download meeting {meeting_id}, skipping")
                continue
            
            # Parse XML
            try:
                df = pd.read_xml(xml_path)
            except Exception as e:
                print(f"✗ Failed to parse {xml_path}: {e}")
                if not keep_xml:
                    fetcher.cleanup_file(xml_path)
                continue
            
            # Upsert contributions (Phase 1)
            contrib_counts = upserter.upsert_contributions(df, meeting_id)
            print(f"Contributions: {contrib_counts['inserted']} new, {contrib_counts['updated']} updated")
            
            # Delete old classifications, speeches, procedural (for rebuild)
            upserter.delete_meeting_classifications(meeting_id)
            upserter.delete_meeting_speeches(meeting_id)
            upserter.delete_meeting_procedural(meeting_id)
            
            # Re-run pipeline phases on fresh contributions
            self._clean_meeting_contributions(session, meeting_id)
            self._classify_meeting_rows(session, meeting_id)
            self._reconstruct_meeting_speeches(session, meeting_id)
            self._build_meeting_procedural(session, meeting_id)
            
            files_processed += 1
            
            # Cleanup XML if requested
            if not keep_xml:
                fetcher.cleanup_file(xml_path)
                print(f"Cleaned up {xml_path}")
            
            print(f"✓ Meeting {meeting_id} processed")
        
        # Record checkpoint
        upserter.record_sync_checkpoint(files_processed, status="success")
        
        session.close()
        print(f"\n✓ Incremental pipeline complete ({files_processed} meetings)")
    
    def _clean_meeting_contributions(self, session, meeting_id: int):
        """Re-clean contributions for a specific meeting."""
        contribs = session.query(RawContribution).filter_by(meeting_id=meeting_id).all()
        
        for contrib in contribs:
            cleaned_text = clean_contribution_verbatim(contrib.contribution_verbatim or '')
            
            existing = session.query(CleanContribution).filter_by(
                contribution_id=contrib.contribution_id
            ).first()
            
            if existing:
                existing.cleaned_verbatim = cleaned_text
            else:
                clean_contrib = CleanContribution(
                    contribution_id=contrib.contribution_id,
                    cleaned_verbatim=cleaned_text
                )
                session.add(clean_contrib)
        
        session.commit()
    
    def _classify_meeting_rows(self, session, meeting_id: int):
        """Re-classify contributions for a specific meeting."""
        raw_contribs = session.query(RawContribution).filter_by(meeting_id=meeting_id).all()
        
        for raw in raw_contribs:
            row_type = classify_contribution(
                member_id=raw.member_id,
                contribution_type=raw.contribution_type,
                job_title=raw.member_job_title_english,
                text=raw.contribution_verbatim or ''
            )
            
            classified = ClassifiedContribution(
                contribution_id=raw.contribution_id,
                row_type=row_type
            )
            session.add(classified)
        
        session.commit()
    
    def _reconstruct_meeting_speeches(self, session, meeting_id: int):
        """Reconstruct speeches for a specific meeting."""
        # Get meeting for context
        meeting = session.query(Meeting).filter_by(meeting_id=meeting_id).first()
        if not meeting:
            return
        
        # Get all contributions for this meeting, classified and cleaned
        contributions = (
            session.query(
                RawContribution,
                CleanContribution,
                ClassifiedContribution
            )
            .filter(RawContribution.meeting_id == meeting_id)
            .join(CleanContribution, RawContribution.contribution_id == CleanContribution.contribution_id)
            .join(ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id)
            .filter(ClassifiedContribution.row_type == RowTypeEnum.SPEECH)
            .order_by(RawContribution.agenda_item_id, RawContribution.contribution_order_id)
            .all()
        )
        
        if not contributions:
            return
        
        # Group by speaker + agenda
        speeches_data = {}
        for raw, clean, classified in contributions:
            key = (raw.member_id, raw.agenda_item_id)
            
            if key not in speeches_data:
                speeches_data[key] = {
                    'member_id': raw.member_id,
                    'meeting_id': meeting_id,
                    'agenda_item_id': raw.agenda_item_id,
                    'agenda_item_english': raw.agenda_item_english,
                    'agenda_item_welsh': raw.agenda_item_welsh,
                    'contributions': [],
                    'text_parts': []
                }
            
            speeches_data[key]['contributions'].append(raw)
            speeches_data[key]['text_parts'].append(clean.cleaned_verbatim or '')
        
        # Create speeches and speech parts
        for (member_id, agenda_id), data in speeches_data.items():
            combined_text = ' '.join([t.strip() for t in data['text_parts'] if t.strip()])
            
            speech = Speech(
                meeting_id=data['meeting_id'],
                member_id=data['member_id'],
                agenda_item_id=data['agenda_item_id'],
                agenda_item_english=data['agenda_item_english'],
                agenda_item_welsh=data['agenda_item_welsh'],
                speech_text=combined_text or None,
                language_detected='EN' if combined_text else None
            )
            session.add(speech)
            session.flush()  # Get speech_id
            
            for contrib in data['contributions']:
                speech_part = SpeechPart(
                    speech_id=speech.speech_id,
                    contribution_id=contrib.contribution_id
                )
                session.add(speech_part)
        
        session.commit()
    
    def _build_meeting_procedural(self, session, meeting_id: int):
        """Build procedural events for a specific meeting."""
        procedurals = (
            session.query(RawContribution, ClassifiedContribution)
            .filter(RawContribution.meeting_id == meeting_id)
            .join(ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id)
            .filter(ClassifiedContribution.row_type == RowTypeEnum.PROCEDURAL)
            .all()
        )
        
        for raw, _ in procedurals:
            event_type = 'LLYWYDD' if raw.member_job_title_english and 'Llywydd' in raw.member_job_title_english else 'MOTION'
            
            proc_event = ProceduralEvent(
                meeting_id=meeting_id,
                event_type=event_type,
                raw_text=raw.contribution_verbatim or '',
                contribution_type=raw.contribution_type
            )
            session.add(proc_event)
        
        session.commit()
