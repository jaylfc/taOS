import { describe, it, expect, beforeEach } from "vitest";
import { useProcessStore } from "./process-store";

const reset = () => useProcessStore.setState({ windows: [], nextZIndex: 1 });

describe("process-store openWindow", () => {
  beforeEach(reset);

  it("creates a new window with launchNonce 0 and forwards props", () => {
    const id = useProcessStore
      .getState()
      .openWindow("browser", { w: 800, h: 600 }, { initialUrl: "https://x.test" });
    const win = useProcessStore.getState().windows.find((w) => w.id === id);
    expect(win).toBeDefined();
    expect(win!.launchNonce).toBe(0);
    expect(win!.props).toEqual({ initialUrl: "https://x.test" });
  });

  it("refocuses existing window without bumping launchNonce when no props are passed", () => {
    const first = useProcessStore.getState().openWindow("browser", { w: 800, h: 600 });
    const second = useProcessStore.getState().openWindow("browser", { w: 800, h: 600 });
    expect(second).toBe(first);
    const win = useProcessStore.getState().windows.find((w) => w.id === first)!;
    expect(win.launchNonce).toBe(0);
  });

  it("merges new props and bumps launchNonce on existing window", () => {
    const first = useProcessStore
      .getState()
      .openWindow("browser", { w: 800, h: 600 }, { initialUrl: "https://a.test" });
    const second = useProcessStore
      .getState()
      .openWindow("browser", { w: 800, h: 600 }, { initialUrl: "https://b.test" });
    expect(second).toBe(first);
    const win = useProcessStore.getState().windows.find((w) => w.id === first)!;
    expect(win.props).toEqual({ initialUrl: "https://b.test" });
    expect(win.launchNonce).toBe(1);
  });

  it("marks a window as closing instead of removing it on closeWindow", () => {
    const id = useProcessStore.getState().openWindow("browser", { w: 800, h: 600 });
    useProcessStore.getState().closeWindow(id);
    const win = useProcessStore.getState().windows.find((w) => w.id === id);
    // Still mounted in the array so the Window can run its close animation.
    expect(win).toBeDefined();
    expect(win!.closing).toBe(true);
  });

  it("removes a window from the array on removeWindow", () => {
    const id = useProcessStore.getState().openWindow("browser", { w: 800, h: 600 });
    useProcessStore.getState().closeWindow(id);
    useProcessStore.getState().removeWindow(id);
    expect(useProcessStore.getState().windows.find((w) => w.id === id)).toBeUndefined();
  });

  it("restores a minimized window when re-opened", () => {
    const id = useProcessStore.getState().openWindow("browser", { w: 800, h: 600 });
    useProcessStore.getState().minimizeWindow(id);
    expect(useProcessStore.getState().windows.find((w) => w.id === id)!.minimized).toBe(true);
    useProcessStore
      .getState()
      .openWindow("browser", { w: 800, h: 600 }, { initialUrl: "https://x.test" });
    const win = useProcessStore.getState().windows.find((w) => w.id === id)!;
    expect(win.minimized).toBe(false);
    expect(win.focused).toBe(true);
  });

  it("opens a second window for the same app when forceNew is set", () => {
    const a = useProcessStore
      .getState()
      .openWindow("projects", { w: 900, h: 600 }, { projectId: "p1" });
    const b = useProcessStore
      .getState()
      .openWindow("projects", { w: 900, h: 600 }, { projectId: "p2" }, { forceNew: true });
    expect(b).not.toBe(a);
    const wins = useProcessStore.getState().windows.filter((w) => w.appId === "projects");
    expect(wins).toHaveLength(2);
    // Each window keeps its own props, so two projects can show side by side.
    expect(wins.find((w) => w.id === a)!.props).toEqual({ projectId: "p1" });
    expect(wins.find((w) => w.id === b)!.props).toEqual({ projectId: "p2" });
  });

  it("still refocuses the existing window when forceNew is not set", () => {
    const a = useProcessStore.getState().openWindow("projects", { w: 900, h: 600 });
    const b = useProcessStore.getState().openWindow("projects", { w: 900, h: 600 });
    expect(b).toBe(a);
    expect(
      useProcessStore.getState().windows.filter((w) => w.appId === "projects"),
    ).toHaveLength(1);
  });
});

describe("process-store reclampAllWindows", () => {
  beforeEach(() => {
    reset();
    // Set a known viewport size so safeBounds clamping is deterministic.
    Object.defineProperty(window, "innerWidth", { value: 1024, writable: true, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 768, writable: true, configurable: true });
  });

  it("clamps an off-screen window back into the viewport", () => {
    // Open a window at a position that would be reachable on a big monitor
    // but simulate it being far outside the current viewport.
    useProcessStore.getState().openWindow("files", { w: 600, h: 400 });
    // Directly set a position way off-screen to the right.
    const winId = useProcessStore.getState().windows[0].id;
    useProcessStore.setState((s) => ({
      windows: s.windows.map((w) =>
        w.id === winId ? { ...w, position: { x: 2000, y: 100 } } : w
      ),
    }));
    // Before clamping: window is unreachable.
    const before = useProcessStore.getState().windows[0];
    expect(before.position.x).toBe(2000);

    useProcessStore.getState().reclampAllWindows();

    const after = useProcessStore.getState().windows[0];
    // After clamping: x should be within the viewport (clamped to right edge).
    expect(after.position.x).toBeLessThan(1024);
    expect(after.position.x).toBeGreaterThan(0);
  });

  it("leaves a window already on-screen unchanged", () => {
    useProcessStore.getState().openWindow("files", { w: 600, h: 400 });
    const winId = useProcessStore.getState().windows[0].id;
    useProcessStore.setState((s) => ({
      windows: s.windows.map((w) =>
        w.id === winId ? { ...w, position: { x: 200, y: 150 } } : w
      ),
    }));

    useProcessStore.getState().reclampAllWindows();

    const after = useProcessStore.getState().windows[0];
    expect(after.position.x).toBe(200);
    expect(after.position.y).toBe(150);
  });

  it("skips minimized windows", () => {
    useProcessStore.getState().openWindow("files", { w: 600, h: 400 });
    const winId = useProcessStore.getState().windows[0].id;
    useProcessStore.getState().minimizeWindow(winId);
    useProcessStore.setState((s) => ({
      windows: s.windows.map((w) =>
        w.id === winId ? { ...w, position: { x: 2000, y: 100 } } : w
      ),
    }));

    useProcessStore.getState().reclampAllWindows();

    // Minimized window should stay at its stored position (off-screen).
    const after = useProcessStore.getState().windows[0];
    expect(after.position.x).toBe(2000);
    expect(after.minimized).toBe(true);
  });

  it("skips maximized windows", () => {
    useProcessStore.getState().openWindow("files", { w: 600, h: 400 });
    const winId = useProcessStore.getState().windows[0].id;
    useProcessStore.getState().maximizeWindow(winId);
    useProcessStore.setState((s) => ({
      windows: s.windows.map((w) =>
        w.id === winId ? { ...w, position: { x: 2000, y: 100 } } : w
      ),
    }));

    useProcessStore.getState().reclampAllWindows();

    // Maximized window keeps its stored position (viewport handles display).
    const after = useProcessStore.getState().windows[0];
    expect(after.position.x).toBe(2000);
    expect(after.maximized).toBe(true);
  });

  it("skips snapped windows", () => {
    useProcessStore.getState().openWindow("files", { w: 600, h: 400 });
    const winId = useProcessStore.getState().windows[0].id;
    useProcessStore.getState().snapWindow(winId, "left");
    useProcessStore.setState((s) => ({
      windows: s.windows.map((w) =>
        w.id === winId ? { ...w, position: { x: 2000, y: 100 } } : w
      ),
    }));

    useProcessStore.getState().reclampAllWindows();

    // Snapped window keeps stored position; display is handled reactively.
    const after = useProcessStore.getState().windows[0];
    expect(after.position.x).toBe(2000);
    expect(after.snapped).toBe("left");
  });
});
