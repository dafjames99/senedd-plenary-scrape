"""Words-per-minute fidelity audit of the raw Senedd transcript feed.

This is a *read-only investigation* of the data that feeds the pipeline, not a
pipeline stage. It looks for contributions whose transcribed text is implausible
for the time they were allotted on the recording — the hallmark of a transcript
that was cut off mid-speech (too few words over a long gap → very low WPM) or one
whose timestamps are wrong / overlapping (too many words over a short gap → very
high WPM).

Method
------
A contribution has no end time, so its duration is inferred as the gap to the
*next* contribution within the same meeting::

    duration = LEAD(contribution_time) - contribution_time      (per meeting,
                                                                 ordered by
                                                                 contribution_order_id)

``contribution_time`` is a full datetime and is equivalent to the ``startPos``
seconds parameter on the SeneddTV URL (verified), so the wall-clock column is
used directly. Word counts come from ``clean_contributions.contribution_verbatim_clean``
(HTML stripped) — the raw verbatim is wrapped in ``<p>`` tags that would inflate
counts — falling back to a tag-strip of the raw field where no clean row exists.

Two levels (see ``--level``):

* ``speech`` (default) — the cleaned, served-unit view. Aggregating to the speech
  removes the contribution-level interjection artifact (a brief interjection's
  near-identical timestamp no longer collapses a duration). Mirrors the persisted
  ``speech_fidelity`` classification from :mod:`senedd_data.fidelity`, so the charts and
  suspect CSV match what the MCP surfaces via ``is_suspect``.
* ``contribution`` — the original lens. Computes two filters side by side: ``raw``
  (``contribution_type = 'C'`` with non-empty verbatim, no downstream dependency)
  and ``classified`` (the pipeline's ``row_type = 'SPEECH'``). Kept because it
  shows the artifact that motivates working at speech level.

Outputs (written to ``--output-dir``, default ``analysis/output/``):

* speech level: ``wpm_speech_hist.png``, ``wpm_speech_scatter.png``,
  ``wpm_speech_by_meeting.png``, ``speech_fidelity_suspects.csv``.
* contribution level: ``wpm_hist.png``, ``wpm_scatter.png``,
  ``wpm_by_meeting.png``, ``wpm_outliers.csv``.

To *persist* the speech-level flags (for the MCP), run the pass in
:mod:`senedd_data.fidelity`; this script is the read-only visual/CSV companion.

Usage
-----
    uv run python analysis/wpm_fidelity.py                       # speech level
    uv run python analysis/wpm_fidelity.py --level contribution --meeting 15768
    uv run python analysis/wpm_fidelity.py --meeting 15768 --show
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine

from senedd_data import setup_logging
from senedd_data.fidelity import (
    DEFAULT_THRESHOLDS,
    SPEECH_FIDELITY_SQL,
    classify,
    count_words,
    ends_midsentence,
)
from senedd_data.settings import settings

logger = logging.getLogger(__name__)

# Pull every timestamped contribution with the fields needed to infer duration.
# LEAD is computed only over rows that *have* a timestamp, so the 1,193 rows with
# a NULL contribution_time collapse into the preceding row's gap rather than
# breaking the chain. agenda_changed lets us tell a genuine session break (gap
# spans an agenda transition) from a truncated transcript.
WPM_QUERY = """
WITH base AS (
    SELECT
        rc.contribution_id,
        rc.meeting_id,
        rc.meeting_date,
        rc.contribution_order_id,
        rc.contribution_type,
        rc.agenda_item_id,
        rc.contribution_time,
        rc.contribution_spoken_seneddtv          AS spoken_url,
        cc.row_type                              AS row_type,
        COALESCE(cl.contribution_verbatim_clean,
                 rc.contribution_verbatim)        AS verbatim
    FROM raw_contributions rc
    LEFT JOIN clean_contributions      cl USING (contribution_id)
    LEFT JOIN classified_contributions cc USING (contribution_id)
    WHERE rc.contribution_time IS NOT NULL
),
seq AS (
    SELECT
        base.*,
        LEAD(contribution_time) OVER w AS next_time,
        LEAD(agenda_item_id)    OVER w AS next_agenda
    FROM base
    WINDOW w AS (PARTITION BY meeting_id ORDER BY contribution_order_id)
)
SELECT
    contribution_id,
    meeting_id,
    meeting_date,
    contribution_order_id,
    contribution_type,
    CAST(row_type AS TEXT)                                   AS row_type,
    agenda_item_id,
    contribution_time,
    EXTRACT(EPOCH FROM (next_time - contribution_time))      AS gap_seconds,
    (agenda_item_id IS DISTINCT FROM next_agenda)            AS agenda_changed,
    spoken_url,
    verbatim
FROM seq
ORDER BY meeting_id, contribution_order_id
"""

def load_frame(meeting: Optional[int]) -> pd.DataFrame:
    """Load the WPM base table and derive per-contribution word count and rate.

    Args:
        meeting: Optional ``meeting_id`` to restrict the audit to one meeting.

    Returns:
        DataFrame with ``words``, ``gap_seconds``, ``wpm`` and the membership
        flags ``is_raw_speech`` / ``is_classified_speech``. Rows without a valid
        forward gap (last row of a meeting, or non-positive gap) are dropped.
    """
    engine = create_engine(settings.database_url)
    try:
        df = pd.read_sql(WPM_QUERY, engine)
    finally:
        engine.dispose()

    if meeting is not None:
        df = df[df["meeting_id"] == meeting].copy()

    df["words"] = df["verbatim"].map(count_words)

    # Speech membership under each lens, computed side by side.
    df["is_raw_speech"] = (df["contribution_type"] == "C") & (df["words"] > 0)
    df["is_classified_speech"] = df["row_type"] == "SPEECH"

    df["gap_seconds"] = pd.to_numeric(df["gap_seconds"], errors="coerce")
    valid = df["gap_seconds"].notna() & (df["gap_seconds"] > 0)
    dropped = int((~valid).sum())
    if dropped:
        logger.info("Dropped %d rows with no positive forward gap "
                    "(meeting boundaries / non-monotone timestamps).", dropped)
    df = df[valid].copy()

    df["wpm"] = df["words"] / (df["gap_seconds"] / 60.0)
    return df


def load_speech_frame(meeting: Optional[int]) -> pd.DataFrame:
    """Load the speech-level fidelity view — the cleaned, served-unit signal.

    Reuses the canonical query and classifier from ``senedd_data.fidelity`` so the
    chart matches the persisted ``speech_fidelity`` flags exactly. Aggregating to
    the speech removes the contribution-level interjection artifact (a brief
    interjection's near-identical timestamp no longer collapses a duration).

    Args:
        meeting: Optional ``meeting_id`` to restrict to one meeting.

    Returns:
        DataFrame with ``words``, ``gap_seconds``, ``wpm``, ``flag``,
        ``is_suspect``, ``ends_midsentence`` and ``spoken_url`` per speech.
    """
    engine = create_engine(settings.database_url)
    try:
        df = pd.read_sql(SPEECH_FIDELITY_SQL, engine)
    finally:
        engine.dispose()

    if meeting is not None:
        df = df[df["meeting_id"] == meeting].copy()

    df["words"] = df["speech_text"].map(count_words)
    df["gap_seconds"] = pd.to_numeric(df["gap_seconds"], errors="coerce")
    df["ends_midsentence"] = df["speech_text"].map(ends_midsentence)

    triples = [
        classify(w, g if pd.notna(g) else None, m, DEFAULT_THRESHOLDS)
        for w, g, m in zip(df["words"], df["gap_seconds"], df["ends_midsentence"])
    ]
    df["flag"] = [t[0] for t in triples]
    df["wpm"] = [t[1] for t in triples]
    df["is_suspect"] = [t[2] for t in triples]
    return df


def flag_outliers(
    df: pd.DataFrame,
    band_low: float,
    band_high: float,
    min_words: int,
    min_gap: float,
    break_gap: float,
) -> pd.DataFrame:
    """Classify each speech row's WPM against a plausible-speech band.

    Args:
        df: Speech-only frame with ``wpm``, ``words``, ``gap_seconds``,
            ``agenda_changed``.
        band_low: Lower bound of plausible WPM.
        band_high: Upper bound of plausible WPM.
        min_words: Below this, WPM is too noisy to trust (low confidence).
        min_gap: Below this gap (seconds), WPM is too noisy to trust.
        break_gap: A low-WPM row whose gap exceeds this and spans an agenda
            change is attributed to a session break rather than a truncation.

    Returns:
        Copy of ``df`` with ``flag`` and ``deviation`` columns added.
    """
    out = df.copy()
    low_confidence = (out["words"] < min_words) | (out["gap_seconds"] < min_gap)
    too_fast = out["wpm"] > band_high
    too_slow = out["wpm"] < band_low
    likely_break = too_slow & out["agenda_changed"] & (out["gap_seconds"] > break_gap)

    flag = pd.Series("ok", index=out.index)
    flag[too_fast] = "too_fast"
    flag[too_slow] = "too_slow"
    flag[likely_break] = "likely_break"
    flag[low_confidence] = "low_confidence"
    out["flag"] = flag

    # Deviation = multiplicative distance from the nearest band edge; ranks the
    # worst offenders regardless of direction.
    dist_high = out["wpm"] / band_high
    dist_low = band_low / out["wpm"].clip(lower=1e-9)
    out["deviation"] = pd.concat([dist_high, dist_low], axis=1).max(axis=1)
    out.loc[flag.isin(["ok", "low_confidence"]), "deviation"] = 0.0
    return out


def _plot_hist(frames: dict[str, pd.DataFrame], band, path: Path, clip: float,
               unit: str = "contribution") -> None:
    """Overlaid WPM histograms for each filter with the normal band shaded."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    for label, fr in frames.items():
        wpm = fr["wpm"].dropna()
        ax.hist(wpm.clip(upper=clip), bins=80, alpha=0.5, label=f"{label} (n={len(wpm)})")
    ax.axvspan(band[0], band[1], color="green", alpha=0.10,
               label=f"plausible band {band[0]:.0f}–{band[1]:.0f}")
    ax.set_xlabel("words per minute (clipped at %.0f)" % clip)
    ax.set_ylabel(f"{unit}s")
    ax.set_title(f"WPM distribution of Senedd {unit}s")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _plot_scatter(df: pd.DataFrame, band, path: Path, unit: str = "contribution") -> None:
    """words vs duration with iso-WPM lines; geometry separates the error modes."""
    import matplotlib.pyplot as plt
    import numpy as np

    colors = {
        "ok": "#9ecae1",
        "low_confidence": "#d9d9d9",
        "too_slow": "#d62728",
        "too_fast": "#ff7f0e",
        "likely_break": "#7f7f7f",
        "broken_timestamp": "#000000",
        "no_duration": "#d9d9d9",
    }
    # Only points with a positive gap and word count can sit on the log–log plane.
    plot_df = df[(df["gap_seconds"] > 0) & (df["words"] > 0)]
    fig, ax = plt.subplots(figsize=(10, 7))
    for flag in plot_df["flag"].unique():
        sub = plot_df[plot_df["flag"] == flag]
        ax.scatter(sub["gap_seconds"], sub["words"], s=10, alpha=0.5,
                   color=colors.get(flag, "#7f7f7f"), label=f"{flag} (n={len(sub)})")

    gap = np.array([plot_df["gap_seconds"].min(), plot_df["gap_seconds"].max()])
    for wpm in (band[0], 150, band[1]):
        ax.plot(gap, wpm * gap / 60.0, "--", lw=1, color="black", alpha=0.4)
        ax.annotate(f"{wpm:.0f} wpm", (gap[1], wpm * gap[1] / 60.0),
                    fontsize=8, alpha=0.6)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(f"duration to next {unit} (seconds, log)")
    ax.set_ylabel("words spoken (log)")
    ax.set_title("Words vs duration — below the band = too fast, above = too slow")
    ax.legend(markerscale=2)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _plot_by_meeting(df: pd.DataFrame, band, path: Path) -> None:
    """Per-meeting WPM spread to reveal whether fidelity issues cluster."""
    import matplotlib.pyplot as plt

    order = sorted(df["meeting_id"].unique())
    data = [df.loc[df["meeting_id"] == m, "wpm"].dropna().clip(upper=band[1] * 2)
            for m in order]
    fig, ax = plt.subplots(figsize=(max(8, len(order) * 0.35), 6))
    ax.boxplot(data, showfliers=True, flierprops=dict(marker=".", markersize=3))
    ax.axhspan(band[0], band[1], color="green", alpha=0.10)
    ax.set_xticks(range(1, len(order) + 1))
    ax.set_xticklabels([str(m) for m in order], rotation=90, fontsize=7)
    ax.set_xlabel("meeting_id")
    ax.set_ylabel("words per minute")
    ax.set_title("Per-meeting WPM spread")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def summarise(label: str, df: pd.DataFrame, band) -> None:
    """Log headline statistics and the worst offenders for one filter."""
    n = len(df)
    if n == 0:
        logger.warning("[%s] no rows.", label)
        return
    q = df["wpm"].quantile([0.05, 0.5, 0.95])
    counts = df["flag"].value_counts().to_dict()
    flagged = int((df["flag"].isin(["too_slow", "too_fast"])).sum())
    logger.info(
        "[%s] n=%d | median %.0f wpm | p5 %.0f | p95 %.0f | "
        "flagged %d (%.1f%%) | %s",
        label, n, q[0.5], q[0.05], q[0.95], flagged, 100 * flagged / n, counts,
    )


def _run_contribution(args, band) -> None:
    """Contribution-level audit (raw vs classified), the original lens.

    Retained for reference: it shows the interjection artifact that motivates
    aggregating to speech level (see ``--level speech``, the default).
    """
    df = load_frame(args.meeting)
    logger.info("Loaded %d contributions with a valid duration.", len(df))

    lenses = {"raw": df[df["is_raw_speech"]], "classified": df[df["is_classified_speech"]]}
    if args.filter != "both":
        lenses = {args.filter: lenses[args.filter]}

    flagged = {
        label: flag_outliers(fr, args.band_low, args.band_high,
                             args.min_words, args.min_gap, args.break_gap)
        for label, fr in lenses.items()
    }
    for label, fr in flagged.items():
        summarise(label, fr, band)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_frames = {label: fr for label, fr in lenses.items()}
    _plot_hist(plot_frames, band, args.output_dir / "wpm_hist.png", args.hist_clip)

    # Scatter and per-meeting plots use the primary lens (raw, unless overridden).
    primary = "raw" if "raw" in flagged else next(iter(flagged))
    _plot_scatter(flagged[primary], band, args.output_dir / "wpm_scatter.png")
    if args.meeting is None:
        _plot_by_meeting(lenses[primary], band, args.output_dir / "wpm_by_meeting.png")

    # Actionable artifact: every flagged row across lenses, worst first, with URL.
    cols = ["meeting_id", "meeting_date", "contribution_id", "contribution_order_id",
            "words", "gap_seconds", "wpm", "flag", "deviation", "agenda_changed",
            "spoken_url"]
    outliers = pd.concat(
        [fr.assign(lens=label) for label, fr in flagged.items()], ignore_index=True
    )
    outliers = outliers[outliers["flag"].isin(["too_slow", "too_fast"])]
    outliers = outliers.sort_values("deviation", ascending=False)
    csv_path = args.output_dir / "wpm_outliers.csv"
    outliers[["lens"] + cols].to_csv(csv_path, index=False)
    logger.info("Wrote %d flagged rows to %s", len(outliers), csv_path)
    logger.info("Charts written to %s", args.output_dir)


def _run_speech(args, band) -> None:
    """Speech-level audit — the cleaned, served-unit signal (the default lens).

    Mirrors the persisted ``speech_fidelity`` classification, so the charts and
    the suspect CSV match what the MCP surfaces via ``is_suspect``.
    """
    df = load_speech_frame(args.meeting)
    logger.info("Loaded %d speeches.", len(df))

    summarise("speech", df, band)
    counts = df["flag"].value_counts().to_dict()
    logger.info("  flags: %s", counts)
    logger.info("  ends mid-sentence: %d | IS_SUSPECT: %d (%.1f%%)",
                int(df["ends_midsentence"].sum()), int(df["is_suspect"].sum()),
                100 * df["is_suspect"].mean())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _plot_hist({"speech": df}, band, args.output_dir / "wpm_speech_hist.png",
               args.hist_clip, unit="speech")
    _plot_scatter(df, band, args.output_dir / "wpm_speech_scatter.png", unit="speech")
    if args.meeting is None:
        _plot_by_meeting(df, band, args.output_dir / "wpm_speech_by_meeting.png")

    cols = ["meeting_id", "speech_id", "speaker_name", "words", "gap_seconds",
            "wpm", "flag", "ends_midsentence", "spoken_url"]
    suspects = df[df["is_suspect"]].sort_values("wpm", na_position="last")
    csv_path = args.output_dir / "speech_fidelity_suspects.csv"
    suspects[cols].to_csv(csv_path, index=False)
    logger.info("Wrote %d suspect speeches to %s", len(suspects), csv_path)
    logger.info("Charts written to %s", args.output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--level", choices=["speech", "contribution"], default="speech",
                        help="speech: cleaned served-unit signal (default). "
                             "contribution: original raw/classified lens.")
    parser.add_argument("--filter", choices=["raw", "classified", "both"],
                        default="both", help="(contribution level) which speech filter.")
    parser.add_argument("--meeting", type=int, default=None,
                        help="Restrict the audit to a single meeting_id.")
    parser.add_argument("--band-low", type=float, default=110.0,
                        help="Lower bound of plausible WPM (visual band).")
    parser.add_argument("--band-high", type=float, default=190.0,
                        help="Upper bound of plausible WPM (visual band).")
    parser.add_argument("--min-words", type=int, default=5,
                        help="(contribution level) below this, WPM is low-confidence.")
    parser.add_argument("--min-gap", type=float, default=4.0,
                        help="(contribution level) below this gap (s), WPM is low-confidence.")
    parser.add_argument("--break-gap", type=float, default=120.0,
                        help="(contribution level) low-WPM gap over this spanning an "
                             "agenda change is a break.")
    parser.add_argument("--hist-clip", type=float, default=400.0,
                        help="Clip WPM at this value for the histogram x-axis.")
    parser.add_argument("--output-dir", type=Path, default=ROOT_DIR / "analysis" / "output")
    parser.add_argument("--show", action="store_true", help="Display plots interactively.")
    args = parser.parse_args()

    setup_logging()
    band = (args.band_low, args.band_high)

    import matplotlib
    if not args.show:
        matplotlib.use("Agg")

    if args.level == "speech":
        _run_speech(args, band)
    else:
        _run_contribution(args, band)

    if args.show:
        import matplotlib.pyplot as plt
        plt.show()


if __name__ == "__main__":
    main()
