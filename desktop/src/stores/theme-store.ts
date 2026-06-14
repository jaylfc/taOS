import { create } from "zustand";
import type { ThemeConfig } from "@/theme/theme-config";
import { ALLOWED_TOKENS } from "@/theme/theme-config";
import { BUILTIN_THEMES } from "@/theme/builtin-themes";

// Wallpapers are split into (image, fallback) pairs rather than a single
// background-shorthand so CSS media queries can control background-size
// per viewport (cover on desktop, contain on mobile so the full image is
// visible instead of cropped at the edges).

interface Wallpaper {
  id: string;
  label: string;
  image: string; // desktop background-image (empty for animated wallpapers)
  mobileImage?: string; // portrait-cropped variant, falls back to `image` if absent
  fallback: string; // background-color used as a fallback colour behind the image
  // "image" = CSS background (url/gradient); "animated" = a live render component
  // selected by `component`. Defaults to "image" when absent.
  kind?: "image" | "animated";
  // Render component id for animated wallpapers (e.g. "neural"). New animated
  // wallpapers register a renderer and reference it here.
  component?: string;
  // Optional default slogan overlaid (centered) on top of this wallpaper. null /
  // absent = no slogan. The overlay is generic, not tied to any wallpaper kind;
  // the user can toggle it off. Styling (colour/size/effects) defaults for now.
  overlayText?: string | null;
  // Optional light-scheme variants. When the active theme reads as light, these
  // are used instead so the wallpaper inverts with the theme. Fall back to the
  // dark image when absent.
  lightImage?: string;
  lightMobileImage?: string;
  lightFallback?: string;
}

const WALLPAPERS: Wallpaper[] = [
  {
    id: "graphite",
    label: "Graphite",
    // The original neural-brain wallpaper regraded to neutral graphite (taOS is
    // baked into the artwork). The animated/custom configurable wallpaper is a
    // follow-up (tsParticles); this static image is the screenshot-ready default.
    image: "url('/static/wallpaper-graphite.png')",
    mobileImage: "url('/static/wallpaper-graphite-mobile.png')",
    fallback: "#141415",
    // Inverted variant (light field, dark network) for the light theme.
    lightImage: "url('/static/wallpaper-graphite-light.png')",
    lightMobileImage: "url('/static/wallpaper-graphite-light-mobile.png')",
    lightFallback: "#eef0f3",
    kind: "image",
  },
  {
    id: "neural-live",
    label: "Neural (Live)",
    // Configurable live particle-network (tsParticles). Theme-aware: it derives
    // its colours from the active scheme, so it inverts with the theme.
    image: "",
    fallback: "#141415",
    lightFallback: "#eef0f3",
    kind: "animated",
    component: "particles",
    overlayText: "taOS",
  },
  {
    id: "default",
    label: "Classic",
    image: "url('/static/wallpaper.png')",
    mobileImage: "url('/static/wallpaper-mobile.png')",
    fallback: "#1a1b2e",
  },
  {
    id: "deep-indigo",
    label: "Deep Indigo",
    image: "linear-gradient(160deg, #1a1b2e 0%, #1e2140 40%, #252848 100%)",
    fallback: "#1a1b2e",
  },
  {
    id: "midnight",
    label: "Midnight Blue",
    image: "linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)",
    fallback: "#0f0c29",
  },
  {
    id: "aurora",
    label: "Aurora",
    image: "linear-gradient(135deg, #0f2027 0%, #203a43 40%, #2c5364 100%)",
    fallback: "#0f2027",
  },
  {
    id: "dusk",
    label: "Dusk",
    image: "linear-gradient(135deg, #1a1a2e 0%, #16213e 40%, #0f3460 100%)",
    fallback: "#1a1a2e",
  },
  {
    id: "forest",
    label: "Forest",
    image: "linear-gradient(160deg, #0d1b0e 0%, #1a2f1a 40%, #1e3a1e 100%)",
    fallback: "#0d1b0e",
  },
  {
    id: "ocean",
    label: "Ocean",
    image: "linear-gradient(160deg, #0a192f 0%, #0d2847 40%, #112d4e 100%)",
    fallback: "#0a192f",
  },
  {
    id: "charcoal",
    label: "Charcoal",
    image: "linear-gradient(180deg, #1c1c1c 0%, #2d2d2d 100%)",
    fallback: "#1c1c1c",
  },
  {
    id: "gunmetal",
    label: "Gunmetal",
    image: "linear-gradient(165deg, #16181f 0%, #1f232c 50%, #252a35 100%)",
    fallback: "#16181f",
  },
];

// Default wallpaper for taOS Dark: the animated graphite field.
const DEFAULT_WP = WALLPAPERS.find((w) => w.id === "graphite") ?? WALLPAPERS[0]!;

// The slogan-overlay toggle persists locally so a clean desktop survives a
// reload. Best-effort: any storage failure falls back to "on".
const SLOGAN_KEY = "taos-wallpaper-slogan";
function loadSloganPref(): boolean {
  try {
    return localStorage.getItem(SLOGAN_KEY) !== "off";
  } catch {
    return true;
  }
}

// User-tunable parameters for animated (live) wallpapers, persisted locally.
// density: particle-count target; speed: drift speed; glow: node bloom radius.
export interface WallpaperParams {
  density: number;
  speed: number;
  glow: number;
}
export const DEFAULT_WALLPAPER_PARAMS: WallpaperParams = { density: 200, speed: 0.5, glow: 6 };
const PARAMS_KEY = "taos-wallpaper-params";
function loadWallpaperParams(): WallpaperParams {
  try {
    const raw = localStorage.getItem(PARAMS_KEY);
    if (raw) return { ...DEFAULT_WALLPAPER_PARAMS, ...JSON.parse(raw) };
  } catch {
    // best-effort
  }
  return { ...DEFAULT_WALLPAPER_PARAMS };
}

interface ThemeStore {
  wallpaperId: string;
  wallpaperImage: string;
  wallpaperMobileImage: string;
  wallpaperFallback: string;
  // Light-scheme variants (empty when the wallpaper has none). The desktop uses
  // these when `scheme` is "light", so the wallpaper inverts with the theme.
  wallpaperLightImage: string;
  wallpaperLightMobileImage: string;
  wallpaperLightFallback: string;
  scheme: "light" | "dark";
  wallpaperKind: "image" | "animated";
  wallpaperComponent: string | null;
  wallpaperOverlayText: string | null;
  showOverlayText: boolean;
  wallpaperParams: WallpaperParams;
  showDesktopIcons: boolean;
  structure: Record<string, { variant?: string } & Record<string, unknown>>;
  effects: { module: string; params?: Record<string, unknown> }[];

  activeThemeId: string;
  wallpaperByTheme: Record<string, string>;
  themeDefaultWallpaper: Record<string, string>;

  setWallpaper: (id: string) => void;
  toggleOverlayText: () => void;
  setWallpaperParam: (key: keyof WallpaperParams, value: number) => void;
  toggleDesktopIcons: () => void;
  getWallpapers: () => Wallpaper[];
}

export const useThemeStore = create<ThemeStore>((set) => ({
  wallpaperId: DEFAULT_WP.id,
  wallpaperImage: DEFAULT_WP.image,
  wallpaperMobileImage: DEFAULT_WP.mobileImage ?? DEFAULT_WP.image,
  wallpaperFallback: DEFAULT_WP.fallback,
  wallpaperLightImage: DEFAULT_WP.lightImage ?? "",
  wallpaperLightMobileImage: DEFAULT_WP.lightMobileImage ?? DEFAULT_WP.lightImage ?? "",
  wallpaperLightFallback: DEFAULT_WP.lightFallback ?? DEFAULT_WP.fallback,
  scheme: "dark",
  wallpaperKind: DEFAULT_WP.kind ?? "image",
  wallpaperComponent: DEFAULT_WP.component ?? null,
  wallpaperOverlayText: DEFAULT_WP.overlayText ?? null,
  showOverlayText: loadSloganPref(),
  wallpaperParams: loadWallpaperParams(),
  showDesktopIcons: true,
  structure: {},
  effects: [],

  activeThemeId: "default",
  wallpaperByTheme: {},
  themeDefaultWallpaper: {},

  setWallpaper(id) {
    const wp = WALLPAPERS.find((w) => w.id === id);
    if (wp) {
      set({
        wallpaperId: id,
        wallpaperImage: wp.image,
        wallpaperMobileImage: wp.mobileImage ?? wp.image,
        wallpaperFallback: wp.fallback,
        wallpaperLightImage: wp.lightImage ?? "",
        wallpaperLightMobileImage: wp.lightMobileImage ?? wp.lightImage ?? "",
        wallpaperLightFallback: wp.lightFallback ?? wp.fallback,
        wallpaperKind: wp.kind ?? "image",
        wallpaperComponent: wp.component ?? null,
        wallpaperOverlayText: wp.overlayText ?? null,
      });
    }
  },

  toggleOverlayText() {
    set((s) => {
      const next = !s.showOverlayText;
      try {
        localStorage.setItem(SLOGAN_KEY, next ? "on" : "off");
      } catch {
        // best-effort
      }
      return { showOverlayText: next };
    });
  },

  setWallpaperParam(key, value) {
    set((s) => {
      const wallpaperParams = { ...s.wallpaperParams, [key]: value };
      try {
        localStorage.setItem(PARAMS_KEY, JSON.stringify(wallpaperParams));
      } catch {
        // best-effort
      }
      return { wallpaperParams };
    });
  },

  toggleDesktopIcons() {
    set((s) => ({ showDesktopIcons: !s.showDesktopIcons }));
  },

  getWallpapers: () => WALLPAPERS,
}));

let _applied: string[] = []; // token keys currently set, for revert

// Decide whether a theme reads as light or dark from its window-body colour,
// so the light-scheme compatibility layer in tokens.css (which inverts the
// hardcoded white overlays apps still use) keys off one attribute. Works for
// the builtin Light theme and any agent-generated light theme alike.
function schemeFromBg(bg: string | undefined): "light" | "dark" {
  if (!bg) return "dark"; // no override (default theme) -> the dark base CSS
  let r: number, g: number, b: number;
  const hex = bg.trim().match(/^#([0-9a-fA-F]{6})$/);
  if (hex) {
    const n = parseInt(hex[1]!, 16);
    [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  } else {
    const m = bg.match(/rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
    if (!m) return "dark";
    [r, g, b] = [+m[1]!, +m[2]!, +m[3]!];
  }
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance > 0.55 ? "light" : "dark";
}

// Safari/WebKit leaves backdrop-filter elements (dock, top bar, modals,
// widgets) on stale GPU compositing layers when the theme custom properties
// on :root change at runtime: the layer keeps its old raster until something
// forces a re-composite (scroll, resize, screenshot), so on a theme switch it
// can flash black on the live screen even though screenshots look correct
// (the screenshot path forces a full raster). Dropping backdrop-filter for a
// frame via [data-theme-switching] (see tokens.css) forces WebKit to rebuild
// every backdrop layer against the new tokens. No-op outside the browser.
export function forceCompositingRepaint() {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-theme-switching", "");
  void root.offsetHeight; // flush the filter:none state before restoring it
  const clear = () => root.removeAttribute("data-theme-switching");
  // Two nested rAFs let one frame paint with the filter off before restoring it.
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => requestAnimationFrame(clear));
  }
  // rAF is paused on a hidden/background tab, which would otherwise leave the
  // attribute (and the backdrop-filter:none rule) stuck until the tab is shown.
  // A timer guarantees cleanup regardless; removeAttribute is idempotent.
  setTimeout(clear, 250);
}

// Safari/WebKit drops or staleifies backdrop-filter compositing layers while a
// tab is hidden and does NOT rebuild them when the tab is shown again, so
// switching back into taOS leaves frosted-glass surfaces (windows, dock, top
// bar, the agent chat panel) blank/black until something forces a re-composite.
// rAF is paused while hidden, so the theme-switch repaint never runs on its own.
// Re-run the same repaint nudge the moment the page becomes visible again (and
// on bfcache restore via pageshow). Idempotent: installs the listeners once.
let _webkitRepaintGuardsInstalled = false;
export function installWebkitRepaintGuards() {
  if (_webkitRepaintGuardsInstalled) return;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  _webkitRepaintGuardsInstalled = true;
  const onVisible = () => {
    if (document.visibilityState === "visible") forceCompositingRepaint();
  };
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("pageshow", forceCompositingRepaint);
  // Some WebKit builds only fire focus, not visibilitychange, on tab return.
  window.addEventListener("focus", forceCompositingRepaint);
}

export function applyThemeConfig(cfg: ThemeConfig) {
  revertTheme({ silent: true }); // applyThemeConfig owns the single repaint below
  const root = document.documentElement;
  for (const [k, v] of Object.entries(cfg.tokens || {})) {
    if (ALLOWED_TOKENS.has(k) && typeof v === "string") {
      root.style.setProperty(k, v);
      _applied.push(k);
    }
  }
  const scheme = schemeFromBg(cfg.tokens?.["--color-shell-bg"]);
  root.dataset.scheme = scheme;
  useThemeStore.setState({ structure: cfg.structure || {}, effects: cfg.effects || [], scheme });
  forceCompositingRepaint();
}

export function revertTheme(opts?: { silent?: boolean }) {
  const root = document.documentElement;
  for (const k of _applied) root.style.removeProperty(k);
  _applied = [];
  root.dataset.scheme = "dark"; // base shell is dark
  useThemeStore.setState({ structure: {}, effects: [], scheme: "dark" });
  // Skip when called from applyThemeConfig (which repaints once after applying
  // the new tokens) so a theme switch does not force two reflows.
  if (!opts?.silent) forceCompositingRepaint();
}

export function setWallpaperForActiveTheme(value: string) {
  const { activeThemeId, wallpaperByTheme } = useThemeStore.getState();
  useThemeStore.setState({ wallpaperByTheme: { ...wallpaperByTheme, [activeThemeId]: value } });
}

export function resolveWallpaper(): string {
  const { activeThemeId, wallpaperByTheme, themeDefaultWallpaper } = useThemeStore.getState();
  return wallpaperByTheme[activeThemeId] ?? themeDefaultWallpaper[activeThemeId] ?? "";
}

let _priorConfig: ThemeConfig | null = null;

export function previewTheme(cfg: ThemeConfig, priorCfg: ThemeConfig | null) {
  _priorConfig = priorCfg;
  applyThemeConfig(cfg);
}

export function revertPreview() {
  if (_priorConfig) applyThemeConfig(_priorConfig);
  else revertTheme();
  _priorConfig = null;
}

// Re-apply the persisted active theme at app boot. Best-effort: any failure
// (network, missing pref, unknown theme) leaves the default theme in place.
export async function restoreActiveTheme(): Promise<void> {
  try {
    const res = await fetch("/api/preferences/themes", { credentials: "include" });
    if (!res.ok) return;
    const pref = (await res.json()) as { active_theme_id?: string } | null;
    const themeId = pref?.active_theme_id;
    if (!themeId || themeId === "default") return;

    let cfg: ThemeConfig | undefined = BUILTIN_THEMES.find((t) => t.theme_id === themeId)?.config;
    if (!cfg) {
      const tRes = await fetch("/api/themes", { credentials: "include" });
      if (tRes.ok) {
        const themes = (await tRes.json()) as { theme_id: string; config: ThemeConfig }[];
        cfg = themes.find((t) => t.theme_id === themeId)?.config;
      }
    }
    if (!cfg) return;

    applyThemeConfig(cfg);
    useThemeStore.setState({
      activeThemeId: themeId,
      themeDefaultWallpaper: {
        ...useThemeStore.getState().themeDefaultWallpaper,
        ...(cfg.wallpaper ? { [themeId]: cfg.wallpaper } : {}),
      },
    });
  } catch {
    // best-effort: ignore
  }
}

export function keepTheme(themeId: string, cfg: ThemeConfig) {
  _priorConfig = null;
  useThemeStore.setState({
    activeThemeId: themeId,
    themeDefaultWallpaper: {
      ...useThemeStore.getState().themeDefaultWallpaper,
      ...(cfg.wallpaper ? { [themeId]: cfg.wallpaper } : {}),
    },
  });
  // persist active theme id
  void fetch("/api/preferences/themes", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active_theme_id: themeId }),
  }).catch(() => {});
}
