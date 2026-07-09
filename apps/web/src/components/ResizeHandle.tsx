"use client";

import { useEffect, useState } from "react";

/** Set once the user has dragged any handle — the discovery pulse never returns. */
const HINT_DONE_KEY = "senedd:resizeHintDone";
const HINT_DONE_EVENT = "senedd:resize-used";

/**
 * Drag handle between two panes. `axis` matches the useResizable axis: "x" is a
 * vertical bar that resizes width, "y" is a horizontal bar that resizes height.
 * The 1px line carries a wider invisible hit area plus a visible grip tab that
 * grows on hover. Until the user's first drag, the grip pulses a few cycles per
 * page load (motion-safe only) to advertise that the panes resize at all.
 */
export default function ResizeHandle({
  axis,
  dragging,
  onPointerDown,
  label,
}: {
  axis: "x" | "y";
  dragging: boolean;
  onPointerDown: (e: React.PointerEvent) => void;
  label: string;
}) {
  const isX = axis === "x";
  const [hinting, setHinting] = useState(false);

  // Hint only users who have never dragged (SSR-safe: decided after mount),
  // and stop every handle's hint the moment any of them is used.
  useEffect(() => {
    if (!window.localStorage.getItem(HINT_DONE_KEY)) setHinting(true);
    const dismiss = () => setHinting(false);
    window.addEventListener(HINT_DONE_EVENT, dismiss);
    return () => window.removeEventListener(HINT_DONE_EVENT, dismiss);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    window.localStorage.setItem(HINT_DONE_KEY, "1");
    window.dispatchEvent(new Event(HINT_DONE_EVENT));
  }, [dragging]);

  return (
    <div
      role="separator"
      aria-orientation={isX ? "vertical" : "horizontal"}
      aria-label={label}
      onPointerDown={onPointerDown}
      className={`group relative z-10 shrink-0 touch-none bg-plum/15 transition-colors hover:bg-heather ${
        dragging ? "bg-heather" : ""
      } ${isX ? "w-px cursor-col-resize" : "h-px cursor-row-resize"}`}
    >
      <span
        className={`absolute ${
          isX ? "inset-y-0 -left-1.5 -right-1.5" : "inset-x-0 -top-1.5 -bottom-1.5"
        }`}
      />
      <span
        aria-hidden
        className={`pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full transition-all duration-150 ${
          isX ? "h-9 w-1" : "h-1 w-9"
        } ${
          dragging
            ? "scale-125 bg-plum"
            : "bg-plum/40 group-hover:scale-150 group-hover:bg-plum"
        } ${hinting && !dragging ? "motion-safe:animate-grip-pulse" : ""}`}
      />
    </div>
  );
}
