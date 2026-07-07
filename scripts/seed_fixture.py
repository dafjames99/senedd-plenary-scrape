"""Seed the database with the synthetic dev-fixture meeting.

Runs the *real* ingestion + transformation path over
``tests/fixtures/260616_Plenary_Bilingual.xml`` — a clearly-labelled synthetic
plenary (fixture members, agenda items suffixed "(dev fixture)") for
environments where the Senedd hosts are unreachable, so the web app and MCP
tools have something to render. On a normal network prefer
``scripts/backfill.py`` for real meetings; this fixture coexists harmlessly
(meeting_id 990001, member ids 99xx are out-of-band).

Usage:
    uv run python scripts/seed_fixture.py            # provision + ingest + transform
    uv run python scripts/seed_fixture.py --drop     # remove the fixture meeting
"""
import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import text

from senedd_data import setup_logging, settings
from senedd_data.acquisition import AcquisitionPipeline
from senedd_data.transformation import TransformationPipeline

FIXTURE_XML = ROOT_DIR / "tests" / "fixtures" / "260616_Plenary_Bilingual.xml"
FIXTURE_MEETING_ID = 990001


def seed():
    acquisition = AcquisitionPipeline(settings.database_url)
    acquisition.create_schema()

    with acquisition.SessionLocal() as session:
        with session.begin():
            acquisition.ingest_xml(session, FIXTURE_XML)

    transformation = TransformationPipeline(settings.database_url)
    transformation.transform_meetings([FIXTURE_MEETING_ID])

    with acquisition.SessionLocal() as session:
        speeches = session.execute(text(
            "SELECT COUNT(*) FROM speeches WHERE meeting_id = :m"
        ), {"m": FIXTURE_MEETING_ID}).scalar()
        events = session.execute(text(
            "SELECT COUNT(*) FROM procedural_events WHERE meeting_id = :m"
        ), {"m": FIXTURE_MEETING_ID}).scalar()
    print(f"Fixture meeting {FIXTURE_MEETING_ID}: {speeches} speeches, {events} procedural events.")


def drop():
    acquisition = AcquisitionPipeline(settings.database_url)
    with acquisition.SessionLocal() as session:
        with session.begin():
            # Cascade FKs purge contributions, speeches, parts, events, fidelity.
            session.execute(text("DELETE FROM meetings WHERE meeting_id = :m"),
                            {"m": FIXTURE_MEETING_ID})
            session.execute(text("DELETE FROM members WHERE member_id BETWEEN 9901 AND 9904"))
    print(f"Fixture meeting {FIXTURE_MEETING_ID} removed.")


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", action="store_true", help="remove the fixture meeting")
    args = parser.parse_args()
    drop() if args.drop else seed()
