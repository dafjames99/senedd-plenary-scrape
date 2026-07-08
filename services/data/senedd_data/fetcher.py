"""Fetch and detect new Senedd meeting XML files."""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Literal
import logging

logger = logging.getLogger(__name__)


class Meeting:
    """Represents a Senedd meeting and its downloadable artifacts.

    A single portal row exposes several XML exports — Bilingual/Welsh/English
    transcript, Votes, QNR — as separate links. ``artifacts`` maps the portal
    ``xmlDownloadType`` to its absolute URL. ``download_url`` is retained as the
    transcript-type URL for the legacy single-type callers (incremental sync and
    the artifact-watch sweep), which build a Meeting per type.
    """

    def __init__(
        self,
        meeting_id: str,
        meeting_date: datetime,
        meeting_type: str,
        download_url: str,
        artifacts: Optional[Dict[str, str]] = None,
    ):
        self.meeting_id = meeting_id
        self.meeting_date = meeting_date
        self.meeting_type = meeting_type
        self.download_url = download_url
        self.artifacts = artifacts or {}

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

    def parse_artifacts_from_html(self, html_content: str) -> List[Meeting]:
        """Parse every row into one Meeting carrying *all* its artifact links.

        Unlike :meth:`parse_meetings_from_html` (one ``transcript_type`` per call,
        used by incremental sync), this captures every ``xmlDownloadType`` link in
        a row in a single pass — so a day's page is fetched once and yields the
        transcript together with Votes and QNR. Built for the backfill harvester.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        meetings: List[Meeting] = []

        table = soup.find('table')
        if not table:
            logger.debug("No data table found in provided HTML context.")
            return []

        for row in table.find_all('tr')[1:]:  # Skip column headers
            cols = row.find_all('td')
            if not cols or len(cols) < 3:
                continue

            artifacts: Dict[str, str] = {}
            meeting_id: Optional[str] = None
            for a in row.find_all('a'):
                href = a.get('href', '') or ''
                if 'xmlDownloadType=' not in href:
                    continue
                artifact_type = href.split('xmlDownloadType=')[-1].split('&')[0]
                url = "https://record.senedd.wales" + href if href.startswith('/') else href
                artifacts[artifact_type] = url
                if meeting_id is None and 'meetingID=' in url:
                    meeting_id = url.split('meetingID=')[-1].split('&')[0]

            if not artifacts or not meeting_id:
                continue

            try:
                meeting_date = datetime.strptime(cols[0].text.strip(), "%d/%m/%Y %H:%M")
            except ValueError as e:
                logger.debug(f"Skipping row with unparseable date: {e}")
                continue

            # The transcript URL backs the legacy single-url field; prefer the
            # bilingual export, falling back to English then anything available.
            transcript_url = (
                artifacts.get("BilingualTranscript")
                or artifacts.get("EnglishTranscript")
                or next(iter(artifacts.values()))
            )
            meetings.append(Meeting(
                meeting_id=meeting_id,
                meeting_date=meeting_date,
                meeting_type=cols[1].text.strip(),
                download_url=transcript_url,
                artifacts=artifacts,
            ))

        return meetings

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

    
    # Map a portal transcript type to the filename suffix used on disk.
    _SUFFIX_BY_TYPE = {
        "BilingualTranscript": "Bilingual",
        "WelshTranscript": "Welsh",
        "EnglishTranscript": "English",
        "Votes": "Votes",
        "QNR": "QNR",
    }

    def download_file(
        self,
        meeting: Meeting,
        save_dir: Path,
        transcript_type: str = "BilingualTranscript",
    ) -> Optional[Path]:
        """
        Download XML file for a meeting.

        Args:
            meeting: Meeting object with download URL
            save_dir: Directory to save the file
            transcript_type: Artifact type being fetched; determines the filename
                suffix so Votes/QNR don't collide with (or overwrite) the
                transcript's ``_Bilingual.xml``.

        Returns:
            Path to saved file, or None if download failed
        """
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        suffix = self._SUFFIX_BY_TYPE.get(transcript_type, "Bilingual")
        filename = f"{meeting.meeting_date.strftime('%Y%m%d')}_{meeting.meeting_type}_{suffix}.xml"
        file_path = save_dir / filename

        # Resolve the URL for the requested artifact. Multi-artifact meetings
        # (from parse_artifacts_from_html) carry a per-type map; single-type
        # meetings (incremental sync, watch sweep) fall back to download_url.
        download_url = (meeting.artifacts or {}).get(transcript_type) or meeting.download_url
        if not download_url:
            logger.error(f"No {transcript_type} artifact URL for {meeting}; skipping download.")
            return None

        # Skip if already downloaded
        if file_path.exists():
            logger.info(f"File already exists: {file_path}")
            return file_path

        try:
            logger.info(f"Downloading {meeting} ({transcript_type}) to {file_path}")
            response = requests.get(download_url, timeout=self.timeout)
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
