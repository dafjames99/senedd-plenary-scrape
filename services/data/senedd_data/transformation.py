"""Derived-data transformation stage (DML, no network).

Reads the raw source-of-truth tables (``raw_contributions`` etc.) and rebuilds
the derived tables — cleaned/classified contributions, reconstructed speeches +
parts, the members dimension, and procedural events. This is the boundary the
transform stage of the production loop calls after raw data has landed; it never
touches the network.

The raw/derived seam here is exactly the one codified by
``purge_downstream_tables`` (``src/db/procedures/001_purge_downstream.sql``).
"""
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from senedd_data.db_schema import (
    ClassifiedContribution,
    CleanContribution,
    Meeting,
    Member,
    MemberJobTitle,
    OralQuestion,
    ProceduralEvent,
    RawContribution,
    RowTypeEnum,
    Speech,
    SpeechPart,
)
from senedd_data.session import get_engine, get_sessionmaker
from senedd_data.transformers import (
    classify_contribution,
    clean_contribution_verbatim,
    parse_oral_question_meta,
)

logger = logging.getLogger(__name__)


class TransformationPipeline:
    """Rebuild derived tables from raw contributions. No network access."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.engine = get_engine(db_url)
        self.SessionLocal = get_sessionmaker(db_url)

    # ------------------------------------------------------------------
    # Phase 2/3 — clean + classify
    # ------------------------------------------------------------------

    def process_and_classify_contributions(self, session: Session, meeting_id: Optional[int] = None):
        """Clean text, extract metadata, and classify rows in one phase."""
        logger.info("Phase 2/3: Processing and classifying rows for meeting_id=%s", meeting_id)

        query = session.query(RawContribution)
        if meeting_id:
            query = query.filter(RawContribution.meeting_id == meeting_id)
        raw_contribs = query.all()

        logger.debug("Found %d raw rows available for transformation.", len(raw_contribs))

        oral_questions_batch = []
        clean_contributions_batch = []
        classified_contributions_batch = []

        for raw in raw_contribs:

            row_dict = {
                'Member_Id': raw.member_id,
                'Member_job_title_English': raw.member_job_title_english,
                'contribution_type': raw.contribution_type,
                'contribution_verbatim': raw.contribution_verbatim,
                'contribution_translated': raw.contribution_translated,
            }
            row_type, reason = classify_contribution(row_dict)
            cleaned_verbatim = clean_contribution_verbatim(raw.contribution_verbatim)
            cleaned_translated = clean_contribution_verbatim(raw.contribution_translated)

            if row_type == "oral-question" or row_type == "topical-question":
                q_num, q_id, clean_text = parse_oral_question_meta(cleaned_verbatim)

                if q_id and q_num:
                    logger.debug("Extracted Oral Question metadata: ID=%s, Num=%s", q_id, q_num)
                    cleaned_verbatim = clean_text

                    if cleaned_translated:
                        _, _, clean_trans_text = parse_oral_question_meta(cleaned_translated)
                        cleaned_translated = clean_trans_text

                    oral_questions_batch.append({
                        "question_id": q_id,
                        "meeting_id": raw.meeting_id,
                        "contribution_id": raw.contribution_id,
                        "question_number": q_num,
                    })

            clean_contributions_batch.append({
                "contribution_id": raw.contribution_id,
                "contribution_verbatim_clean": cleaned_verbatim,
                "contribution_translated_clean": cleaned_translated,
            })
            classified_contributions_batch.append({
                "contribution_id": raw.contribution_id,
                "row_type": RowTypeEnum(row_type),
                "classification_reason": reason,
            })
            if oral_questions_batch:
                session.execute(insert(OralQuestion).values(oral_questions_batch).on_conflict_do_nothing(index_elements=['question_id']))
            if clean_contributions_batch:
                session.execute(insert(CleanContribution).values(clean_contributions_batch).on_conflict_do_nothing(index_elements=['contribution_id']))
            if classified_contributions_batch:
                session.execute(insert(ClassifiedContribution).values(classified_contributions_batch).on_conflict_do_nothing(index_elements=['contribution_id']))

            session.flush()

    # ------------------------------------------------------------------
    # Phase 4 — reconstruct speeches
    # ------------------------------------------------------------------

    def save_reconstructed_speeches(self, session: Session, speech_records: list) -> int:
        """Save speeches using a Postgres RETURNING statement to wire children."""
        if not speech_records:
            return 0

        speech_part_records = []

        for record in speech_records:
            # Pop the temporary raw-parts tracking field so it doesn't break the mapping.
            raw_parts = record.pop('_raw_parts', [])

            stmt = insert(Speech).values(record)
            safe_stmt = stmt.on_conflict_do_nothing(index_elements=['speech_id']).returning(Speech.speech_id)

            result = session.execute(safe_stmt)
            returned_row = result.fetchone()

            if returned_row:
                generated_id = returned_row[0]

                for part in raw_parts:
                    speech_part_records.append({
                        'speech_id': generated_id,
                        'contribution_id': part['contribution_id'],
                        'contribution_order_id': part['contribution_order_id'],
                        'contribution_time': part['contribution_time'],
                        'spoken_url': part['spoken_url'],
                        'translated_url': part['translated_url'],
                        'verbatim_text': part['verbatim_text'],
                    })

        if speech_part_records:
            session.execute(insert(SpeechPart).values(speech_part_records))

        session.flush()
        return len(speech_records)

    def reconstruct_speeches(self, session: Session, meeting_id: Optional[int] = None) -> int:
        """Phase 4: reconstruct speeches; boundary = speaker change OR agenda change."""
        logger.info("Phase 4: Reconstructing speeches. Target filter: meeting_id=%s", meeting_id)
        if meeting_id is not None:
            meeting_ids = [meeting_id]
        else:
            meeting_ids = [m[0] for m in session.query(RawContribution.meeting_id).distinct().all()]

        total_speeches = 0
        for m_id in meeting_ids:
            total_speeches += self._reconstruct_meeting_speeches(session, m_id)

        logger.info("Reconstructed %d speech blocks from row sequences.", total_speeches)
        return total_speeches

    def _deduplicate_overlap(self, existing_text: str, new_text: str) -> str:
        """Remove overlapping words at the boundary of two text segments."""
        existing_words = existing_text.strip().split()
        new_words = new_text.strip().split()

        if not existing_words or not new_words:
            return new_text

        max_overlap = min(len(existing_words), len(new_words))
        for i in range(max_overlap, 0, -1):
            if existing_words[-i:] == new_words[:i]:
                return " ".join(new_words[i:])

        return new_text

    def _reconstruct_meeting_speeches(self, session: Session, meeting_id: int) -> int:
        """Reconstruct speeches for a specific meeting chronologically."""
        meeting = session.query(Meeting).filter_by(meeting_id=meeting_id).first()
        if not meeting:
            logger.warning("Aborting reconstruction: meeting_id=%s not found.", meeting_id)
            return 0

        # Idempotent rebuild: speeches carry an autoincrement PK with no natural
        # key, so re-running would otherwise duplicate them. Purge this meeting's
        # existing speeches first; the FK cascade clears their speech_parts and
        # (now-stale) speech_embeddings, which the embed sweep regenerates.
        deleted = (
            session.query(Speech)
            .filter(Speech.meeting_id == meeting_id)
            .delete(synchronize_session=False)
        )
        if deleted:
            logger.debug("Meeting %s: cleared %d existing speeches before rebuild.", meeting_id, deleted)

        # Get all speech-classified rows, ordered by contribution_order_id.
        speech_rows = session.query(
            RawContribution, CleanContribution
        ).join(
            ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id
        ).join(
            CleanContribution, RawContribution.contribution_id == CleanContribution.contribution_id
        ).filter(
            RawContribution.meeting_id == meeting_id,
            ((ClassifiedContribution.row_type == RowTypeEnum.SPEECH) | (ClassifiedContribution.row_type == RowTypeEnum.ORAL_QUESTION))
        ).order_by(
            RawContribution.contribution_order_id
        ).all()

        speeches = []
        current_speech = None

        for raw, clean in speech_rows:
            # Check if speaker or agenda item changes.
            if current_speech is None or \
               current_speech['speaker_id'] != raw.member_id or \
               current_speech['agenda_item_id'] != raw.agenda_item_id:

                if current_speech is not None:
                    speeches.append(current_speech)

                current_speech = {
                    'meeting_id': raw.meeting_id,
                    'assembly': raw.assembly,
                    'agenda_item_id': raw.agenda_item_id,
                    'speaker_id': raw.member_id,
                    'speaker_name': raw.member_name_english or 'Unknown',
                    'speech_language': raw.contribution_language,
                    'speech_parts': [],
                    'texts': [],
                }

            # Select English translation if available, otherwise verbatim.
            text = None
            if clean.contribution_translated_clean:
                text = clean.contribution_translated_clean
            elif clean.contribution_verbatim_clean:
                text = clean.contribution_verbatim_clean

            if text:
                if not current_speech['texts']:
                    current_speech['texts'].append(text)
                else:
                    current_full_text = " ".join(current_speech['texts'])
                    processed_text = self._deduplicate_overlap(current_full_text, text)
                    if processed_text:  # Avoid appending empty strings if it was a total duplicate.
                        current_speech['texts'].append(processed_text)

            current_speech['speech_parts'].append({
                'contribution_id': raw.contribution_id,
                'contribution_order_id': raw.contribution_order_id,
                'contribution_time': raw.contribution_time,
                'spoken_url': raw.contribution_spoken_seneddtv,
                'translated_url': raw.contribution_translated_seneddtv,
                'verbatim_text': clean.contribution_translated_clean or clean.contribution_verbatim_clean,
            })

        if current_speech is not None:
            speeches.append(current_speech)

        speech_records = []
        for speech_dict in speeches:
            speech_records.append({
                'meeting_id': speech_dict['meeting_id'],
                'assembly': speech_dict['assembly'],
                'agenda_item_id': speech_dict['agenda_item_id'],
                'speaker_id': speech_dict['speaker_id'],
                'speaker_name': speech_dict['speaker_name'],
                'speech_language': speech_dict['speech_language'],
                'speech_text': ' '.join(speech_dict['texts']),
                'source_row_count': len(speech_dict['speech_parts']),
                'created_at': datetime.now(),
                '_raw_parts': speech_dict['speech_parts']
            })

        inserted_count = self.save_reconstructed_speeches(session, speech_records)
        logger.debug("Meeting %s: processed %d speeches via upsert logic.", meeting_id, inserted_count)
        return inserted_count

    # ------------------------------------------------------------------
    # Phase 5a — members dimension
    # ------------------------------------------------------------------

    def build_members_dimension(self, session: Session, meeting_id: Optional[int] = None):
        """Phase 5a: build/complete the members dimension table."""
        logger.info("Phase 5a: Building members dimension for meeting_id=%s", meeting_id)
        query = session.query(RawContribution).filter(RawContribution.member_id.isnot(None))
        if meeting_id:
            query = query.filter(RawContribution.meeting_id == meeting_id)
        raw_rows = query.all()

        unique_members = {}
        unique_job_titles = {}

        for raw in raw_rows:
            unique_members[raw.member_id] = {
                "member_id": raw.member_id,
                "name_english": raw.member_name_english or "Unknown Speaker",
                "biography_english": raw.member_biog_english,
                "biography_welsh": raw.member_biog_welsh,
                "sort_code": raw.member_sortcode
            }

            unique_job_titles[(raw.member_id, raw.meeting_id)] = {
                "member_id": raw.member_id,
                "meeting_id": raw.meeting_id,
                "job_title_english": raw.member_job_title_english,
                "job_title_welsh": raw.member_job_title_welsh,
            }

        if unique_members:
            stmt = insert(Member).values(list(unique_members.values()))
            session.execute(stmt.on_conflict_do_update(
                index_elements=['member_id'],
                set_={
                    'biography_english': stmt.excluded.biography_english,
                    'biography_welsh': stmt.excluded.biography_welsh,
                    'sort_code': stmt.excluded.sort_code
                }
            ))

        if unique_job_titles:
            stmt = insert(MemberJobTitle).values(list(unique_job_titles.values()))
            session.execute(stmt.on_conflict_do_update(
                index_elements=['member_id', 'meeting_id'],
                set_={
                    'job_title_english': stmt.excluded.job_title_english,
                    'job_title_welsh': stmt.excluded.job_title_welsh
                }
            ))

        session.flush()
        member_count = session.query(Member).count()
        title_count = session.query(MemberJobTitle).count()
        logger.info("Dimension build complete. Members: %d | Role entries: %d", member_count, title_count)

    # ------------------------------------------------------------------
    # Phase 5b — procedural events
    # ------------------------------------------------------------------

    def build_procedural_events(self, session: Session, meeting_id: Optional[int] = None) -> int:
        """Phase 5b: extract procedural events."""
        logger.info("Phase 5b: Building procedural events for meeting_id=%s", meeting_id)
        if meeting_id is not None:
            meeting_ids = [meeting_id]
        else:
            meeting_ids = [m[0] for m in session.query(RawContribution.meeting_id).distinct().all()]

        total_events = 0
        for m_id in meeting_ids:
            total_events += self._build_meeting_procedural_events(session, m_id)

        logger.info("Synchronized %d procedural events.", total_events)
        return total_events

    def _build_meeting_procedural_events(self, session: Session, meeting_id: int) -> int:
        meeting = session.query(Meeting).filter_by(meeting_id=meeting_id).first()
        if not meeting:
            return 0

        # Idempotent rebuild: procedural_events has an autoincrement PK and no
        # conflict guard, so purge this meeting's rows before re-inserting.
        session.query(ProceduralEvent).filter(
            ProceduralEvent.meeting_id == meeting_id
        ).delete(synchronize_session=False)

        procedural_rows = (
            session.query(RawContribution)
            .filter(RawContribution.meeting_id == meeting_id)
            .join(ClassifiedContribution, RawContribution.contribution_id == ClassifiedContribution.contribution_id)
            .filter(ClassifiedContribution.row_type == RowTypeEnum.PROCEDURAL)
            .all()
        )
        unique_events = {}
        for raw in procedural_rows:
            event_type = 'ruling' if raw.member_job_title_english and 'Llywydd' in raw.member_job_title_english else raw.contribution_type

            unique_events[raw.contribution_id] = {
                "meeting_id": meeting_id,
                "agenda_item_id": raw.agenda_item_id,
                "event_time": raw.contribution_time,
                "event_type": event_type,
                "speaker_name": raw.member_name_english or 'Unknown',
                "raw_text": raw.contribution_verbatim or raw.contribution_translated,
                "source_contribution_id": raw.contribution_id,
                "senedd_tv_url": raw.contribution_spoken_seneddtv,
            }

        if unique_events:
            stmt = insert(ProceduralEvent).values(list(unique_events.values()))
            session.execute(stmt)
            session.flush()
        return len(unique_events)

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def process_meetings(self, session: Session, meeting_ids: List[int]):
        """Run all transformation phases for a list of meeting IDs."""
        for m_id in meeting_ids:
            self.process_and_classify_contributions(session, m_id)
            self.reconstruct_speeches(session, m_id)
            self.build_members_dimension(session, m_id)
            self.build_procedural_events(session, m_id)

    def _all_raw_meeting_ids(self, session: Session) -> List[int]:
        return [
            m[0] for m in session.query(RawContribution.meeting_id)
            .distinct().order_by(RawContribution.meeting_id).all()
        ]

    def _meetings_needing_transform(self, session: Session) -> List[int]:
        """Meetings that have raw contributions but no reconstructed speeches yet."""
        raw_ids = {m[0] for m in session.query(RawContribution.meeting_id).distinct().all()}
        done_ids = {m[0] for m in session.query(Speech.meeting_id).distinct().all()}
        return sorted(raw_ids - done_ids)

    def transform_meetings(self, meeting_ids: Optional[List[int]] = None) -> int:
        """Transform the given meetings (each in its own transaction).

        With ``meeting_ids=None`` this self-discovers the set of meetings that
        have raw contributions but no speeches — the seam the transform stage of
        the production loop calls after a raw ingest. Returns the number of
        meetings transformed.
        """
        with self.SessionLocal() as session:
            if meeting_ids is None:
                meeting_ids = self._meetings_needing_transform(session)

        if not meeting_ids:
            logger.info("No meetings require transformation.")
            return 0

        logger.info("Transforming %d meeting(s): %s", len(meeting_ids), meeting_ids)
        processed = 0
        for i, m_id in enumerate(meeting_ids, 1):
            logger.info("[%d/%d] Transforming meeting %s", i, len(meeting_ids), m_id)
            try:
                with self.SessionLocal() as session:
                    with session.begin():
                        self.process_meetings(session, [m_id])
                processed += 1
            except Exception as e:
                logger.error("Transformation failed on meeting %s: %s", m_id, e)
                continue

        self.validate_pipeline()
        return processed

    def reprocess_all(self, clear_dimensions: bool = False, clear_embeddings: bool = False):
        """Rebuild every derived table from raw contributions (no network).

        Uses ``purge_downstream_tables`` to drop the derived tables safely, then
        rebuilds each meeting locally from ``raw_contributions``.
        """
        logger.info("Reprocessing all downstream tables from raw contributions.")

        # STAGE 1: purge downstream tables via the native procedure.
        with self.SessionLocal() as session:
            with session.begin():
                logger.info("Calling purge_downstream_tables()...")
                try:
                    session.execute(
                        text("CALL purge_downstream_tables(:clear_dims, :clear_embs);"),
                        {"clear_dims": clear_dimensions, "clear_embs": clear_embeddings}
                    )
                except Exception as e:
                    logger.exception("Failed to run purge procedure; aborting reprocess: %s", e)
                    return

        # STAGE 2: rebuild each meeting from raw.
        with self.SessionLocal() as session:
            meeting_ids = self._all_raw_meeting_ids(session)
        if not meeting_ids:
            logger.warning("No raw data in raw_contributions. Reprocessing halted.")
            return

        logger.info("Rebuilding %d distinct meetings.", len(meeting_ids))
        for i, m_id in enumerate(meeting_ids, 1):
            logger.info("[%d/%d] Rebuilding meeting %s", i, len(meeting_ids), m_id)
            try:
                with self.SessionLocal() as session:
                    with session.begin():
                        self.process_meetings(session, [m_id])
            except Exception as e:
                logger.error("Rebuild failed on meeting %s: %s", m_id, e)
                continue

        self.validate_pipeline()
        logger.info("Downstream reprocessing complete.")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_pipeline(self) -> dict:
        """Validate derived-table output and log a report."""
        logger.info("Validating derived data integrity.")
        session = self.SessionLocal()

        report = {
            'raw_contributions': session.query(RawContribution).count(),
            'clean_contributions': session.query(CleanContribution).count(),
            'classified_contributions': session.query(ClassifiedContribution).count(),
            'speeches': session.query(Speech).count(),
            'speech_parts': session.query(SpeechPart).count(),
            'members': session.query(Member).count(),
            'procedural_events': session.query(ProceduralEvent).count(),
        }

        speech_ids_with_parts = session.query(SpeechPart.speech_id).distinct().count()
        missing_traceability = report['speeches'] - speech_ids_with_parts
        report['speeches_with_parts'] = speech_ids_with_parts
        report['missing_traceability'] = missing_traceability

        empty_speeches = session.query(Speech).filter(
            (Speech.speech_text == None) | (Speech.speech_text == '')  # noqa: E711
        ).count()
        report['empty_speeches'] = empty_speeches

        session.close()

        if missing_traceability > 0:
            logger.warning("%d speeches lack corresponding speech_parts records.", missing_traceability)
        if empty_speeches > 0:
            logger.error("Found %d speeches with empty or null text.", empty_speeches)

        logger.info(
            "\n"
            "============================================================\n"
            "                     VALIDATION REPORT                      \n"
            "============================================================\n"
            f"Raw contributions ingested:        {report['raw_contributions']}\n"
            f"Cleaned contributions:             {report['clean_contributions']}\n"
            f"Classified contributions:          {report['classified_contributions']}\n"
            f"Reconstructed speeches:            {report['speeches']}\n"
            f"Speech parts (lineage):            {report['speech_parts']}\n"
            f"Unique members:                    {report['members']}\n"
            f"Procedural events:                 {report['procedural_events']}\n"
            f"Speeches with parts:               {report['speeches_with_parts']}\n"
            f"Missing traceability:              {report['missing_traceability']}\n"
            f"Empty speeches (data quality):     {report['empty_speeches']}\n"
            "============================================================"
        )

        return report


def main():
    """CLI entry point for the transform stage."""
    import argparse

    from senedd_data.settings import settings, setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Senedd derived-data transformation stage")
    parser.add_argument(
        "--all", action="store_true",
        help="Rebuild every derived table from raw (purge_downstream + full rebuild)."
    )
    parser.add_argument(
        "--meetings", type=str, default=None,
        help="Comma-separated meeting IDs to transform. Omit to auto-discover meetings "
             "that have raw contributions but no speeches yet."
    )
    parser.add_argument(
        "--clear-dimensions", action="store_true",
        help="With --all, also truncate the member_job_titles dimension."
    )
    parser.add_argument(
        "--clear-embeddings", action="store_true",
        help="With --all, also truncate speech_embeddings."
    )
    args = parser.parse_args()

    pipeline = TransformationPipeline(settings.database_url)
    if args.all:
        pipeline.reprocess_all(
            clear_dimensions=args.clear_dimensions,
            clear_embeddings=args.clear_embeddings,
        )
    else:
        meeting_ids = (
            [int(x) for x in args.meetings.split(",") if x.strip()]
            if args.meetings else None
        )
        pipeline.transform_meetings(meeting_ids)


if __name__ == "__main__":
    main()
