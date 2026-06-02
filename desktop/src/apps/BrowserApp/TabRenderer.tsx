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
import type { CSSProperties } from "react";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserSettingsStore } from "@/stores/browser-settings-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import { mintCopilotTicket } from "@/lib/browser-agent-api";
import {
  buildProxiedPath,
  buildRedeemUrl,
  getBrowserProxyOrigin,
  mintProxyTicket,
} from "@/lib/browser-proxy-config";
import { openParentWs } from "./agent-ws-bridge";
import type { AgentWsHandle } from "./agent-ws-bridge";
import { detectLiveExclusion } from "./live-exclusion";
import { ReaderMode } from "./ReaderMode";
import { LiveBrowserView } from "./LiveBrowserView";
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

    // Resolve the proxy origin once for this effect so all postMessage calls
    // target the exact proxy origin rather than "*". This prevents copilot
    // tickets from being broadcast to any origin that happens to be embedded.
    getBrowserProxyOrigin().then((proxyOrigin) => {
      if (cancelled) return;

      for (const agentId of pinnedAgentIdsForEffect) {
        // 1. Mint a ticket for the iframe-side WS and post it.
        mintCopilotTicket(profileId, tabId, agentId).then((ticket) => {
          if (cancelled || !ticket) return;
          if (iframe.contentWindow) {
            iframe.contentWindow.postMessage(
              { type: "taos-copilot:open", ticket: ticket.ticket, agentId },
              proxyOrigin,
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
    });

    return () => {
      cancelled = true;

      // Close all parent-side listeners
      for (const handle of handles) handle.close();

      // Tell the iframe-side copilot.js to close its WS for each agent.
      // Resolve the proxy origin for the close messages too.
      getBrowserProxyOrigin().then((proxyOrigin) => {
        if (!iframe.contentWindow) return;
        for (const agentId of pinnedAgentIdsForEffect) {
          iframe.contentWindow.postMessage(
            { type: "taos-copilot:close", agentId },
            proxyOrigin,
          );
        }
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowId, activeTabIdForEffect, pinnedAgentIdsForEffect.join(","), profileIdForEffect]);

  // Tab-focus postMessage — keep copilot.js informed of which tab is active
  // so it can forward tab-focus events to the server. Fires whenever the
  // active tab changes, not on every render (effect deps are stable).
  useEffect(() => {
    if (!win) return;
    const tabs = win.tabs;
    const activeId = win.activeTabId;
    // Use the proxy origin as the postMessage target so we don't broadcast
    // tab-focus messages to "*".
    getBrowserProxyOrigin().then((proxyOrigin) => {
      for (const tab of tabs) {
        const iframe = document.querySelector(
          `iframe[data-tab-id="${tab.id}"]`,
        ) as HTMLIFrameElement | null;
        if (!iframe?.contentWindow) continue;
        const focused = tab.id === activeId;
        iframe.contentWindow.postMessage(
          {
            type: "taos-copilot:tab-focus",
            window_id: windowId,
            tab_id: tab.id,
            focused,
          },
          proxyOrigin,
        );
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowId, activeTabIdForEffect]);

  // NOTE: Service Worker registration + priming now happen INSIDE the iframe
  // (copilot.js on the proxy origin). The iframe is a real, separate origin
  // with allow-same-origin, so navigator.serviceWorker works there. The SW
  // belongs to the proxy origin — not the shell — so there is no shell-side
  // registration or prime here any more.

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

          // Full Neko session replaces the proxy iframe for the active tab
          if (isActive && tab.liveSession) {
            return (
              <div
                key={tab.id}
                style={{ position: "absolute", inset: 0 }}
                data-window-tab={tab.id}
              >
                <LiveBrowserView
                  nekoUrl={tab.liveSession.nekoUrl}
                  streamToken={tab.liveSession.streamToken}
                />
              </div>
            );
          }

          return (
            <div
              key={tab.id}
              style={{ display: isActive ? "contents" : "none" }}
              data-window-tab={tab.id}
            >
              <TabFrame
                profileId={win.profileId}
                url={tab.url}
                tabId={tab.id}
                title={tab.title}
                zoom={tab.zoom}
                visible={isActive && !showReader}
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

interface TabFrameProps {
  profileId: string;
  url: string;
  tabId: string;
  title: string;
  zoom: number;
  visible: boolean;
}

/**
 * Renders a single tab's iframe via the ticketed redeem flow.
 *
 * For each top-level navigation we mint a fresh 30s single-use proxy ticket
 * (same-origin, credentialed), then point the iframe at
 * `<proxyOrigin>/__taos/redeem?ticket=…&next=<proxiedPath>`. The redeem sets
 * the taos_browser cookie on the proxy origin and 302s to the proxied page;
 * every subsequent in-iframe request carries the cookie automatically. In-page
 * SPA fetches are intercepted by the proxy-origin service worker (registered
 * by copilot.js), not re-redeemed.
 *
 * about:blank URLs render an empty iframe. A ticket-mint failure surfaces an
 * inline error instead of a blank iframe.
 */
function TabFrame({ profileId, url, tabId, title, zoom, visible }: TabFrameProps) {
  // null = loading; "" = about:blank passthrough; string = redeem URL.
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Whether the proxy origin is cross-origin to the shell. Only then is
  // allow-same-origin safe to grant (separate, API-free origin). In
  // single-port mode the proxy IS the shell origin, so we withhold it and
  // keep the historical opaque-origin sandbox.
  const [crossOrigin, setCrossOrigin] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const proxiedPath = buildProxiedPath(profileId, url, tabId);
    if (!proxiedPath) {
      // about:blank / about: — no proxy, no ticket.
      setError(null);
      setSrc("");
      return;
    }

    setError(null);
    setSrc(null); // show nothing until the ticket resolves

    (async () => {
      const [proxyOrigin, ticket] = await Promise.all([
        getBrowserProxyOrigin(),
        mintProxyTicket(),
      ]);
      if (cancelled) return;
      if (!ticket) {
        setError("Couldn’t establish a secure browsing session. Try again.");
        return;
      }
      setCrossOrigin(proxyOrigin !== window.location.origin);
      setSrc(buildRedeemUrl(proxyOrigin, ticket, proxiedPath));
    })();

    return () => {
      cancelled = true;
    };
  }, [profileId, url, tabId]);

  const frameStyle: CSSProperties = {
    display: visible && !error ? "block" : "none",
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    border: "none",
    transform: zoom !== 1 ? `scale(${zoom})` : undefined,
    transformOrigin: "top left",
  };

  return (
    <>
      <iframe
        title={title || url || "Browser tab"}
        // about:blank passthrough; otherwise the redeem URL on the proxy
        // origin (or "" while the ticket is in flight → about:blank).
        src={src === "" ? "about:blank" : src ?? "about:blank"}
        data-tab-id={tabId}
        // sandbox: allow-same-origin is added ONLY when the proxy is served on
        // a SEPARATE, API-free origin (the proxy port), cross-origin to the
        // taOS shell. There it is safe: the page script runs as its own origin
        // that exposes no taOS APIs and cannot reach the shell DOM, so it
        // cannot remove this sandbox or touch taOS state — and allow-same-origin
        // is what lets the proxied page register a service worker (SPA fetch
        // routing). In single-port mode the proxy IS the shell origin, so we
        // withhold allow-same-origin and keep the historical opaque sandbox.
        sandbox={
          crossOrigin
            ? "allow-scripts allow-forms allow-popups allow-downloads allow-same-origin"
            : "allow-scripts allow-forms allow-popups allow-downloads"
        }
        style={frameStyle}
      />
      {error && visible && (
        <div
          className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-shell-text-secondary text-sm"
          data-tab-error={tabId}
        >
          <div className="font-medium">Couldn’t load this page</div>
          <div className="text-xs opacity-70 max-w-[400px] text-center">{error}</div>
        </div>
      )}
    </>
  );
}
