import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { TabRenderer, DISCARD_TIMEOUT_MS, MAX_LIVE_TABS } from "./TabRenderer";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("TabRenderer — iframe pool", () => {
  it("renders an iframe for the single default tab", () => {
    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframes = container.querySelectorAll("iframe");
    expect(iframes.length).toBe(1);
  });

  it("renders one iframe per live tab + only active is display:block", () => {
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    const tabC = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://c.test/",
    );
    // tabC is now the active tab (last added)

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframes = container.querySelectorAll("iframe");
    expect(iframes.length).toBe(3);

    let blockCount = 0;
    let noneCount = 0;
    for (const iframe of Array.from(iframes)) {
      const display = (iframe as HTMLIFrameElement).style.display;
      if (display === "block") blockCount++;
      else if (display === "none") noneCount++;
    }
    expect(blockCount).toBe(1);
    expect(noneCount).toBe(2);
  });

  it("active iframe src is the proxied URL", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://example.com/",
    );

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe.src).toContain("/api/desktop/browser/proxy");
    expect(iframe.src).toContain("profile_id=personal");
    expect(iframe.src).toContain("example.com");
  });

  it("about:blank renders an iframe without proxy URL", () => {
    // Default new-tab is about:blank
    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe.src).not.toContain("/api/desktop/browser/proxy");
  });
});

describe("TabRenderer — discarded tabs", () => {
  it("discarded tabs render a placeholder card, not an iframe", () => {
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    useBrowserStore.getState().markTabDiscarded(TEST_WINDOW_ID, tabB);

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    // Only one iframe (the live tab), one placeholder
    expect(container.querySelectorAll("iframe").length).toBe(1);
    expect(screen.getAllByText(/snoozed|discarded/i).length).toBeGreaterThanOrEqual(1);
  });
});

describe("TabRenderer — discard scheduler", () => {
  it("discards a tab idle past DISCARD_TIMEOUT_MS (non-active, non-pinned)", () => {
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    // tabA was last active long ago — fake by setting lastActiveAt to past
    useBrowserStore.setState((s) => {
      const win = s.windows[TEST_WINDOW_ID];
      const tabs = win.tabs.map((t) =>
        t.id === tabA
          ? { ...t, lastActiveAt: Date.now() - DISCARD_TIMEOUT_MS - 1000 }
          : t,
      );
      return { windows: { ...s.windows, [TEST_WINDOW_ID]: { ...win, tabs } } };
    });

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);

    // Tick the scheduler (60s)
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    const tabs = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs;
    const tabAState = tabs.find((t) => t.id === tabA)?.state;
    expect(tabAState).toBe("discarded");
  });

  it("does NOT discard the active tab even if idle (active tab is by definition just-used)", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.activeTabId;

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);

    act(() => {
      vi.advanceTimersByTime(DISCARD_TIMEOUT_MS + 60_000);
    });

    const tab = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs.find(
      (t) => t.id === tabId,
    );
    expect(tab?.state).toBe("live");
  });

  it("does NOT discard pinned tabs even when idle", () => {
    const pinnedId = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://pinned.test/",
    );
    useBrowserStore.getState().pinTab(TEST_WINDOW_ID, pinnedId);
    // Make pinned tab idle
    useBrowserStore.setState((s) => {
      const win = s.windows[TEST_WINDOW_ID];
      const tabs = win.tabs.map((t) =>
        t.id === pinnedId
          ? { ...t, lastActiveAt: Date.now() - DISCARD_TIMEOUT_MS - 1000 }
          : t,
      );
      return { windows: { ...s.windows, [TEST_WINDOW_ID]: { ...win, tabs } } };
    });
    // Switch to a different tab so pinned isn't active
    const otherTab = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://other.test/",
    );

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    const tab = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs.find(
      (t) => t.id === pinnedId,
    );
    expect(tab?.state).toBe("live");
  });

  it("MAX_LIVE_TABS hard cap discards oldest non-pinned non-active tab when exceeded", () => {
    // Add MAX_LIVE_TABS + 1 tabs — all live
    const ids: string[] = [];
    for (let i = 0; i < MAX_LIVE_TABS + 2; i++) {
      ids.push(
        useBrowserStore.getState().addTab(
          TEST_WINDOW_ID,
          `https://t${i}.test/`,
        ),
      );
    }

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    const liveCount = useBrowserStore
      .getState()
      .getWindow(TEST_WINDOW_ID)!
      .tabs.filter((t) => t.state === "live").length;
    expect(liveCount).toBeLessThanOrEqual(MAX_LIVE_TABS);
  });
});

describe("TabRenderer — graceful handling", () => {
  it("renders nothing when window doesn't exist", () => {
    const { container } = render(<TabRenderer windowId="missing" />);
    expect(container.querySelector("iframe")).toBeNull();
  });
});

describe("TabRenderer — live exclusion exempts discard", () => {
  it("does NOT discard a tab whose iframe has a playing video", () => {
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );

    // Make tabA idle (long past discard timeout)
    useBrowserStore.setState((s) => {
      const win = s.windows[TEST_WINDOW_ID];
      const tabs = win.tabs.map((t) =>
        t.id === tabA
          ? { ...t, lastActiveAt: Date.now() - DISCARD_TIMEOUT_MS - 1000 }
          : t,
      );
      return { windows: { ...s.windows, [TEST_WINDOW_ID]: { ...win, tabs } } };
    });

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);

    // Plant a "playing video" in tabA's iframe
    const iframe = container.querySelector(
      `iframe[data-tab-id="${tabA}"]`,
    ) as HTMLIFrameElement | null;
    if (iframe?.contentDocument) {
      const video = iframe.contentDocument.createElement("video");
      iframe.contentDocument.body.appendChild(video);
      Object.defineProperty(video, "paused", { value: false, configurable: true });
      Object.defineProperty(video, "ended", { value: false, configurable: true });
    }

    // Tick the scheduler
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    const tab = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs.find(
      (t) => t.id === tabA,
    );
    // Should remain live + have the exclusion reason set
    expect(tab?.state).toBe("live");
    expect(tab?.liveExclusion).toBe("video");
  });
});
