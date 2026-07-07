"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { MeetingSummary, Transcript } from "@/lib/types";
import SearchPane from "./SearchPane";
import VideoPane from "./VideoPane";
import TranscriptPane from "./TranscriptPane";

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

  return (
    <div className="flex h-full">
      {/* LEFT: search / ask */}
      <div className="flex w-[38%] min-w-[340px] flex-col border-r border-gray-200 bg-white">
        <SearchPane
          initialMeetings={initialMeetings}
          currentMeetingId={transcript.meeting.meetingId}
          onCite={jumpTo}
        />
      </div>

      {/* RIGHT: video (optional) + transcript */}
      <div className="flex min-w-0 flex-1 flex-col">
        {showVideo && (
          <VideoPane
            baseUrl={transcript.videoBaseUrl!}
            activeSpeech={activeSpeech}
            meetingDate={transcript.meeting.meetingDate}
            following={following}
            onToggleFollow={() => setFollowing((f) => !f)}
          />
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
