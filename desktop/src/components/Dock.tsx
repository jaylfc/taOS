import { useDockStore } from "@/stores/dock-store";
import { useProcessStore } from "@/stores/process-store";
import { useThemeStore } from "@/stores/theme-store";
import { getApp } from "@/registry/app-registry";
import { DOCK_VARIANTS, type DockVariantId } from "./dock/DockVariants";

interface Props {
  onLaunchpadOpen: () => void;
}

export function Dock({ onLaunchpadOpen }: Props) {
  const pinned = useDockStore((s) => s.pinned);
  const windows = useProcessStore((s) => s.windows);
  const { openWindow, focusWindow, restoreWindow } = useProcessStore();
  const variant = useThemeStore((s) => (s.structure?.dock?.variant as DockVariantId) ?? "macos-dock");

  const handleClick = (appId: string) => {
    const existing = windows.find((w) => w.appId === appId);
    if (existing) {
      if (existing.minimized) {
        restoreWindow(existing.id);
      } else {
        focusWindow(existing.id);
      }
    } else {
      const app = getApp(appId);
      if (app) {
        openWindow(appId, app.defaultSize);
      }
    }
  };

  const Variant = DOCK_VARIANTS[variant] ?? DOCK_VARIANTS["macos-dock"];

  return (
    <Variant
      pinned={pinned}
      windows={windows}
      onAppClick={handleClick}
      onLaunchpadOpen={onLaunchpadOpen}
    />
  );
}
