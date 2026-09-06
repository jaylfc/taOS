import type { DragPayload } from "./types";
import { startDrag, endDrag } from "./dnd-bus";

export interface UseDragSourceOpts<T extends DragPayload> {
  payload: T;
  disabled?: boolean;
  htmlMirror?: Record<string, string>;
}

export function useDragSource<T extends DragPayload>(opts: UseDragSourceOpts<T>) {
  const { payload, disabled = false, htmlMirror } = opts;
  return {
    dragHandlers: {
      draggable: !disabled,
      onDragStart: (e: React.DragEvent) => {
        if (disabled) return;
        // Defensive: clear any stale payload from a previous drag whose
        // dragend didn't fire (pointer capture lost, iframe escape).
        // startDrag resets the timer too, but this keeps the bus single-
        // source-of-truth clean.
        endDrag();
        try {
          e.dataTransfer.effectAllowed = "copy";
          if (htmlMirror) {
            for (const [mime, value] of Object.entries(htmlMirror)) {
              try { e.dataTransfer.setData(mime, value); } catch { /* best-effort */ }
            }
          }
        } catch { /* ignore */ }
        startDrag(payload);
      },
      onDragEnd: () => {
        endDrag();
      },
    },
  };
}
