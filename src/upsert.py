"""Smart upsert logic for incremental database updates."""
from datetime import datetime
from typing import Dict, Optional
import pandas as pd
from sqlalchemy.orm import Session
import logging

from src.db_schema import (
    RawContribution, Member, Speech, SpeechPart, ProceduralEvent,
    ClassifiedContribution, RowTypeEnum
)

logger = logging.getLogger(__name__)


def to_native(val):
    """Convert numpy types to Python natives."""
    if pd.isna(val):
        return None
    if hasattr(val, 'item'):
        return val.item()
    return val


def to_datetime(val):
    """Convert to datetime, returning None on failure."""
    if pd.isna(val) or val is None:
        return None
    try:
        return pd.to_datetime(val)
    except:
        return None


class DatabaseUpserter:
    """Handle smart insert/update logic for incremental syncs."""
    
    def __init__(self, session: Session):
        """Initialize upserter with database session."""
        self.session = session
    
    def get_last_sync_date(self) -> datetime:
        """
        Get the date of the most recent processed meeting.
        
        Returns:
            datetime of last meeting, or 2000-01-01 if none exist
        """
        from src.db_schema import SyncCheckpoint
        
        latest = self.session.query(SyncCheckpoint)\
            .order_by(SyncCheckpoint.created_at.desc())\
            .first()
        
        if latest and latest.last_sync_date:
            logger.info(f"Last sync: {latest.last_sync_date.date()} ({latest.file_count} files)")
            return latest.last_sync_date
        else:
            logger.info("No previous sync found, starting from 2000-01-01")
            return datetime(2000, 1, 1)
    
    def record_sync_checkpoint(self, file_count: int, status: str = "success", notes: str = "") -> None:
        """
        Record sync checkpoint for resumability.
        
        Args:
            file_count: Number of files processed
            status: 'success', 'partial', or 'error'
            notes: Optional notes about the sync
        """
        from src.db_schema import SyncCheckpoint
        
        # Get most recent meeting processed
        latest_meeting = self.session.query(RawContribution.meeting_date)\
            .order_by(RawContribution.meeting_date.desc())\
            .first()
        
        checkpoint = SyncCheckpoint(
            last_sync_date=datetime.utcnow(),
            last_meeting_id=latest_meeting[0] if latest_meeting else None,
            file_count=file_count,
            status=status,
            notes=notes
        )
        self.session.add(checkpoint)
        self.session.commit()
        logger.info(f"Recorded checkpoint: {file_count} files, status={status}")
    
    def upsert_member(self, row: Dict) -> Optional[Member]:
        """
        Upsert a single member record.
        
        Args:
            row: DataFrame row with member data
        
        Returns:
            Member object (inserted or updated)
        """
        member_id = to_native(row.get('Member_Id'))
        
        if member_id is None:
            return None
        
        existing = self.session.query(Member).filter_by(member_id=member_id).first()
        
        if existing:
            # Update fields that might have changed
            existing.name_english = to_native(row.get('Member_name_English')) or existing.name_english
            existing.job_title_english = to_native(row.get('Member_job_title_English')) or existing.job_title_english
            existing.job_title_welsh = to_native(row.get('Member_job_title_Welsh')) or existing.job_title_welsh
            existing.biography_english = to_native(row.get('Member_biog_English')) or existing.biography_english
            existing.biography_welsh = to_native(row.get('Member_biog_Welsh')) or existing.biography_welsh
            existing.sort_code = to_native(row.get('Member_Sortcode')) or existing.sort_code
            return existing
        else:
            member = Member(
                member_id=member_id,
                name_english=to_native(row.get('Member_name_English')) or '',
                job_title_english=to_native(row.get('Member_job_title_English')),
                job_title_welsh=to_native(row.get('Member_job_title_Welsh')),
                biography_english=to_native(row.get('Member_biog_English')),
                biography_welsh=to_native(row.get('Member_biog_Welsh')),
                sort_code=to_native(row.get('Member_Sortcode')),
            )
            self.session.add(member)
            return member
    
    def upsert_contributions(self, df: pd.DataFrame, meeting_id: int) -> Dict[str, int]:
        """
        Upsert contribution rows (append-only strategy).
        
        Args:
            df: DataFrame with contribution data
            meeting_id: Meeting ID for context
        
        Returns:
            {inserted: N, updated: M, skipped: K}
        """
        counts = {'inserted': 0, 'updated': 0, 'skipped': 0}
        
        logger.info(f"Upserting contributions for meeting {meeting_id}")
        
        # Ensure members exist first
        for _, row in df.iterrows():
            if pd.notna(row.get('Member_Id')):
                self.upsert_member(row)
        
        # Upsert contributions
        for _, row in df.iterrows():
            contrib_id = to_native(row['Contribution_ID'])
            
            existing = self.session.query(RawContribution).filter_by(
                contribution_id=contrib_id
            ).first()
            
            if existing:
                # Update: metadata might have changed
                existing.contribution_verbatim = to_native(row.get('contribution_verbatim'))
                existing.contribution_translated = to_native(row.get('contribution_translated'))
                existing.member_job_title_english = to_native(row.get('Member_job_title_English'))
                counts['updated'] += 1
            else:
                # Insert: new contribution
                new_contrib = RawContribution(
                    contribution_id=contrib_id,
                    meeting_id=to_native(row['Meeting_ID']),
                    assembly=to_native(row.get('Assembly')),
                    meeting_date=to_datetime(row.get('MeetingDate')),
                    contribution_order_id=to_native(row.get('Contribution_Order_ID')),
                    contribution_language=to_native(row.get('contribution_language')),
                    contribution_time=to_datetime(row.get('ContributionTime')),
                    contribution_spoken_seneddtv=to_native(row.get('contribution_spoken_seneddTv')),
                    contribution_translated_seneddtv=to_native(row.get('contribution_translated_seneddTv')),
                    agenda_item_id=to_native(row.get('Agenda_Item_ID')),
                    agenda_item_welsh=to_native(row.get('Agenda_item_welsh')),
                    agenda_item_english=to_native(row.get('Agenda_item_english')),
                    contribution_type=to_native(row.get('contribution_type')),
                    attendee_id=to_native(row.get('Attendee_Id')) if pd.notna(row.get('Attendee_Id')) else None,
                    member_id=to_native(row.get('Member_Id')) if pd.notna(row.get('Member_Id')) else None,
                    member_name_english=to_native(row.get('Member_name_English')),
                    member_biog_english=to_native(row.get('Member_biog_English')),
                    member_biog_welsh=to_native(row.get('Member_biog_Welsh')),
                    member_job_title_english=to_native(row.get('Member_job_title_English')),
                    member_job_title_welsh=to_native(row.get('Member_job_title_Welsh')),
                    member_sortcode=to_native(row.get('Member_Sortcode')),
                    contribution_english=to_native(row.get('Contribution_English')),
                    contribution_welsh=to_native(row.get('Contribution_Welsh')),
                    contribution_verbatim=to_native(row.get('contribution_verbatim')),
                    contribution_translated=to_native(row.get('contribution_translated')),
                )
                self.session.add(new_contrib)
                counts['inserted'] += 1
        
        self.session.commit()
        logger.info(f"Contributions: {counts['inserted']} inserted, {counts['updated']} updated")
        return counts
    
    def delete_meeting_speeches(self, meeting_id: int) -> int:
        """
        Delete all speeches for a meeting (for rebuild).
        
        Args:
            meeting_id: Meeting to delete speeches for
        
        Returns:
            Number of speeches deleted
        """
        # Get speech IDs for this meeting
        speech_ids = [s[0] for s in self.session.query(Speech.speech_id)\
            .filter_by(meeting_id=meeting_id).all()]
        
        # Delete speech_parts (cascading)
        deleted_parts = self.session.query(SpeechPart)\
            .filter(SpeechPart.speech_id.in_(speech_ids))\
            .delete()
        
        # Delete speeches
        deleted_speeches = self.session.query(Speech)\
            .filter_by(meeting_id=meeting_id)\
            .delete()
        
        self.session.commit()
        
        logger.info(f"Deleted {deleted_speeches} speeches and {deleted_parts} speech_parts for meeting {meeting_id}")
        return deleted_speeches
    
    def delete_meeting_procedural(self, meeting_id: int) -> int:
        """
        Delete all procedural events for a meeting (for rebuild).
        
        Args:
            meeting_id: Meeting to delete events for
        
        Returns:
            Number of events deleted
        """
        deleted = self.session.query(ProceduralEvent)\
            .filter_by(meeting_id=meeting_id)\
            .delete()
        
        self.session.commit()
        
        logger.info(f"Deleted {deleted} procedural events for meeting {meeting_id}")
        return deleted
    
    def delete_meeting_classifications(self, meeting_id: int) -> int:
        """
        Delete all classifications for a meeting's contributions.
        
        Args:
            meeting_id: Meeting to delete classifications for
        
        Returns:
            Number of classifications deleted
        """
        # Find contribution IDs for this meeting
        contrib_ids = [c[0] for c in self.session.query(RawContribution.contribution_id)\
            .filter_by(meeting_id=meeting_id).all()]
        
        # Delete classifications
        deleted = self.session.query(ClassifiedContribution)\
            .filter(ClassifiedContribution.contribution_id.in_(contrib_ids))\
            .delete()
        
        self.session.commit()
        
        logger.info(f"Deleted {deleted} classifications for meeting {meeting_id}")
        return deleted
