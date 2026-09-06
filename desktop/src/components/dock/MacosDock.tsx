import type { WindowState } from "@/stores/process-store";
import type { DockIconSize, DockPosition } from "@/stores/dock-store";
import { DockIcon } from "../DockIcon";
import { pinnedAppId } from "@/registry/app-registry";

export interface DockVariantProps {
  pinned: string[];
  windows: WindowState[];
  onAppClick: (appId: string) => void;
  onLaunchpadOpen: () => void;
  iconSize?: DockIconSize;
  position?: DockPosition;
}

export function MacosDock({
  pinned,
  windows,
  onAppClick,
  onLaunchpadOpen,
  iconSize = "medium",
  position = "bottom",
}: DockVariantProps) {
  const runningAppIds = windows.map((w) => w.appId);
  // A pin is matched by the app it launches, not by its own id: a legacy pin
  // id is not an app id, so comparing the two directly would leave its icon
  // un-dotted and list its own window again as a second, unpinned icon.
  const pinnedAppIds = pinned.map(pinnedAppId);
  const runningNotPinned = runningAppIds.filter((id) => !pinnedAppIds.includes(id));
  const isLeft = position === "left";
  const dividerClassName = isLeft ? "h-px w-8 bg-shell-border my-1" : "w-px h-8 bg-shell-border mx-1";

  return (
    <div
      data-testid="dock-variant-macos-dock"
      className={
        isLeft
          ? "fixed left-3 top-1/2 -translate-y-1/2 flex flex-col items-center gap-1.5 py-3 rounded-2xl z-[9999] select-none"
          : "fixed bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 rounded-2xl z-[9999] select-none"
      }
      style={{
        [isLeft ? "width" : "height"]: "var(--spacing-dock-h)",
        padding: "var(--spacing-dock-padding)",
        backgroundColor: "var(--color-dock-bg)",
        border: "1px solid var(--color-dock-border)",
        boxShadow: "var(--shadow-dock)",
      }}
    >
      <button
        onClick={onLaunchpadOpen}
        className="flex items-center justify-center w-10 h-10 rounded-lg bg-shell-surface hover:bg-shell-surface-active transition-all hover:scale-110"
        aria-label="Launchpad"
        title="Launchpad"
      >
        <svg width="18" height="18" viewBox="0 0 16 16" className="text-shell-text" fill="currentColor">
          <rect x="1" y="1" width="5" height="5" rx="1" />
          <rect x="10" y="1" width="5" height="5" rx="1" />
          <rect x="1" y="10" width="5" height="5" rx="1" />
          <rect x="10" y="10" width="5" height="5" rx="1" />
        </svg>
      </button>

      <div className={dividerClassName} />

      {pinned.map((appId, i) => {
        // pinnedAppIds is built from `pinned` by a straight positional map, so
        // the two arrays are always the same length today -- but indexing one
        // by the other's position is one refactor away from an out-of-bounds
        // read (e.g. a future pre-filter on either side). Fall back to the
        // pin id itself rather than asserting the lookup can't miss.
        const targetId = pinnedAppIds[i] ?? appId;
        return (
          <DockIcon
            key={appId}
            appId={appId}
            isRunning={runningAppIds.includes(targetId)}
            onClick={() => onAppClick(appId)}
            size={iconSize}
          />
        );
      })}

      {runningNotPinned.length > 0 && <div className={dividerClassName} />}

      {runningNotPinned.map((appId) => (
        <DockIcon key={appId} appId={appId} isRunning={true} onClick={() => onAppClick(appId)} size={iconSize} />
      ))}
    </div>
  );
}
