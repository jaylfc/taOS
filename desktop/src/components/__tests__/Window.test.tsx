import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { useProcessStore, type WindowState } from "@/stores/process-store";

// Registry returns a minimal manifest; WindowContent is stubbed so the test
// stays focused on the Window chrome + lifecycle, not whatever app it hosts.
vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) => ({ id, name: "Test App", minSize: { w: 300, h: 200 } }),
}));

vi.mock("../WindowContent", () => ({
  WindowContent: () => <div data-testid="window-content">content</div>,
}));

// Desktop is mobile-aware via matchMedia; vitest.setup defaults it to desktop.

import { Window } from "../Window";

const baseWin: WindowState = {
  id: "win-1",
  appId: "browser",
  position: { x: 100, y: 100 },
  size: { w: 800, h: 600 },
  zIndex: 1,
  minimized: false,
  maximized: false,
  snapped: null,
  focused: true,
  launchNonce: 0,
};

const noop = () => {};
const noopSnap = () => null;

beforeEach(() => {
  useProcessStore.setState({ windows: [baseWin], nextZIndex: 2 });
});

describe("Window lifecycle", () => {
  it("renders the window chrome and content", () => {
    render(<Window win={baseWin} onDrag={noop} onDragStop={noopSnap} />);
    expect(screen.getByTestId("window-content")).toBeTruthy();
    expect(screen.getByText("Test App")).toBeTruthy();
  });

  it("stays mounted when minimized (animated out, not return null)", () => {
    const win = { ...baseWin, minimized: true };
    useProcessStore.setState({ windows: [win], nextZIndex: 2 });
    render(<Window win={win} onDrag={noop} onDragStop={noopSnap} />);
    // The chrome + content are still in the DOM; only visually animated away.
    expect(screen.getByTestId("window-content")).toBeTruthy();
  });

  it("closeWindow sets the closing flag rather than unmounting", () => {
    render(<Window win={baseWin} onDrag={noop} onDragStop={noopSnap} />);
    fireEvent.click(screen.getByLabelText("Close window"));
    const win = useProcessStore.getState().windows.find((w) => w.id === "win-1");
    expect(win).toBeDefined();
    expect(win!.closing).toBe(true);
  });

  it("removes the window from the store when the close animation completes", async () => {
    const closingWin = { ...baseWin, closing: true };
    useProcessStore.setState({ windows: [closingWin], nextZIndex: 2 });
    await act(async () => {
      render(<Window win={closingWin} onDrag={noop} onDragStop={noopSnap} />);
    });
    // In jsdom motion completes instantly and fires onAnimationComplete, which
    // calls removeWindow — so the closing window leaves the store on its own.
    await waitFor(() => {
      expect(useProcessStore.getState().windows.find((w) => w.id === "win-1")).toBeUndefined();
    });
  });

  it("focuses the window on titlebar mouse down", () => {
    const other = { ...baseWin, id: "win-2", focused: true };
    const target = { ...baseWin, id: "win-1", focused: false };
    useProcessStore.setState({ windows: [other, target], nextZIndex: 3 });
    render(<Window win={target} onDrag={noop} onDragStop={noopSnap} />);
    fireEvent.mouseDown(screen.getByText("Test App"));
    const win = useProcessStore.getState().windows.find((w) => w.id === "win-1");
    expect(win!.focused).toBe(true);
  });

  it("minimizes via the traffic-light button", () => {
    render(<Window win={baseWin} onDrag={noop} onDragStop={noopSnap} />);
    fireEvent.click(screen.getByLabelText("Minimize window"));
    const win = useProcessStore.getState().windows.find((w) => w.id === "win-1");
    expect(win!.minimized).toBe(true);
  });
});
