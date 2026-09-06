import { useCallback, useRef, useState } from "react";
import type { CanvasElement } from "./types";

// Snapshots can hold image data URLs (Magic outputs, uploads), so cap the
// history to keep a long session's memory bounded. Oldest entries are evicted.
const MAX_HISTORY = 100;

// Push onto a stack with the cap applied, evicting the oldest entry. Used for
// every push (commit, undo, and redo) so BOTH stacks stay bounded, not just
// undoStack.
function pushCapped(stack: CanvasElement[][], item: CanvasElement[]): void {
  stack.push(item);
  if (stack.length > MAX_HISTORY) stack.shift();
}

/**
 * A simple undo/redo stack over the canvas elements array. `commit` records
 * the elements array as it was *before* a change and applies the new state.
 * Undo/redo replay those snapshots. Kept intentionally small: the canvas
 * never holds more than a few hundred elements, so snapshotting the whole
 * array is cheap and avoids diff/patch bookkeeping. Commits happen once per
 * discrete edit (add/delete/reorder, or a drag/transform *end*), never per
 * pointer-move, so the stack grows slowly.
 */
export function useElementHistory(
  elements: CanvasElement[],
  onElementsChange: (next: CanvasElement[]) => void,
) {
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const undoStack = useRef<CanvasElement[][]>([]);
  const redoStack = useRef<CanvasElement[][]>([]);

  const commit = useCallback(
    (next: CanvasElement[]) => {
      pushCapped(undoStack.current, elements);
      redoStack.current = [];
      setCanUndo(true);
      setCanRedo(false);
      onElementsChange(next);
    },
    [elements, onElementsChange],
  );

  const undo = useCallback(() => {
    const prev = undoStack.current.pop();
    if (!prev) return;
    pushCapped(redoStack.current, elements);
    setCanUndo(undoStack.current.length > 0);
    setCanRedo(true);
    onElementsChange(prev);
  }, [elements, onElementsChange]);

  const redo = useCallback(() => {
    const next = redoStack.current.pop();
    if (!next) return;
    pushCapped(undoStack.current, elements);
    setCanRedo(redoStack.current.length > 0);
    setCanUndo(true);
    onElementsChange(next);
  }, [elements, onElementsChange]);

  return { commit, undo, redo, canUndo, canRedo };
}
