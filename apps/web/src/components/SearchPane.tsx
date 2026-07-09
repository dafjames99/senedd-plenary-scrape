"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { AgendaTocEntry, MeetingSummary } from "@/lib/types";
import AskPanel from "./AskPanel";

/**
 * Left quadrant: one input, two modes. "Meetings" filters plenaries by
 * name/date (search-as-you-type against /api/meetings); "Ask" sends the input
 * to the LLM/MCP route and renders typed blocks (PRD §1, §5). The selected
 * meeting card expands a ToC of its agenda items; clicking one jumps the
 * video/transcript to that item's first speech (via onCite).
 */
export default function SearchPane({
  initialMeetings,
  currentMeetingId,
  agendaToc,
  activeAgendaId,
  onCite,
}: {
  initialMeetings: MeetingSummary[];
  currentMeetingId: number;
  agendaToc: AgendaTocEntry[];
  activeAgendaId: string | null;
  onCite: (speechId: number) => void;
}) {
  const [mode, setMode] = useState<"meetings" | "ask">("meetings");
  const [query, setQuery] = useState("");
  const [meetings, setMeetings] = useState(initialMeetings);
  const [searching, setSearching] = useState(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (mode !== "meetings") return;
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(`/api/meetings?q=${encodeURIComponent(query)}`);
        if (res.ok) {
          const data = await res.json();
          setMeetings(data.meetings);
        }
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [query, mode]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="bg-gradient-to-br from-plum-deep via-plum to-heather px-4 pb-4 pt-5">
        <p className="text-[10px] font-medium uppercase tracking-[0.22em] text-parchment/60">
          Y Cofnod · The Record
        </p>
        <h1 className="mt-1 font-serif text-2xl font-light tracking-wide text-parchment">
          Senedd Record Explorer
        </h1>
        <div className="mt-4 flex gap-1 rounded-lg bg-white/10 p-1 text-sm">
          {(["meetings", "ask"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md px-3 py-1.5 font-medium capitalize transition ${
                mode === m
                  ? "bg-parchment text-plum-deep shadow-sm"
                  : "text-parchment/70 hover:text-parchment"
              }`}
            >
              {m === "ask" ? "Ask the record" : "Meetings"}
            </button>
          ))}
        </div>
      </header>

      {mode === "meetings" ? (
        <>
          <div className="border-b border-plum/10 p-3">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by agenda topic, member, or date…"
              className="w-full rounded-md border border-plum/20 bg-white px-3 py-2 text-sm outline-none focus:border-plum focus:ring-1 focus:ring-plum"
            />
          </div>
          <ul className="min-h-0 flex-1 divide-y divide-plum/10 overflow-y-auto">
            {searching && meetings.length === 0 && (
              <li className="p-4 text-sm text-ink/50">Searching…</li>
            )}
            {!searching && meetings.length === 0 && (
              <li className="p-4 text-sm text-ink/50">No meetings match.</li>
            )}
            {meetings.map((m) => {
              const selected = m.meetingId === currentMeetingId;
              return (
                <li key={m.meetingId}>
                  <Link
                    href={`/meetings/${m.meetingId}`}
                    className={`block px-4 py-3 transition ${
                      selected ? "bg-plum-deep" : "hover:bg-plum/5"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-heather">
                        {m.meetingType}
                      </span>
                      {m.voteCount > 0 ? (
                        <span className="rounded-full bg-gilt-wash px-2 py-0.5 text-[10px] font-semibold text-gilt">
                          {m.voteCount} vote{m.voteCount === 1 ? "" : "s"}
                        </span>
                      ) : (
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                            selected ? "bg-white/10 text-parchment/50" : "bg-ink/5 text-ink/40"
                          }`}
                        >
                          no votes
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 flex items-baseline justify-between gap-2">
                      <span
                        className={`font-serif text-[15px] font-semibold ${
                          selected ? "text-parchment" : "text-ink"
                        }`}
                      >
                        {new Date(m.meetingDate).toLocaleDateString("en-GB", {
                          day: "numeric",
                          month: "short",
                          year: "numeric",
                        })}
                      </span>
                      <span
                        className={`text-xs ${selected ? "text-parchment/60" : "text-ink/40"}`}
                      >
                        {m.speechCount} speeches
                      </span>
                    </div>
                  </Link>
                  {selected && agendaToc.length > 0 && (
                    <ol className="bg-plum py-1">
                      {agendaToc.map((a) => {
                        const active = a.agendaItemId === activeAgendaId;
                        return (
                          <li key={a.agendaItemId}>
                            <button
                              onClick={() => onCite(a.firstSpeechId)}
                              className={`flex w-full items-baseline gap-2 border-l-2 px-4 py-1.5 text-left text-xs transition ${
                                active
                                  ? "border-heather bg-white/15 font-medium text-parchment"
                                  : "border-transparent text-parchment/60 hover:bg-white/10 hover:text-parchment"
                              }`}
                              title={a.title}
                            >
                              <span className="w-4 shrink-0 text-right font-serif">
                                {a.number ?? "·"}
                              </span>
                              <span className="min-w-0 flex-1 truncate">{a.title}</span>
                            </button>
                          </li>
                        );
                      })}
                    </ol>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <AskPanel onCite={onCite} currentMeetingId={currentMeetingId} />
      )}
    </div>
  );
}
