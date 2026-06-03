import type { WindowState } from "@/stores/process-store";
import { DockIcon } from "../DockIcon";

export interface DockVariantProps {
  pinned: string[];
  windows: WindowState[];
  onAppClick: (appId: string) => void;
  onLaunchpadOpen: () => void;
}

export function MacosDock({ pinned, windows, onAppClick, onLaunchpadOpen }: DockVariantProps) {
  const runningAppIds = windows.map((w) => w.appId);
  const runningNotPinned = runningAppIds.filter((id) => !pinned.includes(id));

  return (
    <div
      data-testid="dock-variant-macos-dock"
      className="fixed bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 rounded-2xl z-[9999] select-none"
      style={{
        height: "var(--spacing-dock-h)",
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

      <div className="w-px h-8 bg-shell-border mx-1" />

      {pinned.map((appId) => (
        <DockIcon key={appId} appId={appId} isRunning={runningAppIds.includes(appId)} onClick={() => onAppClick(appId)} />
      ))}

      {runningNotPinned.length > 0 && <div className="w-px h-8 bg-shell-border mx-1" />}

      {runningNotPinned.map((appId) => (
        <DockIcon key={appId} appId={appId} isRunning={true} onClick={() => onAppClick(appId)} />
      ))}
    </div>
  );
}
