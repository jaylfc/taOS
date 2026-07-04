import { useState, useCallback } from "react";
import * as icons from "lucide-react";
import { getApp, prefetchApp } from "@/registry/app-registry";
import { useProcessStore } from "@/stores/process-store";
import { useDockStore } from "@/stores/dock-store";
import { ContextMenu, type MenuItem } from "./ContextMenu";

type DockIconSize = "small" | "medium" | "large";

interface Props {
  appId: string;
  isRunning: boolean;
  onClick: () => void;
  size?: DockIconSize;
}

const SIZE_CLASSES: Record<DockIconSize, string> = {
  small: "w-8 h-8",
  medium: "w-10 h-10",
  large: "w-14 h-14",
};

const ICON_PX: Record<DockIconSize, number> = {
  small: 16,
  medium: 20,
  large: 28,
};

export function DockIcon({ appId, isRunning, onClick, size = "medium" }: Props) {
  const app = getApp(appId);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  const windows = useProcessStore((s) => s.windows);
  const focusWindow = useProcessStore((s) => s.focusWindow);
  const restoreWindow = useProcessStore((s) => s.restoreWindow);
  const minimizeWindow = useProcessStore((s) => s.minimizeWindow);
  const maximizeWindow = useProcessStore((s) => s.maximizeWindow);
  const recenterWindow = useProcessStore((s) => s.recenterWindow);
  const closeWindow = useProcessStore((s) => s.closeWindow);
  const openWindow = useProcessStore((s) => s.openWindow);
  const pinned = useDockStore((s) => s.pinned);
  const pin = useDockStore((s) => s.pin);
  const unpin = useDockStore((s) => s.unpin);

  const win = windows.find((w) => w.appId === appId);
  const isPinned = pinned.includes(appId);

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY });
  }, []);

  if (!app) return null;

  const iconName = app.icon
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  const IconComponent = (icons[iconName] as icons.LucideIcon) ?? icons.HelpCircle;

  // Multi-window apps (singleton: false) can spawn a second, independent
  // window from the dock, the way macOS offers File > New Window.
  const multiWindow = app.singleton === false;
  const newWindowItem: MenuItem = {
    label: "New Window",
    icon: <icons.SquarePlus size={14} />,
    action: () => openWindow(appId, app.defaultSize, undefined, { forceNew: true }),
  };

  const items: MenuItem[] = win
    ? [
        {
          label: win.minimized ? "Show" : "Bring to Front",
          icon: <icons.ArrowUpRight size={14} />,
          action: () => (win.minimized ? restoreWindow(win.id) : focusWindow(win.id)),
        },
        ...(multiWindow ? [newWindowItem] : []),
        ...(!win.minimized
          ? [{ label: "Minimise", icon: <icons.Minus size={14} />, action: () => minimizeWindow(win.id) }]
          : []),
        {
          label: win.maximized ? "Restore" : "Maximise",
          icon: <icons.Maximize2 size={14} />,
          action: () => maximizeWindow(win.id),
        },
        {
          label: "Center Window",
          icon: <icons.LocateFixed size={14} />,
          action: () => recenterWindow(win.id),
        },
        { label: "", separator: true },
        isPinned
          ? { label: "Remove from Dock", icon: <icons.PinOff size={14} />, action: () => unpin(appId) }
          : { label: "Keep in Dock", icon: <icons.Pin size={14} />, action: () => pin(appId) },
        { label: "", separator: true },
        { label: `Quit ${app.name}`, icon: <icons.X size={14} />, action: () => closeWindow(win.id) },
      ]
    : [
        { label: "Open", icon: <icons.ArrowUpRight size={14} />, action: onClick },
        { label: "", separator: true },
        isPinned
          ? { label: "Remove from Dock", icon: <icons.PinOff size={14} />, action: () => unpin(appId) }
          : { label: "Keep in Dock", icon: <icons.Pin size={14} />, action: () => pin(appId) },
      ];

  return (
    <>
      <button
        onClick={onClick}
        onContextMenu={onContextMenu}
        onMouseEnter={() => prefetchApp(appId)}
        className={`group relative flex items-center justify-center ${SIZE_CLASSES[size]} rounded-lg bg-shell-surface hover:bg-shell-surface-active transition-all hover:scale-110`}
        aria-label={`Open ${app.name}`}
        title={app.name}
      >
        <IconComponent size={ICON_PX[size]} className="text-shell-text" />
        {isRunning && (
          <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-accent" />
        )}
      </button>
      {menu && <ContextMenu x={menu.x} y={menu.y} items={items} onClose={() => setMenu(null)} />}
    </>
  );
}
