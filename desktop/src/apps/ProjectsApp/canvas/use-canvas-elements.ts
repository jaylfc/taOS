import { useMemo, useEffect, useState, useRef } from "react";
import { createCanvasStore } from "./canvas-store";
import { canvasApi } from "./canvas-api";
import { subscribeCanvasStream } from "./canvas-sse";
import type { CanvasElement } from "./canvas-api";

export function useCanvasElements(projectId: string, elementId?: string | null): CanvasElement[] {
  const store = useMemo(() => createCanvasStore(), [projectId]);
  const [elements, setElements] = useState<CanvasElement[]>([]);
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;

    // Fetch initial elements
    canvasApi.listElements(projectId, elementId).then((rows) => {
      if (cancelled.current) return;
      store.getState().seed(rows);
    });

    // Subscribe to SSE stream
    const unsubscribe = subscribeCanvasStream(projectId, store);

    // Subscribe to store updates
    const unsubscribeStore = store.subscribe(() => {
      if (cancelled.current) return;
      const stateElements = Object.values(store.getState().elements);
      const filtered = elementId == null
        ? stateElements
        : stateElements.filter((e) => e.element_id === elementId);
      setElements(filtered);
    });

    // Cleanup
    return () => {
      cancelled.current = true;
      unsubscribe();
      unsubscribeStore();
    };
  }, [projectId, elementId, store]);

  return elements;
}