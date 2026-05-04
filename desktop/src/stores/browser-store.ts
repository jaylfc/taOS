/**
 * BrowserApp v2 — Zustand store for browser window/tab state.
 *
 * One entry per browser window, keyed by windowId (which matches
 * process-store's window id). Holds tabs/activeTab/profile/discard
 * state plus a per-window recently-closed graveyard (max 50).
 *
 * Persistence is handled separately by `use-session-persistence.ts`
 * via debounced PUT to /api/desktop/browser/windows.
 */
import { create } from "zustand";
import type {
  BrowserWindowState,
  LiveExclusion,
  RecentlyClosedTab,
  Tab,
} from "@/apps/BrowserApp/types";

const NEW_TAB_URL = "about:blank";
const MAX_RECENTLY_CLOSED = 50;

let _tabIdCounter = 0;
function nextTabId(): string {
  _tabIdCounter += 1;
  return `tab-${Date.now()}-${_tabIdCounter}`;
}

function makeTab(url: string = NEW_TAB_URL): Tab {
  return {
    id: nextTabId(),
    url,
    title: "",
    pinned: false,
    history: url ? [url] : [],
    historyIndex: url ? 0 : -1,
    scrollY: 0,
    zoom: 1.0,
    state: "live",
    lastActiveAt: Date.now(),
    pinnedAgentIds: [],
  };
}

interface BrowserStore {
  windows: Record<string, BrowserWindowState>;

  // Window lifecycle
  createWindow: (windowId: string, profileId: string) => void;
  removeWindow: (windowId: string) => void;
  getWindow: (windowId: string) => BrowserWindowState | undefined;

  // Tab lifecycle
  addTab: (windowId: string, url?: string) => string;
  closeTab: (windowId: string, tabId: string) => void;
  restoreClosedTab: (windowId: string) => void;
  setActiveTab: (windowId: string, tabId: string) => void;
  pinTab: (windowId: string, tabId: string) => void;
  unpinTab: (windowId: string, tabId: string) => void;

  // Navigation
  navigateTab: (windowId: string, tabId: string, url: string) => void;
  goBack: (windowId: string, tabId: string) => void;
  goForward: (windowId: string, tabId: string) => void;

  // Discard policy
  markTabDiscarded: (windowId: string, tabId: string) => void;
  markTabLive: (windowId: string, tabId: string) => void;

  // Per-tab zoom
  setTabZoom: (windowId: string, tabId: string, zoom: number) => void;

  // Live-exclusion tracking
  setTabLiveExclusion: (
    windowId: string,
    tabId: string,
    exclusion: LiveExclusion | undefined,
  ) => void;

  // Cross-window tab move (PR 4: menu-driven; native drag-and-drop with
  // DOM-portal iframe preservation is deferred to a future enhancement)
  moveTab: (fromWindowId: string, tabId: string, toWindowId: string, toIndex?: number) => void;

  // Profile switching — Task 4: minimal (updates profileId only).
  // Task 6 will enhance to snapshot/restore tabs per (window, profile).
  switchProfile: (windowId: string, newProfileId: string) => void;

  // Reader mode
  setTabReader: (
    windowId: string,
    tabId: string,
    patch: Partial<Pick<Tab, "readerAvailable" | "readerActive" | "readerExtract">>,
  ) => void;

  // Agent pin set — local state mutations; server calls happen via browser-agent-api.ts
  addPinnedAgent: (windowId: string, tabId: string, agentId: string) => void;
  removePinnedAgent: (windowId: string, tabId: string, agentId: string) => void;
}

export const useBrowserStore = create<BrowserStore>((set, get) => ({
  windows: {},

  createWindow(windowId, profileId) {
    set((s) => {
      if (s.windows[windowId]) return s; // idempotent
      const initialTab = makeTab();
      const win: BrowserWindowState = {
        windowId,
        profileId,
        tabs: [initialTab],
        activeTabId: initialTab.id,
        recentlyClosed: [],
      };
      return { windows: { ...s.windows, [windowId]: win } };
    });
  },

  removeWindow(windowId) {
    set((s) => {
      const next = { ...s.windows };
      delete next[windowId];
      return { windows: next };
    });
  },

  getWindow(windowId) {
    return get().windows[windowId];
  },

  addTab(windowId, url = NEW_TAB_URL) {
    const tab = makeTab(url);
    set((s) => {
      const win = s.windows[windowId];
      if (!win) return s;
      const updated: BrowserWindowState = {
        ...win,
        tabs: [...win.tabs, tab],
        activeTabId: tab.id,
      };
      return { windows: { ...s.windows, [windowId]: updated } };
    });
    return tab.id;
  },

  closeTab(windowId, tabId) {
    set((s) => {
      const win = s.windows[windowId];
      if (!win) return s;

      const closingIdx = win.tabs.findIndex((t) => t.id === tabId);
      if (closingIdx === -1) return s;

      const closing = win.tabs[closingIdx];
      if (!closing) return s;

      // Capture into recently-closed
      const closedEntry: RecentlyClosedTab = {
        url: closing.url,
        title: closing.title,
        closedAt: Date.now(),
        pinnedAgentIds: closing.pinnedAgentIds ?? [],
      };

      const remainingTabs = win.tabs.filter((_, i) => i !== closingIdx);

      // If that was the last tab, replace with a fresh new-tab page
      const tabsAfter = remainingTabs.length === 0 ? [makeTab()] : remainingTabs;

      // Determine new active tab if we just closed the active one
      let newActiveId = win.activeTabId;
      if (win.activeTabId === tabId) {
        if (remainingTabs.length === 0) {
          // tabsAfter has the fresh replacement; tabsAfter[0] is guaranteed
          // present because we just constructed it with [makeTab()] above.
          const fresh = tabsAfter[0];
          if (fresh) newActiveId = fresh.id;
        } else {
          // Activate the tab to the LEFT (next-by-index when right-of removed
          // is also fine; pick by index clamped to remainingTabs)
          const newIdx = Math.min(closingIdx, remainingTabs.length - 1);
          const newActive = remainingTabs[newIdx];
          if (newActive) newActiveId = newActive.id;
        }
      }

      const updated: BrowserWindowState = {
        ...win,
        tabs: tabsAfter,
        activeTabId: newActiveId,
        recentlyClosed: [closedEntry, ...win.recentlyClosed].slice(0, MAX_RECENTLY_CLOSED),
      };
      return { windows: { ...s.windows, [windowId]: updated } };
    });
  },

  restoreClosedTab(windowId) {
    set((s) => {
      const win = s.windows[windowId];
      if (!win || win.recentlyClosed.length === 0) return s;

      const entry = win.recentlyClosed[0];
      const remaining = win.recentlyClosed.slice(1);
      if (!entry) return s;

      const restored = makeTab(entry.url);
      // Carry forward the pinned agents from the snapshot
      restored.pinnedAgentIds = entry.pinnedAgentIds ?? [];
      restored.title = entry.title;

      const updated: BrowserWindowState = {
        ...win,
        tabs: [...win.tabs, restored],
        activeTabId: restored.id,
        recentlyClosed: remaining,
      };
      return { windows: { ...s.windows, [windowId]: updated } };
    });
  },

  setActiveTab(windowId, tabId) {
    set((s) => {
      const win = s.windows[windowId];
      if (!win) return s;
      const exists = win.tabs.some((t) => t.id === tabId);
      if (!exists) return s;
      const updated = {
        ...win,
        activeTabId: tabId,
        tabs: win.tabs.map((t) =>
          t.id === tabId ? { ...t, lastActiveAt: Date.now() } : t,
        ),
      };
      return { windows: { ...s.windows, [windowId]: updated } };
    });
  },

  pinTab(windowId, tabId) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({ ...t, pinned: true })));
  },

  unpinTab(windowId, tabId) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({ ...t, pinned: false })));
  },

  navigateTab(windowId, tabId, url) {
    set((s) => updateTab(s, windowId, tabId, (t) => {
      // Truncate forward history when navigating from a back state
      const trimmedHistory = t.history.slice(0, t.historyIndex + 1);
      const newHistory = [...trimmedHistory, url];
      return {
        ...t,
        url,
        history: newHistory,
        historyIndex: newHistory.length - 1,
        state: "live",
        lastActiveAt: Date.now(),
        // Reset reader state on navigation — new URL invalidates the extract
        readerAvailable: undefined,
        readerActive: undefined,
        readerExtract: null,
      };
    }));
  },

  goBack(windowId, tabId) {
    set((s) => updateTab(s, windowId, tabId, (t) => {
      if (t.historyIndex <= 0) return t;
      const newIdx = t.historyIndex - 1;
      const newUrl = t.history[newIdx];
      if (newUrl === undefined) return t;
      return {
        ...t,
        historyIndex: newIdx,
        url: newUrl,
        readerAvailable: undefined,
        readerActive: undefined,
        readerExtract: null,
      };
    }));
  },

  goForward(windowId, tabId) {
    set((s) => updateTab(s, windowId, tabId, (t) => {
      if (t.historyIndex >= t.history.length - 1) return t;
      const newIdx = t.historyIndex + 1;
      const newUrl = t.history[newIdx];
      if (newUrl === undefined) return t;
      return {
        ...t,
        historyIndex: newIdx,
        url: newUrl,
        readerAvailable: undefined,
        readerActive: undefined,
        readerExtract: null,
      };
    }));
  },

  markTabDiscarded(windowId, tabId) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({ ...t, state: "discarded" })));
  },

  markTabLive(windowId, tabId) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({
      ...t, state: "live", lastActiveAt: Date.now(),
    })));
  },

  setTabZoom(windowId, tabId, zoom) {
    const clamped = Math.max(0.5, Math.min(3.0, zoom));
    set((s) => updateTab(s, windowId, tabId, (t) => ({ ...t, zoom: clamped })));
  },

  setTabLiveExclusion(windowId, tabId, exclusion) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({
      ...t,
      liveExclusion: exclusion,
    })));
  },

  moveTab(fromWindowId, tabId, toWindowId, toIndex) {
    set((s) => {
      if (fromWindowId === toWindowId) return s;
      const fromWin = s.windows[fromWindowId];
      const toWin = s.windows[toWindowId];
      if (!fromWin || !toWin) return s;

      const tab = fromWin.tabs.find((t) => t.id === tabId);
      if (!tab) return s;

      // Remove from source
      const closingIdx = fromWin.tabs.findIndex((t) => t.id === tabId);
      const fromTabs = fromWin.tabs.filter((t) => t.id !== tabId);
      const replacementTab = fromTabs.length === 0 ? makeTab() : null;
      let sourceActiveId = fromWin.activeTabId;
      if (replacementTab) {
        sourceActiveId = replacementTab.id;
      } else if (fromWin.activeTabId === tabId) {
        const fallback = fromTabs[Math.min(closingIdx, fromTabs.length - 1)];
        if (fallback) sourceActiveId = fallback.id;
      }
      const sourceAfter: BrowserWindowState = {
        ...fromWin,
        tabs: replacementTab ? [replacementTab] : fromTabs,
        activeTabId: sourceActiveId,
      };

      // Insert into destination at toIndex (or append if undefined)
      const insertAt = toIndex ?? toWin.tabs.length;
      const destTabs = [
        ...toWin.tabs.slice(0, insertAt),
        tab,
        ...toWin.tabs.slice(insertAt),
      ];
      const destAfter: BrowserWindowState = {
        ...toWin,
        tabs: destTabs,
        activeTabId: tab.id,
      };

      return {
        windows: {
          ...s.windows,
          [fromWindowId]: sourceAfter,
          [toWindowId]: destAfter,
        },
      };
    });
  },
  setTabReader(windowId, tabId, patch) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({ ...t, ...patch })));
  },

  addPinnedAgent(windowId, tabId, agentId) {
    set((s) => updateTab(s, windowId, tabId, (t) => {
      if (t.pinnedAgentIds.includes(agentId)) return t;
      return { ...t, pinnedAgentIds: [...t.pinnedAgentIds, agentId] };
    }));
  },

  removePinnedAgent(windowId, tabId, agentId) {
    set((s) => updateTab(s, windowId, tabId, (t) => ({
      ...t,
      pinnedAgentIds: t.pinnedAgentIds.filter((id) => id !== agentId),
    })));
  },

  switchProfile(windowId, newProfileId) {
    set((s) => {
      const win = s.windows[windowId];
      if (!win) return s;
      if (win.profileId === newProfileId) return s; // No-op if already active

      // Snapshot current state under the OLD profileId
      const savedMap = win._savedTabsByProfile ?? {};
      const updatedSavedMap = {
        ...savedMap,
        [win.profileId]: {
          tabs: win.tabs,
          activeTabId: win.activeTabId,
        },
      };

      // Restore from snapshot if one exists for the new profile,
      // otherwise initialise with a fresh new-tab page
      const restoredSnapshot = updatedSavedMap[newProfileId];
      const restoredTabs = restoredSnapshot?.tabs ?? [makeTab()];
      // restoredTabs is guaranteed non-empty (snapshots store non-empty tab
      // arrays; the fallback is [makeTab()]), so restoredTabs[0] is defined.
      const firstRestored = restoredTabs[0];
      const restoredActiveId =
        restoredSnapshot?.activeTabId ?? (firstRestored ? firstRestored.id : "");

      // Drop the new profile's snapshot from the saved map (it's now active)
      const { [newProfileId]: _consumed, ...remainingSnapshots } = updatedSavedMap;

      return {
        windows: {
          ...s.windows,
          [windowId]: {
            ...win,
            profileId: newProfileId,
            tabs: restoredTabs,
            activeTabId: restoredActiveId,
            _savedTabsByProfile: remainingSnapshots,
          },
        },
      };
    });
  },
}));

// Helper: produce updated state with a tab transform applied
function updateTab(
  s: { windows: Record<string, BrowserWindowState> },
  windowId: string,
  tabId: string,
  transform: (t: Tab) => Tab,
) {
  const win = s.windows[windowId];
  if (!win) return s;
  const tabs = win.tabs.map((t) => (t.id === tabId ? transform(t) : t));
  return { windows: { ...s.windows, [windowId]: { ...win, tabs } } };
}
