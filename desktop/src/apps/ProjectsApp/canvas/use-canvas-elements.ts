import { useMemo, useEffect, useState } from "react";
import { createCanvasStore } from "./canvas-store";
import { canvasApi } from "./canvas-api";
import { subscribeCanvasStream } from "./canvas-sse";
import type { CanvasElement } from "./canvas-api";

export function useCanvasElements(projectId: string, elementId?: string | null): CanvasElement[] {
  const store = useMemo(() => createCanvasStore(), [projectId]);
  const [elements, setElements] = useState<CanvasElement[]>([]);

  useEffect(() => {
    let cancelled = false;

    // Clear previous scope's rows immediately so they don't flash while new request is in flight
    setElements([]);

    // Fetch initial elements
    canvasApi
      .listElements(projectId, elementId)
      .then((rows) => {
        if (cancelled) return;
        store.getState().seed(rows);
      })
      .catch(() => {
        if (!cancelled) setElements([]);
      });

    // Subscribe to SSE stream
    const unsubscribe = subscribeCanvasStream(projectId, store);

    // Subscribe to store updates
    const unsubscribeStore = store.subscribe(() => {
      if (cancelled) return;
      const stateElements = Object.values(store.getState().elements);
      const filtered = elementId == null
        ? stateElements
        : stateElements.filter((e) => e.element_id === elementId);
      setElements(filtered);
    });

    // Cleanup
    return () => {
      cancelled = true;
      unsubscribe();
      unsubscribeStore();
    };
  }, [projectId, elementId, store]);

  return elements;
}