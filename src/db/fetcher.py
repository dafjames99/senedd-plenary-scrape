"""Fetch and detect new Senedd meeting XML files."""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Literal
import logging

logger = logging.getLogger(__name__)


class Meeting:
    """Represents a Senedd meeting metadata."""
    
    def __init__(self, meeting_id: str, meeting_date: datetime, meeting_type: str, download_url: str):
        self.meeting_id = meeting_id
        self.meeting_date = meeting_date
        self.meeting_type = meeting_type
        self.download_url = download_url
    
    def __repr__(self):
        return f"Meeting({self.meeting_id}, {self.meeting_date.date()}, {self.meeting_type})"


class DataFetcher:
    """Detect new Senedd meetings and download XML files."""
    
    BASE_URL = "https://record.senedd.wales/XMLExport"
    
    def __init__(self, timeout: int = 30):
        """Initialize fetcher with HTTP timeout."""
        self.timeout = timeout
        self.headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        
        
    def get_html_page(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> str:
        """
        1. Utility to retrieve raw HTML text from the export portal.
        Supports optional date boundaries natively injected into the URL structure.
        """
        params = {}
        if start_date:
            params['start'] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params['end'] = end_date.strftime("%Y-%m-%d")
        url = f"{self.BASE_URL}?{ '&'.join([f'{k}={v}' for k, v in params.items()]) }"
        try:
            logger.debug(f"Fetching export matrix page payload. Target filters: {params}")
            # Note: For wider spans, the portal's UI limitation cuts off tables.
            # For backfilling single days, call this with start and end set to the same day.
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"Network error accessing export gateway: {e}")
            raise
        
        
    def parse_meetings_from_html(
        self, 
        html_content: str, 
        transcript_type: Literal["BilingualTranscript", "WelshTranscript", "EnglishTranscript", "Votes", "QNR"] = "BilingualTranscript"
    ) -> List[Meeting]:
        """
        2. Utility to parse all rows in a given HTML context into unique Meeting objects.
        Filters rows based on the requested transcript_type parameter.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        parsed_meetings = []
        
        table = soup.find('table')
        if not table:
            logger.debug("No data table found in provided HTML context.")
            return []
            
        target_filter = f"xmlDownloadType={transcript_type}"
            
        for row in table.find_all('tr')[1:]:  # Skip column headers
            cols = row.find_all('td')
            if not cols or len(cols) < 3:
                continue
            
            try:
                links = row.find_all('a')
                if not links:
                    continue

                chosen_link = None
                for a in links:
                    if target_filter in a.get('href', None):
                        chosen_link = a
                        break
                
                if not chosen_link:
                    continue
                    
                download_url = chosen_link.get('href', '')
                if download_url.startswith('/'):
                    download_url = "https://record.senedd.wales" + download_url
                    
                # Parse distinct operational metrics
                date_str = cols[0].text.strip()
                meeting_date = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
                meeting_type = cols[1].text.strip()
                meeting_id = download_url.split('meetingID=')[-1].split('&')[0]
                
                if meeting_id:
                    parsed_meetings.append(Meeting(
                        meeting_id=meeting_id,
                        meeting_date=meeting_date,
                        meeting_type=meeting_type,
                        download_url=download_url
                    ))
                    
            except (ValueError, IndexError) as e:
                logger.debug(f"Skipping malformed or incomplete row node: {e}")
                continue
                
        return parsed_meetings

    def is_meeting_new(self, meeting: Meeting, last_sync_date: Optional[datetime]) -> bool:
        """
        3. Utility to check if a meeting is newer than the last recorded sync checkpoint.
        """
        if not last_sync_date:
            return True
        return meeting.meeting_date > last_sync_date

    def check_for_updates(
        self, 
        last_sync_date: Optional[datetime] = None,
        transcript_type: Literal["BilingualTranscript", "WelshTranscript", "EnglishTranscript", "VotesTranscript", "QNRTranscript"] = "BilingualTranscript"
    ) -> List[Meeting]:
        """
        Main interface method for standard incremental execution paths.
        Passes the requested transcript type through to the parser seamlessly.
        """
        logger.info(f"Checking for timeline updates ({transcript_type}) since: {last_sync_date}")
        
        # Pull down default recent context
        html = self.get_html_page()
        all_meetings = self.parse_meetings_from_html(html, transcript_type=transcript_type)
        
        # Filter down dynamically using the evaluation utility
        new_meetings = [m for m in all_meetings if self.is_meeting_new(m, last_sync_date)]
        
        # Sort chronologically (oldest to newest)
        new_meetings.sort(key=lambda m: m.meeting_date)
        logger.info(f"Identified {len(new_meetings)} new operational meetings to process.")
        return new_meetings

    
    def download_file(self, meeting: Meeting, save_dir: Path) -> Optional[Path]:
        """
        Download XML file for a meeting.
        
        Args:
            meeting: Meeting object with download URL
            save_dir: Directory to save the file
        
        Returns:
            Path to saved file, or None if download failed
        """
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{meeting.meeting_date.strftime('%Y%m%d')}_{meeting.meeting_type}_Bilingual.xml"
        file_path = save_dir / filename
        
        # Skip if already downloaded
        if file_path.exists():
            logger.info(f"File already exists: {file_path}")
            return file_path
        
        try:
            logger.info(f"Downloading {meeting} to {file_path}")
            response = requests.get(meeting.download_url, timeout=self.timeout)
            response.raise_for_status()
            
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Downloaded successfully: {file_path} ({len(response.content)} bytes)")
            return file_path
        
        except requests.RequestException as e:
            logger.error(f"Failed to download {meeting}: {e}")
            return None
    
    def cleanup_file(self, file_path: Path) -> bool:
        """
        Delete raw XML file after processing.
        
        Args:
            file_path: Path to file to delete
        
        Returns:
            True if deleted, False if error
        """
        try:
            file_path = Path(file_path)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Deleted: {file_path}")
                return True
            else:
                logger.warning(f"File not found for cleanup: {file_path}")
                return False
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")
            return False
