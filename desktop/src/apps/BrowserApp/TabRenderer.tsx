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
 *    than DISCARD_TIMEOUT_MS (default; runtime value from useBrowserSettingsStore).
 *  - Enforces a hard cap of MAX_LIVE_TABS live tabs by discarding the
 *    oldest live tab (by lastActiveAt) if the cap is exceeded (default;
 *    runtime value from useBrowserSettingsStore).
 *
 * PR 5 (live exclusion) will further refine the discard rule — tabs
 * with playing audio/video, active form input, or in-flight upload
 * are exempt regardless of idle time. PR 4 ships the basic policy.
 */
import { useEffect, useState } from "react";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserSettingsStore } from "@/stores/browser-settings-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import { mintCopilotTicket } from "@/lib/browser-agent-api";
import { openParentWs } from "./agent-ws-bridge";
import type { AgentWsHandle } from "./agent-ws-bridge";
import { detectLiveExclusion } from "./live-exclusion";
import { ReaderMode } from "./ReaderMode";
import { AgentPanel } from "./AgentPanel";
import { PageContextMenu } from "./PageContextMenu";
import type { Tab } from "./types";

export const DISCARD_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes
export const MAX_LIVE_TABS = 12;
const SCHEDULER_INTERVAL_MS = 60 * 1000; // 60 seconds

interface TabRendererProps {
  windowId: string;
}

export function TabRenderer({ windowId }: TabRendererProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const markTabLive = useBrowserStore((s) => s.markTabLive);

  // Discard scheduler — ticks every 60s while the window is mounted.
  useEffect(() => {
    if (!win) return;
    const interval = setInterval(() => {
      const current = useBrowserStore.getState().windows[windowId];
      if (!current) return;

      const now = Date.now();
      const liveTabs = current.tabs.filter((t) => t.state === "live");

      // Pass 1: idle-based discard with live-exclusion respect
      for (const tab of liveTabs) {
        if (tab.id === current.activeTabId) continue;
        // Detect any live activity in the iframe — playing media, active
        // form input, in-flight upload — and exempt those tabs.
        const iframe = document.querySelector(
          `iframe[data-tab-id="${tab.id}"]`,
        ) as HTMLIFrameElement | null;
        const exclusion = iframe
          ? detectLiveExclusion(iframe, tab.pinned)
          : (tab.pinned ? "pinned" : undefined);
        // Update the tab so the UI can surface "kept alive: video"
        if (exclusion !== tab.liveExclusion) {
          useBrowserStore.getState().setTabLiveExclusion(
            windowId, tab.id, exclusion,
          );
        }
        if (exclusion) continue; // exempt
        const { discardTimeoutMs } = useBrowserSettingsStore.getState();
        if (now - tab.lastActiveAt > discardTimeoutMs) {
          useBrowserStore.getState().markTabDiscarded(windowId, tab.id);
        }
      }

      // Pass 2: hard cap enforcement
      const refreshed = useBrowserStore.getState().windows[windowId];
      if (!refreshed) return;
      const stillLive = refreshed.tabs.filter((t) => t.state === "live");
      const { maxLiveTabs } = useBrowserSettingsStore.getState();
      const overflowCount = stillLive.length - maxLiveTabs;
      if (overflowCount > 0) {
        // Discard oldest non-pinned non-active until at cap
        const candidates = stillLive
          .filter((t) =>
            !t.pinned
            && t.id !== refreshed.activeTabId
            && !t.liveExclusion
          )
          .sort((a, b) => a.lastActiveAt - b.lastActiveAt);
        for (let i = 0; i < overflowCount && i < candidates.length; i++) {
          const candidate = candidates[i];
          if (!candidate) continue;
          useBrowserStore.getState().markTabDiscarded(windowId, candidate.id);
        }
      }
    }, SCHEDULER_INTERVAL_MS);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowId, !!win]);

  // WS lifecycle — for each pinned agent on the active tab:
  //   1. Mint an iframe ticket and postMessage taos-copilot:open to the iframe.
  //      copilot.js opens the WS and forwards server events back to the parent
  //      via postMessage.
  //   2. Register a parent-side message listener via openParentWs so we
  //      receive forwarded page-changed events and bump the presence pulse.
  // We do NOT open a second parent WebSocket — both connections would register
  // under the same hub key and clobber each other.
  const activeTabIdForEffect = win?.activeTabId;
  const pinnedAgentIdsForEffect = win?.tabs.find((t) => t.id === win?.activeTabId)?.pinnedAgentIds ?? [];
  const profileIdForEffect = win?.profileId;

  useEffect(() => {
    if (!activeTabIdForEffect || !profileIdForEffect) return;

    const tabId = activeTabIdForEffect;
    const profileId = profileIdForEffect;
    const iframe = document.querySelector(
      `iframe[data-tab-id="${tabId}"]`,
    ) as HTMLIFrameElement | null;
    if (!iframe) return;

    const handles: AgentWsHandle[] = [];
    let cancelled = false;

    for (const agentId of pinnedAgentIdsForEffect) {
      // 1. Mint a ticket for the iframe-side WS and post it.
      mintCopilotTicket(profileId, tabId, agentId).then((ticket) => {
        if (cancelled || !ticket) return;
        if (iframe.contentWindow) {
          iframe.contentWindow.postMessage(
            { type: "taos-copilot:open", ticket: ticket.ticket, agentId },
            "*",
          );
        }
      });

      // 2. Register a parent-side listener for events forwarded by copilot.js.
      const handle = openParentWs({
        windowId,
        tabId,
        agentId,
        iframe,
        onEvent: (event) => {
          const store = useBrowserAgentStore.getState();
          store.bumpEventAt(windowId, tabId, agentId);
          store.appendEvent(windowId, tabId, agentId, event);
        },
      });
      handles.push(handle);
    }

    return () => {
      cancelled = true;

      // Close all parent-side listeners
      for (const handle of handles) handle.close();

      // Tell the iframe-side copilot.js to close its WS for each agent
      if (iframe.contentWindow) {
        for (const agentId of pinnedAgentIdsForEffect) {
          iframe.contentWindow.postMessage(
            { type: "taos-copilot:close", agentId },
            "*",
          );
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowId, activeTabIdForEffect, pinnedAgentIdsForEffect.join(","), profileIdForEffect]);

  // Prime the Service Worker with the active tab's page base URL + profile ID
  // so it can rewrite relative SPA fetch calls through the proxy.
  // Registration moved to BrowserApp.tsx (parent shell) since copilot.js runs
  // in a sandboxed iframe where navigator.serviceWorker is unavailable.
  // Uses .ready so the message is delivered whether or not the SW has claimed
  // this client yet.
  const activeTabUrlForSW = win?.tabs.find((t) => t.id === win?.activeTabId)?.url;
  useEffect(() => {
    if (!win || !activeTabUrlForSW) return;
    if (!('serviceWorker' in navigator)) return;
    if (!activeTabUrlForSW || activeTabUrlForSW === "about:blank" || activeTabUrlForSW.startsWith("about:")) return;
    navigator.serviceWorker.ready.then((reg) => {
      if (reg.active) {
        reg.active.postMessage({
          type: "taos-sw:prime",
          pageBaseUrl: activeTabUrlForSW,
          profileId: win.profileId,
        });
      }
    });
  }, [activeTabUrlForSW, win?.profileId, win?.activeTabId]);

  // Subscribe to panel state so TabRenderer re-renders when panel opens/closes.
  // win?.activeTabId is safely undefined when win is undefined (hook must be
  // called unconditionally, so the guard comes after).
  const panelIsOpen =
    useBrowserAgentStore(
      (s) => (win ? s.panels[`${windowId}:${win.activeTabId}`]?.isOpen : false),
    ) ?? false;

  // Driving tint — show subtle green overlay on the iframe when an agent is driving.
  const drivingAgentId = useBrowserAgentStore((s) =>
    win ? s.isAnyDriving(windowId, win.activeTabId) : null,
  );

  // Context menu state — position where the user right-clicked.
  // PR 6 limitation: only fires when right-clicking on the iframe wrapper border,
  // not on the iframe content itself (sandbox blocks contextmenu propagation to
  // the parent). PR 7 will add copilot.js → parent postMessage forwarding so
  // right-click anywhere on the page surface reaches this handler.
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);

  if (!win) return null;

  const activeTab = win.tabs.find((t) => t.id === win.activeTabId);
  const pinnedAgentIds = activeTab?.pinnedAgentIds ?? [];

  return (
    <div className="flex flex-1 overflow-hidden bg-shell-bg-deep">
      {/* Iframe pool — relative container so iframes can position absolutely */}
      <div
        className="relative flex-1 overflow-hidden"
        onContextMenu={(e) => {
          // Right-click on the iframe wrapper. See comment above for PR 6 limitation.
          e.preventDefault();
          if (activeTab) {
            setContextMenu({ x: e.clientX, y: e.clientY });
          }
        }}
      >
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

          const showReader = isActive && !!tab.readerActive && !!tab.readerExtract;

          return (
            <div
              key={tab.id}
              style={{ display: isActive ? "contents" : "none" }}
              data-window-tab={tab.id}
            >
              <iframe
                title={tab.title || tab.url || "Browser tab"}
                src={proxiedSrc(win.profileId, tab.url, tab.id)}
                data-tab-id={tab.id}
                // sandbox: allow-same-origin intentionally OMITTED. The proxy
                // serves on the same origin as the shell; combining
                // allow-same-origin + allow-scripts would let proxied JS reach
                // up into the parent and remove this attribute. The HTTPS+DNS
                // Foundations brainstorm will land an isolated subdomain that
                // makes allow-same-origin safe to add back.
                sandbox="allow-scripts allow-forms allow-popups allow-downloads"
                style={{
                  display: isActive && !showReader ? "block" : "none",
                  position: "absolute",
                  inset: 0,
                  width: "100%",
                  height: "100%",
                  border: "none",
                  transform: tab.zoom !== 1 ? `scale(${tab.zoom})` : undefined,
                  transformOrigin: "top left",
                }}
              />
              {showReader && (
                <ReaderMode tab={tab} windowId={windowId} />
              )}
            </div>
          );
        })}

        {/* Driving tint — semi-transparent green overlay when an agent is driving */}
        {drivingAgentId && (
          <div
            className="absolute inset-0 bg-green-500/10 pointer-events-none"
            aria-hidden="true"
          />
        )}

        {/* Page context menu — shown when user right-clicks the iframe wrapper */}
        {contextMenu && activeTab && (
          <PageContextMenu
            windowId={windowId}
            tabId={activeTab.id}
            profileId={win.profileId}
            url={activeTab.url}
            title={activeTab.title || activeTab.url || ""}
            selection={null}
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={() => setContextMenu(null)}
          />
        )}
      </div>

      {/* Agent panel — renders to the right of the iframe; iframe squeezes via flex */}
      {panelIsOpen && pinnedAgentIds.length > 0 && (
        <AgentPanel
          windowId={windowId}
          tabId={win.activeTabId}
          profileId={win.profileId}
          pinnedAgentIds={pinnedAgentIds}
        />
      )}
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

/** Build the proxied iframe src. about:blank passes through unproxied.
 * tab_id is included so the proxy can fan out page-changed events to
 * any agents pinned to the tab (see proxy.py + CopilotHub).
 */
function proxiedSrc(profileId: string, url: string, tabId: string): string {
  if (!url || url === "about:blank" || url.startsWith("about:")) {
    return "about:blank";
  }
  const params = new URLSearchParams({
    profile_id: profileId,
    url,
    tab_id: tabId,
  });
  return `/api/desktop/browser/proxy?${params.toString()}`;
}
