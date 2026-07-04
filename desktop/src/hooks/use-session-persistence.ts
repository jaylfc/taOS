import { useEffect, useRef } from "react";
import { useProcessStore, type SnapPosition } from "@/stores/process-store";
import { useDockStore } from "@/stores/dock-store";
import { useThemeStore } from "@/stores/theme-store";
import { useWidgetStore, type Widget } from "@/stores/widget-store";
import { getApp } from "@/registry/app-registry";
import { useBrowserStore } from "@/stores/browser-store";
import { loadWindows as loadBrowserWindows, saveWindows as saveBrowserWindows } from "@/lib/browser-windows-api";
import type { BrowserWindowState } from "@/apps/BrowserApp/types";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

interface SavedWindow {
  appId: string;
  x: number;
  y: number;
  w: number;
  h: number;
  maximized: boolean;
  snapped: string | null;
}

export function useSessionPersistence() {
  const windows = useProcessStore((s) => s.windows);
  const openWindow = useProcessStore((s) => s.openWindow);
  const updatePosition = useProcessStore((s) => s.updatePosition);
  const updateSize = useProcessStore((s) => s.updateSize);
  const maximizeWindow = useProcessStore((s) => s.maximizeWindow);
  const snapWindow = useProcessStore((s) => s.snapWindow);
  const pinned = useDockStore((s) => s.pinned);
  const dockIconSize = useDockStore((s) => s.iconSize);
  const dockPosition = useDockStore((s) => s.position);
  const wallpaperId = useThemeStore((s) => s.wallpaperId);
  const widgets = useWidgetStore((s) => s.widgets);

  const browserWindows = useBrowserStore((s) => s.windows);

  const authReady = useAuthReadyStore((s) => s.ready);

  // `restored` guards the whole restore effect below (fires once per
  // authenticated session). `dockRestored`/`wallpaperRestored` gate their own
  // auto-save effects independently: the shared `restored` flag used to flip
  // true the instant the effect *started*, not once its fetches actually
  // landed, so a debounced auto-save (500ms wallpaper / 1s dock) could fire
  // with the still-default in-memory value and overwrite the real saved
  // setting before the slower restore GET came back (#1601, #1603).
  const restored = useRef(false);
  const dockRestored = useRef(false);
  const wallpaperRestored = useRef(false);
  const saveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dockTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wallpaperTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const widgetSaveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const browserSaveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Restore everything once authenticated (once per session).
  //
  // SystemShortcuts (which owns this hook) mounts as a sibling of LoginGate,
  // before LoginGate's /auth/status check resolves, so a mount-gated restore
  // fires while logged out, 401s, and — since it only ran once — is never
  // retried after a subsequent login. Gating on `authReady` (and resetting
  // when it drops) means a fresh login always re-fetches this session's
  // settings instead of leaving the defaults the pre-auth attempt left behind.
  useEffect(() => {
    if (!authReady) {
      restored.current = false;
      dockRestored.current = false;
      wallpaperRestored.current = false;
      return;
    }
    if (restored.current) return;
    restored.current = true;

    // Restore windows
    fetch("/api/desktop/windows")
      .then((r) => r.json())
      .then((positions: SavedWindow[]) => {
        if (!Array.isArray(positions)) return;
        for (const saved of positions) {
          const app = getApp(saved.appId);
          if (!app) continue;
          const wid = openWindow(saved.appId, { w: saved.w, h: saved.h });
          updatePosition(wid, saved.x, saved.y);
          updateSize(wid, saved.w, saved.h);
          if (saved.maximized) {
            maximizeWindow(wid);
          }
          if (saved.snapped) {
            snapWindow(wid, saved.snapped as SnapPosition);
          }
        }
      })
      .catch(() => {});

    // Restore dock
    fetch("/api/desktop/dock")
      .then((r) => r.json())
      .then((data: { pinned?: string[]; iconSize?: string; position?: string }) => {
        if (data.pinned && Array.isArray(data.pinned)) {
          useDockStore.getState().reorder(data.pinned);
        }
        if (data.iconSize === "small" || data.iconSize === "medium" || data.iconSize === "large") {
          useDockStore.getState().setIconSize(data.iconSize);
        }
        if (data.position === "bottom" || data.position === "left") {
          useDockStore.getState().setPosition(data.position);
        }
      })
      .catch(() => {})
      .finally(() => {
        dockRestored.current = true;
      });

    // Restore wallpaper
    fetch("/api/desktop/settings")
      .then((r) => r.json())
      .then((data: { wallpaper?: string }) => {
        if (data.wallpaper) {
          useThemeStore.getState().setWallpaper(data.wallpaper);
        }
      })
      .catch(() => {})
      .finally(() => {
        wallpaperRestored.current = true;
      });

    // Restore widgets
    fetch("/api/desktop/widgets")
      .then((r) => r.json())
      .then((data: Widget[] | { widgets: Widget[] }) => {
        const list = Array.isArray(data) ? data : (data.widgets ?? []);
        if (list.length > 0) {
          useWidgetStore.setState({ widgets: list });
        }
      })
      .catch(() => {});

    // Restore browser windows
    loadBrowserWindows()
      .then((rows) => {
        if (rows.length === 0) return;
        const windows: Record<string, BrowserWindowState> = {};
        for (const row of rows) {
          try {
            const state = JSON.parse(row.state);
            windows[row.window_id] = {
              windowId: row.window_id,
              profileId: row.profile_id,
              tabs: state.tabs ?? [],
              activeTabId: row.active_tab_id ?? "",
              recentlyClosed: state.recentlyClosed ?? [],
            };
          } catch {
            // Skip malformed entries
          }
        }
        useBrowserStore.setState({ windows });
      })
      .catch(() => {});
  }, [authReady, openWindow, updatePosition, updateSize, maximizeWindow, snapWindow]);

  // Auto-save windows (debounced 2s)
  useEffect(() => {
    if (!restored.current) return;

    if (saveTimeout.current) clearTimeout(saveTimeout.current);
    saveTimeout.current = setTimeout(() => {
      const state: SavedWindow[] = windows.map((w) => ({
        appId: w.appId,
        x: w.position.x,
        y: w.position.y,
        w: w.size.w,
        h: w.size.h,
        maximized: w.maximized,
        snapped: w.snapped,
      }));

      fetch("/api/desktop/windows", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ positions: state }),
      }).catch(() => {});
    }, 2000);

    return () => {
      if (saveTimeout.current) clearTimeout(saveTimeout.current);
    };
  }, [windows]);

  // Auto-save dock (debounced 1s). Gated on dockRestored, not the outer
  // `restored` flag: that flips true the instant the restore effect starts,
  // before its fetch resolves, so this would otherwise fire on mount with
  // the still-default dock state and could beat a slow restore GET to the
  // backend, overwriting the real saved dock settings (#1603).
  useEffect(() => {
    if (!dockRestored.current) return;

    if (dockTimeout.current) clearTimeout(dockTimeout.current);
    dockTimeout.current = setTimeout(() => {
      fetch("/api/desktop/dock", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned, iconSize: dockIconSize, position: dockPosition }),
      }).catch(() => {});
    }, 1000);

    return () => {
      if (dockTimeout.current) clearTimeout(dockTimeout.current);
    };
  }, [pinned, dockIconSize, dockPosition]);

  // Auto-save wallpaper (debounced 500ms). Gated on wallpaperRestored (see
  // the dock effect above for why the outer `restored` flag isn't enough).
  useEffect(() => {
    if (!wallpaperRestored.current) return;

    if (wallpaperTimeout.current) clearTimeout(wallpaperTimeout.current);
    wallpaperTimeout.current = setTimeout(() => {
      fetch("/api/desktop/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wallpaper: wallpaperId }),
      }).catch(() => {});
    }, 500);

    return () => {
      if (wallpaperTimeout.current) clearTimeout(wallpaperTimeout.current);
    };
  }, [wallpaperId]);

  // Auto-save widgets (debounced 1s)
  useEffect(() => {
    if (!restored.current) return;

    if (widgetSaveTimeout.current) clearTimeout(widgetSaveTimeout.current);
    widgetSaveTimeout.current = setTimeout(() => {
      fetch("/api/desktop/widgets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ widgets }),
      }).catch(() => {});
    }, 1000);

    return () => {
      if (widgetSaveTimeout.current) clearTimeout(widgetSaveTimeout.current);
    };
  }, [widgets]);

  // Auto-save browser windows (debounced 2s)
  useEffect(() => {
    if (!restored.current) return;
    if (browserSaveTimeout.current) clearTimeout(browserSaveTimeout.current);
    browserSaveTimeout.current = setTimeout(() => {
      saveBrowserWindows(browserWindows).catch(() => {});
    }, 2000);
    return () => {
      if (browserSaveTimeout.current) clearTimeout(browserSaveTimeout.current);
    };
  }, [browserWindows]);
}
