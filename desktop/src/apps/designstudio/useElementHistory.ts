import { useCallback, useRef, useState } from "react";
import type { CanvasElement } from "./types";

// Snapshots can hold image data URLs (Magic outputs, uploads), so cap the
// history to keep a long session's memory bounded. Oldest entries are evicted.
const MAX_HISTORY = 100;

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
      undoStack.current.push(elements);
      if (undoStack.current.length > MAX_HISTORY) undoStack.current.shift();
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
    redoStack.current.push(elements);
    setCanUndo(undoStack.current.length > 0);
    setCanRedo(true);
    onElementsChange(prev);
  }, [elements, onElementsChange]);

  const redo = useCallback(() => {
    const next = redoStack.current.pop();
    if (!next) return;
    undoStack.current.push(elements);
    setCanRedo(redoStack.current.length > 0);
    setCanUndo(true);
    onElementsChange(next);
  }, [elements, onElementsChange]);

  return { commit, undo, redo, canUndo, canRedo };
}
