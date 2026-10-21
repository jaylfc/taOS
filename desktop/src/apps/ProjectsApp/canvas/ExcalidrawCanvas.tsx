import { useMemo } from "react";
import { useCanvasElements } from "./use-canvas-elements";
import { ExcalidrawBoard } from "./ExcalidrawBoard";
import { useIsMobile } from "../../../hooks/use-is-mobile";
import { useThemeStore } from "@/stores/theme-store";
import type { CanvasElement } from "./canvas-api";

export function ExcalidrawCanvas({
  projectId,
  projectSlug,
  elementId,
}: { projectId: string; projectSlug: string; elementId?: string | null }) {
  const elements = useCanvasElements(projectId, elementId);
  const readOnly = useIsMobile();
  const scheme = useThemeStore((s) => s.scheme);
  const getElement = useMemo(() => {
    const map = new Map<string, CanvasElement>();
    for (const el of elements) map.set(el.id, el);
    return (id: string) => map.get(id);
  }, [elements]);
  return (
    <ExcalidrawBoard
      elements={elements}
      projectId={projectId}
      projectSlug={projectSlug}
      elementId={elementId}
      getElement={getElement}
      readOnly={readOnly}
      theme={scheme}
    />
  );
}

export default ExcalidrawCanvas;