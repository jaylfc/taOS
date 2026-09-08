import { useDockStore } from "@/stores/dock-store";
import { useProcessStore } from "@/stores/process-store";
import { useThemeStore } from "@/stores/theme-store";
import { getApp, pinnedAppId, pinnedLaunchProps } from "@/registry/app-registry";
import { DOCK_VARIANTS, type DockVariantId } from "./dock/DockVariants";

interface Props {
  onLaunchpadOpen: () => void;
}

export function Dock({ onLaunchpadOpen }: Props) {
  const pinned = useDockStore((s) => s.pinned);
  const iconSize = useDockStore((s) => s.iconSize);
  const position = useDockStore((s) => s.position);
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
      // `appId` is the pin id the dock stores, which for a legacy pin is not
      // an app id: resolve both the app and the section it opens on.
      const targetId = pinnedAppId(appId);
      const app = getApp(targetId);
      if (app) {
        openWindow(targetId, app.defaultSize, pinnedLaunchProps(appId));
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
      iconSize={iconSize}
      position={position}
    />
  );
}
