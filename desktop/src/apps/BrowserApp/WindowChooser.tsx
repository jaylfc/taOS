/**
 * Mobile sheet listing all browser windows for the user.
 *
 * Per the spec (PR 4 mobile decision): iOS PWAs don't have multi-window,
 * so the desktop's "multiple browser windows, each with tabs" model
 * collapses on mobile to a single BrowserApp instance with this sheet.
 *
 * Tapping a window focuses it via process-store (which switches the
 * mobile mounted-app to that windowId). New windows can be created from
 * here too.
 */
import { useEffect, useRef } from "react";
import { useBrowserStore } from "@/stores/browser-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { Plus, X } from "lucide-react";

interface WindowChooserProps {
  currentWindowId: string;
  onSelect: (windowId: string) => void;
  onClose: () => void;
}

export function WindowChooser({
  currentWindowId,
  onSelect,
  onClose,
}: WindowChooserProps) {
  const allWindows = useBrowserStore((s) => s.windows);
  const openWindow = useProcessStore((s) => s.openWindow);
  const ref = useRef<HTMLDivElement | null>(null);

  // Click-outside dismiss
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      window.removeEventListener("mousedown", handler);
    };
  }, [onClose]);

  function summarizeWindow(w: { profileId: string; tabs: { url: string; title: string }[] }) {
    const count = w.tabs.length;
    const sample = w.tabs[0]?.title || w.tabs[0]?.url || "Empty";
    return `${w.profileId} · ${count} tab${count === 1 ? "" : "s"} · ${sample}`;
  }

  function handleNewWindow() {
    const browserApp = getApp("browser");
    if (!browserApp) return;
    const newId = openWindow("browser", browserApp.defaultSize);
    queueMicrotask(() => onSelect(newId));
    onClose();
  }

  const windowList = Object.values(allWindows);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Browser windows"
      className="absolute top-0 left-0 right-0 z-50 bg-shell-surface border-b border-shell-border shadow-xl"
    >
      <header className="flex items-center justify-between px-3 py-2 border-b border-shell-border-subtle">
        <h2 className="text-sm font-medium">Windows</h2>
        <button
          type="button"
          aria-label="Close windows list"
          onClick={onClose}
          className="p-1 rounded hover:bg-shell-hover"
        >
          <X size={14} />
        </button>
      </header>

      <ul role="listbox" aria-label="Browser windows" className="max-h-[60vh] overflow-y-auto py-1">
        {windowList.length === 0 && (
          <li className="px-3 py-2 text-xs opacity-60">No windows</li>
        )}
        {windowList.map((w) => {
          const isCurrent = w.windowId === currentWindowId;
          return (
            <li key={w.windowId}>
              <button
                type="button"
                role="option"
                aria-selected={isCurrent}
                onClick={() => {
                  onSelect(w.windowId);
                  onClose();
                }}
                className={[
                  "w-full text-left px-3 py-2 text-xs hover:bg-shell-hover flex items-center gap-2",
                  isCurrent ? "bg-shell-hover" : "",
                ].join(" ")}
              >
                <span className="truncate flex-1">{summarizeWindow(w)}</span>
                {isCurrent && (
                  <span className="text-[10px] opacity-60">current</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="border-t border-shell-border-subtle">
        <button
          type="button"
          onClick={handleNewWindow}
          className="w-full text-left px-3 py-2 text-xs hover:bg-shell-hover flex items-center gap-1.5"
        >
          <Plus size={12} />
          New window
        </button>
      </div>
    </div>
  );
}
