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
