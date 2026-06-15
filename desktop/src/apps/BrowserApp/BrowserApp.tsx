/**
 * BrowserApp v2 — top-level container.
 *
 * Mounted by WindowContent for each browser window. Composes (top to bottom):
 *   - TabStrip    (tab strip + Proxy/Streamed engine toggle)
 *   - Chrome      (toolbar: nav buttons, pill omnibox with AddressBar, agent
 *                  presence pill, settings, profile chip)
 *   - BookmarksBar
 *   - TabRenderer (iframe pool + discard scheduler)
 *
 * On mobile, a single bottom bar hosts the window switcher, the AddressBar
 * omnibox, and the tab overview.
 *
 * On mount, auto-creates the window entry in browser-store with the
 * default profile if it doesn't exist. Idempotent — preserves any
 * existing entry (e.g. restored by useSessionPersistence on app boot).
 */
import { useEffect, useState } from "react";
import { useBrowserStore } from "@/stores/browser-store";
import { useProcessStore } from "@/stores/process-store";
import { deleteWindow as deleteServerWindow } from "@/lib/browser-windows-api";
import { Chrome } from "./Chrome";
import { TabStrip } from "./TabStrip";
import { AddressBar } from "./AddressBar";
import { TabRenderer } from "./TabRenderer";
import { useBrowserKeyboardShortcuts } from "./keyboard";
import { FindInPage } from "./FindInPage";
import { TabOverview } from "./TabOverview";
import { WindowChooser } from "./WindowChooser";
import { CapabilityPromptModal } from "./CapabilityPromptModal";
import { BookmarksBar } from "./BookmarksBar";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { Layers, ListChecks } from "lucide-react";
import { bootstrapPushSubscription } from "../../lib/browser-push-bootstrap";

const DEFAULT_PROFILE_ID = "personal";

interface BrowserAppProps {
  windowId: string;
}

export function BrowserApp({ windowId }: BrowserAppProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const createWindow = useBrowserStore((s) => s.createWindow);
  const setActiveTab = useBrowserStore((s) => s.setActiveTab);
  const focusWindow = useProcessStore((s) => s.focusWindow);
  const isFocused = useProcessStore(
    (s) => s.windows.find((w) => w.id === windowId)?.focused ?? false,
  );
  const isMobile = useIsMobile(600);
  const [findOpen, setFindOpen] = useState(false);
  const [tabOverviewOpen, setTabOverviewOpen] = useState(false);
  const [windowChooserOpen, setWindowChooserOpen] = useState(false);

  // Auto-create on first mount. createWindow is idempotent so calling
  // it when the window already exists (e.g. restored by persistence)
  // is a no-op.
  useEffect(() => {
    createWindow(windowId, DEFAULT_PROFILE_ID);
  }, [windowId, createWindow]);

  // The proxy service worker (/__taos/sw.js) now lives on the proxy origin and
  // is registered from INSIDE the iframe (copilot.js), not here — the SW must
  // belong to the proxy origin to intercept the proxied page's SPA fetches.
  // We still bootstrap the web-push subscription, which binds to whichever
  // service worker controls this shell origin.
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    bootstrapPushSubscription().catch(() => { /* swallow — non-fatal */ });
  }, []);

  // Cleanup on unmount: remove the window from browser-store + delete server row
  useEffect(() => {
    return () => {
      useBrowserStore.getState().removeWindow(windowId);
      deleteServerWindow(windowId).catch(() => {});
    };
  }, [windowId]);

  useBrowserKeyboardShortcuts({
    windowId,
    hasFocus: isFocused,
    onOpenFind: () => setFindOpen(true),
  });

  // Wait for the window entry to materialise (one render tick after
  // the createWindow set call). Until then render an empty placeholder.
  if (!win) {
    return <div className="flex-1 bg-shell-bg-deep" />;
  }

  if (isMobile) {
    return (
      <div className="flex flex-col h-full bg-shell-bg overflow-hidden relative">
        {windowChooserOpen && (
          <WindowChooser
            currentWindowId={windowId}
            onSelect={(id) => {
              if (id !== windowId) focusWindow(id);
            }}
            onClose={() => setWindowChooserOpen(false)}
          />
        )}

        {/* `flex` is required: TabRenderer's root is `flex flex-1` and only
            grows to fill height when its parent is itself a flex container.
            Without it the renderer collapses to 0 height and the page area is
            blank (the desktop layout mounts TabRenderer directly in the column
            flex root, so it doesn't hit this). */}
        <div className="flex flex-1 relative overflow-hidden">
          <TabRenderer windowId={windowId} />
          {tabOverviewOpen && (
            <TabOverview
              windowId={windowId}
              onSelect={(id) => setActiveTab(windowId, id)}
              onClose={() => setTabOverviewOpen(false)}
            />
          )}
          {findOpen && (
            <FindInPage windowId={windowId} onClose={() => setFindOpen(false)} />
          )}
        </div>

        <div className="flex items-center gap-1 px-2 py-1 bg-shell-surface border-t border-shell-border">
          <button
            type="button"
            aria-label="Browser windows"
            onClick={() => setWindowChooserOpen(true)}
            className="p-1.5 rounded hover:bg-white/[0.06]"
          >
            <Layers size={14} />
          </button>
          <div className="flex flex-1 min-w-0 items-center h-9 px-3 rounded-full bg-shell-bg-deep border border-shell-border focus-within:border-accent/40">
            <AddressBar windowId={windowId} />
          </div>
          <button
            type="button"
            aria-label="Tab overview"
            onClick={() => setTabOverviewOpen(true)}
            className="p-1.5 rounded hover:bg-white/[0.06]"
          >
            <ListChecks size={14} />
          </button>
        </div>
        <CapabilityPromptModal />
      </div>
    );
  }

  return (
    <div className="relative flex flex-col h-full bg-shell-bg overflow-hidden">
      <TabStrip windowId={windowId} />
      <Chrome windowId={windowId} />
      <BookmarksBar windowId={windowId} profileId={win.profileId} />
      <TabRenderer windowId={windowId} />
      {findOpen && (
        <FindInPage
          windowId={windowId}
          onClose={() => setFindOpen(false)}
        />
      )}
      {/* Listens for `taos-browser:capability-prompt` window events from
           agent-ws-bridge. Mounted at top of BrowserApp so any window's
           agents can trigger it; the modal is global per shell. */}
      <CapabilityPromptModal />
    </div>
  );
}
