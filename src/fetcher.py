"""Fetch and detect new Senedd meeting XML files."""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
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
    
    def check_for_updates(self, last_sync_date: Optional[datetime] = None) -> List[Meeting]:
        """
        Check for new meetings after a given date.
        
        Args:
            last_sync_date: Only return meetings after this date.
                          If None, returns all meetings.
        
        Returns:
            List of Meeting objects for new/updated meetings.
        """
        if last_sync_date is None:
            last_sync_date = datetime(2000, 1, 1)
        
        logger.info(f"Checking for updates since {last_sync_date.date()}")
        
        try:
            response = requests.get(self.BASE_URL, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch XMLExport page: {e}")
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        new_meetings = []
        
        try:
            table = soup.find('table')
            if not table:
                logger.warning("No table found on XMLExport page")
                return []
            
            for row in table.find_all('tr')[1:]:  # Skip header
                cols = row.find_all('td')
                if not cols or len(cols) < 3:
                    continue
                
                try:
                    # Parse meeting date from column 0
                    date_str = cols[0].text.strip()
                    meeting_date = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
                    
                    # Only include meetings after last_sync_date
                    if meeting_date <= last_sync_date:
                        continue
                    
                    # Extract meeting type from column 1
                    meeting_type = cols[1].text.strip()
                    
                    # Extract download URL from column 2 (bilingual link)
                    link = cols[2].find('a')
                    if not link:
                        continue
                    
                    download_url = link.get('href', '')
                    if not download_url:
                        continue
                    if download_url.startswith('/'):
                        download_url = "https://record.senedd.wales" + download_url
                    
                    # Parse meeting ID from URL
                    meeting_id = download_url.split('meetingID=')[-1].split('&')[0]
                    
                    if meeting_id:
                        meeting = Meeting(
                            meeting_id=meeting_id,
                            meeting_date=meeting_date,
                            meeting_type=meeting_type,
                            download_url=download_url
                        )
                        new_meetings.append(meeting)
                        logger.debug(f"Found new meeting: {meeting}")
                
                except (ValueError, IndexError) as e:
                    logger.debug(f"Failed to parse meeting row: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Error parsing XMLExport page: {e}")
            return []
        
        logger.info(f"Found {len(new_meetings)} new meetings")
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
        
        # Generate filename: YYYYMMDD_MeetingID_Bilingual.xml
        filename = f"{meeting.meeting_date.strftime('%Y%m%d')}_{meeting.meeting_id}_Bilingual.xml"
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
