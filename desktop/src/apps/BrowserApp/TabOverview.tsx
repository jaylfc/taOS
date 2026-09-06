/**
 * Mobile fallback for TabStrip — full-screen grid of tab cards.
 *
 * On viewports below ~600px the horizontal tab strip is replaced by
 * a "Tabs" button in the bottom URL bar that opens this overview.
 * Tapping a card activates the tab + dismisses the overview.
 */
import { Globe, X } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import type { Tab } from "./types";

interface TabOverviewProps {
  windowId: string;
  onSelect: (tabId: string) => void;
  onClose: () => void;
}

export function TabOverview({ windowId, onSelect, onClose }: TabOverviewProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const closeTab = useBrowserStore((s) => s.closeTab);
  const addTab = useBrowserStore((s) => s.addTab);

  if (!win) return null;

  const pinned = win.tabs.filter((t) => t.pinned);
  const unpinned = win.tabs.filter((t) => !t.pinned);

  return (
    <div
      role="dialog"
      aria-label="Tabs"
      className="absolute inset-0 z-40 bg-shell-bg-deep overflow-y-auto p-3"
    >
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium">Tabs</h2>
        <button
          type="button"
          aria-label="Close overview"
          onClick={onClose}
          className="p-1 rounded hover:bg-shell-hover"
        >
          <X size={16} />
        </button>
      </header>

      {pinned.length > 0 && (
        <section className="mb-4">
          <div className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-2">
            Pinned
          </div>
          <div role="tablist" aria-label="Pinned tabs" className="grid grid-cols-2 gap-2">
            {pinned.map((tab) => (
              <TabCard
                key={tab.id}
                tab={tab}
                isActive={tab.id === win.activeTabId}
                onSelect={() => {
                  onSelect(tab.id);
                  onClose();
                }}
                onClose={() => closeTab(windowId, tab.id)}
              />
            ))}
          </div>
        </section>
      )}

      <section>
        <div role="tablist" aria-label="Open tabs" className="grid grid-cols-2 gap-2">
          {unpinned.map((tab) => (
            <TabCard
              key={tab.id}
              tab={tab}
              isActive={tab.id === win.activeTabId}
              onSelect={() => {
                onSelect(tab.id);
                onClose();
              }}
              onClose={() => closeTab(windowId, tab.id)}
            />
          ))}
        </div>
      </section>

      <button
        type="button"
        aria-label="New tab"
        onClick={() => {
          const newId = addTab(windowId);
          onSelect(newId);
          onClose();
        }}
        className="mt-4 w-full py-2 rounded border border-dashed border-shell-border text-shell-text-secondary text-xs"
      >
        + New tab
      </button>
    </div>
  );
}

interface TabCardProps {
  tab: Tab;
  isActive: boolean;
  onSelect: () => void;
  onClose: () => void;
}

function TabCard({ tab, isActive, onSelect, onClose }: TabCardProps) {
  return (
    <div
      role="tab"
      aria-selected={isActive}
      data-tab-id={tab.id}
      onClick={onSelect}
      className={[
        "relative aspect-video rounded border p-2 cursor-pointer flex flex-col gap-1",
        isActive
          ? "bg-shell-surface border-accent"
          : "bg-shell-surface border-shell-border-subtle",
      ].join(" ")}
    >
      <div className="flex items-center gap-1.5">
        <Globe size={10} aria-hidden="true" className="opacity-60 shrink-0" />
        <span className="text-xs truncate flex-1">
          {tab.title || tab.url || "New tab"}
        </span>
      </div>
      <div className="text-[10px] text-shell-text-tertiary truncate">
        {tab.url}
      </div>
      <button
        type="button"
        aria-label={`Close ${tab.title || tab.url}`}
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="absolute top-1 right-1 p-0.5 rounded bg-shell-bg-deep/80 hover:bg-shell-hover"
      >
        <X size={10} />
      </button>
    </div>
  );
}
