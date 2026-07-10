import { describe, it, expect, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useElementHistory } from "./useElementHistory";
import type { CanvasElement } from "./types";

function el(id: string, zIndex = 0): CanvasElement {
  return {
    id,
    type: "rect",
    x: 0,
    y: 0,
    width: 10,
    height: 10,
    rotation: 0,
    zIndex,
    visible: true,
    fill: "#000",
    stroke: "#fff",
    strokeWidth: 0,
  } as CanvasElement;
}

/**
 * The hook records the *previous* elements array on commit, so undo/redo only
 * make sense against a `current` value that tracks what was last applied. This
 * harness re-renders the hook with the latest applied array, mirroring how
 * DesignView feeds `elements` back in from parent state.
 */
function setup(initial: CanvasElement[] = []) {
  let current = initial;
  const onChange = vi.fn((next: CanvasElement[]) => {
    current = next;
  });
  const hook = renderHook(({ els }) => useElementHistory(els, onChange), {
    initialProps: { els: current },
  });
  const sync = () => hook.rerender({ els: current });
  return { hook, onChange, get current() { return current; }, sync };
}

describe("useElementHistory", () => {
  it("starts with nothing to undo or redo", () => {
    const { hook } = setup();
    expect(hook.result.current.canUndo).toBe(false);
    expect(hook.result.current.canRedo).toBe(false);
  });

  it("commit applies the new state and enables undo", () => {
    const { hook, onChange, sync } = setup([]);
    const next = [el("a")];
    act(() => hook.result.current.commit(next));
    expect(onChange).toHaveBeenLastCalledWith(next);
    expect(hook.result.current.canUndo).toBe(true);
    expect(hook.result.current.canRedo).toBe(false);
    sync();
  });

  it("undo restores the prior snapshot and enables redo", () => {
    const start = [el("a")];
    const { hook, onChange, sync } = setup(start);

    act(() => hook.result.current.commit([el("a"), el("b", 1)]));
    sync();

    act(() => hook.result.current.undo());
    // Undo replays the array as it was *before* the commit.
    expect(onChange).toHaveBeenLastCalledWith(start);
    expect(hook.result.current.canRedo).toBe(true);
    expect(hook.result.current.canUndo).toBe(false);
    sync();
  });

  it("redo re-applies an undone change", () => {
    const start = [el("a")];
    const committed = [el("a"), el("b", 1)];
    const { hook, onChange, sync } = setup(start);

    act(() => hook.result.current.commit(committed));
    sync();
    act(() => hook.result.current.undo());
    sync();
    act(() => hook.result.current.redo());

    expect(onChange).toHaveBeenLastCalledWith(committed);
    expect(hook.result.current.canRedo).toBe(false);
    expect(hook.result.current.canUndo).toBe(true);
    sync();
  });

  it("a fresh commit clears the redo stack", () => {
    const { hook, sync } = setup([el("a")]);
    act(() => hook.result.current.commit([el("b")]));
    sync();
    act(() => hook.result.current.undo());
    sync();
    expect(hook.result.current.canRedo).toBe(true);

    act(() => hook.result.current.commit([el("c")]));
    sync();
    expect(hook.result.current.canRedo).toBe(false);
  });

  it("undo/redo are no-ops when their stacks are empty", () => {
    const { hook, onChange } = setup([el("a")]);
    act(() => hook.result.current.undo());
    act(() => hook.result.current.redo());
    expect(onChange).not.toHaveBeenCalled();
    expect(hook.result.current.canUndo).toBe(false);
    expect(hook.result.current.canRedo).toBe(false);
  });

  it("caps the undo history and still restores the most recent snapshot", () => {
    const { hook, onChange, sync } = setup([el("v0")]);
    // Push well past the 100-entry cap.
    for (let i = 1; i <= 130; i++) {
      act(() => hook.result.current.commit([el(`v${i}`)]));
      sync();
    }
    expect(hook.result.current.canUndo).toBe(true);

    // Undo once returns the immediately-previous applied state, not a
    // corrupted/evicted one.
    act(() => hook.result.current.undo());
    expect(onChange).toHaveBeenLastCalledWith([el("v129")]);

    // Eviction branch: walk undo to exhaustion. Because the history is capped,
    // the oldest snapshots (including the original v0) were evicted, so we can
    // never restore v0 and the undo depth is bounded by the cap, not by the 130
    // commits pushed.
    let undos = 1; // the undo above
    while (hook.result.current.canUndo) {
      act(() => hook.result.current.undo());
      undos++;
      if (undos > 200) break; // safety: never loop forever if the cap regressed
    }
    expect(undos).toBeLessThanOrEqual(100);
    expect(onChange).not.toHaveBeenLastCalledWith([el("v0")]);
  });
});
