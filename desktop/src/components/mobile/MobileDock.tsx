import * as icons from "lucide-react";
import { useMobileHomeStore } from "@/stores/mobile-home-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { useIsSquareViewport } from "@/hooks/use-is-square-viewport";

interface Props {
  onOpenApp: (appId: string) => void;
  onToggleSwitcher: () => void;
  onOpenLaunchpad: () => void;
  activeAppId: string | null;
  /** True when running in mobile Safari/Chrome (not installed as a PWA). */
  isBrowserMobile?: boolean;
}

function resolveIcon(iconName: string): icons.LucideIcon {
  const key = iconName
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  return (icons[key] as icons.LucideIcon) ?? icons.HelpCircle;
}

export function MobileDock({ onOpenApp, onToggleSwitcher, onOpenLaunchpad, activeAppId, isBrowserMobile = false }: Props) {
  const dockApps = useMobileHomeStore((s) => s.dockApps);
  const windows = useProcessStore((s) => s.windows);

  // The dock is the bottom-most element in the mobile column, so it owns the
  // home-indicator / browser-chrome clearance for everything above it. We add
  // env(safe-area-inset-bottom) on TOP of a base gap so the dock (and the app
  // content that ends just above it) always clears the home indicator. In PWA
  // mode the inset is non-zero (notch devices); in browser mode it is 0, so we
  // add extra room to keep the dock above Safari's ~50 px URL/tab bar.
  //
  // That browser reserve is a fixed guess, and on a square screen it is a bad
  // trade. The root is already sized with 100dvh (see .taos-mobile-root), and
  // the dynamic viewport unit ALREADY excludes whatever browser chrome is
  // showing — so the reserve is belt-and-braces on top of a fit that is
  // correct. On a tall phone the extra 54px is barely noticeable; on a roughly
  // square display it strands the dock well above the bottom edge, because the
  // same fixed cost is a much larger share of a much shorter screen.
  //
  // So keep the full reserve where it was measured and is cheap, and shrink it
  // to the PWA gap on square screens, where dvh is doing the real work anyway.
  const isSquareViewport = useIsSquareViewport();
  const browserGap = isSquareViewport ? 12 : 54;
  const dockBaseGap = isBrowserMobile ? browserGap : 12;
  const dockPaddingBottom = `calc(env(safe-area-inset-bottom, 0px) + ${dockBaseGap}px)`;

  return (
    <div
      role="toolbar"
      aria-label="Dock"
      className="shrink-0"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        paddingTop: 6,
        paddingBottom: dockPaddingBottom,
      }}
    >
      {/* Launchpad / All Apps button */}
      <button
        onClick={onOpenLaunchpad}
        aria-label="All Apps"
        style={{
          width: 44,
          height: 44,
          borderRadius: 12,
          background: "linear-gradient(135deg, rgba(60,65,78,0.85), rgba(35,38,48,0.9))",
          border: "1px solid rgba(255,255,255,0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          padding: 0,
        }}
      >
        <icons.LayoutGrid size={20} style={{ color: "rgba(255,255,255,0.8)" }} />
      </button>

      {dockApps.map((appId) => {
        const app = getApp(appId);
        if (!app) return null;

        const Icon = resolveIcon(app.icon);
        const isActive = appId === activeAppId;
        const isRunning = windows.some((w) => w.appId === appId);

        return (
          <div key={appId} style={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center" }}>
            <button
              onClick={() => onOpenApp(appId)}
              aria-label={`Open ${app.name}`}
              style={{
                width: 44,
                height: 44,
                borderRadius: 12,
                background: "linear-gradient(135deg, rgba(60,65,78,0.85), rgba(35,38,48,0.9))",
                border: "1px solid rgba(255,255,255,0.1)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                padding: 0,
              }}
            >
              <Icon size={22} style={{ color: "rgba(255,255,255,0.8)" }} />
            </button>

            {isActive && (
              <div
                style={{
                  position: "absolute",
                  bottom: -6,
                  left: "50%",
                  transform: "translateX(-50%)",
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  background: "rgba(255,255,255,0.7)",
                }}
              />
            )}

            {!isActive && isRunning && (
              <div
                style={{
                  position: "absolute",
                  bottom: -6,
                  left: "50%",
                  transform: "translateX(-50%)",
                  width: 3,
                  height: 3,
                  borderRadius: "50%",
                  background: "rgba(255,255,255,0.3)",
                }}
              />
            )}
          </div>
        );
      })}

      <div
        style={{
          width: 1,
          height: 28,
          background: "rgba(255,255,255,0.15)",
          margin: "0 6px",
          flexShrink: 0,
        }}
      />

      <button
        onClick={onToggleSwitcher}
        aria-label="App Switcher"
        style={{
          width: 44,
          height: 44,
          borderRadius: 12,
          background: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          padding: 0,
        }}
      >
        <icons.Layers size={20} style={{ color: "rgba(255,255,255,0.7)" }} />
      </button>
    </div>
  );
}
