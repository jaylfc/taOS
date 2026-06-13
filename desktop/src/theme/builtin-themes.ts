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
    name: "Default",
    builtin: true,
    config: { tokens: {}, structure: {}, effects: [], requires: ["assistant", "launcher"], wallpaper: null },
  },
  {
    theme_id: "light",
    name: "Light",
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
    theme_id: "matrix-terminal",
    name: "Matrix Terminal",
    builtin: true,
    config: {
      tokens: {
        "--color-shell-bg": "#000800",
        "--color-shell-bg-deep": "#000400",
        "--color-shell-surface": "rgba(0, 255, 70, 0.06)",
        "--color-shell-surface-hover": "rgba(0, 255, 70, 0.10)",
        "--color-shell-surface-active": "rgba(0, 255, 70, 0.14)",
        "--color-shell-border": "rgba(0, 255, 70, 0.18)",
        "--color-shell-border-strong": "rgba(0, 255, 70, 0.35)",
        "--color-shell-text": "#33ff66",
        "--color-shell-text-secondary": "rgba(51, 255, 102, 0.7)",
        "--color-shell-text-tertiary": "rgba(51, 255, 102, 0.45)",
        "--color-accent": "#00ff46",
        "--color-accent-glow": "rgba(0, 255, 70, 0.45)",
        "--color-dock-bg": "rgba(0, 20, 0, 0.92)",
        "--color-topbar-bg": "rgba(0, 20, 0, 0.92)",
        "--font-ui": "'JetBrains Mono', 'SF Mono', ui-monospace, monospace",
        "--font-mono": "'JetBrains Mono', 'SF Mono', ui-monospace, monospace",
      },
      structure: {},
      effects: [{ module: "crt" }, { module: "scanlines" }, { module: "glow" }],
      requires: ["assistant", "launcher"],
      wallpaper: "radial-gradient(ellipse at center, #001a00 0%, #000400 100%)",
    },
  },
];
