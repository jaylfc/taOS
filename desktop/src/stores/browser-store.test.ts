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

describe("browser-store: moveTab", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("moves a tab from source to destination window", async () => {
    const s = await freshStore();
    s.createWindow("win-a", "personal");
    s.createWindow("win-b", "personal");
    const movedId = s.addTab("win-a", "https://moved.test/");

    s.moveTab("win-a", movedId, "win-b");

    expect(s.getWindow("win-a")?.tabs.find((t) => t.id === movedId)).toBeUndefined();
    expect(s.getWindow("win-b")?.tabs.find((t) => t.id === movedId)).toBeDefined();
    expect(s.getWindow("win-b")?.activeTabId).toBe(movedId);
  });

  it("leaves source with a fresh new-tab when emptied by move", async () => {
    const s = await freshStore();
    s.createWindow("win-a", "personal");
    s.createWindow("win-b", "personal");
    const onlyTab = s.getWindow("win-a")!.tabs[0].id;

    s.moveTab("win-a", onlyTab, "win-b");

    const winA = s.getWindow("win-a");
    expect(winA?.tabs.length).toBe(1);
    expect(winA?.tabs[0].id).not.toBe(onlyTab);
  });

  it("noop when source and destination are the same window", async () => {
    const s = await freshStore();
    s.createWindow("win-a", "personal");
    const tabId = s.addTab("win-a", "https://x.test/");

    s.moveTab("win-a", tabId, "win-a");

    expect(s.getWindow("win-a")?.tabs.find((t) => t.id === tabId)).toBeDefined();
  });

  it("noop when destination window is missing", async () => {
    const s = await freshStore();
    s.createWindow("win-a", "personal");
    const tabId = s.addTab("win-a", "https://x.test/");

    s.moveTab("win-a", tabId, "win-missing");

    expect(s.getWindow("win-a")?.tabs.find((t) => t.id === tabId)).toBeDefined();
  });

  it("activates next-by-original-index when active tab moved out of multi-tab source", async () => {
    const s = await freshStore();
    s.createWindow("win-a", "personal");
    s.createWindow("win-b", "personal");
    const tabA = s.getWindow("win-a")!.tabs[0].id;
    const tabB = s.addTab("win-a", "https://b.test/");
    const tabC = s.addTab("win-a", "https://c.test/");
    // tabC is now active (last added)
    expect(s.getWindow("win-a")?.activeTabId).toBe(tabC);

    // Move tabC (active, index 2) out
    s.moveTab("win-a", tabC, "win-b");

    // win-a should activate the tab at clamp(closingIdx=2, len=2-1=1) = index 1 = tabB
    expect(s.getWindow("win-a")?.activeTabId).toBe(tabB);
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

describe("browser-store: setTabReader", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("patches reader fields onto the tab", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.setTabReader("win-1", tabId, {
      readerAvailable: true,
      readerActive: false,
      readerExtract: {
        title: "Article",
        text: "content",
        html: "<p>content</p>",
        word_count: 300,
      },
    });

    const tab = s.getWindow("win-1")?.tabs[0];
    expect(tab?.readerAvailable).toBe(true);
    expect(tab?.readerActive).toBe(false);
    expect(tab?.readerExtract?.title).toBe("Article");
  });

  it("partial patch only updates specified fields", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.setTabReader("win-1", tabId, { readerAvailable: true });
    s.setTabReader("win-1", tabId, { readerActive: true });

    const tab = s.getWindow("win-1")?.tabs[0];
    expect(tab?.readerAvailable).toBe(true);
    expect(tab?.readerActive).toBe(true);
  });
});

describe("browser-store: navigateTab reader reset", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("resets reader fields when navigating to a new URL", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.setTabReader("win-1", tabId, {
      readerAvailable: true,
      readerActive: true,
      readerExtract: {
        title: "Old article",
        text: "old",
        html: "<p>old</p>",
        word_count: 500,
      },
    });

    s.navigateTab("win-1", tabId, "https://new-page.test/");

    const tab = s.getWindow("win-1")?.tabs[0];
    expect(tab?.readerAvailable).toBeUndefined();
    expect(tab?.readerActive).toBeUndefined();
    expect(tab?.readerExtract).toBeNull();
  });
});

describe("browser-store: goBack/goForward reader reset", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("goBack clears all three reader fields", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.navigateTab("win-1", tabId, "https://a.test/");
    s.navigateTab("win-1", tabId, "https://b.test/");
    s.setTabReader("win-1", tabId, {
      readerAvailable: true,
      readerActive: true,
      readerExtract: {
        title: "Article",
        text: "content",
        html: "<p>content</p>",
        word_count: 300,
      },
    });

    s.goBack("win-1", tabId);

    const tab = s.getWindow("win-1")?.tabs[0];
    expect(tab?.url).toBe("https://a.test/");
    expect(tab?.readerAvailable).toBeUndefined();
    expect(tab?.readerActive).toBeUndefined();
    expect(tab?.readerExtract).toBeNull();
  });

  it("goForward clears all three reader fields", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabId = s.getWindow("win-1")!.tabs[0].id;

    s.navigateTab("win-1", tabId, "https://a.test/");
    s.navigateTab("win-1", tabId, "https://b.test/");
    s.goBack("win-1", tabId);
    // Now set reader state at https://a.test/
    s.setTabReader("win-1", tabId, {
      readerAvailable: true,
      readerActive: true,
      readerExtract: {
        title: "Article A",
        text: "content a",
        html: "<p>content a</p>",
        word_count: 300,
      },
    });

    s.goForward("win-1", tabId);

    const tab = s.getWindow("win-1")?.tabs[0];
    expect(tab?.url).toBe("https://b.test/");
    expect(tab?.readerAvailable).toBeUndefined();
    expect(tab?.readerActive).toBeUndefined();
    expect(tab?.readerExtract).toBeNull();
  });
});

describe("browser-store: switchProfile", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("updates the window's profileId (basic — Task 6 adds tab snapshot)", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    expect(s.getWindow("win-1")?.profileId).toBe("personal");

    s.switchProfile("win-1", "work");
    expect(s.getWindow("win-1")?.profileId).toBe("work");
  });

  it("noop when window doesn't exist", async () => {
    const s = await freshStore();
    s.switchProfile("missing", "work");
    expect(s.getWindow("missing")).toBeUndefined();
  });
});

describe("browser-store: switchProfile snapshot/restore", () => {
  beforeEach(async () => {
    const mod = await import("./browser-store");
    mod.useBrowserStore.setState({ windows: {} });
  });

  it("snapshots current tabs under the old profileId on switch", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabA = s.addTab("win-1", "https://a.test/");
    const tabB = s.addTab("win-1", "https://b.test/");
    expect(s.getWindow("win-1")?.tabs.length).toBe(3);

    s.switchProfile("win-1", "work");

    // After switch: profileId is "work", tabs reset to one fresh new-tab,
    // and the old "personal" tabs are saved under _savedTabsByProfile
    const win = s.getWindow("win-1");
    expect(win?.profileId).toBe("work");
    expect(win?.tabs.length).toBe(1);
    expect(win?._savedTabsByProfile?.personal).toBeDefined();
    expect(win?._savedTabsByProfile?.personal.tabs.length).toBe(3);
  });

  it("restores tabs from the snapshot on switch back", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabA = s.addTab("win-1", "https://a.test/");
    const tabB = s.addTab("win-1", "https://b.test/");

    s.switchProfile("win-1", "work");
    expect(s.getWindow("win-1")?.tabs.length).toBe(1); // Fresh new-tab for "work"

    s.switchProfile("win-1", "personal");

    const win = s.getWindow("win-1");
    expect(win?.profileId).toBe("personal");
    expect(win?.tabs.length).toBe(3);
    // The original tabs are restored
    expect(win?.tabs.find((t) => t.id === tabA)).toBeDefined();
    expect(win?.tabs.find((t) => t.id === tabB)).toBeDefined();
  });

  it("creates fresh new-tab when destination profile has no snapshot", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    s.switchProfile("win-1", "work");

    // Work has no prior snapshot — should init with one fresh new-tab
    const win = s.getWindow("win-1");
    expect(win?.tabs.length).toBe(1);
    expect(win?.profileId).toBe("work");
  });

  it("noop when switching to the already-active profile", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    const tabA = s.addTab("win-1", "https://a.test/");
    const before = s.getWindow("win-1");

    s.switchProfile("win-1", "personal");

    const after = s.getWindow("win-1");
    expect(after?.tabs.length).toBe(before?.tabs.length);
    expect(after?._savedTabsByProfile).toBeUndefined();
  });

  it("preserves snapshots for OTHER profiles when switching between two", async () => {
    const s = await freshStore();
    s.createWindow("win-1", "personal");
    s.addTab("win-1", "https://personal-a.test/");

    s.switchProfile("win-1", "work");
    s.addTab("win-1", "https://work-a.test/");

    s.switchProfile("win-1", "research");
    s.addTab("win-1", "https://research-a.test/");

    // Now both personal AND work snapshots should be in the saved map
    const win = s.getWindow("win-1");
    expect(win?.profileId).toBe("research");
    expect(win?._savedTabsByProfile?.personal).toBeDefined();
    expect(win?._savedTabsByProfile?.work).toBeDefined();
    expect(win?._savedTabsByProfile?.research).toBeUndefined(); // Active

    // Switch back to personal — work snapshot should still be preserved
    s.switchProfile("win-1", "personal");
    const win2 = s.getWindow("win-1");
    expect(win2?._savedTabsByProfile?.work).toBeDefined();
    expect(win2?._savedTabsByProfile?.research).toBeDefined(); // Just snapshotted
    expect(win2?._savedTabsByProfile?.personal).toBeUndefined(); // Just restored
  });
});
