import { useState, useCallback, useEffect } from "react";
import { TopBar } from "@/components/TopBar";
import { Desktop } from "@/components/Desktop";
import { Dock } from "@/components/Dock";
import { Launchpad } from "@/components/Launchpad";
import { SearchPalette } from "@/components/SearchPalette";
import { ShortcutProvider, useShortcut } from "@/hooks/use-shortcut-registry";
import { useSessionPersistence } from "@/hooks/use-session-persistence";
import { useDeviceMode } from "@/hooks/use-device-mode";
import { useIsPwa } from "@/hooks/use-is-pwa";
import { useThemeStore } from "@/stores/theme-store";
import { useProcessStore } from "@/stores/process-store";
import { useDockStore } from "@/stores/dock-store";
import { getApp } from "@/registry/app-registry";
import { MobileDock } from "@/components/mobile/MobileDock";
import { CardSwitcher } from "@/components/mobile/CardSwitcher";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";
import { MobileAppWindow } from "@/components/mobile/MobileAppWindow";
import { MobileHomePages } from "@/components/mobile/MobileHomePages";
import { LoginGate } from "@/components/LoginGate";
import { LoginScreen } from "@/components/LoginScreen";
import { NotificationToasts } from "@/components/NotificationToast";
import { NotificationCentre } from "@/components/NotificationCentre";
import { useNotificationStore } from "@/stores/notification-store";
import { TaosAssistantPanel } from "@/components/TaosAssistantPanel";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { InstallPromptBanner } from "@/shell/InstallPromptBanner";
import { EffectsLayer } from "@/theme/effects/EffectsLayer";
import { SafetyFloor } from "@/components/SafetyFloor";

interface SystemShortcutsProps {
  toggleSearch: () => void;
  toggleLaunchpad: () => void;
  toggleAssistant: () => void;
}

function SystemShortcuts({ toggleSearch, toggleLaunchpad, toggleAssistant }: SystemShortcutsProps) {
  const windows = useProcessStore((s) => s.windows);
  const closeWindow = useProcessStore((s) => s.closeWindow);
  const minimizeWindow = useProcessStore((s) => s.minimizeWindow);
  const maximizeWindow = useProcessStore((s) => s.maximizeWindow);
  const focusWindow = useProcessStore((s) => s.focusWindow);
  const openWindow = useProcessStore((s) => s.openWindow);
  const pinned = useDockStore((s) => s.pinned);

  const getFocusedId = useCallback(() => {
    const sorted = [...windows]
      .filter((w) => !w.minimized)
      .sort((a, b) => b.zIndex - a.zIndex);
    return sorted[0]?.id ?? null;
  }, [windows]);

  const closeFocused = useCallback(() => {
    const id = getFocusedId();
    if (id) closeWindow(id);
  }, [getFocusedId, closeWindow]);

  const minimizeFocused = useCallback(() => {
    const id = getFocusedId();
    if (id) minimizeWindow(id);
  }, [getFocusedId, minimizeWindow]);

  const maximizeFocused = useCallback(() => {
    const id = getFocusedId();
    if (id) maximizeWindow(id);
  }, [getFocusedId, maximizeWindow]);

  const cycleNext = useCallback(() => {
    const visible = [...windows].filter((w) => !w.minimized).sort((a, b) => b.zIndex - a.zIndex);
    if (visible.length < 2) return;
    const next = visible[1]; if (next) focusWindow(next.id);
  }, [windows, focusWindow]);

  const cyclePrev = useCallback(() => {
    const visible = [...windows].filter((w) => !w.minimized).sort((a, b) => a.zIndex - b.zIndex);
    if (visible.length < 2) return;
    const prev = visible[0]; if (prev) focusWindow(prev.id);
  }, [windows, focusWindow]);

  useShortcut("Ctrl+Space", toggleSearch, "Toggle search palette", "system");
  useShortcut("Ctrl+l", toggleLaunchpad, "Toggle launchpad", "system");
  useShortcut("Ctrl+/", toggleAssistant, "Toggle taOS Assistant", "system");
  useShortcut("Ctrl+w", closeFocused, "Close focused window", "system");
  useShortcut("Ctrl+m", minimizeFocused, "Minimize focused window", "system");
  useShortcut("Ctrl+f", maximizeFocused, "Maximize/restore focused window", "system");
  useShortcut("Ctrl+Tab", cycleNext, "Cycle to next window", "system");
  useShortcut("Ctrl+Shift+Tab", cyclePrev, "Cycle to previous window", "system");

  // Ctrl+1 through Ctrl+9 — open/focus Nth pinned dock app
  const openPinned = useCallback((n: number) => {
    const appId = pinned[n];
    if (!appId) return;
    const app = getApp(appId);
    if (app) openWindow(appId, app.defaultSize);
  }, [pinned, openWindow]);

  useShortcut("Ctrl+1", useCallback(() => openPinned(0), [openPinned]), "Open pinned app 1", "system");
  useShortcut("Ctrl+2", useCallback(() => openPinned(1), [openPinned]), "Open pinned app 2", "system");
  useShortcut("Ctrl+3", useCallback(() => openPinned(2), [openPinned]), "Open pinned app 3", "system");
  useShortcut("Ctrl+4", useCallback(() => openPinned(3), [openPinned]), "Open pinned app 4", "system");
  useShortcut("Ctrl+5", useCallback(() => openPinned(4), [openPinned]), "Open pinned app 5", "system");
  useShortcut("Ctrl+6", useCallback(() => openPinned(5), [openPinned]), "Open pinned app 6", "system");
  useShortcut("Ctrl+7", useCallback(() => openPinned(6), [openPinned]), "Open pinned app 7", "system");
  useShortcut("Ctrl+8", useCallback(() => openPinned(7), [openPinned]), "Open pinned app 8", "system");
  useShortcut("Ctrl+9", useCallback(() => openPinned(8), [openPinned]), "Open pinned app 9", "system");

  return null;
}

export function App() {
  // Auto-launch on mobile/PWA — skip the login screen
  const isPwa = useIsPwa();
  const isTouchDevice = typeof window !== "undefined" && (
    "ontouchstart" in window || navigator.maxTouchPoints > 0
  );
  const [launched, setLaunched] = useState(isPwa || isTouchDevice);
  const [isFullscreen, setIsFullscreen] = useState(isPwa);
  const [launchpadOpen, setLaunchpadOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [cardSwitcherOpen, setCardSwitcherOpen] = useState(false);
  const [activeWindowId, setActiveWindowId] = useState<string | null>(null);

  const mode = useDeviceMode();
  const wallpaperImage = useThemeStore((s) => s.wallpaperImage);
  const wallpaperMobileImage = useThemeStore((s) => s.wallpaperMobileImage);
  const wallpaperFallback = useThemeStore((s) => s.wallpaperFallback);
  const windows = useProcessStore((s) => s.windows);
  const openWindow = useProcessStore((s) => s.openWindow);
  const closeWindow = useProcessStore((s) => s.closeWindow);
  const minimizeWindow = useProcessStore((s) => s.minimizeWindow);

  // Browser-mode flag: when on mobile but not in PWA, apply browser-safe
  // layout that accounts for Safari's dynamic URL bar + share/tab bars
  const isBrowserMobile = mode !== "desktop" && !isPwa;

  const activeWindow = windows.find((w) => w.id === activeWindowId);

  // Clear activeWindowId if the window was closed externally
  useEffect(() => {
    if (activeWindowId && !windows.find((w) => w.id === activeWindowId)) {
      setActiveWindowId(null);
    }
  }, [activeWindowId, windows]);

  const toggleLaunchpad = useCallback(() => setLaunchpadOpen((v) => !v), []);
  const toggleSearch = useCallback(() => setSearchOpen((v) => !v), []);
  const toggleAssistant = useCallback(() => useTaosAgentStore.getState().togglePanel(), []);

  // Listen for launchpad open event from context menu
  useEffect(() => {
    const handler = () => setLaunchpadOpen(true);
    window.addEventListener("open-launchpad", handler);
    return () => window.removeEventListener("open-launchpad", handler);
  }, []);

  useSessionPersistence();

  // Welcome notification — shown once per install, gated on a
  // localStorage flag so reload / refresh / re-mount don't replay it.
  // Users can re-trigger by clearing the flag from devtools.
  useEffect(() => {
    const WELCOME_FLAG = "taos.welcome.shown";
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem(WELCOME_FLAG)) return;
    useNotificationStore.getState().addNotification({
      source: "system",
      title: "Welcome to taOS",
      body: "Click the bell to see notifications from your agents",
      level: "info",
    });
    window.localStorage.setItem(WELCOME_FLAG, "1");
  }, []);

  // Track fullscreen state for the "Return to fullscreen" pill
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  // Mobile handlers
  const handleMobileHome = useCallback(() => {
    setActiveWindowId(null);
    setCardSwitcherOpen(false);
    setSearchOpen(false);
  }, []);

  const handleSelectApp = useCallback((windowId: string) => {
    setActiveWindowId(windowId);
    setCardSwitcherOpen(false);
  }, []);

  const handleMobileOpenApp = useCallback((appId: string) => {
    // If already open, focus it
    const existing = windows.find((w) => w.appId === appId);
    if (existing) {
      setActiveWindowId(existing.id);
    } else {
      const app = getApp(appId);
      if (app) {
        const wid = openWindow(appId, app.defaultSize);
        setActiveWindowId(wid);
      }
    }
    setCardSwitcherOpen(false);
    setSearchOpen(false);
  }, [windows, openWindow]);

  const handleMobileClose = useCallback(() => {
    if (activeWindowId) {
      closeWindow(activeWindowId);
      setActiveWindowId(null);
    }
  }, [activeWindowId, closeWindow]);

  const handleMobileMinimise = useCallback(() => {
    if (activeWindowId) {
      minimizeWindow(activeWindowId);
      setActiveWindowId(null);
    }
  }, [activeWindowId, minimizeWindow]);

  if (mode === "desktop") {
    return (
      <ShortcutProvider>
        <SystemShortcuts toggleSearch={toggleSearch} toggleLaunchpad={toggleLaunchpad} toggleAssistant={toggleAssistant} />
        <LoginGate>
          {!launched && <LoginScreen onLaunch={() => setLaunched(true)} />}
          {launched && !isFullscreen && (
            <button
              onClick={() => document.documentElement.requestFullscreen().catch(() => {})}
              className="fixed top-2 left-1/2 -translate-x-1/2 z-[9998] px-4 py-1.5 rounded-full bg-accent/90 text-white text-xs font-medium shadow-lg hover:bg-accent transition-colors"
              aria-label="Return to fullscreen"
            >
              Return to fullscreen
            </button>
          )}
          <div className={`transition-all duration-500 ${launched ? "opacity-100 scale-100" : "opacity-0 scale-95"}`}>
            <div className="h-screen w-screen flex flex-col overflow-hidden bg-shell-bg text-shell-text">
              <EffectsLayer />
              <TopBar onSearchOpen={toggleSearch} onAssistantOpen={toggleAssistant} />
              <Desktop />
              <Dock onLaunchpadOpen={toggleLaunchpad} />
              <Launchpad open={launchpadOpen} onClose={() => setLaunchpadOpen(false)} onOpenApp={(wid) => setActiveWindowId(wid)} />
              <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} onOpenApp={(wid) => setActiveWindowId(wid)} />
              <NotificationToasts />
              <NotificationCentre />
              <TaosAssistantPanel />
              <SafetyFloor />
            </div>
          </div>
        </LoginGate>
      </ShortcutProvider>
    );
  }

  // Mobile/Tablet layout — wrap in LoginGate so a stale or missing
  // session prompts the user to sign in instead of rendering apps with
  // empty data. The LoginScreen splash is intentionally still skipped
  // (mobile auto-launches), but the auth check itself is required.
  return (
    <ShortcutProvider>
      <SystemShortcuts toggleSearch={toggleSearch} toggleLaunchpad={toggleLaunchpad} toggleAssistant={toggleAssistant} />
      <LoginGate>
    <div className={`taos-wallpaper h-screen w-screen flex flex-col text-shell-text${isBrowserMobile ? " taos-browser" : ""}`} style={{ backgroundColor: wallpaperFallback, ["--wallpaper-desktop" as never]: wallpaperImage, ["--wallpaper-mobile" as never]: wallpaperMobileImage }}>
      <EffectsLayer />
      {/* Install banner — shown in browser mode, hidden in PWA */}
      {isBrowserMobile && <InstallPromptBanner />}
      <div className={`flex-1 flex flex-col overflow-hidden transition-all duration-500 ${launched ? "opacity-100 scale-100" : "opacity-0 scale-95"}`}>
        <MobileTopBar
          onHome={handleMobileHome}
          onSearch={() => { setCardSwitcherOpen(false); setSearchOpen((v) => !v); }}
        />
        {/* Main content area */}
        <div className="flex-1 relative overflow-hidden">
          {/* Home pages — always mounted so widgets never re-initialise on return */}
          <div style={{ position: "absolute", inset: 0, display: activeWindowId && activeWindow ? "none" : "flex", flexDirection: "column" }}>
            <MobileHomePages onOpenApp={handleMobileOpenApp} />
          </div>
          {/* App window — mounted only while open */}
          {activeWindowId && activeWindow && (
            <div style={{ position: "absolute", inset: 0 }}>
              <MobileAppWindow
                appId={activeWindow.appId}
                windowId={activeWindowId}
                onClose={handleMobileClose}
                onMinimise={handleMobileMinimise}
              />
            </div>
          )}
        </div>
      </div>
      {/* Dock — outside the transition container so it's never clipped */}
      <MobileDock
        onOpenApp={handleMobileOpenApp}
        onToggleSwitcher={() => setCardSwitcherOpen((v) => !v)}
        onOpenLaunchpad={() => { setCardSwitcherOpen(false); setSearchOpen(false); setLaunchpadOpen((v) => !v); }}
        activeAppId={activeWindow?.appId ?? null}
      />
      <CardSwitcher
        open={cardSwitcherOpen}
        onClose={() => setCardSwitcherOpen(false)}
        onSelectApp={handleSelectApp}
        onLaunchpad={() => {
          setCardSwitcherOpen(false);
          setLaunchpadOpen(true);
        }}
      />
      <Launchpad open={launchpadOpen} onClose={() => setLaunchpadOpen(false)} onOpenApp={(wid) => setActiveWindowId(wid)} />
      <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} onOpenApp={(wid) => setActiveWindowId(wid)} />
      <NotificationToasts />
      <NotificationCentre />
      <TaosAssistantPanel />
      <SafetyFloor />
    </div>
      </LoginGate>
    </ShortcutProvider>
  );
}
