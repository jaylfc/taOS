import { describe, expect, it, beforeEach } from "vitest";

// Reset the store before each test by importing fresh
async function freshStore() {
  const mod = await import("./browser-store");
  // Clear any persistent state by calling resetForTesting (we'll implement it)
  mod.useBrowserStore.setState({ windows: {} });
  return mod.useBrowserStore.getState();
}

describe("browser-store: createWindow", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("creates a window with one default new-tab page", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");

    const win = s.getWindow("win-1");
    expect(win).toBeDefined();
    expect(win?.profileId).toBe("personal");
    expect(win?.tabs.length).toBe(1);
    expect(win?.activeTabId).toBe(win?.tabs[0].id);
    expect(win?.tabs[0].state).toBe("live");
  });

  it("createWindow is idempotent on the same windowId", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    s.createWindow("win-1", "personal"); // no-op
    const win = s.getWindow("win-1");
    expect(win?.tabs.length).toBe(1);
  });
});

describe("browser-store: addTab", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("appends a tab + makes it active", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.addTab("win-1", "https://example.com/");
    const win = s.getWindow("win-1");
    expect(win?.tabs.length).toBe(2);
    expect(win?.tabs[1].url).toBe("https://example.com/");
    expect(win?.activeTabId).toBe(tabId);
  });
});

describe("browser-store: closeTab", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("removes the tab + activates next-by-index when active", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabA = s.getWindow("win-1")!.tabs[0].id;
    const tabB = s.addTab("win-1", "https://b.test/");
    const tabC = s.addTab("win-1", "https://c.test/");

    // tabC is active; close it; tabB should become active (last live)
    s.closeTab("win-1", tabC);
    expect(s.getWindow("win-1")?.activeTabId).toBe(tabB);

    // close active tabB; tabA becomes active
    s.closeTab("win-1", tabB);
    expect(s.getWindow("win-1")?.activeTabId).toBe(tabA);
  });

  it("closing the last tab leaves the window with one fresh new-tab", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabA = s.getWindow("win-1")!.tabs[0].id;

    s.closeTab("win-1", tabA);
    const win = s.getWindow("win-1");
    expect(win?.tabs.length).toBe(1);
    // The replacement is a fresh tab with a different id
    expect(win?.tabs[0].id).not.toBe(tabA);
  });

  it("captures closed tab into recently-closed (max 50)", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabB = s.addTab("win-1", "https://b.test/");
    s.closeTab("win-1", tabB);

    const win = s.getWindow("win-1");
    expect(win?.recentlyClosed.length).toBe(1);
    expect(win?.recentlyClosed[0].url).toBe("https://b.test/");
  });
});

describe("browser-store: pinTab/unpinTab", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("pinTab sets pinned=true; unpinTab sets pinned=false", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.pinTab("win-1", tabId);
    expect(s.getWindow("win-1")?.tabs[0].pinned).toBe(true);

    s.unpinTab("win-1", tabId);
    expect(s.getWindow("win-1")?.tabs[0].pinned).toBe(false);
  });
});

describe("browser-store: navigation", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("navigateTab pushes onto history + advances index", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.navigateTab("win-1", tabId, "https://a.test/");
    s.navigateTab("win-1", tabId, "https://b.test/");

    const tab = s.getWindow("win-1")!.tabs[0];
    expect(tab.url).toBe("https://b.test/");
    expect(tab.history.length).toBeGreaterThanOrEqual(2);
    expect(tab.historyIndex).toBe(tab.history.length - 1);
  });

  it("goBack/goForward move historyIndex without mutating history", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.navigateTab("win-1", tabId, "https://a.test/");
    s.navigateTab("win-1", tabId, "https://b.test/");
    const beforeLen = s.getWindow("win-1")!.tabs[0].history.length;

    s.goBack("win-1", tabId);
    expect(s.getWindow("win-1")?.tabs[0].url).toBe("https://a.test/");

    s.goForward("win-1", tabId);
    expect(s.getWindow("win-1")?.tabs[0].url).toBe("https://b.test/");

    expect(s.getWindow("win-1")?.tabs[0].history.length).toBe(beforeLen);
  });
});

describe("browser-store: discard", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("markTabDiscarded sets state to discarded", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.addTab("win-1", "https://a.test/");

    s.markTabDiscarded("win-1", tabId);
    const tab = s.getWindow("win-1")!.tabs.find((t) => t.id === tabId);
    expect(tab?.state).toBe("discarded");
  });
});

describe("browser-store: removeWindow", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("removes the entry from the store", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    s.removeWindow("win-1");
    expect(s.getWindow("win-1")).toBeUndefined();
  });
});

describe("browser-store: zoom", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("setTabZoom updates the tab zoom field", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.setTabZoom("win-1", tabId, 1.5);
    expect(s.getWindow("win-1")?.tabs[0].zoom).toBeCloseTo(1.5);
  });

  it("setTabZoom clamps to [0.5, 3.0]", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.setTabZoom("win-1", tabId, 10);
    expect(s.getWindow("win-1")?.tabs[0].zoom).toBeCloseTo(3.0);

    s.setTabZoom("win-1", tabId, 0.1);
    expect(s.getWindow("win-1")?.tabs[0].zoom).toBeCloseTo(0.5);
  });
});
