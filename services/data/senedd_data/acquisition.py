"""Raw data-acquisition stage (network + XML → source-of-truth tables).

Scrapes the Senedd portal, downloads meeting XML, and loads the **raw** tables
only: ``meetings``, ``members``, ``raw_contributions``, ``votes``,
``vote_records``, ``written_contributions``, plus the operational
``sync_checkpoints`` and ``artifact_watch``. It deliberately does **not** build
any derived table — that is the transformation stage's job
(``src/db/transformation.py``). Keeping the two apart is what lets the
production loop schedule raw ingest and derived rebuild independently.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Literal, Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from senedd_data.db_schema import (
    ArtifactWatch,
    Member,
    Meeting,
    QaRoleEnum,
    RawContribution,
    SyncCheckpoint,
    Vote,
    VoteRecord,
    VoteResultEnum,
    WrittenContribution,
)
from senedd_data.fetcher import DataFetcher
from senedd_data.parser import parse_qnr_xml, parse_senedd_xml, parse_votes_xml
from senedd_data.provisioning import Provisioner
from senedd_data.session import get_engine, get_sessionmaker
from senedd_data.settings import settings
from senedd_data.transformers import clean_contribution_verbatim
from senedd_data.webcast import resolve_webcast_guid

logger = logging.getLogger(__name__)


class AcquisitionPipeline:
    """Ingest raw Senedd data into the source-of-truth tables. No transformation."""

    # Artifact types we re-check for after a transcript lands, mapped to the
    # portal's transcript-type filter.
    _WATCHED_ARTIFACTS = {
        "votes": "Votes",
        "qnr": "QNR",
    }

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.engine = get_engine(db_url)
        self.SessionLocal = get_sessionmaker(db_url)
        self.provisioner = Provisioner(db_url)

    def create_schema(self):
        """Ensure the database exists, is migrated to head, and procedures are loaded."""
        self.provisioner.create_schema()

    # ------------------------------------------------------------------
    # Phase 1 — raw ingest
    # ------------------------------------------------------------------

    def ingest_xml(self, session: Session, xml_file: Path) -> int:
        """Parse a transcript XML and load raw_contributions, meetings, members."""
        logger.info("Phase 1: Ingesting XML from: %s", xml_file)
        meeting_data, members_list, contributions_list = parse_senedd_xml(xml_file)

        # Resolve the SeneddTV webcast GUID (for the embeddable player) once per
        # meeting. Preserve any previously-resolved value across re-ingest — a
        # transient lookup failure must not wipe a good GUID, and merge would
        # otherwise overwrite the column with the transient object's NULL.
        meeting_id = meeting_data.get("meeting_id")
        prior = session.get(Meeting, meeting_id) if meeting_id is not None else None
        guid = (prior.webcast_guid if prior else None) or resolve_webcast_guid(meeting_id)
        if guid:
            meeting_data = {**meeting_data, "webcast_guid": guid}

        meeting = Meeting(**meeting_data)
        session.merge(meeting)
        session.flush()

        if members_list:
            stmt = insert(Member).values(members_list)
            upsert_stmt = stmt.on_conflict_do_nothing(index_elements=['member_id'])
            session.execute(upsert_stmt)
            session.flush()
        if contributions_list:
            stmt = insert(RawContribution).values(contributions_list)
            upsert_stmt = stmt.on_conflict_do_nothing(index_elements=['contribution_id'])
            session.execute(upsert_stmt)
            session.flush()

        logger.info("Ingested %d raw contribution rows.", len(contributions_list))
        return len(contributions_list)

    def ingest_votes(self, session: Session, xml_file: Path) -> int:
        """Ingest a Plenary *Votes* XML export into ``votes`` + ``vote_records``.

        Designed to run *after* the meeting's transcript so the motion
        ``contribution_id`` and most members already exist. It is defensive
        regardless: it never clobbers the shared ``meetings`` row, upserts any
        member it hasn't seen, and skips (with a warning) any vote whose motion
        contribution is not yet present — those are retried idempotently once the
        transcript lands. Returns the number of vote_records written.
        """
        logger.info("Ingesting Votes payload from: %s", xml_file)
        meeting_data, votes, vote_records, members = parse_votes_xml(xml_file)
        if not meeting_data or not votes:
            logger.warning("No votes parsed from %s; nothing to ingest.", xml_file)
            return 0

        # Ensure the meeting exists without overwriting the transcript's metadata.
        session.execute(
            insert(Meeting).values(**meeting_data).on_conflict_do_nothing(index_elements=["meeting_id"])
        )

        # Defensive member upsert — a member may vote without ever having spoken.
        if members:
            session.execute(
                insert(Member).values(members).on_conflict_do_nothing(index_elements=["member_id"])
            )
        session.flush()

        # Only ingest votes whose motion contribution is already present; the FK
        # would otherwise hard-fail. Missing ones are picked up on a later pass.
        candidate_cids = [v["contribution_id"] for v in votes]
        existing_cids = {
            row[0] for row in session.query(RawContribution.contribution_id)
            .filter(RawContribution.contribution_id.in_(candidate_cids)).all()
        }
        ready_votes = [v for v in votes if v["contribution_id"] in existing_cids]
        skipped = [v["contribution_id"] for v in votes if v["contribution_id"] not in existing_cids]
        if skipped:
            logger.warning(
                "Deferring %d vote(s) whose motion contribution is not yet ingested: %s",
                len(skipped), skipped,
            )
        if not ready_votes:
            return 0

        session.execute(
            insert(Vote).values(ready_votes).on_conflict_do_nothing(index_elements=["contribution_id"])
        )
        session.flush()

        # Map motion contribution_id -> assigned vote_id (both fresh and pre-existing).
        cid_to_vote_id = {
            cid: vid for vid, cid in session.query(Vote.vote_id, Vote.contribution_id)
            .filter(Vote.contribution_id.in_([v["contribution_id"] for v in ready_votes])).all()
        }

        record_rows = []
        for rec in vote_records:
            vote_id = cid_to_vote_id.get(rec["contribution_id"])
            if vote_id is None:
                continue  # belonged to a deferred vote
            try:
                result_enum = VoteResultEnum(rec["result"])
            except ValueError:
                logger.warning("Unknown vote result %r; skipping record.", rec["result"])
                continue
            record_rows.append({
                "vote_id": vote_id,
                "member_id": rec["member_id"],
                "result": result_enum,
            })

        if record_rows:
            session.execute(
                insert(VoteRecord).values(record_rows)
                .on_conflict_do_nothing(index_elements=["vote_id", "member_id"])
            )
            session.flush()

        logger.info(
            "Votes ingest complete: %d motions, %d member records.",
            len(ready_votes), len(record_rows),
        )
        return len(record_rows)

    def ingest_qnr(self, session: Session, xml_file: Path) -> int:
        """Ingest a Plenary *QNR* export into ``written_contributions``.

        The QNR feed has no ``Contribution_ID`` and no clean FK to
        ``raw_contributions``, so this is independent of the transcript apart from
        the shared meeting row (never clobbered). Text is double-escaped HTML;
        decoded and tag-stripped here. Idempotent on the synthetic
        ``(meeting_id, order_index)`` key. Returns rows written.
        """
        logger.info("Ingesting QNR payload from: %s", xml_file)
        meeting_data, written, members = parse_qnr_xml(xml_file)
        if not meeting_data or not written:
            logger.warning("No QNR rows parsed from %s; nothing to ingest.", xml_file)
            return 0

        session.execute(
            insert(Meeting).values(**meeting_data).on_conflict_do_nothing(index_elements=["meeting_id"])
        )
        if members:
            session.execute(
                insert(Member).values(members).on_conflict_do_nothing(index_elements=["member_id"])
            )
        session.flush()

        rows = []
        for w in written:
            verbatim = w.pop("raw_verbatim")
            translated = w.pop("raw_translated")
            english = clean_contribution_verbatim(translated) or clean_contribution_verbatim(verbatim)
            # Only keep a Welsh field when verbatim genuinely differs from the
            # English translation (answers are English-only, duplicated across both).
            welsh = clean_contribution_verbatim(verbatim) if verbatim and verbatim != translated else None
            rows.append({
                **w,
                "qa_role": QaRoleEnum(w["qa_role"]),
                "text_english": english,
                "text_welsh": welsh,
            })

        session.execute(
            insert(WrittenContribution).values(rows)
            .on_conflict_do_nothing(index_elements=["meeting_id", "order_index"])
        )
        session.flush()

        logger.info("QNR ingest complete: %d written contributions.", len(rows))
        return len(rows)

    # ------------------------------------------------------------------
    # Sync checkpoints
    # ------------------------------------------------------------------

    def get_last_sync_date(self, session: Session) -> datetime:
        """Date of the most recent processed meeting from sync checkpoints."""
        latest = session.query(SyncCheckpoint).order_by(SyncCheckpoint.created_at.desc()).first()
        if latest and latest.last_sync_date:
            return latest.last_sync_date
        return datetime(2000, 1, 1)

    def record_sync_checkpoint(self, session: Session, file_count: int, status: str = "success", notes: str = ""):
        """Record a sync checkpoint for resumability."""
        latest_meeting = session.query(RawContribution.meeting_date).order_by(RawContribution.meeting_date.desc()).first()
        checkpoint = SyncCheckpoint(
            last_sync_date=datetime.utcnow(),
            last_meeting_id=latest_meeting[0] if latest_meeting else None,
            file_count=file_count,
            status=status,
            notes=notes
        )
        session.add(checkpoint)

    # ------------------------------------------------------------------
    # Per-meeting acquisition (raw only)
    # ------------------------------------------------------------------

    def acquire_single_meeting(self, meeting: Meeting, data_dir: Path, keep_xml: bool) -> bool:
        """Download and raw-ingest a single meeting's transcript. No transformation.

        Returns True if the transcript was downloaded and its raw rows committed.
        """
        meeting_id = int(meeting.meeting_id)
        logger.info("Acquiring raw data for meeting %s", meeting_id)

        fetcher = DataFetcher()
        xml_path = fetcher.download_file(meeting, data_dir)
        if not xml_path or not xml_path.exists():
            logger.error("Download failed for meeting %s; skipping.", meeting_id)
            return False

        try:
            with self.SessionLocal() as session:
                with session.begin():
                    self.ingest_xml(session, xml_path)
            success = True
        except Exception as e:
            logger.exception("Raw ingest failed for meeting %s: %s", meeting_id, e)
            success = False
        finally:
            if not keep_xml and xml_path and xml_path.exists():
                fetcher.cleanup_file(xml_path)
                logger.info("Cleaned up XML: %s", xml_path)

        if success:
            logger.info("Meeting %s raw data committed.", meeting_id)
        return success

    def acquire_meeting_all_artifacts(self, meeting: Meeting, data_dir: Path, keep_xml: bool) -> bool:
        """Raw-ingest a meeting's transcript plus any Votes/QNR artifacts in one pass.

        Used by the backfill path, where Votes/QNR are typically already published
        so there is no need to defer them to the artifact-watch sweep. Ordering is
        deliberate: the transcript is ingested first (committed in its own
        transaction) so the vote motion's contribution row and members exist
        before ``ingest_votes`` runs; QNR is independent of the transcript.
        Artifact failures are logged but non-fatal — a missing Votes export must
        not lose the transcript. Returns True if the transcript was ingested.
        """
        if not self.acquire_single_meeting(meeting, data_dir, keep_xml):
            return False

        artifacts = meeting.artifacts or {}
        for artifact_type, ingest_fn in (
            ("Votes", self.ingest_votes),
            ("QNR", self.ingest_qnr),
        ):
            if artifact_type in artifacts:
                self._ingest_meeting_artifact(
                    meeting, artifact_type, ingest_fn, data_dir, keep_xml
                )
        return True

    def _ingest_meeting_artifact(self, meeting, artifact_type, ingest_fn, data_dir, keep_xml) -> bool:
        """Download and raw-ingest a single non-transcript artifact (Votes/QNR)."""
        fetcher = DataFetcher()
        xml_path = fetcher.download_file(meeting, data_dir, transcript_type=artifact_type)
        if not xml_path or not xml_path.exists():
            logger.warning(
                "Could not download %s for meeting %s; skipping artifact.",
                artifact_type, meeting.meeting_id,
            )
            return False
        try:
            with self.SessionLocal() as session:
                with session.begin():
                    ingest_fn(session, xml_path)
            return True
        except Exception as e:
            logger.exception(
                "Failed ingesting %s for meeting %s: %s",
                artifact_type, meeting.meeting_id, e,
            )
            return False
        finally:
            if not keep_xml and xml_path and xml_path.exists():
                fetcher.cleanup_file(xml_path)

    # ------------------------------------------------------------------
    # Late-publication artifact watches (Votes/QNR)
    # ------------------------------------------------------------------

    def register_artifact_watches(self, session: Session, meeting_id: int, meeting_date: datetime):
        """Open pending Votes/QNR watches for a freshly-ingested transcript meeting.

        Idempotent on (meeting_id, artifact_type). Skips meetings already older
        than the watch window (e.g. historical backfill) — those artifacts, if
        they exist, are already published and not subject to late attachment.
        """
        deadline = meeting_date + timedelta(days=settings.artifact_watch_days)
        if deadline < datetime.utcnow():
            return
        rows = [
            {"meeting_id": meeting_id, "artifact_type": at, "status": "pending", "deadline": deadline}
            for at in self._WATCHED_ARTIFACTS
        ]
        session.execute(
            insert(ArtifactWatch).values(rows)
            .on_conflict_do_nothing(index_elements=["meeting_id", "artifact_type"])
        )

    def run_artifact_watch_sweep(self, data_dir: Optional[Path] = None, keep_xml: bool = False) -> int:
        """Re-check the portal for any pending Votes/QNR and attach those now available.

        The portal's default (unfiltered) listing covers the recent meetings —
        comfortably wider than the watch window — so it is fetched once and each
        artifact type is matched by ``meeting_id``. For each pending watch: expire
        it silently once past its deadline; otherwise, if the download is now
        present, ingest it idempotently and mark the watch done. Returns the
        number of artifacts ingested this sweep.
        """
        data_dir = data_dir or Path("data")
        data_dir.mkdir(exist_ok=True)

        with self.SessionLocal() as session:
            pending = (
                session.query(
                    ArtifactWatch.id, ArtifactWatch.meeting_id,
                    ArtifactWatch.artifact_type, ArtifactWatch.deadline,
                )
                .filter(ArtifactWatch.status == "pending")
                .all()
            )

        if not pending:
            logger.info("Artifact watch sweep: no pending watches.")
            return 0

        logger.info("Artifact watch sweep: %d pending watch(es) to re-check.", len(pending))
        fetcher = DataFetcher()
        now = datetime.utcnow()
        ingested = 0

        # One portal fetch; build a {meeting_id(str) -> Meeting} map per artifact.
        try:
            html = fetcher.get_html_page()
        except Exception as e:
            logger.warning("Artifact watch sweep aborted — portal fetch failed: %s", e)
            return 0
        available: dict = {}
        for artifact_type, transcript_type in self._WATCHED_ARTIFACTS.items():
            available[artifact_type] = {
                str(m.meeting_id): m
                for m in fetcher.parse_meetings_from_html(html, transcript_type=transcript_type)
            }

        for watch_id, meeting_id, artifact_type, deadline in pending:
            if now > deadline:
                logger.info(
                    "Watch %s (%s for meeting %s) past deadline; expiring.",
                    watch_id, artifact_type, meeting_id,
                )
                self._update_watch(watch_id, status="expired", checked_at=now)
                continue

            transcript_type = self._WATCHED_ARTIFACTS[artifact_type]
            match = available.get(artifact_type, {}).get(str(meeting_id))
            if match is None:
                logger.debug("%s not yet available for meeting %s.", transcript_type, meeting_id)
                self._update_watch(watch_id, checked_at=now, bump_attempt=True)
                continue

            xml_path = fetcher.download_file(match, data_dir, transcript_type=transcript_type)
            if not xml_path or not xml_path.exists():
                self._update_watch(watch_id, checked_at=now, bump_attempt=True)
                continue

            try:
                with self.SessionLocal() as session:
                    with session.begin():
                        if artifact_type == "votes":
                            self.ingest_votes(session, xml_path)
                        else:
                            self.ingest_qnr(session, xml_path)
                self._update_watch(watch_id, status="done", checked_at=now)
                ingested += 1
                logger.info("Attached %s for meeting %s.", artifact_type, meeting_id)
            except Exception as e:
                logger.exception("Failed to ingest %s for meeting %s: %s", artifact_type, meeting_id, e)
                self._update_watch(watch_id, checked_at=now, bump_attempt=True)
            finally:
                if not keep_xml and xml_path and xml_path.exists():
                    fetcher.cleanup_file(xml_path)

        logger.info("Artifact watch sweep complete: %d artifact(s) attached.", ingested)
        return ingested

    def _update_watch(self, watch_id: int, status: Optional[str] = None,
                      checked_at: Optional[datetime] = None, bump_attempt: bool = False):
        """Apply a small status/bookkeeping update to a single watch row."""
        values: dict = {}
        if status is not None:
            values["status"] = status
        if checked_at is not None:
            values["last_checked"] = checked_at
        if bump_attempt:
            values["attempts"] = ArtifactWatch.attempts + 1
        if not values:
            return
        with self.SessionLocal() as session:
            with session.begin():
                session.query(ArtifactWatch).filter(ArtifactWatch.id == watch_id).update(
                    values, synchronize_session=False
                )

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def run_incremental(
        self,
        data_dir: Optional[Path] = None,
        keep_xml: bool = False,
        last_sync_date: Optional[datetime] = None,
        transcript_type: Literal["BilingualTranscript", "WelshTranscript", "EnglishTranscript", "Votes", "QNR"] = "BilingualTranscript"
    ) -> List[int]:
        """Detect and raw-ingest meetings published since the last sync.

        Writes only raw tables; derived tables are rebuilt separately by the
        transformation stage. Returns the list of meeting IDs whose transcript
        was newly ingested (the set the transform stage should process).
        """
        logger.info("Running incremental raw acquisition.")
        data_dir = data_dir or Path("data")
        data_dir.mkdir(exist_ok=True)

        self.create_schema()
        fetcher = DataFetcher()

        with self.SessionLocal() as session:
            if last_sync_date is None:
                last_sync_date = self.get_last_sync_date(session)
            logger.info("Scanning for meetings uploaded since: %s", last_sync_date.date())

        new_meetings = fetcher.check_for_updates(last_sync_date, transcript_type)
        if not new_meetings:
            logger.info("No new transcripts on the Senedd feed.")
        else:
            logger.info("Discovered %d new transcripts to ingest.", len(new_meetings))

        ingested_meeting_ids: List[int] = []
        for meeting in new_meetings:
            if self.acquire_single_meeting(meeting, data_dir, keep_xml):
                ingested_meeting_ids.append(int(meeting.meeting_id))
                with self.SessionLocal() as session:
                    with session.begin():
                        self.register_artifact_watches(
                            session, int(meeting.meeting_id), meeting.meeting_date
                        )

        if ingested_meeting_ids:
            with self.SessionLocal() as session:
                with session.begin():
                    self.record_sync_checkpoint(session, len(ingested_meeting_ids), status="success")

        # Always sweep for late-publishing Votes/QNR — they attach to meetings
        # processed on earlier runs, not just this one.
        self.run_artifact_watch_sweep(data_dir, keep_xml)

        logger.info("Incremental raw acquisition complete. New transcripts: %d", len(ingested_meeting_ids))
        return ingested_meeting_ids

    def acquire_meetings(
        self,
        meetings: List[Meeting],
        data_dir: Optional[Path] = None,
        keep_xml: bool = False,
    ) -> List[int]:
        """Raw-ingest an explicit list of Meeting objects (transcript + artifacts).

        Backfill entry point. Returns the list of meeting IDs whose transcript was
        ingested. Derived tables are built separately by the transformation stage.
        """
        logger.info("Acquiring raw data for %d explicit meetings.", len(meetings))
        data_dir = data_dir or Path("data")
        data_dir.mkdir(exist_ok=True)

        self.create_schema()

        ingested_meeting_ids: List[int] = []
        for meeting in meetings:
            if self.acquire_meeting_all_artifacts(meeting, data_dir, keep_xml):
                ingested_meeting_ids.append(int(meeting.meeting_id))
                # Open watches for any artifact not yet published. Self-skips
                # historic meetings (deadline already past).
                with self.SessionLocal() as session:
                    with session.begin():
                        self.register_artifact_watches(
                            session, int(meeting.meeting_id), meeting.meeting_date
                        )

        logger.info("Raw backfill acquisition complete. Ingested: %d", len(ingested_meeting_ids))
        return ingested_meeting_ids


def main():
    """CLI entry point for the incremental raw-acquisition stage."""
    import argparse

    from senedd_data.settings import settings, setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Senedd raw data-acquisition stage")
    parser.add_argument("--data-dir", type=Path, default=Path("data"),
                        help="Directory for downloaded XML (default: ./data/).")
    parser.add_argument("--keep-xml", action="store_true",
                        help="Keep raw XML files after processing (default: delete).")
    parser.add_argument("--last-sync", type=str, default=None,
                        help="Override last sync date (YYYY-MM-DD).")
    args = parser.parse_args()

    last_sync = None
    if args.last_sync:
        last_sync = datetime.strptime(args.last_sync, "%Y-%m-%d")

    pipeline = AcquisitionPipeline(settings.database_url)
    pipeline.run_incremental(
        data_dir=args.data_dir,
        keep_xml=args.keep_xml,
        last_sync_date=last_sync,
    )


if __name__ == "__main__":
    main()
