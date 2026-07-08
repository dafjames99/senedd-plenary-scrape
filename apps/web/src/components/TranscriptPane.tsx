"use client";

import { useEffect, useRef } from "react";
import type { Transcript } from "@/lib/types";
import { formatOffset } from "@/lib/tv";

/**
 * Bottom-right quadrant: the transcript, grouped by agenda item, built from
 * speeches (text) + speech_parts timing (startPos chips). Clicking a speech
 * jumps the video; the followed highlight tracks the virtual clock.
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

  let lastAgenda: string | null = null;

  return (
    <div ref={containerRef} className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
      {transcript.speeches.length === 0 && (
        <p className="text-sm text-gray-500">No speeches reconstructed for this meeting.</p>
      )}
      {transcript.speeches.map((s) => {
        const showAgendaHeader = s.agendaItemId !== lastAgenda;
        lastAgenda = s.agendaItemId;
        const isActive = s.speechId === activeSpeechId;
        const isFollowed = s.speechId === highlightId;
        return (
          <div key={s.speechId}>
            {showAgendaHeader && (
              <h2 className="sticky top-0 -mx-2 mb-2 mt-6 bg-paper/95 px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500 backdrop-blur first:mt-0">
                {s.agendaTitle}
              </h2>
            )}
            <article
              data-speech-id={s.speechId}
              onClick={() => onSelect(s.speechId)}
              className={`mb-3 cursor-pointer rounded-lg border p-4 transition ${
                isFollowed
                  ? "border-accent bg-red-50/50 shadow-sm"
                  : isActive
                    ? "border-accent/50 bg-white"
                    : "border-transparent bg-white hover:border-gray-200"
              }`}
            >
              <header className="mb-1.5 flex items-baseline gap-2">
                <span className="text-sm font-semibold">{s.speakerName}</span>
                {s.startPos !== null && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(s.speechId);
                    }}
                    className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] text-gray-500 transition hover:bg-red-50 hover:text-accent"
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
                    className="ml-auto text-[11px] text-gray-400 underline-offset-2 hover:text-gray-600 hover:underline"
                  >
                    Senedd.tv ↗
                  </a>
                )}
              </header>
              <p className="whitespace-pre-line text-sm leading-relaxed text-gray-800">
                {s.text}
              </p>
            </article>
          </div>
        );
      })}
    </div>
  );
}
