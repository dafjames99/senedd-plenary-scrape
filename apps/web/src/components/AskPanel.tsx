"use client";

import { useState } from "react";
import type { AskBlock, AskResponse } from "@/lib/types";
import CitationCard from "./CitationCard";

/**
 * LLM output area (PRD §5, format A): prose paragraphs carry numbered
 * footnote markers; each marker maps 1:1 to a citation card rendered below.
 * Hovering a marker highlights its card; clicking a card (or its play button)
 * jumps the video/transcript to that speech.
 */
export default function AskPanel({
  onCite,
  currentMeetingId,
}: {
  onCite: (speechId: number) => void;
  currentMeetingId: number;
}) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [hoverMarker, setHoverMarker] = useState<number | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || loading) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      setResult((await res.json()) as AskResponse);
    } catch {
      setResult({
        mode: "mock",
        blocks: [{ type: "notice", text: "Request failed — is the server running?" }],
      });
    } finally {
      setLoading(false);
    }
  }

  const citations = (result?.blocks.filter((b) => b.type === "citation") ??
    []) as Extract<AskBlock, { type: "citation" }>[];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <form onSubmit={submit} className="border-b border-plum/10 p-3">
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about the plenary record…"
            className="min-w-0 flex-1 rounded-md border border-plum/20 bg-white px-3 py-2 text-sm outline-none focus:border-plum focus:ring-1 focus:ring-plum"
          />
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="rounded-md bg-plum px-4 py-2 text-sm font-medium text-white transition hover:bg-plum-deep disabled:opacity-40"
          >
            {loading ? "…" : "Ask"}
          </button>
        </div>
      </form>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {!result && !loading && (
          <p className="text-sm text-ink/50">
            Ask a question of the record — e.g.{" "}
            <em>&ldquo;What has been said about rural bus services?&rdquo;</em>. Answers cite
            the speeches they rest on; click a citation to jump the video and transcript to
            that moment.
          </p>
        )}
        {loading && (
          <p className="animate-pulse text-sm text-ink/50">Searching the record…</p>
        )}

        {result?.blocks.map((block, i) => {
          if (block.type === "notice") {
            return (
              <p
                key={i}
                className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs italic text-amber-800"
              >
                {block.text}
              </p>
            );
          }
          if (block.type === "prose") {
            return (
              <div key={i} className="text-sm leading-relaxed">
                {renderProseWithMarkers(block.text, {
                  onHover: setHoverMarker,
                  onClick: (marker) => {
                    const c = citations.find((c) => c.marker === marker);
                    if (c) onCite(c.speechId);
                  },
                })}
              </div>
            );
          }
          return (
            <CitationCard
              key={i}
              citation={block}
              highlighted={hoverMarker === block.marker}
              sameMeeting={block.meetingId === currentMeetingId}
              onJump={() => onCite(block.speechId)}
            />
          );
        })}
      </div>
    </div>
  );
}

/** Split prose on [n] markers and render them as interactive superscripts. */
function renderProseWithMarkers(
  text: string,
  handlers: { onHover: (m: number | null) => void; onClick: (m: number) => void },
) {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (!m) return <span key={i}>{part}</span>;
    const marker = Number(m[1]);
    return (
      <sup key={i}>
        <button
          onMouseEnter={() => handlers.onHover(marker)}
          onMouseLeave={() => handlers.onHover(null)}
          onClick={() => handlers.onClick(marker)}
          className="mx-0.5 rounded bg-plum/10 px-1 font-semibold text-plum transition hover:bg-plum/20"
          title={`Jump to citation ${marker}`}
        >
          {marker}
        </button>
      </sup>
    );
  });
}
