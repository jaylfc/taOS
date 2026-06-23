import { useState, useCallback } from "react";
import { Stage, Layer, Group, Rect, Text } from "react-konva";
import type Konva from "konva";
import { CanvasElement } from "./canvas-api";
import { CanvasNode, elementsToNodes } from "./element-to-konva";

// Read-only Konva (MIT) renderer for the project canvas: an infinite board with
// Stage pan (drag) + wheel zoom that draws each CanvasElement by kind. This is
// slice 1 of the tldraw -> Konva migration; it is built alongside the existing
// tldraw board and not yet wired in. Interactions (select/move/create/edit) and
// the swap that retires tldraw are later slices.

const NOTE_FILL: Record<string, string> = {
  yellow: "#fde68a",
  blue: "#bfdbfe",
  green: "#bbf7d0",
  pink: "#fbcfe8",
  purple: "#e9d5ff",
  gray: "#e5e7eb",
};

const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const ZOOM_STEP = 1.05;

function NodeView({ node }: { node: CanvasNode }) {
  const common = { x: node.x, y: node.y, rotation: node.rotation };
  switch (node.type) {
    case "note":
      return (
        <Group {...common}>
          <Rect
            width={node.w}
            height={node.h}
            fill={NOTE_FILL[node.color] ?? NOTE_FILL.yellow}
            cornerRadius={6}
            shadowColor="#000"
            shadowOpacity={0.12}
            shadowBlur={6}
            shadowOffsetY={2}
          />
          <Text
            text={node.text}
            x={8}
            y={8}
            width={node.w - 16}
            height={node.h - 16}
            fontSize={node.fontSize}
            fill="#1f2937"
            wrap="word"
          />
        </Group>
      );
    case "link":
      return (
        <Group {...common}>
          <Rect width={node.w} height={node.h} fill="#1e293b" cornerRadius={6} />
          <Text
            text={node.title || node.url}
            x={8}
            y={8}
            width={node.w - 16}
            fontSize={13}
            fontStyle="bold"
            fill="#e2e8f0"
            wrap="word"
            ellipsis
          />
          <Text
            text={node.url}
            x={8}
            y={node.h - 20}
            width={node.w - 16}
            fontSize={11}
            fill="#7dd3fc"
            ellipsis
          />
        </Group>
      );
    case "image":
      // Placeholder frame; actual image loading (file_id -> src) is a later slice.
      return (
        <Group {...common}>
          <Rect width={node.w} height={node.h} fill="#0f172a" stroke="#334155" cornerRadius={6} />
          <Text
            text={node.alt || "image"}
            x={8}
            y={node.h / 2 - 8}
            width={node.w - 16}
            fontSize={12}
            fill="#94a3b8"
            align="center"
            ellipsis
          />
        </Group>
      );
    default:
      return (
        <Rect
          {...common}
          width={node.w}
          height={node.h}
          fill="#334155"
          stroke="#475569"
          cornerRadius={4}
        />
      );
  }
}

export function KonvaBoard({
  elements,
  width,
  height,
}: {
  elements: CanvasElement[];
  width: number;
  height: number;
}) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const nodes = elementsToNodes(elements);

  const onWheel = useCallback((e: Konva.KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault();
    const stage = e.target.getStage();
    if (!stage) return;
    const oldScale = stage.scaleX();
    const pointer = stage.getPointerPosition();
    if (!pointer) return;
    // Zoom toward the cursor, clamped.
    const mouseTo = {
      x: (pointer.x - stage.x()) / oldScale,
      y: (pointer.y - stage.y()) / oldScale,
    };
    const next = e.evt.deltaY < 0 ? oldScale * ZOOM_STEP : oldScale / ZOOM_STEP;
    const clamped = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, next));
    setScale(clamped);
    setPos({
      x: pointer.x - mouseTo.x * clamped,
      y: pointer.y - mouseTo.y * clamped,
    });
  }, []);

  return (
    <Stage
      width={width}
      height={height}
      scaleX={scale}
      scaleY={scale}
      x={pos.x}
      y={pos.y}
      draggable
      onWheel={onWheel}
      onDragEnd={(e) => setPos({ x: e.target.x(), y: e.target.y() })}
    >
      <Layer>
        {nodes.map((n) => (
          <NodeView key={n.id} node={n} />
        ))}
      </Layer>
    </Stage>
  );
}
