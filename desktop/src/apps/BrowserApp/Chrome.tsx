/**
 * BrowserApp v2 — Chrome.
 *
 * Browser-specific nav row rendered INSIDE the window, ABOVE the tab strip.
 * Contains back / forward / refresh buttons and the profile chip.
 *
 * NOTE: The OS-level traffic lights (close / minimize / maximize) live in
 * `desktop/src/components/Window.tsx` — every window in taOS gets them
 * automatically. This component does NOT render its own traffic lights.
 *
 * For PR 4 the profile chip is display-only; PR 5's ProfileSwitcher will
 * wire the click-to-open-dropdown behavior.
 */
import { ArrowLeft, ArrowRight, RotateCw } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";

interface ChromeProps {
  windowId: string;
}

export function Chrome({ windowId }: ChromeProps) {
  // Subscribe to store changes so the buttons re-render with current state.
  const win = useBrowserStore((s) => s.windows[windowId]);
  const goBack = useBrowserStore((s) => s.goBack);
  const goForward = useBrowserStore((s) => s.goForward);
  const navigateTab = useBrowserStore((s) => s.navigateTab);

  if (!win) return null;

  const activeTab = win.tabs.find((t) => t.id === win.activeTabId);
  if (!activeTab) return null;

  const canGoBack = activeTab.historyIndex > 0;
  const canGoForward = activeTab.historyIndex < activeTab.history.length - 1;

  const handleRefresh = () => {
    // Re-navigate to the current URL — bumps the iframe to reload (TabRenderer
    // listens for navigateTab in PR 4 Task 8).
    if (activeTab.url) {
      navigateTab(windowId, activeTab.id, activeTab.url);
    }
  };

  return (
    <div
      className="flex items-center gap-2 px-2 py-1 bg-shell-surface border-b border-shell-border-subtle"
      role="toolbar"
      aria-label="Browser navigation"
    >
      {/* Nav buttons */}
      <button
        type="button"
        aria-label="Back"
        onClick={() => goBack(windowId, activeTab.id)}
        disabled={!canGoBack}
        className="p-1 rounded hover:bg-shell-hover disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ArrowLeft size={16} />
      </button>

      <button
        type="button"
        aria-label="Forward"
        onClick={() => goForward(windowId, activeTab.id)}
        disabled={!canGoForward}
        className="p-1 rounded hover:bg-shell-hover disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ArrowRight size={16} />
      </button>

      <button
        type="button"
        aria-label="Refresh"
        onClick={handleRefresh}
        className="p-1 rounded hover:bg-shell-hover"
      >
        <RotateCw size={16} />
      </button>

      {/* Spacer pushes the profile chip to the right */}
      <div className="flex-1" />

      {/* Profile chip — display-only for PR 4; PR 5 wires the dropdown */}
      <div
        className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-shell-bg-deep border border-shell-border-subtle text-xs"
        aria-label={`Profile: ${win.profileId}`}
        role="status"
      >
        <span
          className="inline-block w-2 h-2 rounded-full"
          style={{ backgroundColor: profileColor(win.profileId) }}
          aria-hidden="true"
        />
        <span className="capitalize">{win.profileId}</span>
      </div>
    </div>
  );
}

// Default colors for the two seeded profiles. PR 5's ProfileSwitcher will
// fetch real per-profile colors from the backend.
function profileColor(profileId: string): string {
  switch (profileId) {
    case "personal":
      return "#6c8df0";
    case "work":
      return "#f5b86b";
    default:
      return "#8b92a3";
  }
}
