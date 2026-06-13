import { useState, useCallback } from "react";
import { FolderPlus, Image, Monitor, Settings, LayoutGrid, Layers, BookmarkPlus } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { useThemeStore } from "@/stores/theme-store";
import { useWidgetStore } from "@/stores/widget-store";
import { useSnapZones } from "@/hooks/use-snap-zones";
import { useDeepNavigation } from "@/hooks/use-deep-navigation";
import { getApp } from "@/registry/app-registry";
import { Window } from "./Window";
import { SnapOverlay } from "./SnapOverlay";
import { WidgetLayer } from "./WidgetLayer";
import { ContextMenu, type MenuItem } from "./ContextMenu";
import { WallpaperPicker } from "./WallpaperPicker";
import { NeuralWallpaper } from "./NeuralWallpaper";

type ContextMenuState = {
  x: number;
  y: number;
} | null;

export function Desktop() {
  const windows = useProcessStore((s) => s.windows);
  const { openWindow } = useProcessStore();
  const wallpaperImage = useThemeStore((s) => s.wallpaperImage);
  const wallpaperMobileImage = useThemeStore((s) => s.wallpaperMobileImage);
  const wallpaperFallback = useThemeStore((s) => s.wallpaperFallback);
  const wallpaperKind = useThemeStore((s) => s.wallpaperKind);
  const showWallpaperWordmark = useThemeStore((s) => s.showWallpaperWordmark);
  const isNeural = wallpaperKind === "neural";
  const { showWidgets, toggleWidgets } = useWidgetStore();
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const [wallpaperPickerOpen, setWallpaperPickerOpen] = useState(false);

  const viewport = {
    width: typeof window !== "undefined" ? window.innerWidth : 1920,
    height: typeof window !== "undefined" ? window.innerHeight : 1080,
    topBarH: 32,
    // Match Window.tsx: dock visual inset = bottom-3 (12) + dock (64) + breathing (8)
    dockH: 84,
  };

  const { previewBounds, onDrag, onDragStop } = useSnapZones(viewport);

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    // Only show on the desktop surface itself, not on windows
    if (e.target === e.currentTarget || (e.target as HTMLElement).closest("[data-desktop-surface]")) {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY });
    }
  }, []);

  const openApp = useCallback((appId: string) => {
    const app = getApp(appId);
    if (app) openWindow(appId, app.defaultSize);
  }, [openWindow]);

  // Deep-navigation API: `?app=` URL param on load + `taos:open-app` event at
  // runtime (lets the taOS agent drive the desktop). See the hook for details.
  useDeepNavigation(openWindow);

  const menuItems: MenuItem[] = [
    {
      label: "New Folder",
      icon: <FolderPlus size={14} />,
      action: () => openApp("files"),
    },
    { label: "", separator: true },
    {
      label: "Change Wallpaper",
      icon: <Image size={14} />,
      action: () => setWallpaperPickerOpen(true),
    },
    {
      label: "Save to Memory",
      icon: <BookmarkPlus size={14} />,
      action: () => {
        const text = window.prompt("Save to memory:");
        if (text) {
          fetch("/api/user-memory/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: text, collection: "snippets" }),
          }).catch(() => {
            // Fallback to localStorage if endpoint doesn't exist yet
            const snippets = JSON.parse(localStorage.getItem("tinyagentos-snippets") || "[]");
            snippets.push({ content: text, savedAt: Date.now() });
            localStorage.setItem("tinyagentos-snippets", JSON.stringify(snippets));
          });
        }
      },
    },
    {
      label: "Display Settings",
      icon: <Monitor size={14} />,
      action: () => openApp("settings"),
    },
    { label: "", separator: true },
    {
      label: showWidgets ? "Hide Widgets" : "Show Widgets",
      icon: <Layers size={14} />,
      action: () => toggleWidgets(),
    },
    { label: "", separator: true },
    {
      label: "Open Launchpad",
      icon: <LayoutGrid size={14} />,
      action: () => {
        // Dispatch a custom event that App.tsx listens for
        window.dispatchEvent(new CustomEvent("open-launchpad"));
      },
    },
    {
      label: "System Settings",
      icon: <Settings size={14} />,
      action: () => openApp("settings"),
    },
  ];

  return (
    <div
      className="taos-wallpaper relative flex-1 overflow-hidden"
      style={{ backgroundColor: wallpaperFallback, ["--wallpaper-desktop" as never]: isNeural ? "none" : wallpaperImage, ["--wallpaper-mobile" as never]: isNeural ? "none" : wallpaperMobileImage }}
      onContextMenu={handleContextMenu}
      data-desktop-surface
    >
      {isNeural && <NeuralWallpaper wordmark={showWallpaperWordmark} />}
      <SnapOverlay bounds={previewBounds} />
      <WidgetLayer />
      {windows.map((win) => (
        <Window key={win.id} win={win} onDrag={onDrag} onDragStop={onDragStop} />
      ))}

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={menuItems}
          onClose={() => setContextMenu(null)}
        />
      )}

      <WallpaperPicker
        open={wallpaperPickerOpen}
        onClose={() => setWallpaperPickerOpen(false)}
      />
    </div>
  );
}
