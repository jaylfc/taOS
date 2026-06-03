import { DockIcon } from "../DockIcon";
import type { DockVariantProps } from "./MacosDock";

export function WindowsTaskbar({ pinned, windows, onAppClick, onLaunchpadOpen }: DockVariantProps) {
  const runningAppIds = windows.map((w) => w.appId);
  const runningNotPinned = runningAppIds.filter((id) => !pinned.includes(id));
  const items = [...pinned, ...runningNotPinned];

  return (
    <div
      data-testid="dock-variant-windows-taskbar"
      className="fixed bottom-0 left-0 right-0 flex items-center gap-1 px-2 z-[9999] select-none"
      style={{
        height: "var(--spacing-dock-h)",
        backgroundColor: "var(--color-dock-bg)",
        borderTop: "1px solid var(--color-dock-border)",
        boxShadow: "var(--shadow-dock)",
      }}
    >
      <button
        onClick={onLaunchpadOpen}
        className="flex items-center justify-center w-10 h-10 rounded-md bg-shell-surface hover:bg-shell-surface-active transition-colors"
        aria-label="Start"
        title="Start"
      >
        <svg width="18" height="18" viewBox="0 0 16 16" className="text-shell-text" fill="currentColor">
          <rect x="1" y="1" width="6" height="6" rx="1" />
          <rect x="9" y="1" width="6" height="6" rx="1" />
          <rect x="1" y="9" width="6" height="6" rx="1" />
          <rect x="9" y="9" width="6" height="6" rx="1" />
        </svg>
      </button>

      <div className="w-px h-7 bg-shell-border mx-1" />

      <div className="flex items-center gap-1">
        {items.map((appId) => (
          <DockIcon
            key={appId}
            appId={appId}
            isRunning={runningAppIds.includes(appId)}
            onClick={() => onAppClick(appId)}
          />
        ))}
      </div>
    </div>
  );
}
