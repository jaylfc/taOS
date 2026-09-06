import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { DragKind, DragPayload } from "./types";
import { endDrag, getCurrent, subscribe } from "./dnd-bus";

export interface UseDropTargetOpts {
  accept: DragKind[];
  onDrop: (payload: DragPayload) => void | Promise<void>;
  disabled?: boolean;
}

export function useDropTarget(opts: UseDropTargetOpts) {
  const { accept, onDrop, disabled = false } = opts;
  const current = useSyncExternalStore(subscribe, getCurrent, () => null);
  const enterCounter = useRef(0);
  const [isOver, setIsOver] = useState(false);

  const isValidTarget = !disabled && current !== null && accept.includes(current.kind);

  useEffect(() => {
    if (current === null) {
      enterCounter.current = 0;
      setIsOver(false);
    }
  }, [current]);

  return {
    isOver,
    isValidTarget,
    dropHandlers: {
      onDragEnter: (e: React.DragEvent) => {
        // Only react when the in-flight drag is a type we accept; this keeps
        // unrelated drags from highlighting this target or stealing default.
        if (disabled || !isValidTarget) return;
        e.preventDefault();
        enterCounter.current += 1;
        if (enterCounter.current === 1) setIsOver(true);
      },
      onDragOver: (e: React.DragEvent) => {
        if (disabled || !isValidTarget) return;
        e.preventDefault();
      },
      onDragLeave: (_e: React.DragEvent) => {
        if (disabled || !isValidTarget) return;
        enterCounter.current = Math.max(0, enterCounter.current - 1);
        if (enterCounter.current === 0) setIsOver(false);
      },
      onDrop: (e: React.DragEvent) => {
        enterCounter.current = 0;
        setIsOver(false);
        const payload = getCurrent();
        if (!disabled && payload && accept.includes(payload.kind)) {
          e.preventDefault();
          try {
            const r = onDrop(payload);
            if (r && typeof (r as Promise<void>).then === "function") {
              (r as Promise<void>).catch((err) => console.warn("drop handler failed", err));
            }
          } catch (err) {
            console.warn("drop handler failed", err);
          }
          // Clear bus now — don't wait for the 30s stale timer. Firefox also
          // fires dragend after drop but other browsers can skip it.
          endDrag();
        }
      },
    },
  };
}
