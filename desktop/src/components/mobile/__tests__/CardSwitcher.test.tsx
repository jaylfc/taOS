import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useProcessStore } from "@/stores/process-store";

const { isShell, list, focus, close, snap } = vi.hoisted(() => ({
  isShell: vi.fn(),
  list: vi.fn(),
  focus: vi.fn(),
  close: vi.fn(),
  snap: vi.fn(),
}));
vi.mock("@/lib/mobile-shell", () => ({
  isShellDevice: isShell,
  listShellWindows: list,
  focusShellWindow: focus,
  closeShellWindow: close,
  fetchShellSnapshot: snap,
}));

import { CardSwitcher } from "../CardSwitcher";

const WINDOWS = [
  { con_id: 11, app_id: "chrome-127.0.0.1__app.html-Default", title: "Files", label: "Files", workspace: "1", focused: false },
  { con_id: 12, app_id: "firefox", title: "Example Domain", label: "Firefox", workspace: "2", focused: true },
];

function setup(open = true) {
  const props = { open, onClose: vi.fn(), onSelectApp: vi.fn(), onLaunchpad: vi.fn() };
  const view = render(<CardSwitcher {...props} />);
  return { ...props, unmount: view.unmount };
}

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

describe("CardSwitcher shell windows", () => {
  beforeEach(() => {
    useProcessStore.setState({ windows: [], nextZIndex: 1 });
    isShell.mockReturnValue(true);
    list.mockReset();
    list.mockResolvedValue(WINDOWS);
    focus.mockReset();
    focus.mockResolvedValue(true);
    close.mockReset();
    close.mockResolvedValue(true);
    snap.mockReset();
    snap.mockResolvedValue(null);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders compositor windows as cards named by label, Firefox as Browser", async () => {
    setup();
    await flush();
    expect(screen.getByRole("button", { name: "Switch to Files" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Switch to Browser" })).toBeTruthy();
    expect(screen.queryByText("No apps open")).toBeNull();
  });

  it("tap focuses the window and closes the switcher", async () => {
    const props = setup();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Switch to Browser" }));
    expect(focus).toHaveBeenCalledWith(12);
    expect(props.onClose).toHaveBeenCalled();
    expect(props.onSelectApp).not.toHaveBeenCalled();
  });

  it.each(["Enter", " "])("keyboard %j on a card focuses the window", async (key) => {
    const props = setup();
    await flush();
    fireEvent.keyDown(screen.getByRole("button", { name: "Switch to Browser" }), { key });
    expect(focus).toHaveBeenCalledWith(12);
    expect(props.onClose).toHaveBeenCalled();
  });

  it("Enter on the X button does not also switch to the card", async () => {
    setup();
    await flush();
    fireEvent.keyDown(screen.getByRole("button", { name: "Close Browser" }), { key: "Enter" });
    expect(focus).not.toHaveBeenCalled();
  });

  it("the X button closes the window and refreshes the list", async () => {
    setup();
    await flush();
    list.mockResolvedValue([WINDOWS[1]]);
    const calls = list.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Close Files" }));
    await flush();
    expect(close).toHaveBeenCalledWith(11);
    expect(list.mock.calls.length).toBeGreaterThan(calls);
    expect(screen.queryByRole("button", { name: "Switch to Files" })).toBeNull();
  });

  it("polls every 2s while open", async () => {
    vi.useFakeTimers();
    setup();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const first = list.mock.calls.length;
    expect(first).toBeGreaterThanOrEqual(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(list.mock.calls.length).toBe(first + 1);
  });

  it("empty state shows when both lists are empty", async () => {
    list.mockResolvedValue([]);
    setup();
    await flush();
    expect(screen.getByText("No apps open")).toBeTruthy();
  });

  it("non-shell device makes no shell calls and shows in-page windows only", async () => {
    isShell.mockReturnValue(false);
    setup();
    await flush();
    expect(list).not.toHaveBeenCalled();
    expect(screen.getByText("No apps open")).toBeTruthy();
  });
});

describe("CardSwitcher in-page windows", () => {
  // The phone layout mounts only the active app (MobileAppWindow), and nothing
  // there finishes a desktop close animation: a window left marked closing
  // stays in the store, and in this switcher, for good.
  const FILES = {
    id: "w-files",
    appId: "files",
    position: { x: 0, y: 0 },
    size: { w: 900, h: 550 },
    zIndex: 1,
    minimized: false,
    maximized: false,
    snapped: null,
    focused: false,
    launchNonce: 0,
  };

  beforeEach(() => {
    useProcessStore.setState({ windows: [FILES], nextZIndex: 2 } as never);
    isShell.mockReturnValue(false);
    list.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("the X button removes the app, not just marks it closing", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Close Files" }));
    expect(useProcessStore.getState().windows).toEqual([]);
    expect(screen.queryByRole("button", { name: "Switch to Files" })).toBeNull();
    expect(screen.getByText("No apps open")).toBeTruthy();
  });

  it("swiping a card up removes the app", () => {
    vi.useFakeTimers();
    setup();
    const card = screen.getByRole("button", { name: "Switch to Files" });
    fireEvent.touchStart(card, { touches: [{ clientY: 600 }] });
    fireEvent.touchMove(card, { touches: [{ clientY: 400 }] });
    fireEvent.touchEnd(card);
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(useProcessStore.getState().windows).toEqual([]);
  });

  it("a short drag does not close the app", () => {
    vi.useFakeTimers();
    setup();
    const card = screen.getByRole("button", { name: "Switch to Files" });
    fireEvent.touchStart(card, { touches: [{ clientY: 600 }] });
    fireEvent.touchMove(card, { touches: [{ clientY: 560 }] });
    fireEvent.touchEnd(card);
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(useProcessStore.getState().windows.map((w) => w.id)).toEqual(["w-files"]);
  });
});

describe("CardSwitcher shell window snapshots", () => {
  const SNAP_WIN = {
    con_id: 7,
    app_id: "chrome-127.0.0.1__app.html-Default",
    title: "Files",
    label: "Files",
    workspace: "1",
    focused: false,
    snapshot: 1792237262.5,
  };
  const revoke = vi.fn();
  const originalRevoke = URL.revokeObjectURL;

  function imgs(): HTMLImageElement[] {
    return Array.from(document.querySelectorAll("img"));
  }

  beforeEach(() => {
    useProcessStore.setState({ windows: [], nextZIndex: 1 });
    isShell.mockReturnValue(true);
    list.mockReset();
    list.mockResolvedValue([SNAP_WIN]);
    focus.mockReset();
    close.mockReset();
    snap.mockReset();
    snap.mockResolvedValue("blob:mock-7");
    revoke.mockReset();
    Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true, writable: true });
  });

  afterEach(() => {
    Object.defineProperty(URL, "revokeObjectURL", { value: originalRevoke, configurable: true, writable: true });
    vi.useRealTimers();
  });

  it("shows the snapshot as an image from an object URL, with the name still visible", async () => {
    setup();
    await flush();
    expect(snap).toHaveBeenCalledWith(7);
    const card = screen.getByRole("button", { name: "Switch to Files" });
    const img = card.querySelector("img");
    expect(img?.getAttribute("src")).toBe("blob:mock-7");
    expect(img?.getAttribute("alt")).toBe("");
    expect(screen.getByText("Files")).toBeTruthy();
  });

  it("never points an img at the shell daemon (the CSP img-src blocks it)", async () => {
    list.mockResolvedValue([SNAP_WIN, { ...SNAP_WIN, con_id: 8, label: "Browser", snapshot: 5 }]);
    snap.mockImplementation((id: number) => Promise.resolve(`blob:mock-${id}`));
    setup();
    await flush();
    expect(imgs().length).toBe(2);
    for (const img of imgs()) {
      expect(img.getAttribute("src") ?? "").not.toContain("127.0.0.1:6973");
      expect(img.getAttribute("src") ?? "").toMatch(/^blob:/);
    }
  });

  it("a window with no snapshot is an icon card and fetches nothing", async () => {
    list.mockResolvedValue([{ ...SNAP_WIN, snapshot: null }]);
    setup();
    await flush();
    expect(snap).not.toHaveBeenCalled();
    expect(imgs()).toHaveLength(0);
  });

  it("a snapshot fetch that fails falls back to the icon card", async () => {
    snap.mockResolvedValue(null);
    setup();
    await flush();
    expect(snap).toHaveBeenCalledWith(7);
    expect(imgs()).toHaveLength(0);
  });

  it("does not refetch while con_id and snapshot are unchanged", async () => {
    vi.useFakeTimers();
    setup();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(list.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(snap).toHaveBeenCalledTimes(1);
    expect(revoke).not.toHaveBeenCalled();
  });

  it("revokes the object URL on unmount", async () => {
    const { unmount } = setup();
    await flush();
    expect(revoke).not.toHaveBeenCalled();
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:mock-7");
  });

  it("a new snapshot value revokes the old URL and shows the new one", async () => {
    vi.useFakeTimers();
    setup();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(imgs()[0]?.getAttribute("src")).toBe("blob:mock-7");
    list.mockResolvedValue([{ ...SNAP_WIN, snapshot: 1792237300 }]);
    snap.mockResolvedValue("blob:mock-7b");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(snap).toHaveBeenCalledTimes(2);
    expect(revoke).toHaveBeenCalledWith("blob:mock-7");
    expect(revoke).not.toHaveBeenCalledWith("blob:mock-7b");
    expect(imgs().map((i) => i.getAttribute("src"))).toEqual(["blob:mock-7b"]);
  });

  it("a fetch that resolves after unmount revokes its URL instead of keeping it", async () => {
    let resolve: (u: string) => void = () => {};
    snap.mockImplementation(() => new Promise<string>((r) => (resolve = r)));
    const { unmount } = setup();
    await flush();
    expect(snap).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => {
      resolve("blob:late");
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });
    expect(revoke).toHaveBeenCalledWith("blob:late");
  });

  it("a fetch that resolves after the snapshot changed revokes its URL and is not shown", async () => {
    vi.useFakeTimers();
    const resolvers: Array<(u: string) => void> = [];
    snap.mockImplementation(() => new Promise<string>((r) => resolvers.push(r)));
    setup();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    list.mockResolvedValue([{ ...SNAP_WIN, snapshot: 1792237300 }]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(resolvers).toHaveLength(2);
    await act(async () => {
      resolvers[1]?.("blob:new");
      resolvers[0]?.("blob:stale");
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(revoke).toHaveBeenCalledWith("blob:stale");
    expect(revoke).not.toHaveBeenCalledWith("blob:new");
    expect(imgs().map((i) => i.getAttribute("src"))).toEqual(["blob:new"]);
  });
});
