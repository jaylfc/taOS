/**
 * Fetch wrappers for /api/desktop/browser/windows.
 *
 * Used by useSessionPersistence to restore window state on app boot
 * and to debounce-save the browser-store state every 2 seconds.
 *
 * 401 responses no-op silently — the user isn't logged in yet, and
 * they shouldn't see an error in the console for that.
 */

import type { BrowserWindowState } from "@/apps/BrowserApp/types";

const ENDPOINT = "/api/desktop/browser/windows";

export interface PersistedWindow {
  window_id: string;
  profile_id: string;
  active_tab_id: string | null;
  state: string; // JSON-serialised BrowserWindowState (sans windowId — server has it)
  updated_at?: number;
}

export async function loadWindows(): Promise<PersistedWindow[]> {
  try {
    const resp = await fetch(ENDPOINT, { credentials: "include" });
    if (resp.status === 401) return [];
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.windows) ? body.windows : [];
  } catch {
    return [];
  }
}

export async function saveWindows(
  windows: Record<string, BrowserWindowState>,
): Promise<void> {
  const payload = {
    windows: Object.values(windows).map((w) => ({
      window_id: w.windowId,
      profile_id: w.profileId,
      active_tab_id: w.activeTabId,
      state: JSON.stringify({
        tabs: w.tabs,
        recentlyClosed: w.recentlyClosed,
      }),
    })),
  };
  try {
    const resp = await fetch(ENDPOINT, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    // Silent failure on 401 (user not logged in yet)
    if (!resp.ok && resp.status !== 401) {
      console.warn("browser windows save failed", resp.status);
    }
  } catch (err) {
    console.warn("browser windows save error", err);
  }
}

export async function deleteWindow(windowId: string): Promise<void> {
  try {
    await fetch(`${ENDPOINT}/${encodeURIComponent(windowId)}`, {
      method: "DELETE",
      credentials: "include",
    });
  } catch {
    // Silent failure — the worst case is a stale persisted entry
  }
}
