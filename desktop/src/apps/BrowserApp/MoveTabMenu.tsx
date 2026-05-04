/**
 * Context-menu popover for moving a tab to another browser window.
 *
 * PR 4 ships this as a right-click affordance on tabs (no native
 * drag-and-drop). Native HTML5 drag-and-drop with DOM-portal iframe
 * preservation has known React-vs-DOM hazards; punted to a follow-up.
 *
 * Lists other browser windows for the same user + a "+ New window"
 * option that creates a fresh window before moving the tab.
 */
import { useEffect, useRef } from "react";
import { useBrowserStore } from "@/stores/browser-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { Plus } from "lucide-react";

interface MoveTabMenuProps {
  fromWindowId: string;
  tabId: string;
  anchorRect: { x: number; y: number };
  onClose: () => void;
}

export function MoveTabMenu({
  fromWindowId,
  tabId,
  anchorRect,
  onClose,
}: MoveTabMenuProps) {
  const allWindows = useBrowserStore((s) => s.windows);
  const moveTab = useBrowserStore((s) => s.moveTab);
  const openWindow = useProcessStore((s) => s.openWindow);
  const ref = useRef<HTMLDivElement | null>(null);

  // Other browser windows (exclude source)
  const otherWindows = Object.values(allWindows).filter(
    (w) => w.windowId !== fromWindowId,
  );

  // Click-outside dismiss
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    // Defer one tick so the event that opened the menu doesn't immediately close it
    const id = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      window.removeEventListener("mousedown", handler);
    };
  }, [onClose]);

  function handleNewWindow() {
    const browserApp = getApp("browser");
    if (!browserApp) return;
    const newWindowId = openWindow("browser", browserApp.defaultSize);

    // openWindow enqueues a React render. The new BrowserApp's mount
    // effect will call createWindow(newWindowId). queueMicrotask fires
    // BEFORE React effects — so we subscribe to the store and perform
    // the move once the new window entry materialises.
    const unsub = useBrowserStore.subscribe((state) => {
      if (state.windows[newWindowId]) {
        unsub();
        moveTab(fromWindowId, tabId, newWindowId);
      }
    });
    onClose();
  }

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="Move tab to window"
      className="fixed z-[60] min-w-[180px] rounded-md bg-shell-surface border border-shell-border shadow-lg py-1 text-xs"
      style={{ left: anchorRect.x, top: anchorRect.y }}
    >
      <div className="px-2 py-1 text-shell-text-tertiary uppercase tracking-wide text-[10px]">
        Move tab to…
      </div>
      {otherWindows.length === 0 ? (
        <div className="px-2 py-1 opacity-50 italic">No other windows</div>
      ) : (
        otherWindows.map((w) => (
          <button
            key={w.windowId}
            type="button"
            role="menuitem"
            onClick={() => {
              moveTab(fromWindowId, tabId, w.windowId);
              onClose();
            }}
            className="w-full text-left px-2 py-1 hover:bg-shell-hover"
          >
            {w.profileId} · {w.tabs.length} tab{w.tabs.length === 1 ? "" : "s"}
          </button>
        ))
      )}
      <div className="border-t border-shell-border-subtle my-1" />
      <button
        type="button"
        role="menuitem"
        onClick={handleNewWindow}
        className="w-full text-left px-2 py-1 hover:bg-shell-hover flex items-center gap-1.5"
      >
        <Plus size={12} />
        New window
      </button>
    </div>
  );
}
