/**
 * BrowserApp v2 - Chrome (toolbar).
 *
 * The unified browser toolbar rendered INSIDE the window, BELOW the tab strip.
 * Left to right: back / forward / reload / home nav buttons, the pill omnibox
 * (AddressBar, fronted by a connection-security lock), an agent-presence pill +
 * add-agent affordance, menu and settings buttons, and the profile chip whose
 * dropdown lists the user's and agents' profiles.
 *
 * NOTE: The OS-level traffic lights (close / minimize / maximize) live in
 * `desktop/src/components/Window.tsx` — every window in taOS gets them
 * automatically. This component does NOT render its own traffic lights.
 *
 * The Proxy/Streamed engine toggle lives in the tab strip (BrowserModeToggle),
 * not here.
 */
import { useState, useEffect, useRef } from "react";
import {
  ArrowLeft,
  ArrowRight,
  RotateCw,
  Home,
  Lock,
  ChevronDown,
  Settings,
} from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import { listProfiles, type Profile } from "@/lib/browser-profile-api";
import { AddressBar } from "./AddressBar";
import { ProfileSwitcher } from "./ProfileSwitcher";
import { ProfileManager } from "./ProfileManager";
import { SettingsPanel } from "./SettingsPanel";
import { AgentPickerPopover } from "./AgentPickerPopover";
import { AgentPresencePill } from "./AgentPresencePill";
import { CoPilotBanner } from "./CoPilotBanner";

import { HOME_URL } from "@/stores/browser-store";

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
  const [pickerOpen, setPickerOpen] = useState(false);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const agentChipRef = useRef<HTMLButtonElement>(null);

  const currentProfileId = win?.profileId ?? "";

  useEffect(() => {
    let cancelled = false;
    listProfiles().then((list) => {
      if (!cancelled) setProfiles(list);
    });
    return () => { cancelled = true; };
  }, [currentProfileId]);

  // Cmd+Shift+A keyboard shortcut → open agent picker
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ windowId: string }>;
      if (ce.detail?.windowId !== windowId) return;
      setSettingsOpen(false);
      setSwitcherOpen(false);
      setManagerOpen(false);
      setPickerOpen(true);
    };
    window.addEventListener("taos-browser:open-agent-picker", handler);
    return () => window.removeEventListener("taos-browser:open-agent-picker", handler);
  }, [windowId]);

  // Subscribe to drivingState so the banner mounts/unmounts reactively.
  const drivingAgentId = useBrowserAgentStore((s) => {
    if (!win) return null;
    const activeTabId = win.activeTabId;
    return s.isAnyDriving(windowId, activeTabId);
  });

  if (!win) return null;

  const activeTab = win.tabs.find((t) => t.id === win.activeTabId);
  if (!activeTab) return null;

  const canGoBack = activeTab.historyIndex > 0;
  const canGoForward = activeTab.historyIndex < activeTab.history.length - 1;
  // Connection security: the proxy serves over https. Treat an https URL as
  // secure; about:blank / new-tab pages show no lock.
  const isSecure = /^https:\/\//i.test(activeTab.url);
  const hasUrl = !!activeTab.url && activeTab.url !== "about:blank";

  const handleRefresh = () => {
    // Re-navigate to the current URL to bump the iframe to reload.
    if (activeTab.url) {
      navigateTab(windowId, activeTab.id, activeTab.url);
    }
  };

  const navBtn =
    "flex h-[34px] w-[34px] items-center justify-center rounded-[9px] text-shell-text-secondary transition-colors hover:bg-white/[0.06] hover:text-shell-text disabled:text-shell-text-tertiary disabled:hover:bg-transparent disabled:cursor-default";

  return (
    <div className="flex flex-col">
      {drivingAgentId && (
        <CoPilotBanner
          windowId={windowId}
          tabId={activeTab.id}
          profileId={win.profileId}
          agentId={drivingAgentId}
        />
      )}
      <div
        className="flex items-center gap-1.5 px-3 h-[48px] bg-shell-surface border-b border-shell-border"
        role="toolbar"
        aria-label="Browser navigation"
      >
        {/* Nav buttons */}
        <button
          type="button"
          aria-label="Back"
          onClick={() => goBack(windowId, activeTab.id)}
          disabled={!canGoBack}
          className={navBtn}
        >
          <ArrowLeft size={18} />
        </button>

        <button
          type="button"
          aria-label="Forward"
          onClick={() => goForward(windowId, activeTab.id)}
          disabled={!canGoForward}
          className={navBtn}
        >
          <ArrowRight size={18} />
        </button>

        <button
          type="button"
          aria-label="Refresh"
          onClick={handleRefresh}
          className={navBtn}
        >
          <RotateCw size={18} />
        </button>

        <button
          type="button"
          aria-label="Home"
          title="Home"
          onClick={() => navigateTab(windowId, activeTab.id, HOME_URL)}
          className={navBtn}
        >
          <Home size={18} />
        </button>

        {/* Omnibox: pill wrapper around the address bar with a security lock. */}
        <div className="flex flex-1 min-w-0 items-center gap-2 h-[36px] px-3.5 rounded-full bg-shell-bg-deep border border-shell-border transition-colors hover:border-shell-border-strong focus-within:border-accent/40 focus-within:ring-2 focus-within:ring-accent/20">
          {hasUrl && (
            <Lock
              size={13}
              aria-label={isSecure ? "Connection is secure" : "Connection is not secure"}
              className={`shrink-0 ${isSecure ? "text-[var(--color-traffic-maximize)]" : "text-shell-text-tertiary"}`}
            />
          )}
          <AddressBar windowId={windowId} />
        </div>

        {/* Agent presence pill + "+ agent" affordance */}
        <div className="relative flex items-center gap-1">
          {activeTab.pinnedAgentIds.length > 0 && (
            <AgentPresencePill
              windowId={windowId}
              tabId={activeTab.id}
              pinnedAgentIds={activeTab.pinnedAgentIds}
              triggerRef={agentChipRef}
            />
          )}
          {/* "+ agent" affordance: always visible (until at cap) so users can
               add a 2nd/3rd/4th agent without remembering Cmd+Shift+A. */}
          {activeTab.pinnedAgentIds.length < 4 && (
            <button
              ref={agentChipRef}
              type="button"
              aria-label="Add agent"
              aria-haspopup="listbox"
              aria-expanded={pickerOpen}
              onClick={() => {
                setSettingsOpen(false);
                setSwitcherOpen(false);
                setManagerOpen(false);
                setPickerOpen((p) => !p);
              }}
              className={
                activeTab.pinnedAgentIds.length === 0
                  ? "flex h-[32px] items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-3 text-[11.5px] font-semibold text-accent-strong transition-colors hover:bg-accent-glow"
                  : "flex h-5 w-5 items-center justify-center rounded-full border border-shell-border bg-shell-bg-deep text-xs text-shell-text-secondary transition-colors hover:bg-white/[0.06] hover:text-shell-text"
              }
              title={activeTab.pinnedAgentIds.length === 0 ? undefined : "Add agent"}
            >
              {activeTab.pinnedAgentIds.length === 0 ? "+ agent" : "+"}
            </button>
          )}
          {pickerOpen && (
            <AgentPickerPopover
              windowId={windowId}
              tabId={activeTab.id}
              profileId={win.profileId}
              pinnedAgentIds={activeTab.pinnedAgentIds}
              onClose={() => setPickerOpen(false)}
              triggerRef={agentChipRef}
            />
          )}
        </div>

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
              setPickerOpen(false);
              setSettingsOpen((s) => !s);
            }}
            className={navBtn}
          >
            <Settings size={18} />
          </button>
          {settingsOpen && (
            <SettingsPanel profileId={currentProfileId} onClose={() => setSettingsOpen(false)} />
          )}
        </div>

        {/* Profile chip: clicking opens the ProfileSwitcher dropdown */}
        <div className="relative">
          {(() => {
            const activeProfile = profiles?.find((p) => p.profile_id === win.profileId);
            const chipColor = activeProfile?.color ?? "#8b92a3";
            const chipName = activeProfile?.name ?? win.profileId;
            const initial = (chipName?.[0] ?? "?").toUpperCase();
            return (
              <button
                type="button"
                onClick={() => {
                  setSettingsOpen(false);
                  setManagerOpen(false);
                  setPickerOpen(false);
                  setSwitcherOpen((s) => !s);
                }}
                className="flex h-[34px] items-center gap-2 rounded-full border border-shell-border bg-shell-bg-deep pl-1.5 pr-2.5 transition-colors hover:bg-white/[0.06] hover:border-shell-border-strong"
                aria-label={`Profile: ${win.profileId}`}
                aria-haspopup="menu"
                aria-expanded={switcherOpen}
              >
                <span
                  className="flex h-[22px] w-[22px] items-center justify-center rounded-full text-[10px] font-bold text-white"
                  style={{ backgroundColor: chipColor }}
                  aria-hidden="true"
                >
                  {initial}
                </span>
                <span className="text-xs font-semibold capitalize text-shell-text">{chipName}</span>
                <ChevronDown size={12} className="text-shell-text-tertiary" aria-hidden="true" />
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
    </div>
  );
}
