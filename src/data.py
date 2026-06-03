import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict



def check_for_updates(last_sync_date: datetime) -> List[Dict[str, str]]:
    url = "https://record.senedd.wales/XMLExport"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Locate the central data table on the XMLExport view
    table = soup.find('table') 
    new_meetings = []
    
    for row in table.find_all('tr')[1:]: # Skip header row
        cols = row.find_all('td')
        if not cols: continue
        
        # Parse date string out of column index 0 (e.g., '12/05/2026 14:00')
        meeting_date = datetime.strptime(cols[0].text.strip(), "%d/%m/%Y %H:%M")
        
        if meeting_date > last_sync_date:
            # Extract the raw download anchor tags for standard transcript processing
            download_link = cols[2].find('a')['href'] # The "Bilingual" XML link
            meeting_id = download_link.split('meetingID=')[-1].split('&')[0]
            
            new_meetings.append({
                'id': meeting_id,
                'date': meeting_date,
                'type': cols[1].text.strip()
            })
            
    return new_meetings


