import { useCallback, useRef, useState } from "react";
import type { SnapPosition } from "@/stores/process-store";

const EDGE_THRESHOLD = 16;
const CORNER_SIZE = 100;

interface Viewport {
  width: number;
  height: number;
  topBarH: number;
  dockH: number;
}

export function detectSnapZone(x: number, y: number, vp: Viewport): SnapPosition {
  const nearLeft = x <= EDGE_THRESHOLD;
  const nearRight = x >= vp.width - EDGE_THRESHOLD;
  const nearTop = y <= vp.topBarH + CORNER_SIZE;
  const nearBottom = y >= vp.height - vp.dockH - CORNER_SIZE;

  if (nearLeft && nearTop) return "top-left";
  if (nearLeft && nearBottom) return "bottom-left";
  if (nearRight && nearTop) return "top-right";
  if (nearRight && nearBottom) return "bottom-right";
  if (nearLeft) return "left";
  if (nearRight) return "right";
  return null;
}

export function getSnapBounds(snap: SnapPosition, vp: Viewport): { x: number; y: number; w: number; h: number } | null {
  if (!snap) return null;

  // Window coords are relative to the Desktop container, which already
  // sits below the top bar. y=0 means "top of Desktop = flush with top
  // bar bottom". Useable height = full viewport height minus top bar
  // (already excluded by Desktop) minus dock reservation.
  const usableH = vp.height - vp.topBarH - vp.dockH;
  const halfW = Math.floor(vp.width / 2);
  const halfH = Math.floor(usableH / 2);

  switch (snap) {
    case "left":
      return { x: 0, y: 0, w: halfW, h: usableH };
    case "right":
      return { x: halfW, y: 0, w: halfW, h: usableH };
    case "top-left":
      return { x: 0, y: 0, w: halfW, h: halfH };
    case "top-right":
      return { x: halfW, y: 0, w: halfW, h: halfH };
    case "bottom-left":
      return { x: 0, y: halfH, w: halfW, h: halfH };
    case "bottom-right":
      return { x: halfW, y: halfH, w: halfW, h: halfH };
  }
}

export function useSnapZones(viewport: Viewport) {
  const [preview, setPreview] = useState<SnapPosition>(null);
  // Refs so onDrag/onDragStop keep a STABLE identity across renders. react-rnd's
  // position prop is controlled, so a Window re-render mid-drag re-applies the
  // stored position and yanks the window back ("jumping"). Stable callbacks let
  // <Window> be memoized and skip those re-renders while a drag is in flight.
  const previewRef = useRef<SnapPosition>(null);
  const viewportRef = useRef(viewport);
  viewportRef.current = viewport;

  const onDrag = useCallback((x: number, y: number) => {
    const zone = detectSnapZone(x, y, viewportRef.current);
    previewRef.current = zone;
    // Only flip state when the zone actually changes, so crossing the same zone
    // repeatedly does not re-render the desktop on every pointer move.
    setPreview((prev) => (prev === zone ? prev : zone));
  }, []);

  const onDragStop = useCallback((): SnapPosition => {
    const result = previewRef.current;
    previewRef.current = null;
    setPreview(null);
    return result;
  }, []);

  return { preview, previewBounds: getSnapBounds(preview, viewport), onDrag, onDragStop };
}
