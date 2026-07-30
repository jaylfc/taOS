import type { ThemeConfig } from "./theme-config";

export interface BuiltinTheme {
  theme_id: string;
  name: string;
  builtin: boolean;
  config: ThemeConfig;
}

export const BUILTIN_THEMES: BuiltinTheme[] = [
  {
    theme_id: "default",
    name: "taOS Dark",
    builtin: true,
    config: { tokens: {}, structure: {}, effects: [], requires: ["assistant", "launcher"], wallpaper: null },
  },
  {
    theme_id: "light",
    name: "taOS Light",
    builtin: true,
    config: {
      tokens: {
        // Cool off-white window body and a slightly deeper sidebar layer.
        "--color-shell-bg": "#f4f5f7",
        "--color-shell-bg-deep": "#e9ebef",
        // Surfaces invert from white-on-dark to subtle black-on-light fills.
        "--color-shell-surface": "rgba(0, 0, 0, 0.035)",
        "--color-shell-surface-hover": "rgba(0, 0, 0, 0.055)",
        "--color-shell-surface-active": "rgba(0, 0, 0, 0.08)",
        "--color-shell-border": "rgba(0, 0, 0, 0.09)",
        "--color-shell-border-strong": "rgba(0, 0, 0, 0.15)",
        "--color-shell-scrim": "rgba(0, 0, 0, 0.45)",
        "--color-shell-subtle": "rgba(0, 0, 0, 0.055)",
        // Near-black ink: 14:1 / 6.4:1 / 4.0:1 on the #f4f5f7 body.
        "--color-shell-text": "rgba(0, 0, 0, 0.85)",
        "--color-shell-text-secondary": "rgba(0, 0, 0, 0.55)",
        "--color-shell-text-tertiary": "rgba(0, 0, 0, 0.42)",
        // Slate accent — the dark theme's cool-neutral grey, darkened to read on light.
        "--color-accent": "#5b6472",
        "--color-accent-glow": "rgba(91, 100, 114, 0.25)",
        // Frosted near-white chrome.
        "--color-dock-bg": "rgba(245, 246, 248, 0.82)",
        "--color-dock-border": "rgba(0, 0, 0, 0.1)",
        "--color-topbar-bg": "rgba(245, 246, 248, 0.82)",
        "--color-snap-preview": "rgba(91, 100, 114, 0.16)",
        "--color-snap-border": "rgba(91, 100, 114, 0.45)",
        // Lighter, softer shadows — heavy dark drops look wrong on a light surface.
        "--shadow-window": "0 8px 32px rgba(0, 0, 0, 0.16)",
        "--shadow-window-unfocused": "0 4px 16px rgba(0, 0, 0, 0.1)",
        "--shadow-dock": "0 4px 24px rgba(0, 0, 0, 0.12)",
        "--shadow-card": "0 1px 3px rgba(0, 0, 0, 0.1), 0 0 1px rgba(0, 0, 0, 0.06)",
        "--shadow-card-hover": "0 8px 24px rgba(0, 0, 0, 0.14), 0 0 1px rgba(0, 0, 0, 0.08)",
      },
      structure: {},
      effects: [],
      requires: ["assistant", "launcher"],
      wallpaper: "linear-gradient(160deg, #eef0f3 0%, #e6e9ee 45%, #dee2e8 100%)",
    },
  },
  {
    theme_id: "indigo",
    name: "taOS Indigo",
    builtin: true,
    config: {
      tokens: {
        // Deep indigo graphite body + a darker sidebar layer. Desaturated and
        // blue-leaning, not a saturated AI-purple. Body text at 0.85 reads 12.5:1.
        "--color-shell-bg": "#1a1b2b",
        "--color-shell-bg-deep": "#131420",
        // Indigo-tinted surface fills — additive light with a faint blue cast so
        // raised surfaces feel of-a-piece with the base rather than neutral grey.
        "--color-shell-surface": "rgba(150, 158, 230, 0.05)",
        "--color-shell-surface-hover": "rgba(150, 158, 230, 0.08)",
        "--color-shell-surface-active": "rgba(150, 158, 230, 0.11)",
        "--color-shell-border": "rgba(150, 158, 230, 0.09)",
        "--color-shell-border-strong": "rgba(150, 158, 230, 0.16)",
        // Text: 12.5:1 / 5.2:1 / 4.0:1 on the #1a1b2b body.
        "--color-shell-text": "rgba(255, 255, 255, 0.85)",
        "--color-shell-text-secondary": "rgba(255, 255, 255, 0.5)",
        "--color-shell-text-tertiary": "rgba(255, 255, 255, 0.42)",
        // Refined periwinkle indigo accent (6:1 on the body), with matching
        // tinted fills, hairline, and a brighter strong variant (10.5:1).
        "--color-accent": "#8b93e6",
        "--color-accent-glow": "rgba(139, 147, 230, 0.3)",
        "--color-accent-soft": "rgba(139, 147, 230, 0.16)",
        "--color-accent-line": "rgba(139, 147, 230, 0.4)",
        "--color-accent-strong": "#c4c8f5",
        "--color-unread": "#7c8af0",
        "--color-bubble-self": "rgba(139, 147, 230, 0.16)",
        // Frosted indigo-graphite chrome for the dock + top bar.
        "--color-dock-bg": "rgba(20, 21, 33, 0.92)",
        "--color-dock-border": "rgba(150, 158, 230, 0.1)",
        "--color-topbar-bg": "rgba(20, 21, 33, 0.92)",
        "--color-snap-preview": "rgba(139, 147, 230, 0.16)",
        "--color-snap-border": "rgba(139, 147, 230, 0.45)",
      },
      structure: {},
      effects: [],
      requires: ["assistant", "launcher"],
      // Indigo defaults to the original neural/particles animated wallpaper; the
      // user can still override it from the wallpaper picker.
      defaultWallpaperId: "neural-live",
    },
  },
];
