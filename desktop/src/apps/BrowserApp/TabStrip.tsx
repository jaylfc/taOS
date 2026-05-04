/**
 * BrowserApp v2 — TabStrip.
 *
 * Compact tab strip per Q8 layout A:
 *  - Pinned tabs (favicon-only, ~32px wide) on the left, in their own group
 *  - Inactive unpinned tabs (favicon + truncated title, ~140px wide)
 *  - Active tab is wider (~360px); for PR 4 the URL renders as plain text
 *    inside it. PR 4 Task 7 (AddressBar) replaces the URL text with an
 *    embedded input field.
 *  - `+` button at the right edge opens a new tab.
 *
 * Each tab exposes:
 *  - role="tab" + aria-selected
 *  - data-tab-id (for drag/drop targeting in Task 11)
 *  - data-pinned ("true" / "false")
 *  - a child element with data-drag-handle (Task 11 wires drag events here)
 *
 * Close button (×) appears on hover after the title; absent on pinned tabs.
 */
import { Plus, X, Globe } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import type { Tab } from "./types";

interface TabStripProps {
  windowId: string;
}

export function TabStrip({ windowId }: TabStripProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const setActiveTab = useBrowserStore((s) => s.setActiveTab);
  const closeTab = useBrowserStore((s) => s.closeTab);
  const addTab = useBrowserStore((s) => s.addTab);

  if (!win) return null;

  // Order: pinned first, then unpinned, preserving relative order within each
  // group (matching the model the brainstorm mockups showed).
  const pinned = win.tabs.filter((t) => t.pinned);
  const unpinned = win.tabs.filter((t) => !t.pinned);
  const ordered = [...pinned, ...unpinned];

  return (
    <div
      role="tablist"
      aria-label="Browser tabs"
      className="flex items-end gap-1 px-2 pt-1 bg-shell-bg border-b border-shell-border-subtle min-h-[36px]"
    >
      {ordered.map((tab) => (
        <TabItem
          key={tab.id}
          tab={tab}
          isActive={tab.id === win.activeTabId}
          onActivate={() => setActiveTab(windowId, tab.id)}
          onClose={() => closeTab(windowId, tab.id)}
        />
      ))}

      <button
        type="button"
        aria-label="New tab"
        onClick={() => addTab(windowId)}
        className="ml-1 p-1 rounded hover:bg-shell-hover"
      >
        <Plus size={14} />
      </button>
    </div>
  );
}

interface TabItemProps {
  tab: Tab;
  isActive: boolean;
  onActivate: () => void;
  onClose: () => void;
}

function TabItem({ tab, isActive, onActivate, onClose }: TabItemProps) {
  const titleText = tab.title || tab.url || "New tab";

  // Width per Q8 layout A. Pinned: 32px (favicon-only). Inactive: 140px.
  // Active: 360px (will host the embedded AddressBar in Task 7).
  const widthClass = tab.pinned
    ? "w-[32px]"
    : isActive
    ? "w-[360px]"
    : "w-[140px]";

  return (
    <div
      role="tab"
      aria-selected={isActive}
      data-tab-id={tab.id}
      data-pinned={tab.pinned ? "true" : "false"}
      onClick={onActivate}
      className={[
        widthClass,
        "h-[28px] px-2 flex items-center gap-1.5 rounded-t cursor-pointer",
        "border-t border-l border-r border-shell-border-subtle",
        isActive
          ? "bg-shell-surface text-shell-text"
          : "bg-shell-bg-deep text-shell-text-secondary hover:bg-shell-hover",
      ].join(" ")}
    >
      {/* Drag handle — Task 11 wires drag events on this child */}
      <div
        data-drag-handle
        className="flex items-center gap-1.5 flex-1 min-w-0"
      >
        <Globe size={12} className="shrink-0 opacity-60" aria-hidden="true" />
        {!tab.pinned && (
          <span className="truncate text-xs flex-1">{titleText}</span>
        )}
      </div>

      {!tab.pinned && (
        <button
          type="button"
          aria-label={`Close ${titleText}`}
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          className="opacity-0 group-hover:opacity-100 hover:opacity-100 hover:bg-shell-hover rounded p-0.5 shrink-0"
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}
