import argparse
import time
from datetime import datetime, timedelta
from typing_extensions import final
import requests
from bs4 import BeautifulSoup
from pathlib import Path
import pandas as pd

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"

def generate_backfill_urls(start_date_str: str, end_date_str: str):
    """
    Iterates day-by-day between dates to extract direct XML links,
    completely bypassing the UI truncation cap.
    """
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    
    url = "https://record.senedd.wales/XMLExport"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    current_date = start_date
    all_discovered_links = []
    
    print(f"[*] Commencing single-day window harvest from {start_date_str} to {end_date_str}...")
    
    while current_date <= end_date:
        date_iso = current_date.strftime("%Y-%m-%d")
        date_next_iso = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Structure payload to match form inputs for a single day
        form_payload = {
            "FromDate": date_iso,
            "ToDate": date_iso,
            "Submission": "Search"
        }
        
        try:
            # Issue the POST request to get the data table for just this day
            final_url = f"{url}/?start={date_iso}&end={date_next_iso}"
            # print(f"[*] Querying for {final_url}...")
            response = requests.get(final_url, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table')
            
            # If a table exists, it means a plenary meeting was held on this day
            if table:
                # Find all table row cells containing download paths
                links_in_row = table.find_all('a', href=True)
                
                day_links = []
                for a in links_in_row:
                    href = a['href']
                    # Isolate direct transcript download pathways
                    if "/XMLExport/Download" in href:
                        full_link = f"https://record.senedd.wales{href}"
                        day_links.append(full_link)
                
                if day_links:
                    print(f"[+] Found {len(day_links)} links on {date_iso}")
                    all_discovered_links.extend(day_links)
                else:
                    print(f"[-] No transcript links found on {date_iso}")
        except Exception as e:
            print(f"[!] Network or parsing error encountered on {date_iso}: {e}")
            
        # Respectful trailing throttle to avoid hammering the Senedd web application gateway
        print("sleep")
        time.sleep(0.5)
        current_date += timedelta(days=1)
        
    print("\n" + "="*50)
    print(f"[#] Extraction Cycle Concluded. Harvested {len(all_discovered_links)} total links.")
    print("="*50)
    
    # Print the links list so you can copy/pipe it as a seed source
    for link in all_discovered_links:
        print(link)
        
    return all_discovered_links

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Backfill URL Harvester for Senedd Plenary Transcripts")
    parser.add_argument("--start", type=str, required=False, default="2025-01-01", help="Start date for backfill in YYYY-MM-DD format")
    parser.add_argument("--end", type=str, required=False, default="2025-06-01", help="End date for backfill in YYYY-MM-DD format")
    args = parser.parse_args()
    
    all_discovered_links = generate_backfill_urls(args.start, args.end)
    pd.DataFrame(all_discovered_links, columns=["URL"]).to_csv(DATA_DIR / "backfill_links.csv", index=False)
    # CURRENT FILE STATE: --start 2026-02-01 --end 2026-06-05
    
    