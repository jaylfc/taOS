/**
 * Shared types for the BrowserApp v2 frontend.
 *
 * Tab represents one browser tab inside a browser window.
 * BrowserWindowState is one entry in the browser-store, keyed by
 * windowId (which matches the process-store's window id).
 */

export type LiveExclusion = "audio" | "video" | "form-active" | "upload" | "pinned";

export interface Tab {
  id: string;
  url: string;
  title: string;
  faviconUrl?: string;
  pinned: boolean;
  history: string[];
  historyIndex: number;
  scrollY: number;
  zoom: number;
  state: "live" | "discarded";
  lastActiveAt: number;
  liveExclusion?: LiveExclusion;
}

export interface RecentlyClosedTab {
  url: string;
  title: string;
  closedAt: number;
}

export interface SavedProfileTabs {
  tabs: Tab[];
  activeTabId: string;
}

export interface BrowserWindowState {
  windowId: string;
  profileId: string;
  tabs: Tab[];
  activeTabId: string;
  recentlyClosed: RecentlyClosedTab[];
  /**
   * Per-(window, profile) snapshot of tabs + activeTabId.
   * switchProfile snapshots the current state under the OLD profileId
   * before swapping; restores from this map on switch back.
   * Keyed by profile_id.
   */
  _savedTabsByProfile?: Record<string, SavedProfileTabs>;
}
