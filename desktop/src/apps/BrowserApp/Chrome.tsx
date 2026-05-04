/**
 * BrowserApp v2 — Chrome.
 *
 * Browser-specific nav row rendered INSIDE the window, ABOVE the tab strip.
 * Contains back / forward / refresh buttons and the profile chip.
 *
 * NOTE: The OS-level traffic lights (close / minimize / maximize) live in
 * `desktop/src/components/Window.tsx` — every window in taOS gets them
 * automatically. This component does NOT render its own traffic lights.
 */
import { useState, useEffect } from "react";
import { ArrowLeft, ArrowRight, RotateCw, Settings } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import { listProfiles, type Profile } from "@/lib/browser-profile-api";
import { ProfileSwitcher } from "./ProfileSwitcher";
import { ProfileManager } from "./ProfileManager";
import { SettingsPanel } from "./SettingsPanel";

interface ChromeProps {
  windowId: string;
}

export function Chrome({ windowId }: ChromeProps) {
  // Subscribe to store changes so the buttons re-render with current state.
  const win = useBrowserStore((s) => s.windows[windowId]);
  const goBack = useBrowserStore((s) => s.goBack);
  const goForward = useBrowserStore((s) => s.goForward);
  const navigateTab = useBrowserStore((s) => s.navigateTab);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [managerOpen, setManagerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);

  const currentProfileId = win?.profileId ?? "";

  useEffect(() => {
    let cancelled = false;
    listProfiles().then((list) => {
      if (!cancelled) setProfiles(list);
    });
    return () => { cancelled = true; };
  }, [currentProfileId]);

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

      {/* Settings button */}
      <div className="relative">
        <button
          type="button"
          aria-label="Settings"
          title="Settings"
          aria-haspopup="dialog"
          aria-expanded={settingsOpen}
          onClick={() => {
            setSwitcherOpen(false);
            setManagerOpen(false);
            setSettingsOpen((s) => !s);
          }}
          className="p-1 rounded hover:bg-shell-hover"
        >
          <Settings size={16} />
        </button>
        {settingsOpen && (
          <SettingsPanel onClose={() => setSettingsOpen(false)} />
        )}
      </div>

      {/* Profile chip — clicking opens the ProfileSwitcher dropdown */}
      <div className="relative">
        {(() => {
          const activeProfile = profiles?.find((p) => p.profile_id === win.profileId);
          const chipColor = activeProfile?.color ?? "#8b92a3";
          const chipName = activeProfile?.name ?? win.profileId;
          return (
            <button
              type="button"
              onClick={() => {
                setSettingsOpen(false);
                setManagerOpen(false);
                setSwitcherOpen((s) => !s);
              }}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-shell-bg-deep border border-shell-border-subtle text-xs hover:bg-shell-hover"
              aria-label={`Profile: ${win.profileId}`}
              aria-haspopup="menu"
              aria-expanded={switcherOpen}
            >
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: chipColor }}
                aria-hidden="true"
              />
              <span className="capitalize">{chipName}</span>
            </button>
          );
        })()}
        {switcherOpen && (
          <ProfileSwitcher
            windowId={windowId}
            onClose={() => setSwitcherOpen(false)}
            onManage={() => {
              setSwitcherOpen(false);
              setManagerOpen(true);
            }}
          />
        )}
      </div>
      {managerOpen && (
        <ProfileManager
          activeProfileId={win.profileId}
          onClose={() => setManagerOpen(false)}
        />
      )}
    </div>
  );
}

