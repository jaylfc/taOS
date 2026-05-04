/**
 * BrowserApp v2 — TabRenderer.
 *
 * Iframe pool + discard scheduler. All non-discarded tabs render their
 * iframe at all times (display:none for inactive ones — preserves scroll,
 * video position, form state across tab switches without reload). Discarded
 * tabs render a snapshot card with a "click to reload" affordance.
 *
 * The discard scheduler runs every 60s and:
 *  - Discards non-pinned, non-active tabs whose lastActiveAt is older
 *    than DISCARD_TIMEOUT_MS.
 *  - Enforces a hard cap of MAX_LIVE_TABS live tabs by discarding the
 *    oldest live tab (by lastActiveAt) if the cap is exceeded.
 *
 * PR 5 (live exclusion) will further refine the discard rule — tabs
 * with playing audio/video, active form input, or in-flight upload
 * are exempt regardless of idle time. PR 4 ships the basic policy.
 */
import { useEffect } from "react";
import { useBrowserStore } from "@/stores/browser-store";
import type { Tab } from "./types";

export const DISCARD_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes
export const MAX_LIVE_TABS = 12;
const SCHEDULER_INTERVAL_MS = 60 * 1000; // 60 seconds

interface TabRendererProps {
  windowId: string;
}

export function TabRenderer({ windowId }: TabRendererProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const markTabDiscarded = useBrowserStore((s) => s.markTabDiscarded);
  const markTabLive = useBrowserStore((s) => s.markTabLive);

  // Discard scheduler — ticks every 60s while the window is mounted.
  useEffect(() => {
    if (!win) return;
    const interval = setInterval(() => {
      const current = useBrowserStore.getState().windows[windowId];
      if (!current) return;

      const now = Date.now();
      const liveTabs = current.tabs.filter((t) => t.state === "live");

      // Pass 1: idle-based discard
      for (const tab of liveTabs) {
        if (tab.id === current.activeTabId) continue;
        if (tab.pinned) continue;
        if (now - tab.lastActiveAt > DISCARD_TIMEOUT_MS) {
          useBrowserStore.getState().markTabDiscarded(windowId, tab.id);
        }
      }

      // Pass 2: hard cap enforcement
      const refreshed = useBrowserStore.getState().windows[windowId];
      if (!refreshed) return;
      const stillLive = refreshed.tabs.filter((t) => t.state === "live");
      const overflowCount = stillLive.length - MAX_LIVE_TABS;
      if (overflowCount > 0) {
        // Discard oldest non-pinned non-active until at cap
        const candidates = stillLive
          .filter((t) => !t.pinned && t.id !== refreshed.activeTabId)
          .sort((a, b) => a.lastActiveAt - b.lastActiveAt);
        for (let i = 0; i < overflowCount && i < candidates.length; i++) {
          useBrowserStore.getState().markTabDiscarded(
            windowId,
            candidates[i].id,
          );
        }
      }
    }, SCHEDULER_INTERVAL_MS);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowId, !!win]);

  if (!win) return null;

  return (
    <div className="relative flex-1 bg-shell-bg-deep overflow-hidden">
      {win.tabs.map((tab) => {
        const isActive = tab.id === win.activeTabId;
        if (tab.state === "discarded") {
          return isActive ? (
            <DiscardedPlaceholder
              key={tab.id}
              tab={tab}
              onReload={() => markTabLive(windowId, tab.id)}
            />
          ) : null;
        }

        return (
          <iframe
            key={tab.id}
            title={tab.title || tab.url || "Browser tab"}
            src={proxiedSrc(win.profileId, tab.url)}
            data-tab-id={tab.id}
            // sandbox: allow-same-origin intentionally OMITTED. The proxy
            // serves on the same origin as the shell; combining
            // allow-same-origin + allow-scripts would let proxied JS reach
            // up into the parent and remove this attribute. The HTTPS+DNS
            // Foundations brainstorm will land an isolated subdomain that
            // makes allow-same-origin safe to add back.
            sandbox="allow-scripts allow-forms allow-popups allow-downloads"
            style={{
              display: isActive ? "block" : "none",
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              border: "none",
              transform: tab.zoom !== 1 ? `scale(${tab.zoom})` : undefined,
              transformOrigin: "top left",
            }}
          />
        );
      })}
    </div>
  );
}

interface DiscardedPlaceholderProps {
  tab: Tab;
  onReload: () => void;
}

function DiscardedPlaceholder({ tab, onReload }: DiscardedPlaceholderProps) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-shell-text-secondary text-sm">
      <div className="text-xs uppercase tracking-wide opacity-70">
        Tab snoozed
      </div>
      <div className="font-medium">{tab.title || tab.url || "Untitled tab"}</div>
      {tab.url && (
        <div className="text-xs opacity-70 max-w-[400px] truncate">
          {tab.url}
        </div>
      )}
      <button
        type="button"
        onClick={onReload}
        className="mt-2 px-3 py-1 rounded bg-shell-surface border border-shell-border-subtle hover:bg-shell-hover text-xs"
      >
        Click to reload
      </button>
    </div>
  );
}

/** Build the proxied iframe src. about:blank passes through unproxied. */
function proxiedSrc(profileId: string, url: string): string {
  if (!url || url === "about:blank" || url.startsWith("about:")) {
    return "about:blank";
  }
  const params = new URLSearchParams({ profile_id: profileId, url });
  return `/api/desktop/browser/proxy?${params.toString()}`;
}
