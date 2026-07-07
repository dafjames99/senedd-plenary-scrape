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
}: {
  baseUrl: string;
  activeSpeech: TranscriptSpeech | null;
  meetingDate: string;
  following: boolean;
  onToggleFollow: () => void;
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
    <div className="border-b border-gray-200 bg-black/95">
      <div className="flex items-center justify-between px-4 py-2 text-xs text-gray-300">
        <span className="truncate">
          Plenary, {date}
          {activeSpeech && (
            <span className="text-gray-500">
              {" "}
              — {activeSpeech.speakerName} at {formatOffset(activeSpeech.startPos)}
            </span>
          )}
        </span>
        <button
          onClick={onToggleFollow}
          className={`ml-3 shrink-0 rounded-full px-2.5 py-1 font-medium transition ${
            following ? "bg-accent text-white" : "bg-gray-700 text-gray-300"
          }`}
          title="Advance the transcript highlight with playback (approximate — the player cannot report its position)"
        >
          {following ? "Following (approx.)" : "Follow off"}
        </button>
      </div>

      {loadFailed ? (
        <div className="flex aspect-video max-h-[38vh] w-full flex-col items-center justify-center gap-2 text-sm text-gray-300">
          <p>The Senedd.tv player couldn&apos;t be embedded here.</p>
          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            className="rounded-md bg-accent px-3 py-1.5 font-medium text-white"
          >
            Watch on Senedd.tv ↗
          </a>
        </div>
      ) : (
        <iframe
          key={src} // reload on jump
          src={src}
          onError={() => setLoadFailed(true)}
          allow="encrypted-media; autoplay; fullscreen"
          allowFullScreen
          className="aspect-video max-h-[38vh] w-full"
          title="Senedd.tv player"
        />
      )}
    </div>
  );
}
