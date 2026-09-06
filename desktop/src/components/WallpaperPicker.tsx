import { useThemeStore } from "@/stores/theme-store";
import type { Wallpaper } from "@/stores/theme-store";
import { Check, Globe } from "lucide-react";
import { WallhavenBrowser } from "./WallhavenBrowser";
import { useState } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
}

const SLIDERS: { key: "density" | "speed" | "glow"; label: string; min: number; max: number; step: number }[] = [
  { key: "density", label: "Density", min: 40, max: 340, step: 10 },
  { key: "speed", label: "Speed", min: 0, max: 2, step: 0.1 },
  { key: "glow", label: "Glow", min: 0, max: 16, step: 1 },
];

function WallpaperTile({
  wp,
  isSelected,
  onClick,
  showBadge,
}: {
  wp: Wallpaper;
  isSelected: boolean;
  onClick: () => void;
  showBadge?: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={wp.label}
      aria-pressed={isSelected}
      className={`relative rounded-lg overflow-hidden border-2 transition-all ${
        isSelected
          ? "border-accent ring-1 ring-accent/30"
          : "border-shell-border hover:border-shell-border-strong"
      }`}
    >
      <div
        className="relative h-24 w-full"
        style={
          wp.kind === "animated"
            ? { background: "radial-gradient(120% 120% at 50% 46%, #2a2a2e 0%, #1d1d1f 45%, #101011 100%)" }
            : {
                backgroundImage: wp.image,
                backgroundColor: wp.fallback,
                backgroundSize: "cover",
                backgroundPosition: "center",
                backgroundRepeat: "no-repeat",
              }
        }
      >
        {wp.overlayText && (
          <span className="absolute inset-0 grid place-items-center text-[13px] font-semibold tracking-tight text-white/85">
            {wp.overlayText}
          </span>
        )}
      </div>
      <div className="px-2 py-1.5 text-xs text-shell-text-secondary text-left flex items-center gap-1.5">
        {wp.label}
        {showBadge && (
          <span className="text-[10px] px-1 py-px rounded bg-accent/15 text-accent font-medium">
            {showBadge}
          </span>
        )}
      </div>
      {isSelected && (
        <div className="absolute top-2 right-2 w-5 h-5 rounded-full bg-accent flex items-center justify-center">
          <Check size={12} className="text-white" />
        </div>
      )}
    </button>
  );
}

function SectionHeading({ label }: { label: string }) {
  return (
    <h4 className="text-[11px] font-medium uppercase tracking-wider text-shell-text-tertiary px-1 pt-2 pb-1">
      {label}
    </h4>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-shell-border px-3 py-4 text-center">
      <span className="text-xs text-shell-text-tertiary">{message}</span>
    </div>
  );
}

export function WallpaperPicker({ open, onClose }: Props) {
  const {
    wallpaperId,
    setWallpaper,
    getWallpapersBySection,
    wallpaperOverlayText,
    showOverlayText,
    toggleOverlayText,
    wallpaperKind,
    wallpaperParams,
    setWallpaperParam,
    themeDefaultWallpaperId,
    activeThemeId,
  } = useThemeStore();
  const sections = getWallpapersBySection();
  const themeDefaultId = themeDefaultWallpaperId[activeThemeId] || "graphite";
  const isThemeDefault = wallpaperId === themeDefaultId;
  const [browseOnlineOpen, setBrowseOnlineOpen] = useState(false);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[10002] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      onClick={onClose}
      style={{
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 16px)",
        paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px) * 0.35 + 16px)",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Change Wallpaper"
        className="w-full max-w-[500px] max-h-full flex flex-col rounded-xl border border-shell-border-strong overflow-hidden"
        style={{ backgroundColor: "rgba(29, 29, 31, 0.98)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-shell-border shrink-0">
          <h3 className="text-sm font-medium text-shell-text">Change Wallpaper</h3>
          <button
            onClick={onClose}
            className="text-shell-text-tertiary hover:text-shell-text text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="p-4 flex flex-col gap-1 overflow-y-auto flex-1">
          {sections.map((section) => (
            <div key={section.id}>
              <SectionHeading label={section.label} />
              {section.items.length > 0 ? (
                <div
                  className={
                    section.id === "themeDefault"
                      ? "grid grid-cols-1 gap-3"
                      : "grid grid-cols-2 gap-3"
                  }
                >
                  {section.items.map((wp) => (
                    <WallpaperTile
                      key={wp.id}
                      wp={wp}
                      isSelected={wallpaperId === wp.id}
                      onClick={() => setWallpaper(wp.id)}
                      showBadge={
                        section.id === "themeDefault" && isThemeDefault
                          ? "Theme default"
                          : undefined
                      }
                    />
                  ))}
                </div>
              ) : (
                <EmptyState
                  message={
                    section.id === "user"
                      ? "Upload an image"
                      : section.id === "online"
                        ? "Search Wallhaven"
                        : "No wallpapers"
                  }
                />
              )}
              {/* Browse online: render inside the Online section so multi-row
                  results scroll with the picker instead of getting clipped in
                  a shrink-0 footer. */}
              {section.id === "online" && (
                <div className="mt-2">
                  <button
                    onClick={() => setBrowseOnlineOpen((v) => !v)}
                    className="flex items-center gap-2 w-full px-1 py-1.5 text-xs text-shell-text-secondary hover:text-shell-text transition-colors"
                    aria-expanded={browseOnlineOpen}
                    aria-label="Browse online wallpapers"
                  >
                    <Globe size={13} />
                    Browse online
                    <span
                      className={`ml-auto text-[10px] transition-transform ${browseOnlineOpen ? "rotate-180" : ""}`}
                    >
                      ▼
                    </span>
                  </button>
                  {browseOnlineOpen && (
                    <div className="pt-2">
                      <WallhavenBrowser
                        onSelect={(url, label) => {
                          // Escape backslashes, single-quotes, and parens to prevent
                          // CSS injection and render breakage from remote URLs.
                          // ')' prematurely terminates url('...'); '(' is also unsafe.
                          const safeUrl = url
                            .replace(/\\/g, "\\\\")
                            .replace(/'/g, "\\'")
                            .replace(/\(/g, "%28")
                            .replace(/\)/g, "%29")
                            .replace(/[\x00-\x1f\x7f]/g, "");
                          // Reject non-http(s) schemes (data:, javascript:, etc.)
                          if (!/^https?:\/\//i.test(safeUrl)) return;
                          const id = `wallhaven-${Date.now()}`;
                          const image = `url('${safeUrl}')`;
                          // Route through a state updater so wallpaperIdByTheme is set for
                          // the active theme — otherwise a theme switch loses the pick.
                          // Also set light/mobile/fallback variants so the remote wallpaper
                          // works in every scheme.
                          useThemeStore.setState((s) => ({
                            wallpaperId: id,
                            wallpaperImage: image,
                            wallpaperMobileImage: image,
                            wallpaperFallback: "#1d1d1f",
                            wallpaperLightImage: image,
                            wallpaperLightMobileImage: image,
                            wallpaperLightFallback: "#f0f0f0",
                            wallpaperKind: "image",
                            wallpaperOverlayText: label,
                            wallpaperIdByTheme: {
                              ...s.wallpaperIdByTheme,
                              [s.activeThemeId]: id,
                            },
                          }));
                        }}
              />
            </div>
          )}
        </div>
          )}
        </div>
          ))}
        </div>
        {wallpaperKind === "animated" && (
          <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-shell-border shrink-0">
            {SLIDERS.map((s) => (
              <div key={s.key} className="flex items-center gap-3">
                <label htmlFor={`wp-${s.key}`} className="w-14 text-xs text-shell-text-secondary">
                  {s.label}
                </label>
                <input
                  id={`wp-${s.key}`}
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={wallpaperParams[s.key]}
                  onChange={(e) => setWallpaperParam(s.key, Number(e.target.value))}
                  className="flex-1 accent-accent"
                />
                <span className="w-9 text-right text-[11px] tabular-nums text-shell-text-tertiary">
                  {wallpaperParams[s.key]}
                </span>
              </div>
            ))}
          </div>
        )}
        {wallpaperOverlayText && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-shell-border shrink-0">
            <label htmlFor="wp-slogan" className="text-xs text-shell-text-secondary">
              Show slogan ({wallpaperOverlayText})
            </label>
            <button
              id="wp-slogan"
              role="switch"
              aria-checked={showOverlayText}
              onClick={toggleOverlayText}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                showOverlayText ? "bg-accent" : "bg-shell-surface-active"
              }`}
            >
              <span
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  showOverlayText ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
