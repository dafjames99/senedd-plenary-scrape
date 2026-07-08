"use client";

/**
 * Drag handle between two panes. `axis` matches the useResizable axis: "x" is a
 * vertical bar that resizes width, "y" is a horizontal bar that resizes height.
 * The 1px line carries a wider invisible hit area so it's easy to grab.
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
  return (
    <div
      role="separator"
      aria-orientation={isX ? "vertical" : "horizontal"}
      aria-label={label}
      onPointerDown={onPointerDown}
      className={`relative z-10 shrink-0 touch-none bg-gray-200 transition-colors hover:bg-accent ${
        dragging ? "bg-accent" : ""
      } ${isX ? "w-px cursor-col-resize" : "h-px cursor-row-resize"}`}
    >
      <span
        className={`absolute ${
          isX ? "inset-y-0 -left-1.5 -right-1.5" : "inset-x-0 -top-1.5 -bottom-1.5"
        }`}
      />
    </div>
  );
}
