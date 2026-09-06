/**
 * BrowserSidebar — collapsible left panel showing sessions and tab list.
 *
 * Expanded: 220px wide, shows session label + tab titles with favicons.
 * Collapsed: 44px wide, shows icon-only favicons (or Globe fallback).
 *
 * The collapse animation uses a CSS width transition. When
 * prefers-reduced-motion is active, the transition is skipped.
 *
 * Behavior preserved: clicking a tab row calls setActiveTab; the active tab
 * is highlighted. No session/tab data is mutated here.
 */
import { Globe, PanelLeftClose, PanelLeftOpen, Plus, X } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserUiStore } from "@/stores/browser-ui-store";

interface BrowserSidebarProps {
  windowId: string;
}

export function BrowserSidebar({ windowId }: BrowserSidebarProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const setActiveTab = useBrowserStore((s) => s.setActiveTab);
  const closeTab = useBrowserStore((s) => s.closeTab);
  const addTab = useBrowserStore((s) => s.addTab);

  const collapsed = useBrowserUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useBrowserUiStore((s) => s.toggleSidebar);

  if (!win) return null;

  const pinned = win.tabs.filter((t) => t.pinned);
  const unpinned = win.tabs.filter((t) => !t.pinned);

  return (
    <aside
      aria-label="Tabs sidebar"
      data-collapsed={collapsed ? "true" : "false"}
      className="browser-sidebar flex flex-col flex-none bg-shell-bg-deep border-r border-shell-border overflow-hidden"
      style={{
        width: collapsed ? 44 : 220,
      }}
    >
      {/* Sidebar header row */}
      <div
        className={[
          "flex items-center h-[42px] flex-none border-b border-shell-border px-2 gap-1",
          collapsed ? "justify-center" : "justify-between",
        ].join(" ")}
      >
        {!collapsed && (
          <span className="text-[11px] font-semibold text-shell-text-tertiary uppercase tracking-wider pl-1 select-none">
            Tabs
          </span>
        )}
        <button
          type="button"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          onClick={toggleSidebar}
          className="flex h-[28px] w-[28px] items-center justify-center rounded-lg text-shell-text-tertiary transition-colors hover:bg-white/[0.06] hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {collapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
        </button>
      </div>

      {/* Tab list — scrollable */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden py-1" style={{ scrollbarWidth: "none" }}>
        {pinned.length > 0 && (
          <>
            {!collapsed && (
              <div className="px-3 pb-0.5 pt-1">
                <span className="text-[10px] font-semibold text-shell-text-tertiary uppercase tracking-wider select-none">
                  Pinned
                </span>
              </div>
            )}
            {pinned.map((tab) => (
              <SidebarTabRow
                key={tab.id}
                windowId={windowId}
                tabId={tab.id}
                title={tab.title || tab.url || "Pinned tab"}
                faviconUrl={tab.faviconUrl}
                isActive={tab.id === win.activeTabId}
                isPinned
                collapsed={collapsed}
                onActivate={() => setActiveTab(windowId, tab.id)}
                onClose={() => closeTab(windowId, tab.id)}
              />
            ))}
            {!collapsed && pinned.length > 0 && unpinned.length > 0 && (
              <div className="mx-3 my-1 h-px bg-shell-border" aria-hidden="true" />
            )}
          </>
        )}

        {unpinned.map((tab) => (
          <SidebarTabRow
            key={tab.id}
            windowId={windowId}
            tabId={tab.id}
            title={tab.title || tab.url || "New tab"}
            faviconUrl={tab.faviconUrl}
            isActive={tab.id === win.activeTabId}
            isPinned={false}
            collapsed={collapsed}
            onActivate={() => setActiveTab(windowId, tab.id)}
            onClose={() => closeTab(windowId, tab.id)}
          />
        ))}
      </div>

      {/* New tab button */}
      <div className={["flex-none border-t border-shell-border py-1.5", collapsed ? "px-2" : "px-2"].join(" ")}>
        <button
          type="button"
          aria-label="New tab"
          onClick={() => addTab(windowId)}
          className={[
            "flex items-center gap-2 rounded-lg transition-colors",
            "text-shell-text-secondary hover:text-shell-text hover:bg-white/[0.06]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
            collapsed ? "h-[30px] w-[30px] justify-center" : "h-[30px] w-full px-2",
          ].join(" ")}
        >
          <Plus size={13} aria-hidden="true" />
          {!collapsed && <span className="text-[12px] font-medium">New tab</span>}
        </button>
      </div>
    </aside>
  );
}

interface SidebarTabRowProps {
  windowId: string;
  tabId: string;
  title: string;
  faviconUrl?: string;
  isActive: boolean;
  isPinned: boolean;
  collapsed: boolean;
  onActivate: () => void;
  onClose: () => void;
}

function SidebarTabRow({
  title,
  faviconUrl,
  isActive,
  isPinned,
  collapsed,
  onActivate,
  onClose,
}: SidebarTabRowProps) {
  return (
    <button
      type="button"
      aria-label={title}
      aria-pressed={isActive}
      onClick={onActivate}
      title={title}
      className={[
        "group relative flex w-full items-center gap-2 select-none",
        "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
        collapsed ? "mx-2 my-0.5 h-[32px] w-[28px] rounded-lg justify-center" : "mx-1.5 my-0.5 h-[32px] rounded-lg px-2",
        isActive
          ? "bg-shell-surface-active text-shell-text"
          : "text-shell-text-secondary hover:bg-white/[0.04] hover:text-shell-text",
      ].join(" ")}
    >
      {/* Active indicator bar */}
      {isActive && !collapsed && (
        <span
          className="absolute left-0 top-1/2 -translate-y-1/2 h-[16px] w-[3px] rounded-r-full bg-accent"
          aria-hidden="true"
        />
      )}

      {/* Favicon / Globe fallback */}
      <span className="flex-none flex items-center justify-center">
        {faviconUrl ? (
          <img
            src={faviconUrl}
            alt=""
            aria-hidden="true"
            width={14}
            height={14}
            className="w-[14px] h-[14px] object-contain rounded-sm"
          />
        ) : (
          <Globe size={13} className="opacity-40" aria-hidden="true" />
        )}
      </span>

      {/* Title — hidden when collapsed */}
      {!collapsed && (
        <span className="flex-1 min-w-0 truncate text-[12px] font-medium leading-none">
          {title}
        </span>
      )}

      {/* Close button — only on non-pinned rows, only when expanded */}
      {!isPinned && !collapsed && (
        <span
          role="button"
          tabIndex={0}
          aria-label={`Close ${title}`}
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              onClose();
            }
          }}
          className={[
            "flex-none flex h-[18px] w-[18px] items-center justify-center",
            "rounded-[5px] text-shell-text-tertiary",
            "transition-opacity hover:bg-white/[0.08] hover:text-shell-text",
            isActive ? "opacity-100" : "opacity-0 group-hover:opacity-100",
          ].join(" ")}
        >
          <X size={10} />
        </span>
      )}
    </button>
  );
}
