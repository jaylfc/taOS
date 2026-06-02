import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { TabRenderer, DISCARD_TIMEOUT_MS, MAX_LIVE_TABS } from "./TabRenderer";
import { useBrowserStore } from "@/stores/browser-store";
import { __resetProxyConfigCache } from "@/lib/browser-proxy-config";

const TEST_WINDOW_ID = "win-test";

// Mock the proxy-config probe + ticket mint. Default: single-port (port 0 →
// same-origin redeem) and a successful ticket. Individual tests override.
function mockProxyFetch(opts?: { port?: number; ticket?: string | null }) {
  const port = opts?.port ?? 0;
  const ticket = opts && "ticket" in opts ? opts.ticket : "tok-abc";
  return vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const urlStr = typeof input === "string" ? input : input.toString();
    if (urlStr.includes("/api/desktop/browser/proxy-config")) {
      return Promise.resolve(
        new Response(JSON.stringify({ port }), { status: 200 }),
      );
    }
    if (urlStr.includes("/api/desktop/browser/proxy-ticket")) {
      if (ticket === null) {
        return Promise.resolve(new Response("nope", { status: 500 }));
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ticket, expires_in: 30 }), { status: 200 }),
      );
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
}

beforeEach(() => {
  __resetProxyConfigCache();
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
  vi.stubGlobal("fetch", mockProxyFetch());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
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

  it("active iframe src is the redeem URL on the proxy origin with a ticket + encoded next", async () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://example.com/",
    );

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;

    await waitFor(() => {
      expect(iframe.src).toContain("/__taos/redeem");
    });
    // Ticket present
    expect(iframe.src).toContain("ticket=tok-abc");
    // next= is the URL-encoded proxied path (so the proxy params are encoded
    // INSIDE next, not bare query params on the redeem URL).
    const u = new URL(iframe.src);
    const next = u.searchParams.get("next");
    expect(next).toContain("/api/desktop/browser/proxy");
    expect(next).toContain("profile_id=personal");
    expect(next).toContain("example.com");
    // Single-port mode (mocked port 0) → redeem is on the current origin.
    expect(u.origin).toBe(window.location.origin);
  });

  it("builds the redeem URL on the cross-origin proxy host when a proxy port is set", async () => {
    vi.stubGlobal("fetch", mockProxyFetch({ port: 6970, ticket: "tok-xyz" }));
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://example.com/",
    );

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;

    await waitFor(() => {
      expect(iframe.src).toContain("/__taos/redeem");
    });
    const u = new URL(iframe.src);
    expect(u.hostname).toBe(window.location.hostname);
    expect(u.port).toBe("6970");
    expect(u.protocol).toBe(window.location.protocol);
  });

  it("about:blank renders an iframe without a redeem/proxy URL", () => {
    // Default new-tab is about:blank
    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe.src).not.toContain("/__taos/redeem");
    expect(iframe.src).not.toContain("/api/desktop/browser/proxy");
  });

  it("iframe sandbox adds allow-same-origin when the proxy is cross-origin", async () => {
    vi.stubGlobal("fetch", mockProxyFetch({ port: 6970, ticket: "tok-xyz" }));
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://example.com/");

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;

    await waitFor(() => {
      expect(iframe.getAttribute("sandbox")).toContain("allow-same-origin");
    });
    expect(iframe.getAttribute("sandbox")).toContain("allow-scripts");
  });

  it("iframe sandbox withholds allow-same-origin in single-port mode", async () => {
    // Default mock reports port 0 → same-origin proxy → no allow-same-origin.
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://example.com/");

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;

    await waitFor(() => {
      expect(iframe.src).toContain("/__taos/redeem");
    });
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(iframe.getAttribute("sandbox")).toContain("allow-scripts");
  });

  it("surfaces an error in the tab when the ticket mint fails", async () => {
    vi.stubGlobal("fetch", mockProxyFetch({ ticket: null }));
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://example.com/",
    );

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);

    await waitFor(() => {
      expect(screen.getByText(/Couldn’t load this page/i)).toBeInTheDocument();
    });
  });
});

describe("TabRenderer — discarded tabs", () => {
  beforeEach(() => vi.useFakeTimers());

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
  beforeEach(() => vi.useFakeTimers());

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

describe("TabRenderer — liveSession renders LiveBrowserView", () => {
  it("renders a LiveBrowserView iframe (src contains nekoUrl) instead of the proxy iframe when liveSession is set", async () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://example.com/",
    );
    useBrowserStore.getState().setTabLiveSession(TEST_WINDOW_ID, tabId, {
      nekoUrl: "http://neko.local:8080/room",
      streamToken: "tok-live",
    });

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframes = container.querySelectorAll("iframe");
    // Should be exactly one iframe — the LiveBrowserView one
    expect(iframes.length).toBe(1);
    const iframe = iframes[0] as HTMLIFrameElement;
    expect(iframe.src).toContain("http://neko.local:8080/room");
    expect(iframe.src).toContain("tok-live");
    expect(iframe.title).toBe("Full browser");
  });

  it("returns to the proxy iframe after liveSession is cleared (navigateTab)", async () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://example.com/",
    );
    useBrowserStore.getState().setTabLiveSession(TEST_WINDOW_ID, tabId, {
      nekoUrl: "http://neko.local:8080/room",
      streamToken: "tok-live",
    });
    // Navigate away — clears liveSession
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://other.com/",
    );

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    const iframes = container.querySelectorAll("iframe");
    expect(iframes.length).toBe(1);
    const iframe = iframes[0] as HTMLIFrameElement;
    // Should NOT be the Neko URL
    expect(iframe.src).not.toContain("neko.local");
    expect(iframe.title).not.toBe("Full browser");
  });
});

describe("TabRenderer — reader mode", () => {
  it("renders ReaderMode for active tab when readerActive is true", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.activeTabId;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://article.test/story",
    );
    useBrowserStore.getState().setTabReader(TEST_WINDOW_ID, tabId, {
      readerAvailable: true,
      readerActive: true,
      readerExtract: {
        title: "Amazing Article",
        text: "content",
        html: "<p>content</p>",
        word_count: 500,
      },
    });

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    expect(screen.getByTestId("reader-mode")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Amazing Article",
    );
  });

  it("renders iframe normally when readerActive is false", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.activeTabId;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://article.test/story",
    );
    useBrowserStore.getState().setTabReader(TEST_WINDOW_ID, tabId, {
      readerAvailable: true,
      readerActive: false,
      readerExtract: {
        title: "Amazing Article",
        text: "content",
        html: "<p>content</p>",
        word_count: 500,
      },
    });

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    expect(screen.queryByTestId("reader-mode")).toBeNull();
    const iframe = container.querySelector("iframe");
    expect(iframe).toBeTruthy();
    expect((iframe as HTMLIFrameElement).style.display).toBe("block");
  });

  it("iframe stays in DOM when reader mode is active (toggling off preserves iframe state)", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.activeTabId;
    useBrowserStore.getState().navigateTab(
      TEST_WINDOW_ID,
      tabId,
      "https://article.test/story",
    );
    useBrowserStore.getState().setTabReader(TEST_WINDOW_ID, tabId, {
      readerAvailable: true,
      readerActive: true,
      readerExtract: {
        title: "Amazing Article",
        text: "content",
        html: "<p>content</p>",
        word_count: 500,
      },
    });

    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    // iframe should still be in DOM even with reader active (display:none)
    const iframe = container.querySelector("iframe");
    expect(iframe).toBeTruthy();
    expect((iframe as HTMLIFrameElement).style.display).toBe("none");
  });
});

describe("TabRenderer — live exclusion exempts discard", () => {
  beforeEach(() => vi.useFakeTimers());

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

describe("TabRenderer — tab-focus postMessage", () => {
  it("postMessages taos-copilot:tab-focus to iframes when active tab changes", async () => {
    // Add a second tab and switch to it — the postMessage should fire for both
    // iframes: focused:true for the new active tab, focused:false for the old.
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    // tabB is now active. Render while capturing postMessages.
    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    // Let the async redeem-URL state updates in each TabFrame settle so they
    // don't fire outside act() during the assertions below.
    await act(async () => { await Promise.resolve(); });

    // Attach a spy to each iframe's contentWindow.postMessage.
    const iframes = Array.from(
      container.querySelectorAll("iframe"),
    ) as HTMLIFrameElement[];
    const spies = iframes.map((iframe) => {
      const spy = vi.fn();
      // jsdom iframes don't have a real contentWindow; polyfill for this test.
      if (!iframe.contentWindow) {
        Object.defineProperty(iframe, "contentWindow", {
          value: { postMessage: spy },
          configurable: true,
        });
      } else {
        vi.spyOn(iframe.contentWindow, "postMessage").mockImplementation(spy);
      }
      return spy;
    });

    // Now switch to tabA — triggers the activeTabId change.
    act(() => {
      useBrowserStore.getState().setActiveTab(TEST_WINDOW_ID, tabA);
    });
    // Flush the async getBrowserProxyOrigin() inside the effect.
    await act(async () => { await Promise.resolve(); });

    // At least one iframe should have received a taos-copilot:tab-focus message.
    const allCalls = spies.flatMap((spy) => spy.mock.calls);
    const focusCalls = allCalls.filter(
      (args) => args[0]?.type === "taos-copilot:tab-focus",
    );
    expect(focusCalls.length).toBeGreaterThan(0);

    // The active tab's iframe should have received focused:true.
    const activeFocusCalls = focusCalls.filter(
      (args) => args[0]?.tab_id === tabA && args[0]?.focused === true,
    );
    expect(activeFocusCalls.length).toBeGreaterThan(0);

    // window_id must be present on every tab-focus message.
    for (const args of focusCalls) {
      expect(args[0].window_id).toBe(TEST_WINDOW_ID);
    }
  });
});

describe("TabRenderer — postMessage target origin (Fix 2)", () => {
  it("tab-focus postMessage uses the proxy origin, not '*', in single-port mode", async () => {
    // Single-port mode: proxy port is 0 → proxy origin equals window.location.origin.
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    await act(async () => { await Promise.resolve(); });

    const iframes = Array.from(container.querySelectorAll("iframe")) as HTMLIFrameElement[];
    const spies = iframes.map((iframe) => {
      const spy = vi.fn();
      if (!iframe.contentWindow) {
        Object.defineProperty(iframe, "contentWindow", {
          value: { postMessage: spy },
          configurable: true,
        });
      } else {
        vi.spyOn(iframe.contentWindow, "postMessage").mockImplementation(spy);
      }
      return spy;
    });

    act(() => {
      useBrowserStore.getState().setActiveTab(TEST_WINDOW_ID, tabA);
    });
    await act(async () => { await Promise.resolve(); });

    const allCalls = spies.flatMap((spy) => spy.mock.calls);
    const focusCalls = allCalls.filter(
      (args) => args[0]?.type === "taos-copilot:tab-focus",
    );
    expect(focusCalls.length).toBeGreaterThan(0);

    // Every tab-focus call must use the resolved proxy origin, never "*".
    for (const args of focusCalls) {
      expect(args[1]).not.toBe("*");
      // In single-port mode (mocked port 0), the proxy origin is the current origin.
      expect(args[1]).toBe(window.location.origin);
    }
  });

  it("tab-focus postMessage uses the cross-origin proxy origin when a port is configured", async () => {
    // Cross-origin mode: proxy port is 6970 → proxy origin is a separate host:port.
    vi.stubGlobal("fetch", mockProxyFetch({ port: 6970, ticket: "tok-xyz" }));
    __resetProxyConfigCache();

    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    const { container } = render(<TabRenderer windowId={TEST_WINDOW_ID} />);
    await act(async () => { await Promise.resolve(); });

    const iframes = Array.from(container.querySelectorAll("iframe")) as HTMLIFrameElement[];
    const spies = iframes.map((iframe) => {
      const spy = vi.fn();
      if (!iframe.contentWindow) {
        Object.defineProperty(iframe, "contentWindow", {
          value: { postMessage: spy },
          configurable: true,
        });
      } else {
        vi.spyOn(iframe.contentWindow, "postMessage").mockImplementation(spy);
      }
      return spy;
    });

    act(() => {
      useBrowserStore.getState().setActiveTab(TEST_WINDOW_ID, tabA);
    });
    await act(async () => { await Promise.resolve(); });

    const allCalls = spies.flatMap((spy) => spy.mock.calls);
    const focusCalls = allCalls.filter(
      (args) => args[0]?.type === "taos-copilot:tab-focus",
    );
    expect(focusCalls.length).toBeGreaterThan(0);

    for (const args of focusCalls) {
      expect(args[1]).not.toBe("*");
      // Cross-origin proxy: hostname unchanged, port 6970.
      expect(args[1]).toContain(":6970");
    }
  });
});
