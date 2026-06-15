/**
 * BrowserApp v2 — TabStrip.
 *
 * Compact tab strip per Q8 layout A:
 *  - Pinned tabs (favicon-only, ~38px wide) on the left, in their own group
 *  - Inactive unpinned tabs (favicon + truncated title, ~140px wide)
 *  - Active tab is wider (~360px); the URL renders inside the toolbar omnibox.
 *  - `+` button opens a new tab.
 *  - A Proxy/Streamed segmented toggle sits at the right of the strip. Proxy is
 *    the URL-rewriting iframe browser; Streamed escalates the active tab to a
 *    real Chromium session streamed from the host over WebRTC (the existing
 *    Neko/liveSession path).
 *
 * Each tab exposes:
 *  - role="tab" + aria-selected
 *  - data-tab-id (for drag/drop targeting in Task 11)
 *  - data-pinned ("true" / "false")
 *  - a child element with data-drag-handle (Task 11 wires drag events here)
 *
 * Close button (×) appears on hover after the title; absent on pinned tabs.
 */
import { useState } from "react";
import { Plus, X, Globe } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import { MoveTabMenu } from "./MoveTabMenu";
import { BrowserModeToggle } from "./BrowserModeToggle";
import type { Tab } from "./types";

interface TabStripProps {
  windowId: string;
}

export function TabStrip({ windowId }: TabStripProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const setActiveTab = useBrowserStore((s) => s.setActiveTab);
  const closeTab = useBrowserStore((s) => s.closeTab);
  const addTab = useBrowserStore((s) => s.addTab);

  const [contextMenu, setContextMenu] = useState<
    { tabId: string; x: number; y: number } | null
  >(null);

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
      className="flex items-end gap-1 px-3 pt-1.5 bg-shell-bg-deep border-b border-shell-border min-h-[42px]"
    >
      {ordered.map((tab) => (
        <TabItem
          key={tab.id}
          windowId={windowId}
          tab={tab}
          isActive={tab.id === win.activeTabId}
          onActivate={() => setActiveTab(windowId, tab.id)}
          onClose={() => closeTab(windowId, tab.id)}
          onContextMenu={(e) => {
            e.preventDefault();
            setContextMenu({ tabId: tab.id, x: e.clientX, y: e.clientY });
          }}
        />
      ))}

      <button
        type="button"
        aria-label="New tab"
        onClick={() => addTab(windowId)}
        className="mb-1 ml-0.5 flex h-[30px] w-[30px] items-center justify-center rounded-lg text-shell-text-secondary transition-colors hover:bg-white/[0.06] hover:text-shell-text"
      >
        <Plus size={15} />
      </button>

      {/* Proxy / Streamed segmented toggle, pushed to the right edge. */}
      <div className="mb-1 ml-auto self-center">
        <BrowserModeToggle windowId={windowId} />
      </div>

      {contextMenu && (
        <MoveTabMenu
          fromWindowId={windowId}
          tabId={contextMenu.tabId}
          anchorRect={{ x: contextMenu.x, y: contextMenu.y }}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}

interface TabItemProps {
  windowId: string;
  tab: Tab;
  isActive: boolean;
  onActivate: () => void;
  onClose: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
}

function TabItem({ windowId, tab, isActive, onActivate, onClose, onContextMenu }: TabItemProps) {
  const titleText = tab.title || tab.url || "New tab";

  // Slate accent underline when any pinned agent on this tab is in driving state.
  const tabDriving = useBrowserAgentStore((s) => {
    for (const aid of tab.pinnedAgentIds ?? []) {
      if (s.drivingState[`${windowId}:${tab.id}:${aid}`] === "driving") return true;
    }
    return false;
  });

  const hasAgent = (tab.pinnedAgentIds?.length ?? 0) > 0;

  // Width per Q8 layout A. Pinned: 38px (favicon-only). Inactive: 140px.
  // Active: 360px.
  const widthClass = tab.pinned
    ? "w-[38px] justify-center"
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
      onContextMenu={onContextMenu}
      className={[
        widthClass,
        "group relative",
        "h-[31px] px-2.5 flex items-center gap-2 rounded-t-[9px] cursor-pointer",
        "border border-b-0 transition-colors",
        isActive
          ? "bg-shell-surface text-shell-text border-shell-border-strong"
          : "border-transparent text-shell-text-secondary hover:bg-white/[0.06] hover:text-shell-text",
        // Agent-owned session tab: slate accent line on the bottom edge,
        // brighter while the agent is actively driving.
        tabDriving
          ? "shadow-[inset_0_-2px_0_var(--color-accent-strong)]"
          : hasAgent
          ? "shadow-[inset_0_-2px_0_var(--color-accent-line)]"
          : "",
      ].join(" ")}
    >
      {/* Drag handle — Task 11 wires drag events on this child */}
      <div
        data-drag-handle
        className="flex items-center gap-2 flex-1 min-w-0"
      >
        <Globe size={13} className="shrink-0 opacity-50" aria-hidden="true" />
        {!tab.pinned && (
          <span className="truncate text-[12.5px] font-medium flex-1">{titleText}</span>
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
          className={[
            "flex h-[17px] w-[17px] items-center justify-center rounded-[5px] shrink-0 text-shell-text-tertiary transition-opacity hover:bg-white/[0.08] hover:text-shell-text",
            isActive ? "opacity-100" : "opacity-0 group-hover:opacity-100",
          ].join(" ")}
        >
          <X size={11} />
        </button>
      )}
    </div>
  );
}
