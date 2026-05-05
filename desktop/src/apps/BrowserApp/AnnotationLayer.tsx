/**
 * Parent-overlay SVG for cursor + free-floating arrows.
 *
 * Positioned absolutely over the iframe (TabRenderer mounts it inside the
 * iframe wrapper). Pointer-events none — must not block iframe interaction.
 *
 * Renders a cursor (filled triangle with optional label) and arrows
 * (line + arrowhead at end).
 */
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import type { Annotation, AnnotationCursor, AnnotationArrow } from "@/stores/browser-agent-store";

const DEFAULT_COLOR = "#6c8df0";
const EMPTY: Annotation[] = [];

/** Stable marker id per color — avoids collisions between AnnotationLayer instances. */
const markerIdFor = (color: string) =>
  `annotation-arrowhead-${color.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

export interface AnnotationLayerProps {
  windowId: string;
  tabId: string;
}

export function AnnotationLayer({ windowId, tabId }: AnnotationLayerProps) {
  const key = `${windowId}:${tabId}`;
  // Select the specific array for this tab — falls back to the stable EMPTY
  // constant so Zustand's Object.is comparison never sees a new reference
  // when the key is absent (which would cause an infinite render loop).
  const annotations = useBrowserAgentStore((s) => s.annotations[key] ?? EMPTY);

  const cursors = annotations.filter((a): a is AnnotationCursor => a.kind === "cursor");
  const arrows = annotations.filter((a): a is AnnotationArrow => a.kind === "arrow");

  return (
    <svg
      aria-hidden="true"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        overflow: "visible",
      }}
    >
      {arrows.length > 0 && (
        <defs>
          {[...new Set(arrows.map((a) => a.color ?? DEFAULT_COLOR))].map((color) => (
            <marker
              key={color}
              id={markerIdFor(color)}
              markerWidth="8"
              markerHeight="8"
              refX="6"
              refY="3"
              orient="auto"
            >
              <path d="M0,0 L0,6 L8,3 z" fill={color} />
            </marker>
          ))}
        </defs>
      )}

      {cursors.map((cursor) => {
        const color = cursor.color ?? DEFAULT_COLOR;
        // Triangle pointing top-left: tip at (x, y), opening to bottom-right
        const pts = `${cursor.x},${cursor.y} ${cursor.x + 10},${cursor.y + 16} ${cursor.x + 4},${cursor.y + 12}`;
        return (
          <g key={cursor.id}>
            <polygon points={pts} fill={color} />
            {cursor.label && (
              <>
                <rect
                  x={cursor.x + 12}
                  y={cursor.y - 1}
                  width={cursor.label.length * 7 + 8}
                  height={18}
                  rx={3}
                  fill={color}
                  opacity={0.9}
                />
                <text
                  x={cursor.x + 16}
                  y={cursor.y + 12}
                  fontSize={12}
                  fill="#fff"
                  fontFamily="sans-serif"
                >
                  {cursor.label}
                </text>
              </>
            )}
          </g>
        );
      })}

      {arrows.map((arrow) => {
        const color = arrow.color ?? DEFAULT_COLOR;
        return (
          <line
            key={arrow.id}
            x1={arrow.from.x}
            y1={arrow.from.y}
            x2={arrow.to.x}
            y2={arrow.to.y}
            stroke={color}
            strokeWidth={2}
            markerEnd={`url(#${markerIdFor(color)})`}
          />
        );
      })}
    </svg>
  );
}
