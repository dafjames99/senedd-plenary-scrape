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

Two speech filters are computed side by side (see ``--filter``):

* ``raw``        — pure raw-data heuristic: ``contribution_type = 'C'`` with
                   non-empty verbatim. No dependency on downstream classification.
* ``classified`` — the pipeline's own ``row_type = 'SPEECH'`` rows.

Outputs (written to ``--output-dir``, default ``analysis/output/``):

* ``wpm_hist.png``        — WPM distribution, both filters overlaid, normal band shaded.
* ``wpm_scatter.png``     — words vs duration with iso-WPM reference lines; the
                            geometry separates cut-offs (slow) from overruns (fast).
* ``wpm_by_meeting.png``  — per-meeting WPM spread, to see if errors cluster.
* ``wpm_outliers.csv``    — ranked worst offenders with SeneddTV URLs for manual review.

Usage
-----
    uv run python analysis/wpm_fidelity.py
    uv run python analysis/wpm_fidelity.py --filter classified --meeting 15768
    uv run python analysis/wpm_fidelity.py --band-low 110 --band-high 190 --show
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine

from src import setup_logging
from src.db.settings import settings

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

_TAG_RE = re.compile(r"<[^>]+>")


def count_words(text: Optional[str]) -> int:
    """Count whitespace-delimited tokens, stripping any residual HTML tags.

    Idempotent on already-cleaned text and a safe fallback for raw verbatim that
    never reached the cleaning phase.

    Args:
        text: Verbatim contribution text, possibly ``None`` or tag-wrapped.

    Returns:
        Word count (``0`` for empty/``None`` input).
    """
    if not isinstance(text, str) or not text:
        return 0
    return len(_TAG_RE.sub(" ", text).split())


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


def _plot_hist(frames: dict[str, pd.DataFrame], band, path: Path, clip: float) -> None:
    """Overlaid WPM histograms for each filter with the normal band shaded."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    for label, fr in frames.items():
        ax.hist(fr["wpm"].clip(upper=clip), bins=80, alpha=0.5, label=f"{label} (n={len(fr)})")
    ax.axvspan(band[0], band[1], color="green", alpha=0.10,
               label=f"plausible band {band[0]:.0f}–{band[1]:.0f}")
    ax.set_xlabel("words per minute (clipped at %.0f)" % clip)
    ax.set_ylabel("contributions")
    ax.set_title("WPM distribution of Senedd contributions")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _plot_scatter(df: pd.DataFrame, band, path: Path) -> None:
    """words vs duration with iso-WPM lines; geometry separates the error modes."""
    import matplotlib.pyplot as plt
    import numpy as np

    colors = {
        "ok": "#9ecae1",
        "low_confidence": "#d9d9d9",
        "too_slow": "#d62728",
        "too_fast": "#ff7f0e",
        "likely_break": "#7f7f7f",
    }
    fig, ax = plt.subplots(figsize=(10, 7))
    for flag, color in colors.items():
        sub = df[df["flag"] == flag]
        if sub.empty:
            continue
        ax.scatter(sub["gap_seconds"], sub["words"], s=10, alpha=0.5,
                   color=color, label=f"{flag} (n={len(sub)})")

    gap = np.array([df["gap_seconds"].min(), df["gap_seconds"].max()])
    for wpm in (band[0], 150, band[1]):
        ax.plot(gap, wpm * gap / 60.0, "--", lw=1, color="black", alpha=0.4)
        ax.annotate(f"{wpm:.0f} wpm", (gap[1], wpm * gap[1] / 60.0),
                    fontsize=8, alpha=0.6)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("duration to next contribution (seconds, log)")
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
    data = [df.loc[df["meeting_id"] == m, "wpm"].clip(upper=band[1] * 2) for m in order]
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--filter", choices=["raw", "classified", "both"],
                        default="both", help="Which speech filter to report/plot.")
    parser.add_argument("--meeting", type=int, default=None,
                        help="Restrict the audit to a single meeting_id.")
    parser.add_argument("--band-low", type=float, default=110.0,
                        help="Lower bound of plausible WPM.")
    parser.add_argument("--band-high", type=float, default=190.0,
                        help="Upper bound of plausible WPM.")
    parser.add_argument("--min-words", type=int, default=5,
                        help="Below this word count, WPM is low-confidence.")
    parser.add_argument("--min-gap", type=float, default=4.0,
                        help="Below this gap (s), WPM is low-confidence.")
    parser.add_argument("--break-gap", type=float, default=120.0,
                        help="Low-WPM gap over this spanning an agenda change is a break.")
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
    if not outliers.empty:
        logger.info("Worst 10 offenders:\n%s",
                    outliers[["lens", "meeting_id", "words", "gap_seconds",
                              "wpm", "flag", "spoken_url"]].head(10).to_string(index=False))
    if args.show:
        import matplotlib.pyplot as plt
        plt.show()


if __name__ == "__main__":
    main()
