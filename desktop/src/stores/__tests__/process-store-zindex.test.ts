import { describe, it, expect, beforeEach } from "vitest";
import { useProcessStore } from "../process-store";

// Overlays (dialogs, menus) portal to document.body at a FIXED z such as
// z-[10001]. Window z must therefore stay bounded, or a long session pushes
// windows above modals. Reported 2026-07-21: the invite dialog rendered behind
// the Projects window.
const OVERLAY_Z = 10001;

describe("window z-index stays below the overlay layer", () => {
  beforeEach(() => {
    useProcessStore.setState({ windows: [], nextZIndex: 1 });
  });

  it("does not grow z without bound as windows are focused", () => {
    const s = useProcessStore.getState();
    const a = s.openWindow("files");
    const b = s.openWindow("chat");

    // Simulate a long session: thousands of focus switches.
    for (let i = 0; i < 5000; i++) {
      useProcessStore.getState().focusWindow(i % 2 === 0 ? a : b);
    }

    const maxZ = Math.max(...useProcessStore.getState().windows.map((w) => w.zIndex));
    expect(maxZ).toBeLessThan(OVERLAY_Z);
    // With two windows the stack is 1..2 regardless of focus count.
    expect(maxZ).toBeLessThanOrEqual(2);
  });

  it("preserves relative stacking order after normalisation", () => {
    const s = useProcessStore.getState();
    const a = s.openWindow("files");
    const b = s.openWindow("chat");
    useProcessStore.getState().focusWindow(a);

    const wins = useProcessStore.getState().windows;
    const za = wins.find((w) => w.id === a)!.zIndex;
    const zb = wins.find((w) => w.id === b)!.zIndex;
    // The most recently focused window is on top.
    expect(za).toBeGreaterThan(zb);
  });

  it("keeps z bounded as many windows open", () => {
    const s = useProcessStore.getState();
    for (let i = 0; i < 40; i++) s.openWindow(`app-${i}`);
    const maxZ = Math.max(...useProcessStore.getState().windows.map((w) => w.zIndex));
    expect(maxZ).toBeLessThan(OVERLAY_Z);
  });
});
