"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { MeetingSummary } from "@/lib/types";
import AskPanel from "./AskPanel";

/**
 * Left quadrant: one input, two modes. "Meetings" filters plenaries by
 * name/date (search-as-you-type against /api/meetings); "Ask" sends the input
 * to the LLM/MCP route and renders typed blocks (PRD §1, §5).
 */
export default function SearchPane({
  initialMeetings,
  currentMeetingId,
  onCite,
}: {
  initialMeetings: MeetingSummary[];
  currentMeetingId: number;
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
      <header className="border-b border-gray-200 p-4 pb-3">
        <h1 className="text-lg font-semibold tracking-tight">
          <span className="text-accent">Senedd</span> Record Explorer
        </h1>
        <div className="mt-3 flex gap-1 rounded-lg bg-gray-100 p-1 text-sm">
          {(["meetings", "ask"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md px-3 py-1.5 font-medium capitalize transition ${
                mode === m ? "bg-white shadow-sm" : "text-gray-500 hover:text-gray-800"
              }`}
            >
              {m === "ask" ? "Ask the record" : "Meetings"}
            </button>
          ))}
        </div>
      </header>

      {mode === "meetings" ? (
        <>
          <div className="border-b border-gray-100 p-3">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by agenda topic, member, or date…"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent"
            />
          </div>
          <ul className="min-h-0 flex-1 divide-y divide-gray-100 overflow-y-auto">
            {searching && meetings.length === 0 && (
              <li className="p-4 text-sm text-gray-500">Searching…</li>
            )}
            {!searching && meetings.length === 0 && (
              <li className="p-4 text-sm text-gray-500">No meetings match.</li>
            )}
            {meetings.map((m) => (
              <li key={m.meetingId}>
                <Link
                  href={`/meetings/${m.meetingId}`}
                  className={`block p-4 transition hover:bg-gray-50 ${
                    m.meetingId === currentMeetingId ? "border-l-2 border-accent bg-red-50/40" : ""
                  }`}
                >
                  <div className="flex items-baseline justify-between">
                    <span className="font-medium">
                      Plenary,{" "}
                      {new Date(m.meetingDate).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "long",
                        year: "numeric",
                      })}
                    </span>
                    <span className="text-xs text-gray-400">{m.speechCount} speeches</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-gray-500">
                    {m.agendaItems.join(" · ")}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <AskPanel onCite={onCite} currentMeetingId={currentMeetingId} />
      )}
    </div>
  );
}
