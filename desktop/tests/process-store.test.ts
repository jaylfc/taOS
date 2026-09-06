import { describe, it, expect, beforeEach } from "vitest";
import { useProcessStore } from "../src/stores/process-store";

beforeEach(() => {
  useProcessStore.setState({ windows: [], nextZIndex: 1 });
});

describe("process store", () => {
  it("opens a window and assigns z-index", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    const { windows } = useProcessStore.getState();
    expect(windows).toHaveLength(1);
    expect(windows[0].appId).toBe("messages");
    expect(windows[0].zIndex).toBe(1);
    expect(windows[0].size).toEqual({ w: 900, h: 600 });
    expect(id).toBe(windows[0].id);
  });

  it("closes a window by id", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().closeWindow(id);
    expect(useProcessStore.getState().windows).toHaveLength(0);
  });

  it("focuses a window — moves to top z-index", () => {
    const { openWindow } = useProcessStore.getState();
    const id1 = openWindow("messages", { w: 900, h: 600 });
    const id2 = openWindow("agents", { w: 800, h: 500 });
    useProcessStore.getState().focusWindow(id1);
    const { windows } = useProcessStore.getState();
    const w1 = windows.find((w) => w.id === id1)!;
    const w2 = windows.find((w) => w.id === id2)!;
    expect(w1.zIndex).toBeGreaterThan(w2.zIndex);
    expect(w1.focused).toBe(true);
    expect(w2.focused).toBe(false);
  });

  it("minimizes and restores a window", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().minimizeWindow(id);
    expect(useProcessStore.getState().windows[0].minimized).toBe(true);
    useProcessStore.getState().restoreWindow(id);
    expect(useProcessStore.getState().windows[0].minimized).toBe(false);
  });

  it("maximizes and restores a window", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().maximizeWindow(id);
    expect(useProcessStore.getState().windows[0].maximized).toBe(true);
    useProcessStore.getState().maximizeWindow(id);
    expect(useProcessStore.getState().windows[0].maximized).toBe(false);
  });

  it("enforces singleton — does not open duplicate", () => {
    const { openWindow } = useProcessStore.getState();
    openWindow("messages", { w: 900, h: 600 });
    const id2 = openWindow("messages", { w: 900, h: 600 });
    const { windows } = useProcessStore.getState();
    expect(windows).toHaveLength(1);
    expect(windows[0].focused).toBe(true);
    expect(id2).toBe(windows[0].id);
  });

  it("updates window position", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().updatePosition(id, 100, 200);
    const w = useProcessStore.getState().windows[0];
    expect(w.position).toEqual({ x: 100, y: 200 });
  });

  it("updates window size", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().updateSize(id, 1000, 700);
    const w = useProcessStore.getState().windows[0];
    expect(w.size).toEqual({ w: 1000, h: 700 });
  });

  it("sets snap state", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().snapWindow(id, "left");
    expect(useProcessStore.getState().windows[0].snapped).toBe("left");
    useProcessStore.getState().snapWindow(id, null);
    expect(useProcessStore.getState().windows[0].snapped).toBeNull();
  });

  it("returns running app IDs", () => {
    const { openWindow } = useProcessStore.getState();
    openWindow("messages", { w: 900, h: 600 });
    openWindow("agents", { w: 800, h: 500 });
    expect(useProcessStore.getState().runningAppIds()).toEqual(["messages", "agents"]);
  });
});
