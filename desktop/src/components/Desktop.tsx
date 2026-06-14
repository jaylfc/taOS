import { useState, useCallback } from "react";
import { FolderPlus, Image, Monitor, Settings, LayoutGrid, Layers, BookmarkPlus } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { useThemeStore } from "@/stores/theme-store";
import { useWidgetStore } from "@/stores/widget-store";
import { useSnapZones } from "@/hooks/use-snap-zones";
import { useDeepNavigation } from "@/hooks/use-deep-navigation";
import { useDesktopControl } from "@/hooks/use-desktop-control";
import { useDesktopCommandStream } from "@/hooks/use-desktop-command-stream";
import { getApp } from "@/registry/app-registry";
import { Window } from "./Window";
import { SnapOverlay } from "./SnapOverlay";
import { WidgetLayer } from "./WidgetLayer";
import { ContextMenu, type MenuItem } from "./ContextMenu";
import { WallpaperPicker } from "./WallpaperPicker";
import { DesktopIcons } from "./DesktopIcons";
import { ParticlesWallpaper } from "./ParticlesWallpaper";
import { WallpaperTextOverlay } from "./WallpaperTextOverlay";

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
  const wallpaperLightImage = useThemeStore((s) => s.wallpaperLightImage);
  const wallpaperLightMobileImage = useThemeStore((s) => s.wallpaperLightMobileImage);
  const wallpaperLightFallback = useThemeStore((s) => s.wallpaperLightFallback);
  const scheme = useThemeStore((s) => s.scheme);
  const wallpaperKind = useThemeStore((s) => s.wallpaperKind);
  const wallpaperComponent = useThemeStore((s) => s.wallpaperComponent);
  const wallpaperOverlayText = useThemeStore((s) => s.wallpaperOverlayText);
  const showOverlayText = useThemeStore((s) => s.showOverlayText);
  const isAnimated = wallpaperKind === "animated";
  // Invert the wallpaper with the theme: use the light variant when present.
  const useLight = scheme === "light" && !!wallpaperLightImage;
  const effImage = useLight ? wallpaperLightImage : wallpaperImage;
  const effMobile = useLight ? wallpaperLightMobileImage : wallpaperMobileImage;
  const effFallback = useLight ? wallpaperLightFallback : wallpaperFallback;
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
  useDesktopControl();
  // Backend -> browser command channel: lets the taOS agent drive this desktop
  // (open apps, move/arrange windows) by pushing commands the controller streams
  // here. Re-dispatches to the taos:open-app / taos:window receivers above.
  useDesktopCommandStream();

  const menuItems: MenuItem[] = [
    {
      label: "New Folder",
      icon: <FolderPlus size={14} />,
      action: () => window.dispatchEvent(new CustomEvent("taos:new-desktop-folder")),
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
      style={{ backgroundColor: effFallback, ["--wallpaper-desktop" as never]: isAnimated ? "none" : effImage, ["--wallpaper-mobile" as never]: isAnimated ? "none" : effMobile }}
      onContextMenu={handleContextMenu}
      data-desktop-surface
    >
      {isAnimated && wallpaperComponent === "particles" && <ParticlesWallpaper />}
      {showOverlayText && wallpaperOverlayText && <WallpaperTextOverlay text={wallpaperOverlayText} />}
      <DesktopIcons />
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
