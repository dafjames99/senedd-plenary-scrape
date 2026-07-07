/**
 * SeneddTV URL helpers. Stored URLs look like
 *   http://www.senedd.tv/en/{clipId}?startPos={seconds}
 * `startPos` equals seconds into the clip (verified against contribution_time
 * by analysis/wpm_fidelity.py in the data service).
 */

export function parseStartPos(url: string | null): number | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    const raw = parsed.searchParams.get("startPos");
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

/** Strip startPos → the clip's base URL (video pane initial src). */
export function baseClipUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    parsed.searchParams.delete("startPos");
    return parsed.toString();
  } catch {
    return null;
  }
}

/**
 * SeneddTV embeddable player URL for a meeting's webcast GUID. This is the bare
 * player (player.senedd.tv/Player/Index/{guid}) that Senedd.tv itself iframes —
 * no nav, no cookie banner — unlike the `/en/{clipId}` clip URL, which serves
 * the full website. `startPos` (seconds) is layered on by `clipUrlAt`; the
 * autostart/captions params are pre-set here.
 */
export function playerBaseUrl(webcastGuid: string | null): string | null {
  if (!webcastGuid) return null;
  return `https://player.senedd.tv/Player/Index/${webcastGuid}?autostart=True&captionsOn=False`;
}

/** Build a jump URL for a given offset. */
export function clipUrlAt(base: string, startPos: number): string {
  try {
    const parsed = new URL(base);
    parsed.searchParams.set("startPos", String(Math.max(0, Math.floor(startPos))));
    return parsed.toString();
  } catch {
    return base;
  }
}

export function formatOffset(seconds: number | null): string {
  if (seconds === null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}
