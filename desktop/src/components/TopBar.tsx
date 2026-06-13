import { Bell, Search, LayoutGrid, Power, Lock, Settings, RotateCcw, LogOut, Sparkles } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useClock } from "@/hooks/use-clock";
import { useWidgetStore } from "@/stores/widget-store";
import { useNotificationStore } from "@/stores/notification-store";
import { useProcessStore } from "@/stores/process-store";
import { StatusIndicators } from "./StatusIndicators";
import { AgentKillSwitch } from "./AgentKillSwitch";
import { withCsrf } from "@/lib/csrf";

interface Props {
  onSearchOpen: () => void;
  onAssistantOpen: () => void;
}

function PowerMenu() {
  const openWindow = useProcessStore((s) => s.openWindow);

  const lock = async () => {
    await fetch("/auth/lock", { method: "POST", credentials: "include", headers: withCsrf({ method: "POST" })?.headers }).catch(() => {});
    window.location.reload();
  };

  const openSettings = () => {
    openWindow("settings", { w: 760, h: 520 });
  };

  // TODO: lift RestartProgressModal up the tree to allow triggering from here
  // without opening Settings. For now navigate to Settings → Updates where the
  // Restart button exists.
  const restartServer = () => {
    openWindow("settings", { w: 760, h: 520 });
  };

  const menuItem =
    "flex items-center gap-2.5 w-full px-3 py-2 text-sm rounded-md text-shell-text-secondary hover:bg-shell-surface-hover hover:text-shell-text outline-none focus:bg-shell-surface-hover focus:text-shell-text cursor-pointer select-none transition-colors";

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
          aria-label="Power menu"
          title="Power"
        >
          <Power size={14} />
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[180px] rounded-xl border border-white/10 bg-shell-surface p-1.5 shadow-2xl backdrop-blur-xl"
          style={{ backgroundColor: "rgba(28,26,44,0.96)" }}
        >
          <DropdownMenu.Item
            className={menuItem}
            onSelect={lock}
          >
            <Lock size={14} />
            <span className="flex-1">Lock taOS</span>
            <kbd className="text-[10px] opacity-40 font-mono">⌘L</kbd>
          </DropdownMenu.Item>

          <DropdownMenu.Item
            className={menuItem}
            onSelect={openSettings}
          >
            <Settings size={14} />
            <span className="flex-1">Settings</span>
          </DropdownMenu.Item>

          <DropdownMenu.Separator className="my-1 h-px bg-white/10" />

          <DropdownMenu.Item
            className={menuItem}
            onSelect={restartServer}
            // TODO: trigger RestartProgressModal directly once it is lifted
            // into a context/store (see comment in restartServer above)
          >
            <RotateCcw size={14} />
            <span className="flex-1">Restart server</span>
          </DropdownMenu.Item>

          <DropdownMenu.Item
            className={menuItem}
            onSelect={lock}
          >
            <LogOut size={14} />
            <span className="flex-1">Sign out</span>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function TopBar({ onSearchOpen, onAssistantOpen }: Props) {
  const clock = useClock();
  const { showWidgets, toggleWidgets } = useWidgetStore();
  const unreadCount = useNotificationStore((s) => s.notifications.filter((n) => !n.read).length);
  const toggleCentre = useNotificationStore((s) => s.toggleCentre);

  return (
    <div
      className="relative flex items-center px-4 shrink-0 select-none"
      style={{
        height: "var(--spacing-topbar-h)",
        backgroundColor: "var(--color-topbar-bg)",
        borderBottom: "1px solid var(--color-shell-border)",
      }}
    >
      <div className="flex items-center gap-2">
        <img src="/static/taos-logo.png" alt="taOS" className="h-4 w-auto" />
        <span className="text-xs font-medium text-shell-text-secondary">taOS</span>
      </div>

      <button
        onClick={onAssistantOpen}
        className="ml-3 p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
        aria-label="Open taOS agent"
        title="taOS agent"
      >
        <Sparkles size={14} />
      </button>

      <button
        onClick={onSearchOpen}
        className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2 px-3 py-1 rounded-md bg-shell-surface-hover text-shell-text-tertiary text-xs hover:bg-shell-surface-active transition-colors"
        aria-label="Search"
      >
        <Search size={12} />
        <span>Search</span>
        <kbd className="ml-2 text-[10px] opacity-50">Ctrl+Space</kbd>
      </button>

      <div className="flex items-center gap-3 ml-auto">
        <StatusIndicators />
        <span className="text-xs text-shell-text-tertiary">{clock}</span>
        <AgentKillSwitch />
        <PowerMenu />
        <button
          onClick={toggleWidgets}
          className={`p-1 rounded transition-colors ${showWidgets ? "text-accent bg-accent/10" : "text-shell-text-secondary hover:bg-shell-surface-hover"}`}
          aria-label="Toggle widgets"
          title="Toggle widgets"
        >
          <LayoutGrid size={14} />
        </button>
        <button
          onClick={toggleCentre}
          className="relative p-1 rounded hover:bg-shell-surface-hover transition-colors"
          aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
        >
          <Bell size={14} className="text-shell-text-secondary" />
          {unreadCount > 0 && (
            <span className="absolute top-0 right-0 w-1.5 h-1.5 bg-red-500 rounded-full" />
          )}
        </button>
      </div>
    </div>
  );
}
