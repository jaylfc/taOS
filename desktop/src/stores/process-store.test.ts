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
});
