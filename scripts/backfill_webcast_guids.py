"""Backfill ``meetings.webcast_guid`` for existing rows (one-off, network).

New meetings get their SeneddTV webcast GUID at ingest (``acquisition.ingest_xml``);
this populates the column for meetings ingested before that wiring existed.
Idempotent: skips meetings that already have a GUID unless ``--force``. Commits
per meeting so an interrupted run resumes cleanly.

    python scripts/backfill_webcast_guids.py            # only NULL rows
    python scripts/backfill_webcast_guids.py --force    # re-resolve everything
    python scripts/backfill_webcast_guids.py --sleep 1  # slower (gentler on host)
"""
import argparse
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from senedd_data import setup_logging
from senedd_data.db_schema import Meeting
from senedd_data.session import get_session
from senedd_data.settings import settings
from senedd_data.webcast import resolve_webcast_guid

logger = logging.getLogger("webcast_backfill")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="Re-resolve even if a GUID is already set."
    )
    parser.add_argument(
        "--sleep", type=float, default=0.5, help="Seconds between requests (rate limit)."
    )
    args = parser.parse_args()
    setup_logging()

    with get_session(settings.database_url) as session:
        query = session.query(Meeting)
        if not args.force:
            query = query.filter(Meeting.webcast_guid.is_(None))
        meetings = query.order_by(Meeting.meeting_id.desc()).all()
        logger.info("Resolving webcast GUID for %d meeting(s).", len(meetings))

        resolved = 0
        for meeting in meetings:
            guid = resolve_webcast_guid(meeting.meeting_id)
            if guid:
                meeting.webcast_guid = guid
                resolved += 1
                logger.info("meeting %s -> %s", meeting.meeting_id, guid)
            else:
                logger.warning("meeting %s -> unresolved", meeting.meeting_id)
            session.commit()
            time.sleep(args.sleep)

    logger.info(
        "Backfill complete: %d resolved, %d unresolved.",
        resolved,
        len(meetings) - resolved,
    )


if __name__ == "__main__":
    main()
