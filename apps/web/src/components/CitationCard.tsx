"use client";

import Link from "next/link";
import type { AskBlock } from "@/lib/types";
import { formatOffset } from "@/lib/tv";

type Citation = Extract<AskBlock, { type: "citation" }>;

/**
 * The citation block (PRD §5): accent left border distinguishes evidence from
 * LLM prose; carries speaker, date, agenda context, the quoted excerpt, and a
 * play affordance that jumps the video/transcript.
 */
export default function CitationCard({
  citation,
  highlighted,
  sameMeeting,
  onJump,
}: {
  citation: Citation;
  highlighted: boolean;
  sameMeeting: boolean;
  onJump: () => void;
}) {
  const date = new Date(citation.meetingDate).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  const body = (
    <>
      <div className="flex items-baseline gap-2">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-white">
          {citation.marker}
        </span>
        <span className="truncate text-sm font-semibold">{citation.speaker}</span>
        <span className="ml-auto shrink-0 text-xs text-gray-400">{date}</span>
      </div>
      {citation.agendaTitle && (
        <p className="mt-1 truncate text-xs text-gray-500">{citation.agendaTitle}</p>
      )}
      <blockquote className="mt-2 border-l-2 border-gray-200 pl-2 text-xs leading-relaxed text-gray-700">
        “{citation.quote}”
      </blockquote>
      <div className="mt-2 flex items-center gap-3 text-xs">
        <span className="inline-flex items-center gap-1 font-medium text-accent">
          ▶ {sameMeeting ? "Jump to moment" : "Open meeting"}
          {citation.startPos !== null && (
            <span className="text-gray-400">({formatOffset(citation.startPos)})</span>
          )}
        </span>
        {citation.tvUrl && (
          <a
            href={citation.tvUrl}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-gray-400 underline-offset-2 hover:text-gray-600 hover:underline"
          >
            Senedd.tv ↗
          </a>
        )}
      </div>
    </>
  );

  const className = `block w-full rounded-lg border bg-white p-3 text-left shadow-sm transition ${
    highlighted ? "border-accent ring-2 ring-accent/30" : "border-gray-200 hover:border-accent/60"
  }`;

  // Same meeting → jump in place; other meeting → navigate with a deep link.
  return sameMeeting ? (
    <button onClick={onJump} className={className}>
      {body}
    </button>
  ) : (
    <Link
      href={`/meetings/${citation.meetingId}?speech=${citation.speechId}`}
      className={className}
    >
      {body}
    </Link>
  );
}
