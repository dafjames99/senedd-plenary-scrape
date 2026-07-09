"use client";

import { useMemo, useState } from "react";
import type { TranscriptSpeech } from "@/lib/types";
import { clipUrlAt, formatOffset } from "@/lib/tv";

/**
 * Top-right quadrant: the SeneddTV player iframe (player.senedd.tv, built from
 * the meeting's webcast GUID — the bare player, not the full site). Jumping =
 * reloading the iframe src with the target speech's startPos (the only control
 * surface the player exposes — PRD §2). If framing is denied, the notice below
 * plus NEXT_PUBLIC_VIDEO_MODE=link are the fallback.
 */
export default function VideoPane({
  baseUrl,
  activeSpeech,
  meetingDate,
  following,
  onToggleFollow,
  heightPx,
}: {
  baseUrl: string;
  activeSpeech: TranscriptSpeech | null;
  meetingDate: string;
  following: boolean;
  onToggleFollow: () => void;
  /** Total pane height (px), set by the drag handle below the pane. */
  heightPx: number;
}) {
  const [loadFailed, setLoadFailed] = useState(false);

  const src = useMemo(() => {
    if (activeSpeech?.startPos != null) return clipUrlAt(baseUrl, activeSpeech.startPos);
    return baseUrl;
  }, [baseUrl, activeSpeech]);

  const date = new Date(meetingDate).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <div className="flex min-h-0 flex-col bg-plum-deep" style={{ height: heightPx }}>
      <div className="flex items-center justify-between px-4 py-2 text-xs text-parchment/80">
        <span className="truncate">
          Plenary, {date}
          {activeSpeech && (
            <span className="text-parchment/45">
              {" "}
              — {activeSpeech.speakerName} at {formatOffset(activeSpeech.startPos)}
            </span>
          )}
        </span>
        <button
          onClick={onToggleFollow}
          className={`ml-3 shrink-0 rounded-full px-2.5 py-1 font-medium transition ${
            following ? "bg-heather text-plum-deep" : "bg-white/10 text-parchment/70"
          }`}
          title="Advance the transcript highlight with playback (approximate — the player cannot report its position)"
        >
          {following ? "Following (approx.)" : "Follow off"}
        </button>
      </div>

      {loadFailed ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 text-sm text-parchment/80">
          <p>The Senedd.tv player couldn&apos;t be embedded here.</p>
          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            className="rounded-md bg-heather px-3 py-1.5 font-medium text-plum-deep"
          >
            Watch on Senedd.tv ↗
          </a>
        </div>
      ) : (
        // Height-driven 16:9 box: the video area fills the pane below the header
        // and the iframe width follows from its height, so the whole player
        // stays visible (centered, letterboxed) at any pane height.
        <div className="flex min-h-0 flex-1 justify-center">
          <iframe
            key={src} // reload on jump
            src={src}
            onError={() => setLoadFailed(true)}
            allow="encrypted-media; autoplay; fullscreen"
            allowFullScreen
            className="aspect-video h-full max-w-full"
            title="Senedd.tv player"
          />
        </div>
      )}
    </div>
  );
}
