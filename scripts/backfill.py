"""Unified script to harvest, cache, and ingest historical Senedd transcript references.

A single portal row carries every artifact for a meeting (Bilingual/Welsh/English
transcript, Votes, QNR), so the harvester fetches each day once via
``parse_artifacts_from_html`` and captures all artifacts together. Ingestion then
routes the transcript, Votes and QNR each to their own ingest path.
"""
import argparse
import json
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

# Automatically resolve root path boundaries for module resolution
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import setup_logging, settings
from src.db.acquisition import AcquisitionPipeline
from src.db.fetcher import DataFetcher, Meeting
from src.db.transformation import TransformationPipeline

logger = logging.getLogger(__name__)

BACKFILL_DIR = ROOT_DIR / "data" / "backfill_links"
BACKFILL_DIR.mkdir(parents=True, exist_ok=True)


def harvest_backfill_meetings(start_date_str: str, end_date_str: str) -> list[Meeting]:
    """STAGE 1: Safely step day-by-day and return meetings with all their artifacts.

    Each day's page is fetched once; every artifact link in a row (transcript,
    Votes, QNR) is captured together. Meetings are de-duplicated by id across
    day-boundary overlaps.
    """
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

    fetcher = DataFetcher()
    current_date = start_date
    by_id: dict[str, Meeting] = {}

    logger.info(f"[*] Commencing single-day harvest window from {start_date_str} to {end_date_str}...")

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        try:
            # Shift end_date to current + 1 day to encapsulate the daily threshold perfectly
            html = fetcher.get_html_page(start_date=current_date, end_date=current_date + timedelta(days=1))
            meetings_on_day = fetcher.parse_artifacts_from_html(html)

            if meetings_on_day:
                logger.info(f"[+] Found {len(meetings_on_day)} meeting(s) on {date_str}")
                for m in meetings_on_day:
                    by_id.setdefault(m.meeting_id, m)
            else:
                logger.debug(f"[-] No meetings found on {date_str}")

        except Exception as e:
            logger.error(f"[!] Processing exception hit on checkpoint window {date_str}: {e}")

        time.sleep(0.5)
        current_date += timedelta(days=1)

    all_meetings = list(by_id.values())
    logger.info(f"[#] Harvest cycle completed. Discovered {len(all_meetings)} unique meetings.")
    return all_meetings


def save_to_csv(meetings: list[Meeting], csv_path: Path):
    """Utility to turn meeting memory models into a persistent data checkpoint file."""
    data = [
        {
            "meeting_id": m.meeting_id,
            "meeting_date": m.meeting_date,
            "meeting_type": m.meeting_type,
            "download_url": m.download_url,
            "artifacts": json.dumps(m.artifacts),  # {xmlDownloadType: url} for all artifacts
        }
        for m in meetings
    ]
    df = pd.DataFrame(data)
    df.to_csv(csv_path, index=False)
    logger.info(f"[✓] Backfill catalog cache saved successfully -> {csv_path}")


def load_from_csv(csv_path: Path) -> list[Meeting]:
    """Utility to reconstitute structural Meeting instances back out of a CSV matrix file."""
    if not csv_path.exists():
        logger.error(f"[!] Target file reference missing: {csv_path}")
        return []
        
    df = pd.read_csv(csv_path)
    meetings = []
    for _, row in df.iterrows():
        # Handle parsed timestamps safely out of string representations
        m_date = pd.to_datetime(row["meeting_date"])
        raw_artifacts = row.get("artifacts")
        artifacts = (
            json.loads(raw_artifacts)
            if isinstance(raw_artifacts, str) and raw_artifacts
            else {}
        )
        meetings.append(Meeting(
            meeting_id=str(row["meeting_id"]),
            meeting_date=m_date,
            meeting_type=str(row["meeting_type"]),
            download_url=str(row["download_url"]),
            artifacts=artifacts,
        ))
    return meetings


def ingest_meetings_to_db(meetings: list[Meeting]) -> bool:
    """STAGE 2: Process direct XML compilation down through the database pipeline layers."""
    if not meetings:
        logger.warning("[!] Ingestion task received an empty list of meetings. Halting execution.")
        return False
        
    logger.info(f"[*] Initializing pipeline database sync for {len(meetings)} meetings...")
    try:
        acquisition = AcquisitionPipeline(settings.database_url)
        transformation = TransformationPipeline(settings.database_url)
        # Raw ingest (transcript + Votes/QNR), then rebuild derived tables for the
        # meetings that were ingested.
        ingested_ids = acquisition.acquire_meetings(meetings, keep_xml=False)
        if ingested_ids:
            transformation.transform_meetings(ingested_ids)
        return True
    except Exception as e:
        logger.error(f"[!] Fatal pipeline break encountered during backfill migration: {e}")
        return False


if __name__ == "__main__":
    setup_logging()
    
    parser = argparse.ArgumentParser(description="Unified Senedd Pipeline Backfill Management Utility")
    parser.add_argument("--start", type=str, required=True, help="Start date in YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=False, default=datetime.now().strftime("%Y-%m-%d"), help="End date in YYYY-MM-DD. Defaults to today.")
    parser.add_argument(
        "--action",
        type=str,
        required=False,
        default="all",
        choices=["harvest", "ingest", "all"],
        help="Execution target: 'harvest' maps web to CSV, 'ingest' saves CSV to DB, 'all' runs sequential end-to-end processing."
    )
    parser.add_argument(
        "--cleanup_csv",
        action="store_true",
        help="If set, will delete the intermediate CSV file after successful end-to-end processing."
    )
    args = parser.parse_args()

    # Pre-calculate predictable file storage paths. One catalog per window now
    # covers all artifact types (transcript + Votes + QNR).
    target_csv = BACKFILL_DIR / f"backfill_{args.start}_to_{args.end}.csv"
    
    # Execution Routing Matrix
    if args.action == "harvest":
        # Only run web extraction and save file
        discovered = harvest_backfill_meetings(args.start, args.end)
        if discovered:
            save_to_csv(discovered, target_csv)

    elif args.action == "ingest":
        # Only read existing file and parse database changes
        logger.info(f"[*] Loading targets from static cache: {target_csv.name}")
        staged_meetings = load_from_csv(target_csv)
        success = ingest_meetings_to_db(staged_meetings)
        if success and args.cleanup_csv:
                target_csv.unlink()

    elif args.action == "all":
        # Run end-to-end: Scan -> Cache -> Process Database Transactions
        discovered = harvest_backfill_meetings(args.start, args.end)
        if discovered:
            save_to_csv(discovered, target_csv)
            success = ingest_meetings_to_db(discovered)
            if success and args.cleanup_csv:
                target_csv.unlink()
        else:
            logger.info("[!] No operational targets detected inside window range constraints.")