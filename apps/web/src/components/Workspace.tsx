"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MeetingSummary, Transcript } from "@/lib/types";
import { agendaToc } from "@/lib/agenda";
import { useResizable } from "@/hooks/useResizable";
import SearchPane from "./SearchPane";
import VideoPane from "./VideoPane";
import TranscriptPane from "./TranscriptPane";
import ResizeHandle from "./ResizeHandle";

export type VideoMode = "embed" | "link" | "off";

/**
 * Four-quadrant workspace (PRD §1). Owns the sync state:
 *  - activeSpeechId: which speech the video/transcript are focused on
 *  - virtual clock: after a jump we advance a highlight through subsequent
 *    speeches by their relative startPos deltas (one-way sync — the iframe
 *    cannot report playback position; PRD §2).
 */
export default function Workspace({
  transcript,
  initialMeetings,
  initialSpeechId,
  videoMode,
}: {
  transcript: Transcript;
  initialMeetings: MeetingSummary[];
  initialSpeechId: number | null;
  videoMode: VideoMode;
}) {
  const [activeSpeechId, setActiveSpeechId] = useState<number | null>(initialSpeechId);
  const [followedSpeechId, setFollowedSpeechId] = useState<number | null>(null);
  const [following, setFollowing] = useState(true);
  // Virtual clock anchor: clip offset + wall-clock time at the moment of the jump.
  const anchor = useRef<{ startPos: number; wallStart: number } | null>(null);

  const showVideo = videoMode === "embed" && transcript.videoBaseUrl !== null;

  // Draggable quadrant boundaries (persisted per-user). Max sizes track the
  // viewport so a pane can't crowd out its flex-1 partner.
  const leftPane = useResizable({
    storageKey: "senedd:leftWidth",
    axis: "x",
    initial: 460,
    min: 320,
    max: () => Math.min(760, window.innerWidth * 0.6),
  });
  const videoPane = useResizable({
    storageKey: "senedd:videoHeight",
    axis: "y",
    initial: 360,
    min: 200,
    max: () => window.innerHeight * 0.75,
  });

  const jumpTo = useCallback(
    (speechId: number) => {
      const speech = transcript.speeches.find((s) => s.speechId === speechId);
      if (!speech) return;
      setActiveSpeechId(speechId);
      setFollowedSpeechId(speechId);
      if (speech.startPos !== null) {
        anchor.current = { startPos: speech.startPos, wallStart: Date.now() };
      }
    },
    [transcript.speeches],
  );

  // Deep link (?speech=) → jump once on mount.
  useEffect(() => {
    if (initialSpeechId !== null) jumpTo(initialSpeechId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Virtual clock: advance the followed highlight through speech boundaries.
  useEffect(() => {
    if (!showVideo || !following) return;
    const timer = setInterval(() => {
      if (!anchor.current) return;
      const elapsed = (Date.now() - anchor.current.wallStart) / 1000;
      const pos = anchor.current.startPos + elapsed;
      let current: number | null = null;
      for (const s of transcript.speeches) {
        if (s.startPos !== null && s.startPos <= pos) current = s.speechId;
      }
      if (current !== null) setFollowedSpeechId(current);
    }, 1000);
    return () => clearInterval(timer);
  }, [showVideo, following, transcript.speeches]);

  const activeSpeech =
    transcript.speeches.find((s) => s.speechId === activeSpeechId) ?? null;

  // ToC for the LHS: unique agenda items + which one the video/transcript is
  // currently on (the followed highlight when the virtual clock is running).
  const toc = useMemo(() => agendaToc(transcript.speeches), [transcript.speeches]);
  const currentSpeechId = (showVideo ? followedSpeechId : null) ?? activeSpeechId;
  const activeAgendaId =
    transcript.speeches.find((s) => s.speechId === currentSpeechId)?.agendaItemId ?? null;

  return (
    <div className="flex h-full">
      {/* LEFT: search / ask */}
      <div
        className="flex min-w-0 shrink-0 flex-col bg-white"
        style={{ width: leftPane.size }}
      >
        <SearchPane
          initialMeetings={initialMeetings}
          currentMeetingId={transcript.meeting.meetingId}
          agendaToc={toc}
          activeAgendaId={activeAgendaId}
          onCite={jumpTo}
        />
      </div>

      <ResizeHandle
        axis="x"
        dragging={leftPane.dragging}
        onPointerDown={leftPane.onPointerDown}
        label="Resize search panel"
      />

      {/* RIGHT: video (optional) + transcript */}
      <div className="flex min-w-0 flex-1 flex-col">
        {showVideo && (
          <>
            <VideoPane
              baseUrl={transcript.videoBaseUrl!}
              activeSpeech={activeSpeech}
              meetingDate={transcript.meeting.meetingDate}
              following={following}
              onToggleFollow={() => setFollowing((f) => !f)}
              heightPx={videoPane.size}
            />
            <ResizeHandle
              axis="y"
              dragging={videoPane.dragging}
              onPointerDown={videoPane.onPointerDown}
              label="Resize video player"
            />
          </>
        )}
        <TranscriptPane
          transcript={transcript}
          activeSpeechId={activeSpeechId}
          followedSpeechId={showVideo ? followedSpeechId : null}
          showWatchLinks={videoMode !== "off"}
          onSelect={jumpTo}
        />
      </div>
    </div>
  );
}
