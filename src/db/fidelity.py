"""Per-speech transcript-fidelity signal: compute, classify, persist.

A reconstructed speech is the unit the corpus actually serves, so fidelity is
measured here rather than per raw contribution (which is dominated by an
interjection artifact — brief interjections get a near-identical timestamp,
collapsing the inferred duration). At speech level the WPM distribution is tight
(IQR ~120–176), so deviations are informative.

This is a *measurement*, not a fix: missing source text cannot be recovered. The
output is a confidence signal a consumer can caveat with — persisted to the
``speech_fidelity`` table and surfaced by the MCP as ``is_suspect``.

Two complementary signals, because each misses what the other catches:

* **WPM** — words / (gap to next speech start, within the meeting). Catches a
  speech whose text is implausibly short (or long) for its time slot. Blind to a
  truncation that happens to leave plausible aggregate WPM.
* **ends-mid-sentence** — text not ending in terminal punctuation. Intended to
  catch a *within-speech* cut-off that WPM misses. Empirically the corpus is clean
  on this axis: ~97% of mid-sentence-looking endings are em-dash *interruptions*
  (correctly recorded; the speaker resumes in a later speech), so the dash is
  treated as terminal. The residue is a handful of parser artifacts (e.g. an
  appended ``FootnoteLink``), not lost text — so this signal mostly confirms the
  transcript's sentence boundaries are well-formed rather than finding truncations.

Run the pass with ``uv run python -m src.db.fidelity`` (``--dry-run`` to report
without writing). Idempotent: it fully refreshes the table each run.
"""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.db.db_schema import SpeechFidelity
from src.db.settings import settings

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
# Trailing wrappers stripped before judging the final character: closing quotes,
# brackets and stray markup that legitimately follow terminal punctuation.
_CLOSERS = "\"'”’»)]}>"
# Terminal markers. The em/en-dash matters: Senedd transcripts end an interrupted
# speech with "—" (the speaker resumes in a later speech), so a trailing dash is a
# well-formed *interruption* marker, not a data-loss truncation — empirically it
# accounts for ~97% of mid-sentence-looking endings in the corpus.
_TERMINALS = ".!?…—–"

# Speech start = earliest timestamped part; duration = gap to the next speech's
# start within the meeting (NULLS LAST keeps untimed speeches from breaking the
# ordering — they fall out as no_duration). spoken_url is for the spot-check only.
SPEECH_FIDELITY_SQL = """
WITH starts AS (
    SELECT
        s.speech_id,
        s.meeting_id,
        s.speaker_name,
        s.speech_text,
        MIN(sp.contribution_time) AS start_t,
        (SELECT sp2.spoken_url FROM speech_parts sp2
          WHERE sp2.speech_id = s.speech_id AND sp2.spoken_url IS NOT NULL
          ORDER BY sp2.contribution_order_id ASC LIMIT 1) AS spoken_url
    FROM speeches s
    LEFT JOIN speech_parts sp ON sp.speech_id = s.speech_id
    GROUP BY s.speech_id, s.meeting_id, s.speaker_name, s.speech_text
),
seq AS (
    SELECT starts.*,
           LEAD(start_t) OVER (PARTITION BY meeting_id ORDER BY start_t NULLS LAST) AS next_t
    FROM starts
)
SELECT
    speech_id, meeting_id, speaker_name, speech_text, spoken_url,
    EXTRACT(EPOCH FROM (next_t - start_t)) AS gap_seconds
FROM seq
ORDER BY meeting_id, start_t NULLS LAST
"""


@dataclass(frozen=True)
class FidelityThresholds:
    """Tunable bounds for the fidelity classifier.

    Defaults derive from the observed speech-level distribution (median ~150 WPM,
    p5 ~34, p95 ~251). ``wpm_slow`` sits well below any plausible sustained speech;
    ``wpm_fast`` above the legitimate p95. Gaps outside ``[min_gap, max_gap]`` make
    WPM untrustworthy (denominator noise, or a session break rather than a speech
    duration) and are demoted to ``low_confidence`` — the ends-mid-sentence signal
    still catches real truncations there.
    """

    wpm_slow: float = 60.0
    wpm_fast: float = 300.0
    min_gap_seconds: float = 8.0
    max_gap_seconds: float = 600.0
    # WPM is meaningless when the numerator is tiny: a 2-word "Diolch, Llywydd."
    # followed by a pause reads as ~0 WPM but is not a truncation. Below this word
    # count the WPM flags are demoted to low_confidence — truncations of short text
    # are then caught by the orthogonal ends-mid-sentence signal instead.
    min_words_for_wpm: int = 25
    suspect_min_words: int = 12


DEFAULT_THRESHOLDS = FidelityThresholds()


def count_words(text_value: Optional[str]) -> int:
    """Count whitespace-delimited tokens, stripping any residual HTML tags.

    Args:
        text_value: Speech text, possibly ``None`` or tag-wrapped.

    Returns:
        Word count (``0`` for empty/``None`` input).
    """
    if not isinstance(text_value, str) or not text_value:
        return 0
    return len(_TAG_RE.sub(" ", text_value).split())


def ends_midsentence(text_value: Optional[str]) -> bool:
    """Whether the text ends without sentence-terminal punctuation (likely cut off).

    Closing quotes/brackets and stray tags after the punctuation are ignored, so
    ``... future.")`` still counts as a clean ending.

    Args:
        text_value: Speech text.

    Returns:
        ``True`` if the last meaningful character is not one of ``. ! ? …``.
        ``False`` for empty/``None`` text (nothing to judge).
    """
    if not isinstance(text_value, str):
        return False
    stripped = _TAG_RE.sub("", text_value).rstrip()
    stripped = stripped.rstrip(_CLOSERS).rstrip()
    if not stripped:
        return False
    return stripped[-1] not in _TERMINALS


def classify(
    word_count: int,
    gap_seconds: Optional[float],
    ends_mid: bool,
    thresholds: FidelityThresholds = DEFAULT_THRESHOLDS,
) -> tuple[str, Optional[float], bool]:
    """Classify a speech's fidelity from its word count, duration and ending.

    Args:
        word_count: Words in the served ``speech_text``.
        gap_seconds: Seconds to the next speech's start, or ``None`` if unknown.
        ends_mid: Result of :func:`ends_midsentence` for this speech.
        thresholds: Classifier bounds.

    Returns:
        ``(flag, wpm, is_suspect)``. ``flag`` is one of ``ok``, ``too_slow``,
        ``too_fast``, ``broken_timestamp``, ``low_confidence``, ``no_duration``.
        ``wpm`` is ``None`` when no positive duration was available.
    """
    if gap_seconds is None:
        flag, wpm = "no_duration", None
    elif gap_seconds <= 0:
        flag, wpm = "broken_timestamp", None
    else:
        wpm = word_count / (gap_seconds / 60.0)
        if (
            gap_seconds < thresholds.min_gap_seconds
            or gap_seconds > thresholds.max_gap_seconds
            or word_count < thresholds.min_words_for_wpm
        ):
            flag = "low_confidence"
        elif wpm < thresholds.wpm_slow:
            flag = "too_slow"
        elif wpm > thresholds.wpm_fast:
            flag = "too_fast"
        else:
            flag = "ok"

    is_suspect = (
        flag in ("broken_timestamp", "too_slow")
        or (ends_mid and word_count >= thresholds.suspect_min_words)
    )
    return flag, wpm, is_suspect


@dataclass
class SpeechFidelityRow:
    """Computed fidelity for one speech (pre-persistence / for reporting)."""

    speech_id: int
    meeting_id: int
    speaker_name: Optional[str]
    word_count: int
    duration_seconds: Optional[float]
    wpm: Optional[float]
    ends_midsentence: bool
    flag: str
    is_suspect: bool
    spoken_url: Optional[str]


def compute_rows(
    session, thresholds: FidelityThresholds = DEFAULT_THRESHOLDS
) -> List[SpeechFidelityRow]:
    """Compute a fidelity row for every speech (one query, classified in Python).

    Args:
        session: An open SQLAlchemy session.
        thresholds: Classifier bounds.

    Returns:
        One :class:`SpeechFidelityRow` per speech.
    """
    result = session.execute(text(SPEECH_FIDELITY_SQL))
    rows: List[SpeechFidelityRow] = []
    for r in result:
        gap = float(r.gap_seconds) if r.gap_seconds is not None else None
        words = count_words(r.speech_text)
        ends_mid = ends_midsentence(r.speech_text)
        flag, wpm, suspect = classify(words, gap, ends_mid, thresholds)
        rows.append(SpeechFidelityRow(
            speech_id=r.speech_id,
            meeting_id=r.meeting_id,
            speaker_name=r.speaker_name,
            word_count=words,
            duration_seconds=gap,
            wpm=wpm,
            ends_midsentence=ends_mid,
            flag=flag,
            is_suspect=suspect,
            spoken_url=r.spoken_url,
        ))
    return rows


def store_rows(session, rows: Sequence[SpeechFidelityRow]) -> None:
    """Replace the ``speech_fidelity`` table contents with ``rows`` (idempotent).

    Args:
        session: An open SQLAlchemy session (committed by this call).
        rows: Computed fidelity rows to persist.
    """
    now = datetime.now()
    payload = [{
        "speech_id": r.speech_id,
        "word_count": r.word_count,
        "duration_seconds": r.duration_seconds,
        "wpm": r.wpm,
        "ends_midsentence": r.ends_midsentence,
        "flag": r.flag,
        "is_suspect": r.is_suspect,
        "computed_at": now,
    } for r in rows]

    session.execute(text("DELETE FROM speech_fidelity"))
    if payload:
        session.execute(SpeechFidelity.__table__.insert(), payload)
    session.commit()


def _summarise(rows: Sequence[SpeechFidelityRow]) -> None:
    """Log the headline corpus-fidelity numbers."""
    n = len(rows)
    if n == 0:
        logger.warning("No speeches to summarise.")
        return
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.flag] = counts.get(r.flag, 0) + 1
    suspect = sum(r.is_suspect for r in rows)
    midsentence = sum(r.ends_midsentence for r in rows)
    wpms = sorted(r.wpm for r in rows if r.wpm is not None)

    def pct(k: str) -> float:
        return 100 * counts.get(k, 0) / n

    logger.info("Speeches: %d", n)
    logger.info(
        "  flags: ok %.1f%% | too_slow %.1f%% | too_fast %.1f%% | "
        "broken_timestamp %.1f%% | low_confidence %.1f%% | no_duration %.1f%%",
        pct("ok"), pct("too_slow"), pct("too_fast"),
        pct("broken_timestamp"), pct("low_confidence"), pct("no_duration"),
    )
    if wpms:
        q = lambda p: wpms[min(len(wpms) - 1, int(p * len(wpms)))]
        logger.info("  WPM: p5 %.0f | median %.0f | p95 %.0f", q(0.05), q(0.5), q(0.95))
    logger.info(
        "  ends mid-sentence: %d (%.1f%%) | IS_SUSPECT: %d (%.1f%%)",
        midsentence, 100 * midsentence / n, suspect, 100 * suspect / n,
    )


def _spot_check(rows: Sequence[SpeechFidelityRow], limit: int) -> None:
    """Log the worst suspect speeches with URLs for manual calibration."""
    suspects = [r for r in rows if r.is_suspect]
    # Provably-broken first, then the slowest substantial speeches (the genuine
    # text/time anomalies), then the residual malformed endings.
    rank = {"broken_timestamp": 0, "too_slow": 1}
    suspects.sort(key=lambda r: (rank.get(r.flag, 2),
                                 r.wpm if r.wpm is not None else 1e9,
                                 -r.word_count))
    logger.info("Calibration spot-check (worst %d of %d suspect):", limit, len(suspects))
    for r in suspects[:limit]:
        wpm = f"{r.wpm:.0f}" if r.wpm is not None else "  -"
        logger.info(
            "  speech %-7d | %4d words | %5s wpm | %-16s | mid=%s | %s",
            r.speech_id, r.word_count, wpm, r.flag, r.ends_midsentence, r.spoken_url,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute and persist per-speech transcript-fidelity flags.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report without writing to the database.")
    parser.add_argument("--spot-check", type=int, default=10,
                        help="How many worst-suspect speeches to print for calibration.")
    args = parser.parse_args()

    from src import setup_logging
    setup_logging()

    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        rows = compute_rows(session)
        _summarise(rows)
        if args.spot_check:
            _spot_check(rows, args.spot_check)
        if args.dry_run:
            logger.info("Dry run — nothing written.")
        else:
            store_rows(session, rows)
            logger.info("Wrote %d rows to speech_fidelity.", len(rows))
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
