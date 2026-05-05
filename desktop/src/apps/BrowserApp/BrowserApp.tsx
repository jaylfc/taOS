/**
 * BrowserApp v2 — top-level container.
 *
 * Mounted by WindowContent for each browser window. Composes:
 *   - Chrome      (browser-specific nav row + profile chip)
 *   - TabStrip    (compact tab strip with embedded AddressBar in active tab)
 *   - AddressBar  (URL input + suggest popover) — for now rendered ABOVE
 *                 TabStrip; PR 5 may move it inside the active tab per
 *                 the Q8 layout A "compact unified bar" mockup.
 *   - TabRenderer (iframe pool + discard scheduler)
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
import { useIsMobile } from "@/hooks/use-is-mobile";
import { Layers, ListChecks } from "lucide-react";

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

        <div className="flex-1 relative overflow-hidden">
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

        <div className="flex items-center gap-1 px-2 py-1 bg-shell-surface border-t border-shell-border-subtle">
          <button
            type="button"
            aria-label="Browser windows"
            onClick={() => setWindowChooserOpen(true)}
            className="p-1.5 rounded hover:bg-shell-hover"
          >
            <Layers size={14} />
          </button>
          <AddressBar windowId={windowId} />
          <button
            type="button"
            aria-label="Tab overview"
            onClick={() => setTabOverviewOpen(true)}
            className="p-1.5 rounded hover:bg-shell-hover"
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
      <Chrome windowId={windowId} />
      <div className="flex items-center gap-1 px-2 py-1 bg-shell-surface border-b border-shell-border-subtle">
        <AddressBar windowId={windowId} />
      </div>
      <TabStrip windowId={windowId} />
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
