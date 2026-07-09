"use client";

import { useEffect, useRef } from "react";
import type { Transcript, TranscriptSpeech } from "@/lib/types";
import { splitAgendaTitle } from "@/lib/agenda";
import { formatOffset } from "@/lib/tv";

/**
 * Bottom-right quadrant: the transcript as one continuous script page on
 * parchment — no per-speech cards. Speeches are grouped into agenda sections;
 * each section's scene-heading rule sticks to the top of the scroll while the
 * section is in view (so the current item is never out of sight), echoed by a
 * marginalia numeral pinned in the left gutter. Speaker turns open with a bold
 * serif name and time / Senedd.tv markers inline beside it. Clicking a turn
 * jumps the video; the followed highlight (virtual clock) is a plum rule +
 * wash on the turn.
 */
export default function TranscriptPane({
  transcript,
  activeSpeechId,
  followedSpeechId,
  showWatchLinks,
  onSelect,
}: {
  transcript: Transcript;
  activeSpeechId: number | null;
  followedSpeechId: number | null;
  showWatchLinks: boolean;
  onSelect: (speechId: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const highlightId = followedSpeechId ?? activeSpeechId;

  // Keep the highlighted speech in view.
  useEffect(() => {
    if (highlightId === null || !containerRef.current) return;
    const el = containerRef.current.querySelector(`[data-speech-id="${highlightId}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightId]);

  // Consecutive runs of the same agenda item form a section.
  const sections: {
    agendaItemId: string;
    agendaTitle: string;
    speeches: TranscriptSpeech[];
  }[] = [];
  for (const s of transcript.speeches) {
    const last = sections[sections.length - 1];
    if (last && last.agendaItemId === s.agendaItemId) last.speeches.push(s);
    else
      sections.push({
        agendaItemId: s.agendaItemId,
        agendaTitle: s.agendaTitle,
        speeches: [s],
      });
  }

  return (
    <div ref={containerRef} className="min-h-0 flex-1 overflow-y-auto bg-parchment px-8 py-6">
      <div className="mx-auto max-w-3xl">
        {transcript.speeches.length === 0 && (
          <p className="text-sm text-ink/50">No speeches reconstructed for this meeting.</p>
        )}
        {sections.map((sec, i) => {
          const { number } = splitAgendaTitle(sec.agendaTitle);
          return (
            <section key={`${sec.agendaItemId}-${i}`} className="pt-5 first:pt-0">
              <h2 className="sticky top-0 z-10 -mx-4 flex items-center gap-3 bg-parchment/95 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-plum backdrop-blur">
                <span aria-hidden className="h-px min-w-6 flex-1 bg-plum/25" />
                <span className="text-center">{sec.agendaTitle}</span>
                <span aria-hidden className="h-px min-w-6 flex-1 bg-plum/25" />
              </h2>
              <div className="relative flex gap-2 pt-1">
                {/* Marginalia gutter: the item's official number rides the scroll
                    beside the text, over a hairline spanning the section. */}
                <div aria-hidden className="w-8 shrink-0">
                  <span className="absolute bottom-2 left-4 top-1 w-px -translate-x-1/2 bg-plum/15" />
                  <span className="sticky top-10 z-[1] flex justify-center bg-parchment py-1 font-serif text-xs font-semibold text-plum/50">
                    {number ?? "·"}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  {sec.speeches.map((s) => {
                    const isActive = s.speechId === activeSpeechId;
                    const isFollowed = s.speechId === highlightId;
                    return (
                      <article
                        key={s.speechId}
                        data-speech-id={s.speechId}
                        onClick={() => onSelect(s.speechId)}
                        className={`-mx-3 mb-2 cursor-pointer rounded-sm border-l-2 px-3 py-2 transition ${
                          isFollowed
                            ? "border-plum bg-plum/[0.07]"
                            : isActive
                              ? "border-heather bg-plum/[0.04]"
                              : "border-transparent hover:bg-plum/[0.03]"
                        }`}
                      >
                        <header className="flex flex-wrap items-baseline gap-x-2.5">
                          <span className="font-serif text-[15px] font-bold text-plum-deep">
                            {s.speakerName}
                          </span>
                          {s.startPos !== null && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onSelect(s.speechId);
                              }}
                              className="font-mono text-[11px] text-plum/50 transition hover:text-plum"
                              title="Jump video to this speech"
                            >
                              ▶ {formatOffset(s.startPos)}
                            </button>
                          )}
                          {showWatchLinks && s.tvUrl && (
                            <a
                              href={s.tvUrl}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-[11px] text-plum/40 underline-offset-2 transition hover:text-plum hover:underline"
                            >
                              Senedd.tv ↗
                            </a>
                          )}
                        </header>
                        <p className="mt-0.5 whitespace-pre-line font-serif text-[15px] leading-7 text-ink/90">
                          {s.text}
                        </p>
                      </article>
                    );
                  })}
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
