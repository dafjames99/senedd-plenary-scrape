"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Axis = "x" | "y";

interface ResizableConfig {
  /** localStorage key the size is persisted under. */
  storageKey: string;
  /** Drag axis: "x" resizes width (vertical handle), "y" resizes height. */
  axis: Axis;
  /** Default size in px — used until a saved value hydrates. */
  initial: number;
  /** Minimum size in px. */
  min: number;
  /** Maximum size in px, evaluated each drag frame so it can track the viewport. */
  max: () => number;
}

interface Resizable {
  size: number;
  dragging: boolean;
  onPointerDown: (e: React.PointerEvent) => void;
}

const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

/**
 * Pointer-drag resizing for a single pane, persisted to localStorage. The
 * partner pane is expected to be `flex-1` so it absorbs the remaining space.
 *
 * The saved size is applied after mount (not during SSR) to avoid a hydration
 * mismatch — expect a one-frame flash at the default size on first paint.
 */
export function useResizable(config: ResizableConfig): Resizable {
  const { storageKey, axis, initial, min, max } = config;
  const [size, setSize] = useState(initial);
  const [dragging, setDragging] = useState(false);
  const sizeRef = useRef(initial);
  const hydrated = useRef(false);
  const drag = useRef({ origin: 0, start: 0 });

  const apply = useCallback((n: number) => {
    sizeRef.current = n;
    setSize(n);
  }, []);

  // Load the saved size once, after mount.
  useEffect(() => {
    const saved = Number(window.localStorage.getItem(storageKey));
    if (Number.isFinite(saved) && saved > 0) apply(clamp(saved, min, max()));
    hydrated.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist whenever the size settles (post-hydration only).
  useEffect(() => {
    if (hydrated.current) window.localStorage.setItem(storageKey, String(size));
  }, [size, storageKey]);

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      const point = axis === "x" ? e.clientX : e.clientY;
      apply(clamp(drag.current.start + (point - drag.current.origin), min, max()));
    },
    [axis, apply, min, max],
  );

  const onPointerUp = useCallback(() => {
    setDragging(false);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
  }, [onPointerMove]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      drag.current = {
        origin: axis === "x" ? e.clientX : e.clientY,
        start: sizeRef.current,
      };
      setDragging(true);
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
      // Suppress text selection / show the resize cursor for the whole drag.
      document.body.style.userSelect = "none";
      document.body.style.cursor = axis === "x" ? "col-resize" : "row-resize";
    },
    [axis, onPointerMove, onPointerUp],
  );

  return { size, dragging, onPointerDown };
}
