import { create } from "zustand";
import type { ThemeConfig } from "@/theme/theme-config";
import { ALLOWED_TOKENS } from "@/theme/theme-config";

// Wallpapers are split into (image, fallback) pairs rather than a single
// background-shorthand so CSS media queries can control background-size
// per viewport (cover on desktop, contain on mobile so the full image is
// visible instead of cropped at the edges).

interface Wallpaper {
  id: string;
  label: string;
  image: string; // desktop background-image
  mobileImage?: string; // portrait-cropped variant, falls back to `image` if absent
  fallback: string; // background-color used as a fallback colour behind the image
}

const WALLPAPERS: Wallpaper[] = [
  {
    id: "default",
    label: "Default",
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

interface ThemeStore {
  wallpaperId: string;
  wallpaperImage: string;
  wallpaperMobileImage: string;
  wallpaperFallback: string;
  showDesktopIcons: boolean;
  structure: Record<string, { variant?: string } & Record<string, unknown>>;
  effects: { module: string; params?: Record<string, unknown> }[];

  activeThemeId: string;
  wallpaperByTheme: Record<string, string>;
  themeDefaultWallpaper: Record<string, string>;

  setWallpaper: (id: string) => void;
  toggleDesktopIcons: () => void;
  getWallpapers: () => Wallpaper[];
}

export const useThemeStore = create<ThemeStore>((set) => ({
  wallpaperId: "default",
  wallpaperImage: WALLPAPERS[0]!.image,
  wallpaperMobileImage: WALLPAPERS[0]!.mobileImage ?? WALLPAPERS[0]!.image,
  wallpaperFallback: WALLPAPERS[0]!.fallback,
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
      });
    }
  },

  toggleDesktopIcons() {
    set((s) => ({ showDesktopIcons: !s.showDesktopIcons }));
  },

  getWallpapers: () => WALLPAPERS,
}));

let _applied: string[] = []; // token keys currently set, for revert

export function applyThemeConfig(cfg: ThemeConfig) {
  revertTheme();
  const root = document.documentElement;
  for (const [k, v] of Object.entries(cfg.tokens || {})) {
    if (ALLOWED_TOKENS.has(k) && typeof v === "string") {
      root.style.setProperty(k, v);
      _applied.push(k);
    }
  }
  useThemeStore.setState({ structure: cfg.structure || {}, effects: cfg.effects || [] });
}

export function revertTheme() {
  const root = document.documentElement;
  for (const k of _applied) root.style.removeProperty(k);
  _applied = [];
  useThemeStore.setState({ structure: {}, effects: [] });
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
