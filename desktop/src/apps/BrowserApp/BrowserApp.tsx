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
import { Chrome } from "./Chrome";
import { TabStrip } from "./TabStrip";
import { AddressBar } from "./AddressBar";
import { TabRenderer } from "./TabRenderer";
import { useBrowserKeyboardShortcuts } from "./keyboard";
import { FindInPage } from "./FindInPage";

const DEFAULT_PROFILE_ID = "personal";

interface BrowserAppProps {
  windowId: string;
}

export function BrowserApp({ windowId }: BrowserAppProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const createWindow = useBrowserStore((s) => s.createWindow);
  const [findOpen, setFindOpen] = useState(false);

  // Auto-create on first mount. createWindow is idempotent so calling
  // it when the window already exists (e.g. restored by persistence)
  // is a no-op.
  useEffect(() => {
    createWindow(windowId, DEFAULT_PROFILE_ID);
  }, [windowId, createWindow]);

  useBrowserKeyboardShortcuts({
    windowId,
    hasFocus: true, // PR 4: always-on while mounted; PR 5+ may scope to focused window
    onOpenFind: () => setFindOpen(true),
  });

  // Wait for the window entry to materialise (one render tick after
  // the createWindow set call). Until then render an empty placeholder.
  if (!win) {
    return <div className="flex-1 bg-shell-bg-deep" />;
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
    </div>
  );
}
